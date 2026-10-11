"""Concurrency acceptance for independent library and overlay runtimes."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from kiosk import Runtime

ADDRESS = "35129:" + "a" * 64 + ":game"


class SharedDaemon:
    def __init__(self, fail_first=False):
        self.installed = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.installs = 0
        self.fail_first = fail_first

    def __call__(self, args, **kwargs):
        command = args[2]
        if command == "installed":
            data = {"items": [{"address": ADDRESS}] if self.installed else [], "next_offset": None}
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        assert command == "install", args
        self.installs += 1
        if self.installs == 1:
            self.started.set()
            assert self.release.wait(timeout=5)
            if self.fail_first:
                return subprocess.CompletedProcess(args, 1, "", "private error")
        self.installed = True
        return subprocess.CompletedProcess(args, 0, "{}", "")


class CrossInterfaceInstallTests(unittest.TestCase):
    def test_two_interfaces_download_once_and_waiter_rechecks_files(self):
        with tempfile.TemporaryDirectory() as root, concurrent.futures.ThreadPoolExecutor(2) as pool:
            daemon = SharedDaemon()
            first, second = Runtime(daemon, root), Runtime(daemon, root)
            download = pool.submit(first.request, "install", ADDRESS)
            self.assertTrue(daemon.started.wait(timeout=3))
            waiter = pool.submit(second.request, "install", ADDRESS)
            daemon.release.set()
            self.assertEqual(download.result(timeout=5), {})
            self.assertEqual(waiter.result(timeout=5), {"already_installed": True})
            self.assertEqual(daemon.installs, 1)
            self.assertEqual(len(list(Path(root).iterdir())), 1)
            self.assertEqual(next(Path(root).iterdir()).stat().st_mode & 0o777, 0o600)

    def test_failed_first_download_unlocks_for_waiter_retry(self):
        with tempfile.TemporaryDirectory() as root, concurrent.futures.ThreadPoolExecutor(2) as pool:
            daemon = SharedDaemon(fail_first=True)
            first, second = Runtime(daemon, root), Runtime(daemon, root)
            download = pool.submit(first.request, "install", ADDRESS)
            self.assertTrue(daemon.started.wait(timeout=3))
            waiter = pool.submit(second.request, "install", ADDRESS)
            daemon.release.set()
            with self.assertRaisesRegex(RuntimeError, "Retry"):
                download.result(timeout=5)
            self.assertEqual(waiter.result(timeout=5), {})
            self.assertEqual(daemon.installs, 2)

    def test_reconciliation_failure_never_claims_install_success(self):
        with tempfile.TemporaryDirectory() as root:
            calls = []
            def failure(args, **kwargs):
                calls.append(args[2])
                return subprocess.CompletedProcess(args, 1, "private", "secret")
            with self.assertRaisesRegex(RuntimeError, "Retry"):
                Runtime(failure, root).request("install", ADDRESS)
            self.assertEqual(calls, ["installed"])

    def test_existing_install_is_used_without_downloading(self):
        with tempfile.TemporaryDirectory() as root:
            daemon = SharedDaemon()
            daemon.installed = True
            self.assertEqual(Runtime(daemon, root).request("install", ADDRESS), {"already_installed": True})
            self.assertEqual(daemon.installs, 0)
