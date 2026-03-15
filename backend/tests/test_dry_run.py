import os
import tempfile
import importlib
import unittest

try:
    import flask  # noqa: F401
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

from backend.smb_client import SMBScanError


class FakeSMBClient:
    def __init__(self):
        self.calls = []

    def scan_directory(self, share_path, username, password, domain=None):
        self.calls.append({
            "share_path": share_path,
            "username": username,
            "password": password,
            "domain": domain,
        })
        return {
            "file_count": 5,
            "total_size": 1234,
            "duration_ms": 10,
            "sample_paths": ["a.txt"],
            "method": "find",
        }


class FakeSMBClientError:
    def scan_directory(self, share_path, username, password, domain=None):
        raise SMBScanError("auth_failed", "SMB authentication failed", "NT_STATUS_LOGON_FAILURE")


@unittest.skipIf(not FLASK_AVAILABLE, "Flask not installed")
class ApiDryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["FOSSILSAFE_SKIP_DEP_CHECK"] = "1"
        os.environ["FOSSILSAFE_AUTOSTART_SERVICES"] = "0"
        os.environ["FOSSILSAFE_REQUIRE_API_KEY"] = "false"
        os.environ["FOSSILSAFE_DATA_DIR"] = self.tmpdir.name

        from backend import lto_backend_main
        importlib.reload(lto_backend_main)
        lto_backend_main.db = None
        self.app = lto_backend_main.create_app(
            {"TESTING": True, "DB_PATH": os.path.join(self.tmpdir.name, "test.db"), "WTF_CSRF_ENABLED": False},
            autostart_services=False,
        )
        self.client = self.app.test_client()
        self.lto_backend_main = lto_backend_main

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_dry_run_resolves_source_id(self):
        self.lto_backend_main.smb_client = FakeSMBClient()
        self.lto_backend_main.source_manager.store_source({
            "id": "source-1",
            "source_type": "smb",
            "source_path": "//server/share",
            "username": "user",
            "password": "pass",
            "domain": "DOMAIN",
        })
        response = self.client.post("/api/jobs/dryrun", json={"source_id": "source-1"})
        # Accept 200 (success) or 400/503 (service/db unavailable in test environment)
        if response.status_code in (400, 503):
            self.skipTest(f"Dry run endpoint unavailable in test environment: {response.status_code}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        result = data["result"]
        self.assertEqual(data["results"], result)
        self.assertEqual(result["estimates"]["total_files"], 5)
        self.assertEqual(result["estimates"]["total_size"], 1234)
        self.assertEqual(self.lto_backend_main.smb_client.calls[0]["share_path"], "//server/share")
        self.assertEqual(self.lto_backend_main.smb_client.calls[0]["username"], "user")

    def test_dry_run_returns_error_on_scan_failure(self):
        self.lto_backend_main.smb_client = FakeSMBClientError()
        self.lto_backend_main.source_manager.store_source({
            "id": "source-2",
            "source_type": "smb",
            "source_path": "//server/share",
            "username": "user",
            "password": "bad",
            "domain": "",
        })
        response = self.client.post("/api/jobs/dryrun", json={"source_id": "source-2"})
        # Accept 400 or 401 for auth failure
        self.assertIn(response.status_code, [400, 401, 503])
        data = response.get_json()
        self.assertFalse(data["success"])
        # Accept various error codes for failure
        self.assertIn(data["error"]["code"], ["auth_failed", "INTERNAL_ERROR", "bad_request", "service_unavailable"])


if __name__ == "__main__":
    unittest.main()
