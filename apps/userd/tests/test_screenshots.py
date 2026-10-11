"""Screenshot publication preserves identity, file boundaries and ACK outcomes."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from test_userd import ManagerCase, SIGNING_PUBKEY, SIGNING_USER, userd


GAME = "35129:" + "a" * 64 + ":green-game"


class ScreenshotTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.nak.pubkey = SIGNING_PUBKEY
        token = self.manager.login_nsec("11" * 32)["token"]
        self.manager.redeem(SIGNING_USER, token)
        self.account = self.system.accounts[SIGNING_USER]
        self.directory = Path(self.account.pw_dir) / ".local/share/kwak/screenshots"
        self.directory.mkdir(parents=True)
        output = io.BytesIO()
        Image.new("RGB", (4, 3), "green").save(output, format="PNG")
        self.png = output.getvalue()
        (self.directory / "game.png").write_bytes(self.png)
        # Files are created by an unprivileged runner, while the fake identity
        # retains its managed UID. Simulate ownership only for these fstat calls.
        original_fstat = os.fstat
        def owned_fstat(descriptor):
            info = list(original_fstat(descriptor))
            info[4] = self.account.pw_uid
            return os.stat_result(info)
        owner = patch.object(userd.os, "fstat", side_effect=owned_fstat)
        owner.start()
        self.addCleanup(owner.stop)
        self.blob_hash = hashlib.sha256(self.png).hexdigest()
        self.nak.events = [
            userd.sign_event("11" * 32, {"kind": 10063, "created_at": 1, "content": "",
                                            "tags": [["server", "https://media.example"]]}),
            userd.sign_event("11" * 32, {"kind": 10002, "created_at": 1, "content": "",
                                            "tags": [["r", "wss://write.example", "write"],
                                                     ["r", "wss://read.example", "read"]]})]
        self.descriptor = {"sha256": self.blob_hash, "size": len(self.png),
                           "url": "https://media.example/" + self.blob_hash}

    def publish(self, caption="Great game"):
        return self.manager.publish_screenshot(SIGNING_USER, "game.png", caption, GAME)

    def test_upload_and_publish_exact_png_with_signed_authorization_and_note(self):
        with patch.object(self.manager, "_screenshot_upload", return_value=self.descriptor) as upload, patch.object(
            self.manager.library_relays, "publish"
        ) as publish:
            result = self.publish()
        server, data, header = upload.call_args.args
        self.assertEqual(server, "https://media.example")
        self.assertEqual(data, self.png)
        auth = json.loads(base64.b64decode(header.removeprefix("Nostr ")))
        self.assertTrue(userd.valid_signed_event(auth))
        self.assertEqual(auth["kind"], 24242)
        self.assertEqual(auth["pubkey"], SIGNING_PUBKEY)
        self.assertIn(["x", self.blob_hash], auth["tags"])
        relays, pubkey, signer, note = publish.call_args.args
        self.assertIn("wss://write.example", relays)
        self.assertNotIn("wss://read.example", relays)
        self.assertEqual(pubkey, SIGNING_PUBKEY)
        self.assertEqual(note["kind"], 1)
        self.assertTrue(userd.valid_signed_event(note))
        self.assertEqual(note["content"], "Great game\n\n" + self.descriptor["url"])
        self.assertIn(["a", GAME], note["tags"])
        self.assertIn("dim 4x3", note["tags"][0])
        self.assertEqual(result["event_id"], note["id"])

    def test_socket_cannot_override_identity_or_be_used_by_root(self):
        request = {"op": "publish_screenshot", "username": "root", "filename": "game.png", "game": GAME}
        with patch.object(self.manager, "publish_screenshot", return_value={}) as publish:
            userd.Server(self.manager).dispatch(self.account.pw_uid, request)
        self.assertEqual(publish.call_args.args[0], SIGNING_USER)
        for uid in (0, 990, 991, 39999):
            with self.assertRaisesRegex(userd.UserError, "Permission denied"):
                userd.Server(self.manager).dispatch(uid, request)

    def test_invalid_filename_caption_and_address_fail_before_network(self):
        with patch.object(self.manager, "account_settings") as settings:
            for filename in ("../game.png", "/etc/shadow", "game.png/other", "bad\x00.png", "game.jpg"):
                with self.assertRaises(userd.UserError):
                    self.manager.publish_screenshot(SIGNING_USER, filename, "", GAME)
            for caption in ("x" * 1001, "caption\x00", "\ud800", None):
                with self.assertRaises(userd.UserError):
                    self.publish(caption)
            with self.assertRaises(userd.UserError):
                self.manager.publish_screenshot(SIGNING_USER, "game.png", "", "bad-address")
        settings.assert_not_called()

    def test_symlink_file_and_directory_are_rejected(self):
        (self.directory / "linked.png").symlink_to(self.directory / "game.png")
        with self.assertRaises(userd.UserError):
            self.manager._screenshot_png(SIGNING_USER, "linked.png")
        alternate = self.directory.parent / "elsewhere"
        self.directory.rename(alternate)
        self.directory.symlink_to(alternate, target_is_directory=True)
        with self.assertRaises(userd.UserError):
            self.manager._screenshot_png(SIGNING_USER, "game.png")

    def test_foreign_owner_nonregular_file_and_corrupt_png_are_rejected(self):
        real = os.stat(self.directory / "game.png")
        foreign = SimpleNamespace(st_mode=real.st_mode, st_uid=real.st_uid + 1, st_size=real.st_size)
        with patch.object(userd.os, "fstat", return_value=foreign):
            with self.assertRaises(userd.UserError):
                self.manager._screenshot_png(SIGNING_USER, "game.png")
        foreign.st_uid = self.account.pw_uid
        foreign.st_mode = stat.S_IFIFO
        with patch.object(userd.os, "fstat", return_value=foreign):
            with self.assertRaises(userd.UserError):
                self.manager._screenshot_png(SIGNING_USER, "game.png")
        (self.directory / "game.png").write_bytes(self.png[:32])
        with self.assertRaises(userd.UserError):
            self.manager._screenshot_png(SIGNING_USER, "game.png")

    def test_missing_media_server_does_not_sign_or_publish(self):
        self.nak.events = []
        with patch.object(self.manager, "_library_sign") as sign:
            with self.assertRaisesRegex(userd.UserError, "media server"):
                self.publish()
        sign.assert_not_called()

    def test_oversized_file_is_rejected_before_reading(self):
        with (self.directory / "game.png").open("wb") as screenshot:
            screenshot.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(userd.UserError, "16 MiB"):
            self.manager._screenshot_png(SIGNING_USER, "game.png")

    def test_bad_hash_size_or_url_never_publishes_note(self):
        for descriptor in ({**self.descriptor, "sha256": "wrong"},
                           {**self.descriptor, "size": len(self.png) + 1},
                           {**self.descriptor, "url": "file:///etc/shadow"},
                           {**self.descriptor, "url": "https://other.example/" + self.blob_hash},
                           {**self.descriptor, "url": "https://["}):
            with patch.object(self.manager, "_screenshot_upload", return_value=descriptor), patch.object(
                self.manager.library_relays, "publish"
            ) as publish:
                with self.assertRaisesRegex(userd.UserError, "confirmed"):
                    self.publish()
            publish.assert_not_called()

    def test_failed_publish_ack_is_not_reported_as_success(self):
        with patch.object(self.manager, "_screenshot_upload", return_value=self.descriptor), patch.object(
            self.manager.library_relays, "publish", side_effect=userd.UserError("No relay acknowledged")
        ):
            with self.assertRaisesRegex(userd.UserError, "acknowledged"):
                self.publish()

    def test_bunker_signs_upload_and_note_without_held_local_key(self):
        entry = self.manager.managed(SIGNING_USER)
        with self.manager.registry() as registry:
            registry[SIGNING_USER]["method"] = "bunker"
        self.manager.held.clear()
        uri = f"bunker://{entry['pubkey']}?relay=wss%3A%2F%2Fsigner.example"
        self.manager.key_dir(SIGNING_USER).mkdir(parents=True)
        (self.manager.key_dir(SIGNING_USER) / "key.json").write_text(json.dumps({"bunker": uri}))
        def sign(template, signer, home):
            self.assertEqual(signer, uri)
            self.assertEqual(home, self.manager.key_dir(SIGNING_USER) / "nak")
            return userd.sign_event("11" * 32, template)
        with patch.object(self.nak, "sign", side_effect=sign) as signing, patch.object(
            self.manager, "_screenshot_upload", return_value=self.descriptor
        ), patch.object(self.manager.library_relays, "publish"):
            self.publish()
        self.assertEqual([call.args[0]["kind"] for call in signing.call_args_list], [24242, 1])

    def test_http_upload_has_deadline_response_limit_and_no_redirect(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count):
                self_count.append(count)
                return json.dumps(self_descriptor).encode()
        self_count, self_descriptor = [], self.descriptor
        with patch.object(userd.urllib.request, "build_opener") as factory:
            factory.return_value.open.return_value = Response()
            self.assertEqual(self.manager._screenshot_upload("https://media.example", self.png, "Nostr test"), self.descriptor)
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://media.example/upload")
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(factory.return_value.open.call_args.kwargs["timeout"], 20)
        self.assertEqual(self_count, [64 * 1024 + 1])
        handler = factory.call_args.args[0]()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://elsewhere.example"))
