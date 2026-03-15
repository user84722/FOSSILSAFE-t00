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
class ApiKeyAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["FOSSILSAFE_SKIP_DEP_CHECK"] = "1"
        os.environ["FOSSILSAFE_AUTOSTART_SERVICES"] = "0"
        os.environ["FOSSILSAFE_REQUIRE_API_KEY"] = "true"
        os.environ["FOSSILSAFE_DATA_DIR"] = self.tmpdir.name

        self.config_path = os.path.join(self.tmpdir.name, "config.json")
        with open(self.config_path, "w") as handle:
            handle.write('{"api_key": "test-key"}')
        os.environ["FOSSILSAFE_CONFIG_PATH"] = self.config_path

        from backend import lto_backend_main
        importlib.reload(lto_backend_main)
        lto_backend_main.db = None
        self.app = lto_backend_main.create_app(
            {"TESTING": True, "DB_PATH": os.path.join(self.tmpdir.name, "test.db")},
            autostart_services=False,
        )
        self.client = self.app.test_client()
        self.lto_backend_main = lto_backend_main

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_status_requires_api_key(self):
        response = self.client.get("/api/system/info")
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/api/system/info?api_key=test-key")
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/system/info", headers={"X-API-Key": "bad-key"})
        self.assertEqual(response.status_code, 403)

    def test_diagnostics_download_with_query_key(self):
        json_path = os.path.join(self.tmpdir.name, "diag.json")
        text_path = os.path.join(self.tmpdir.name, "diag.txt")
        with open(json_path, "w") as handle:
            handle.write('{"ok": true}')
        with open(text_path, "w") as handle:
            handle.write("ok")

        report_id = self.lto_backend_main.db.add_diagnostics_report(
            job_id=1,
            status="pass",
            summary="ok",
            report_json_path=json_path,
            report_text_path=text_path,
        )

        response = self.client.get(f"/api/diagnostics/reports/{report_id}/download?kind=json&api_key=test-key")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
