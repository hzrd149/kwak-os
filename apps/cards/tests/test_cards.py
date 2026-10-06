import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cards  # noqa: E402
from skc_cards import CardData, encode_skc1, encode_skc2, encode_skc3  # noqa: E402
from skc_cards.codec import encode_skc3_payload  # noqa: E402
from skc_cards.device import DeviceError  # noqa: E402
from skc_cards.nip49 import decode_ncryptsec, encrypt_secret_key  # noqa: E402


SECRET = bytes.fromhex("11" * 32)


class FakeReader:
    """Yields each card in turn, then raises ``end``."""

    def __init__(self, cards, end):
        self.cards = list(cards)
        self.end = end
        self.open = False
        self.closed = False

    def __enter__(self):
        self.open = True
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self):
        if self.cards:
            return self.cards.pop(0)
        raise self.end


class ListenTests(unittest.TestCase):
    def listen(self, swipes, end=cards.Stop(), delivered=True, fail=False):
        sent, logs = [], []

        def send(event):
            sent.append(event)
            if fail:
                raise OSError("connection refused")
            return {"delivered": delivered}

        reader = FakeReader(swipes, end)
        status = cards.listen(reader, send=send, log=logs.append)
        self.assertTrue(reader.closed)
        return status, sent, logs

    def test_skc1_card_sends_secret_key(self):
        status, sent, logs = self.listen([encode_skc1(SECRET)])
        self.assertEqual(status, 0)
        self.assertEqual(sent, [{"format": "SKC1", "secret_key": "11" * 32}])
        self.assertNotIn("11" * 32, "\n".join(logs))

    def test_skc2_card_sends_ncryptsec(self):
        payload = encrypt_secret_key(SECRET, "hunter22", log_n=4)
        _, sent, logs = self.listen([encode_skc2(payload)])
        self.assertEqual(sent[0]["format"], "SKC2")
        self.assertEqual(decode_ncryptsec(sent[0]["ncryptsec"]), payload)
        self.assertNotIn(sent[0]["ncryptsec"], "\n".join(logs))

    def test_unreadable_card_reports_error_and_keeps_reading(self):
        _, sent, _ = self.listen([CardData("SKC9NOPE"), encode_skc1(SECRET)])
        self.assertEqual(sent, [{"error": "card_format"},
                                {"format": "SKC1", "secret_key": "11" * 32}])

    def test_skc3_card_sends_bunker_connection(self):
        # The x-only public key of secret key 1, which is on the curve.
        signer = bytes.fromhex(
            "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798")
        local = bytes.fromhex("22" * 32)
        payload = encode_skc3_payload(signer, local, ["wss://relay.nsec.app",
                                                      "ws://10.0.0.2:7777"])
        _, sent, logs = self.listen([encode_skc3(payload)])
        self.assertEqual(sent, [{
            "format": "SKC3",
            "bunker": f"bunker://{signer.hex()}?relay=wss%3A%2F%2Frelay.nsec.app"
                      "&relay=ws%3A%2F%2F10.0.0.2%3A7777",
            "client_key": "22" * 32,
        }])
        self.assertNotIn("22" * 32, "\n".join(logs))

    def test_other_formats_are_unreadable(self):
        with patch.object(cards, "decode_card", return_value=("SKC9", b"")):
            _, sent, _ = self.listen([CardData("SKC9")])
        self.assertEqual(sent, [{"error": "card_format"}])

    def test_device_error_exits_nonzero(self):
        status, _, logs = self.listen([], end=DeviceError("unplugged"))
        self.assertEqual(status, 1)
        self.assertIn("reader failed: unplugged", logs)

    def test_unavailable_userd_keeps_reading(self):
        status, sent, logs = self.listen([encode_skc1(SECRET)] * 2, fail=True)
        self.assertEqual((status, len(sent)), (0, 2))
        self.assertTrue(any("unavailable" in line for line in logs))

    def test_ignored_swipe_is_logged(self):
        _, _, logs = self.listen([encode_skc1(SECRET)], delivered=False)
        self.assertTrue(any("ignored" in line for line in logs))

    def test_usage(self):
        self.assertEqual(cards.main([]), 2)


if __name__ == "__main__":
    unittest.main()
