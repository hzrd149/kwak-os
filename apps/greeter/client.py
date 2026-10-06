"""Client for the kwak-userd socket: one newline-delimited JSON request per connection."""

import json
import socket


SOCKET_PATH = "/run/kwak-userd.sock"


class UserError(Exception):
    """A failure that can be shown to the person signing in."""


def request(op, path=SOCKET_PATH, timeout=180, **fields):
    """Send one request to kwak-userd and return its result or raise UserError."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(path)
            stream = sock.makefile("rwb")
            stream.write(json.dumps({"op": op, **fields}).encode() + b"\n")
            stream.flush()
            response = json.loads(stream.readline() or b"{}")
        except OSError as error:
            raise UserError(f"The user service is unavailable: {error}") from error
    if not response.get("ok"):
        raise UserError(response.get("error") or "No response from the user service.")
    return response["result"]
