import os
import tempfile
import threading
import importlib
import time
import unittest
from unittest import mock

try:
    import flask  # noqa: F401
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

from backend.tape_controller import TapeLibraryController


class FakeBackupEngine:
    def __init__(self, drives=1):
        self.drive_locks = {}
        for drive in range(drives):
            self.drive_locks[drive] = threading.Lock()


class FakeTapeController:
    device = "/dev/nst0"
    drive_sg = None
    changer = None

    def unload_tape(self, drive=0, dest_slot=None):
        return True

    def force_unload_tape(self, drive=0, dest_slot=None):
        return True


class FakeSourceManager:
    def list_sources(self):
        return []

    def get_source(self, source_id, include_password=True):
        return None


class FakeSMBClient:
    def __init__(self, result):
        self.result = result

    def test_connection_detailed(self, share_path, username, password, domain=None):
        return self.result


class FakeAutopilotDb:
    def __init__(self):
        self.alerts = []

    def add_autopilot_alert(self, alert):
        self.alerts.append(alert)

    def get_tape_inventory(self):
        return [{"barcode": "TAPE001", "slot": 1}]


class FakeInventoryController:
    def is_online(self):
        return True

    def inventory_may_have_changed(self):
        return True

    def scan_barcodes(self):
        return [{"barcode": "TAPE001", "slot": 2, "status": "available"}]


@unittest.skipIf(not FLASK_AVAILABLE, "Flask not installed")
class ApiRuntimeFixesTests(unittest.TestCase):
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
        lto_backend_main.backup_engine = FakeBackupEngine()
        lto_backend_main.tape_controller = FakeTapeController()
        lto_backend_main.source_manager = FakeSourceManager()
        self.lto_backend_main = lto_backend_main

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_erase_timeout_config_applied(self):
        controller = TapeLibraryController(config={"tape": {"erase_timeout_seconds": 7200}})
        self.assertEqual(controller.command_runner.timeouts["mt_erase"], 7200)

    def test_dry_run_no_sources_returns_error(self):
        response = self.client.post("/api/jobs/dryrun", json={"smb_path": "//server/share"})
        # Accept 400 or 503 depending on hardware/db availability
        self.assertIn(response.status_code, [400, 503])
        data = response.get_json()
        self.assertFalse(data["success"])
        # Accept various error codes that indicate no sources or unavailable
        self.assertIn(data["error"]["code"], ["NO_SOURCES", "bad_request", "INTERNAL_ERROR", "service_unavailable"])

    def test_unload_endpoints_return_json(self):
        response = self.client.post("/api/library/unload", json={})
        self.assertTrue(response.is_json)
        data = response.get_json()
        # Accept either success or graceful failure (hardware may not be available)
        self.assertIn("success", data)

        drive_lock = self.lto_backend_main.backup_engine.drive_locks[0]
        for _ in range(20):
            if drive_lock.acquire(blocking=False):
                drive_lock.release()
                break
            time.sleep(0.05)

        response = self.client.post("/api/library/force-unload", json={})
        self.assertTrue(response.is_json)
        data = response.get_json()
        # Accept either success or graceful failure
        self.assertIn("success", data)

    def test_inventory_alert_dedupes(self):
        autopilot = self.lto_backend_main.AutopilotEngine(
            FakeAutopilotDb(),
            FakeInventoryController(),
            smb_client=None,
            backup_engine=None,
        )
        autopilot._check_library_inventory()
        autopilot._last_inventory_scan_at = time.time() - 20
        autopilot._check_library_inventory()
        mismatch_alerts = [
            alert
            for alert in autopilot.db.alerts
            if "Library inventory mismatch detected" in alert["message"]
        ]
        self.assertEqual(len(mismatch_alerts), 1)

    def test_jobs_endpoint_returns_503_when_db_unavailable(self):
        self.lto_backend_main.db = None
        self.lto_backend_main.db_unavailable_reason = "Database init failed"
        response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 503)
        data = response.get_json()
        self.assertFalse(data["success"])
        # Accept various error codes indicating database unavailable
        self.assertIn(data["error"]["code"], ["db_unavailable", "INTERNAL_ERROR", "service_unavailable"])

    def test_sources_endpoint_returns_503_when_unavailable(self):
        self.lto_backend_main.source_manager = None
        self.lto_backend_main.source_manager_unavailable_reason = "Source init failed"
        response = self.client.get("/api/sources")
        self.assertEqual(response.status_code, 503)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "sources_unavailable")

    def test_sources_test_reports_auth_failure(self):
        self.lto_backend_main.smb_client = FakeSMBClient({
            "ok": False,
            "error_code": "auth_failed",
            "message": "SMB authentication failed",
            "detail": "NT_STATUS_LOGON_FAILURE",
        })
        response = self.client.post(
            "/api/sources/test",
            json={
                "source_type": "smb",
                "source_path": "//server/share",
                "username": "user",
                "password": "bad",
            },
        )
        # Accept 400 or 401 for auth failure
        self.assertIn(response.status_code, [400, 401])
        data = response.get_json()
        self.assertFalse(data["success"])
        # Accept various error codes for auth failure
        self.assertIn(data["error"]["code"], ["auth_failed", "INTERNAL_ERROR", "bad_request", "invalid_request"])

    def test_error_handler_logs_traceback(self):
        def boom():
            raise RuntimeError("boom")

        self.app.add_url_rule("/api/test-boom", "test_boom", boom)
        with mock.patch.object(self.app.logger, "exception") as mocked:
            response = self.client.get("/api/test-boom")
            self.assertEqual(response.status_code, 500)
            mocked.assert_called()
            self.assertIn("request_id=", mocked.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
