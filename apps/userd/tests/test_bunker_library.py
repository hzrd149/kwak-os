"""Library persistence belongs to kwakore, not the account service."""
import unittest
from test_userd import userd


class NativeLibraryOwnershipTests(unittest.TestCase):
    def test_account_service_has_no_library_sync_methods(self):
        self.assertFalse(hasattr(userd.UserManager, "napplet_library"))
        self.assertFalse(hasattr(userd.UserManager, "update_napplet_library"))
