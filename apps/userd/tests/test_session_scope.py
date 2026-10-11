"""UWSM user-manager processes have no session.scope ancestry."""
from test_userd import ManagerCase, USER, userd


class SessionScopeTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.manager.login_nsec("11" * 32)
        self.sessions.add("c5", USER)
        self.sessions.active_id = "c5"

    def test_user_manager_process_can_switch_its_visible_account(self):
        self.assertIsNone(self.sessions.of_pid(700))
        self.assertEqual(self.manager.switch_account(USER, 700), {"opened": True})
        self.assertTrue(any("freeze" in command for command in self.system.commands))

    def test_no_peer_pid_cannot_use_visible_session_fallback(self):
        with self.assertRaisesRegex(userd.UserError, "your own session"):
            self.manager.switch_account(USER, None)

    def test_background_identity_cannot_switch_other_visible_account(self):
        self.sessions.add("c6", "n0000000000")
        self.sessions.active_id = "c6"
        with self.assertRaisesRegex(userd.UserError, "your own session"):
            self.manager.switch_account(USER, 700)
        self.assertFalse(any("freeze" in command for command in self.system.commands))

    def test_known_foreign_session_does_not_use_visible_session_fallback(self):
        self.sessions.add("c6", "n0000000000", pid=700)
        with self.assertRaisesRegex(userd.UserError, "your own session"):
            self.manager.switch_account(USER, 700)
