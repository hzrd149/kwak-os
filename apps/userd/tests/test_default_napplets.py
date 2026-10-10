import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install_default_napplets import install_defaults


class DefaultNappletsTests(unittest.TestCase):
    def test_retries_failures_and_keeps_successful_removals_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            manifest = home / ".config/kwak/default-napplets.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps(["first", "second"]))
            with patch("install_default_napplets.subprocess.run") as run:
                run.side_effect = [
                    type("Result", (), {"returncode": 0})(),
                    type("Result", (), {"returncode": 1})(),
                    type("Result", (), {"returncode": 0})(),
                ]
                self.assertFalse(install_defaults(home, "/bin/kwakore"))
                self.assertTrue(install_defaults(home, "/bin/kwakore"))
                self.assertTrue(install_defaults(home, "/bin/kwakore"))
                self.assertEqual([call.args for call in run.call_args_list], [
                    (["/bin/kwakore", "install", "first"],),
                    (["/bin/kwakore", "install", "second"],),
                    (["/bin/kwakore", "install", "second"],),
                ])


if __name__ == "__main__":
    unittest.main()
