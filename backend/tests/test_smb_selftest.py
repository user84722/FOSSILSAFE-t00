import os
import shutil
import tempfile
import importlib
import socket
import unittest
from unittest import mock

try:
    import flask  # noqa: F401
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

from backend.smb_client import SMBClient
from backend.smb_fixture import start_smb_fixture


@unittest.skipIf(not FLASK_AVAILABLE, "Flask not installed")
class SMBFixtureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["FOSSILSAFE_SKIP_DEP_CHECK"] = "1"
        os.environ["FOSSILSAFE_AUTOSTART_SERVICES"] = "0"
        os.environ["FOSSILSAFE_REQUIRE_API_KEY"] = "false"
        os.environ["FOSSILSAFE_DATA_DIR"] = self.tmpdir.name

        from backend import lto_backend_main
        importlib.reload(lto_backend_main)
        lto_backend_main.db = None
        lto_backend_main.smb_client = SMBClient()
        self.app = lto_backend_main.create_app(
            {"TESTING": True, "DB_PATH": os.path.join(self.tmpdir.name, "test.db"), "WTF_CSRF_ENABLED": False},
            autostart_services=False,
        )
        self.client = self.app.test_client()
        self.lto_backend_main = lto_backend_main

    def tearDown(self):
        self.tmpdir.cleanup()

    def _skip_if_missing_tools(self):
        import platform
        if platform.system() == "Darwin":
            self.skipTest("SMB fixture tests are not supported on macOS (Apple smbd is incompatible with Samba flags).")
        if not shutil.which("smbd"):
            self.skipTest("smbd not available; skipping SMB fixture integration test.")
        if not shutil.which("smbclient"):
            self.skipTest("smbclient not available; skipping SMB fixture integration test.")

    def test_smb_selftest_endpoint(self):
        self._skip_if_missing_tools()
        response = self.client.post("/api/diagnostics/smb_selftest", json={})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("result", payload)
        self.assertGreater(payload["result"]["file_count"], 0)
        self.assertGreater(payload["result"]["total_bytes"], 0)

    def test_smb_fixture_listens_on_port(self):
        self._skip_if_missing_tools()
        with start_smb_fixture() as fixture:
            with socket.create_connection(("127.0.0.1", fixture.port), timeout=1):
                pass

    def test_smb_selftest_reports_missing_smbd(self):
        with mock.patch("backend.smb_fixture.shutil.which") as which_mock:
            def _fake_which(cmd):
                return None if cmd == "smbd" else f"/usr/bin/{cmd}"
            which_mock.side_effect = _fake_which
            with mock.patch("backend.smb_selftest._run_smbclient_version_check") as version_check:
                version_check.return_value = {
                    "success": True,
                    "command": ["smbclient", "-V"],
                    "returncode": 0,
                    "stdout": "smbclient 4.0",
                    "stderr": "",
                    "message": "smbclient available",
                }
                response = self.client.post("/api/diagnostics/smb_selftest", json={})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["strategy_used"], "smbclient_check")
        diagnostics = payload.get("diagnostics") or {}
        self.assertIn("sandbox", diagnostics)

    def test_dry_run_uses_fixture(self):
        self._skip_if_missing_tools()
        with start_smb_fixture() as fixture:
            response = self.client.post("/api/jobs/dryrun", json={
                "source_path": fixture.share_path,
                "username": fixture.username,
                "password": fixture.password,
                "scan_mode": "full",
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        result = payload["result"]
        self.assertGreater(result["estimates"]["total_files"], 0)
        self.assertGreater(result["estimates"]["total_size"], 0)


if __name__ == "__main__":
    unittest.main()
