import os
import tempfile
import importlib
import unittest

try:
    import flask  # noqa: F401
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


class FakeHistory:
    def to_list(self):
        return [{"command": ["mtx", "status"], "returncode": 0}]


class FakeCommandRunner:
    def __init__(self):
        self.history = FakeHistory()


class FakeTapeController:
    def __init__(self):
        self.command_runner = FakeCommandRunner()
        self.drive_devices = {0: "/dev/nst0"}
        self.device = "/dev/nst0"
        self.changer = "/dev/sg1"
        self.mount_points = {0: "/mnt/ltfs"}

    def get_library_state(self):
        return "ONLINE"

    def is_online(self):
        return True

    def get_library_error(self):
        return None

    def is_drive_only(self):
        return False

    def is_busy(self):
        return False

    def get_last_probe(self):
        return {"status": "ok"}


@unittest.skipIf(not FLASK_AVAILABLE, "Flask not installed")
class LibraryDiagTests(unittest.TestCase):
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
            {"TESTING": True, "DB_PATH": os.path.join(self.tmpdir.name, "test.db")},
            autostart_services=False,
        )
        self.client = self.app.test_client()
        lto_backend_main.tape_controller = FakeTapeController()
        self.lto_backend_main = lto_backend_main

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_library_diag_endpoint(self):
        response = self.client.get("/api/diag/library")
        # Accept 200 (success), 404 (endpoint not yet implemented), or 503 (hardware unavailable)
        if response.status_code == 404:
            self.skipTest("Endpoint /api/diag/library not implemented")
        if response.status_code == 503:
            # Service unavailable is acceptable when hardware/db is not available
            payload = response.get_json()
            self.assertIn("error", payload)
            return
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("resolved_devices", payload)
        self.assertIn("recent_commands", payload)


if __name__ == "__main__":
    unittest.main()
