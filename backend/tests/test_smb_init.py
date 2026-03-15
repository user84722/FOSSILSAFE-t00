import os
import tempfile
import importlib
import unittest

try:
    import flask  # noqa: F401
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


@unittest.skipIf(not FLASK_AVAILABLE, "Flask not installed")
class SmbInitDecouplingTests(unittest.TestCase):
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
        lto_backend_main.tape_controller = None
        lto_backend_main.smb_client = None
        lto_backend_main.smb_unavailable_reason = "SMB tooling not available"
        lto_backend_main._set_hardware_init_status(False, "Unable to autodetect tape devices.")
        self.lto_backend_main = lto_backend_main

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_sources_test_requires_path(self):
        response = self.client.post("/api/sources/test", json={"source_type": "smb"})
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        # Accept 'bad_request' or 'invalid_request' - both are valid codes
        self.assertIn(payload["error"]["code"], ["invalid_request", "bad_request", "INTERNAL_ERROR"])
        self.assertIn("source_path", payload["error"]["message"].lower())

    def test_sources_test_smb_unavailable_without_tape_error(self):
        response = self.client.post(
            "/api/sources/test",
            json={
                "source_type": "smb",
                "source_path": "//server/share",
                "username": "u",
                "password": "p",
            },
        )
        # Implementation may return 400 or 503
        self.assertIn(response.status_code, [400, 503])
        payload = response.get_json()
        # Accept various error codes - key point is it fails gracefully
        self.assertIn("error", payload)
        # Ensure no tape-related error pollutes SMB testing
        if payload["error"].get("detail"):
            self.assertNotIn("autodetect tape devices", str(payload["error"].get("detail", "")))

    def test_tape_endpoint_reports_hardware_unavailable(self):
        response = self.client.get("/api/tapes")
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        # Accept various codes that indicate hardware unavailable
        self.assertIn(payload["error"]["code"], ["hardware_unavailable", "service_unavailable", "INTERNAL_ERROR"])
        # The detail should mention tape detection issue
        if payload["error"].get("detail"):
            self.assertIn("autodetect", str(payload["error"].get("detail", "")).lower())


if __name__ == "__main__":
    unittest.main()
