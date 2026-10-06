#!/usr/bin/env python3
"""Pass Nostr swipe card reads from an MSR90 reader to kwak-userd.

SKC1 cards carry a secret key, SKC2 cards an ncryptsec, and SKC3 cards a
bunker connection (an nbunksec without its connect secret).

``kwak-cards read /dev/hidrawN`` holds the reader for as long as it runs. The
reader's keyboard output is inhibited meanwhile, so a swipe is never typed
into a window; every read goes to kwak-userd, which drops it unless the
sign-in screen is waiting for one. Card contents are never logged.
"""

import json
from pathlib import Path
import signal
import socket
import sys
from urllib.parse import urlencode

from skc_cards import MSR90Reader, decode_card
from skc_cards.codec import CardFormatError
from skc_cards.device import DeviceError
from skc_cards.nbunksec import from_skc3_payload
from skc_cards.nip49 import encode_ncryptsec


SOCKET_PATH = "/run/kwak-userd.sock"


class Stop(Exception):
    """SIGTERM: leave the reader cleanly so its keyboard is restored."""


def send(event, path=SOCKET_PATH, timeout=30):
    """Send one card_swipe request to kwak-userd; failures are not fatal."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        stream = sock.makefile("rwb")
        stream.write(json.dumps({"op": "card_swipe", **event}).encode() + b"\n")
        stream.flush()
        response = json.loads(stream.readline() or b"{}")
    if not response.get("ok"):
        raise OSError(response.get("error") or "no response")
    return response["result"]


def card_event(card):
    """The card_swipe fields for one read; raises CardFormatError for unknown cards."""
    kind, value = decode_card(card.track1, card.track2, card.track3)
    if kind == "SKC1":
        return {"format": "SKC1", "secret_key": value.hex()}
    if kind == "SKC2":
        return {"format": "SKC2", "ncryptsec": encode_ncryptsec(value)}
    if kind == "SKC3":
        # A bunker connection the signer has already paired with the card's
        # local key, so it needs no connect secret.
        info = from_skc3_payload(value)
        relays = urlencode([("relay", relay) for relay in info.relays])
        return {"format": "SKC3", "bunker": f"bunker://{info.pubkey.hex()}?{relays}",
                "client_key": info.local_key.hex()}
    raise CardFormatError(f"unsupported card format {kind}")


def listen(reader, send=send, log=None):
    """Read swipes until the reader fails. Returns the exit status."""
    log = log or (lambda message: print(f"kwak-cards: {message}", file=sys.stderr))
    try:
        with reader:
            log("listening for card swipes")
            while True:
                try:
                    event = card_event(reader.read())
                except CardFormatError:
                    log("unreadable card")
                    event = {"error": "card_format"}
                try:
                    result = send(event)
                    if not result.get("delivered"):
                        log("swipe ignored: the sign-in screen is not waiting")
                except OSError as error:
                    log(f"kwak-userd is unavailable: {error}")
                finally:
                    event = None
    except Stop:
        return 0
    except DeviceError as error:
        log(f"reader failed: {error}")
        return 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "read":
        print("usage: kwak-cards read /dev/hidrawN", file=sys.stderr)
        return 2

    def stop(_signum, _frame):
        raise Stop

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return listen(MSR90Reader(path=Path(argv[1]), read_timeout=None))


if __name__ == "__main__":
    raise SystemExit(main())
