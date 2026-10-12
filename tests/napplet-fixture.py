"""Local signed napplets, NIP-01 relay and content-addressed Blossom fixture.

Run inside a disposable VM. Requires nak, openssl and Python websockets.
No runtime APIs are mocked: kwakore downloads, verifies and runs actual HTML.
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import ssl
import shutil
import subprocess
import threading
import time


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sign(nak, secret, kind, content, tags):
    template = {"kind": kind, "created_at": int(time.time()), "content": content, "tags": tags}
    result = subprocess.run([nak, "event"], input=json.dumps(template), capture_output=True,
                            text=True, check=True, timeout=10,
                            env={**os.environ, "NOSTR_SECRET_KEY": secret})
    return json.loads(result.stdout)


def prepare(args):
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "blobs").mkdir(exist_ok=True)
    html = Path(__file__).with_suffix(".html").read_text()
    events, games = [], []
    for identifier in ("kiosk-heartbeat", "kiosk-second", "kiosk-trial"):
        artifact = html.replace("__FIXTURE_ID__", identifier).encode()
        blob_hash = digest(artifact)
        aggregate = digest(f"{blob_hash} /index.html\n".encode())
        (directory / "blobs" / blob_hash).write_bytes(artifact)
        event = sign(args.nak, "33" * 32, 35129, "", [
            ["d", identifier], ["title", identifier.replace("-", " ").title()],
            ["description", "Real sandboxed kiosk test: heartbeat, focus, score and audio."],
            ["path", "/index.html", blob_hash], ["x", aggregate, "aggregate"],
            ["requires", "storage"], ["t", "game"]])
        address = f"35129:{event['pubkey']}:{identifier}"
        games.append({"identifier": identifier, "address": address, "blob_hash": blob_hash,
                      "artifact_hash": aggregate,
                      "storage_filename": digest(f"{address}\0{aggregate}".encode()) + ".json"})
        events.append(event)
    relay = f"wss://localhost:{args.relay_port}"
    blossom = f"http://127.0.0.1:{args.blossom_port}"
    upload_server = f"https://localhost:{args.upload_port}"
    # Use the exact encrypted native sync format; this is test fixture data.
    import importlib.util
    source = Path(__file__).resolve().parents[1] / "apps/userd/userd.py"
    spec = importlib.util.spec_from_file_location("fixture_signer", source)
    signer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(signer)
    pubkey = sign(args.nak, "11" * 32, 0, "", [])["pubkey"]
    payload = {"v": 1, "napplets": [{"address": game["address"], "event_id": event["id"]}
               for game, event in zip(games[:2], events[:2])] if args.saved else []}
    library = sign(args.nak, "11" * 32, 30078,
                   signer.nip44_encrypt("11" * 32, pubkey, json.dumps(payload)),
                   [["d", "kwak-library"]])
    events.extend([library, sign(args.nak, "11" * 32, 0,
                               json.dumps({"name": "Kiosk Test Player"}), []),
                   sign(args.nak, "11" * 32, 10002, "", [["r", relay]]),
                   sign(args.nak, "11" * 32, 10063, "", [["server", upload_server]])])
    (directory / "events.json").write_text(json.dumps(events))
    (directory / "kwakore-config.json").write_text(json.dumps({
        "relays": [relay], "blossom_servers": [blossom], "discover_on_user_relays": False,
        "sync_id": "kwak-library",
        "signer": {"mode": "system", "socket": "/run/kwak-userd.sock"}}))
    metadata = {"relay": relay, "blossom": blossom, "upload_server": upload_server, "games": games,
                "player_pubkey": library["pubkey"], "storage_key": "kiosk.fixture",
                "certificate": str(directory / "relay-cert.pem")}
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2))
    if args.tls_cert and args.tls_key:
        shutil.copyfile(args.tls_cert, directory / "relay-cert.pem")
        shutil.copyfile(args.tls_key, directory / "relay-key.pem")
    else:
        subprocess.run([args.openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(directory / "relay-key.pem"), "-out", str(directory / "relay-cert.pem"),
                    "-days", "2", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
    (directory / "relay-key.pem").chmod(0o600)
    print(json.dumps(metadata))


def matches(event, filters):
    for criterion in filters:
        if not isinstance(criterion, dict):
            continue
        if "kinds" in criterion and event["kind"] not in criterion["kinds"]:
            continue
        if "authors" in criterion and not any(event["pubkey"].startswith(author) for author in criterion["authors"]):
            continue
        if "ids" in criterion and not any(event["id"].startswith(identifier) for identifier in criterion["ids"]):
            continue
        if event["created_at"] < criterion.get("since", 0) or event["created_at"] > criterion.get("until", float("inf")):
            continue
        if any(not any(len(tag) > 1 and tag[0] == key[1:] and tag[1] in values for tag in event["tags"])
               for key, values in criterion.items() if key.startswith("#")):
            continue
        return True
    return False


def serve(args):
    from websockets.sync.server import serve as websocket_serve
    directory = Path(args.directory)
    events = json.loads((directory / "events.json").read_text())
    lock = threading.RLock()
    subscribers = {}

    def handler(connection):
        try:
            for raw in connection:
                frame = json.loads(raw)
                if frame[0] == "REQ" and len(frame) >= 3:
                    subscription, filters = frame[1], frame[2:]
                    with lock:
                        subscribers[(connection, subscription)] = filters
                        selected = sorted((event for event in events if matches(event, filters)),
                                          key=lambda event: (-event["created_at"], event["id"]))
                    # Each requested filter's limit constrains historical results.
                    limit = max((item.get("limit", len(selected)) for item in filters), default=len(selected))
                    for event in selected[:limit]:
                        connection.send(json.dumps(["EVENT", subscription, event]))
                    connection.send(json.dumps(["EOSE", subscription]))
                elif frame[0] == "CLOSE" and len(frame) == 2:
                    with lock:
                        subscribers.pop((connection, frame[1]), None)
                elif frame[0] == "EVENT" and len(frame) == 2:
                    event = frame[1]
                    verified = subprocess.run([args.nak, "verify"], input=json.dumps(event), text=True,
                                              capture_output=True, timeout=10).returncode == 0
                    connection.send(json.dumps(["OK", event.get("id", ""), verified,
                                                "" if verified else "invalid: bad signature"]))
                    if not verified:
                        continue
                    with lock:
                        if not any(item["id"] == event["id"] for item in events):
                            events.append(event)
                            (directory / "events.json").write_text(json.dumps(events))
                        recipients = list(subscribers.items())
                    for (recipient, subscription), filters in recipients:
                        if matches(event, filters):
                            try:
                                recipient.send(json.dumps(["EVENT", subscription, event]))
                            except Exception:
                                pass
        finally:
            with lock:
                for key in list(subscribers):
                    if key[0] is connection:
                        del subscribers[key]

    class Blossom(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split("?", 1)[0].strip("/")
            if len(name) != 64 or any(char not in "0123456789abcdef" for char in name):
                self.send_error(404)
                return
            path = directory / "blobs" / name
            if not path.is_file():
                self.send_error(404)
                return
            blob = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if blob.startswith(b"\x89PNG\r\n\x1a\n") else "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_PUT(self):
            if self.path != "/upload":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16 * 1024 * 1024:
                    raise ValueError("invalid size")
                authorization = self.headers.get("Authorization", "")
                if not authorization.startswith("Nostr "):
                    raise ValueError("missing authorization")
                auth = json.loads(base64.b64decode(authorization[6:], validate=True))
                verified = subprocess.run([args.nak, "verify"], input=json.dumps(auth), text=True,
                                          capture_output=True, timeout=10).returncode == 0
                tags = {tag[0]: tag[1] for tag in auth["tags"] if len(tag) > 1}
                if (not verified or auth["kind"] != 24242 or tags.get("t") != "upload" or
                        int(tags.get("expiration", "0")) <= int(time.time())):
                    raise ValueError("invalid authorization")
                blob = self.rfile.read(size)
                blob_hash = digest(blob)
                if len(blob) != size or not blob.startswith(b"\x89PNG\r\n\x1a\n") or tags.get("x") != blob_hash:
                    raise ValueError("invalid PNG or authorization hash")
            except (ValueError, KeyError, TypeError):
                self.send_error(403)
                return
            (directory / "blobs" / blob_hash).write_bytes(blob)
            result = json.dumps({"url": f"https://localhost:{args.upload_port}/{blob_hash}",
                                 "sha256": blob_hash, "size": size, "type": "image/png"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    blob_server = http.server.ThreadingHTTPServer(("127.0.0.1", args.blossom_port), Blossom)
    thread = threading.Thread(target=blob_server.serve_forever, daemon=True)
    thread.start()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(directory / "relay-cert.pem", directory / "relay-key.pem")
    upload_server = http.server.ThreadingHTTPServer(("127.0.0.1", args.upload_port), Blossom)
    upload_server.socket = tls.wrap_socket(upload_server.socket, server_side=True)
    upload_thread = threading.Thread(target=upload_server.serve_forever, daemon=True)
    upload_thread.start()
    try:
        with websocket_serve(handler, "127.0.0.1", args.relay_port, ssl=tls, compression=None) as relay:
            print("napplet fixture ready", flush=True)
            relay.serve_forever()
    finally:
        blob_server.shutdown()
        upload_server.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve"))
    parser.add_argument("--directory", required=True)
    parser.add_argument("--nak", default="nak")
    parser.add_argument("--openssl", default="openssl")
    parser.add_argument("--tls-cert", help="Existing trusted test certificate to copy into fixture")
    parser.add_argument("--tls-key", help="Private key for --tls-cert")
    parser.add_argument("--relay-port", type=int, default=18443)
    parser.add_argument("--blossom-port", type=int, default=18080)
    parser.add_argument("--upload-port", type=int, default=18444)
    parser.add_argument("--saved", action="store_true", help="Preseed first two games in player11's NIP-78 library")
    args = parser.parse_args()
    (prepare if args.action == "prepare" else serve)(args)


if __name__ == "__main__":
    main()
