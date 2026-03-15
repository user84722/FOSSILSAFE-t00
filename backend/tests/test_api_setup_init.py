import os
import tempfile
import importlib
import unittest
from unittest import mock
import json
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    import flask
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

@unittest.skipIf(not FLASK_AVAILABLE, "Flask not installed")
class ApiSetupInitTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["FOSSILSAFE_SKIP_DEP_CHECK"] = "1"
        os.environ["FOSSILSAFE_AUTOSTART_SERVICES"] = "0"
        os.environ["FOSSILSAFE_REQUIRE_API_KEY"] = "false"
        os.environ["FOSSILSAFE_DATA_DIR"] = self.tmpdir.name
        os.environ["FOSSILSAFE_CONFIG_PATH"] = os.path.join(self.tmpdir.name, "config.json")

        from backend import lto_backend_main
        importlib.reload(lto_backend_main)
        
        db_path = os.path.join(self.tmpdir.name, "test.db")
        lto_backend_main._init_db(db_path)
        
        self.app = lto_backend_main.create_app(
            {"TESTING": True, "DB_PATH": db_path, "WTF_CSRF_ENABLED": False},
            autostart_services=False,
        )
        self.client = self.app.test_client()

        # Create admin user for testing
        from backend.auth import get_auth_manager
        auth_manager = get_auth_manager()
        auth_manager.create_user("admin", "password123", role="admin")
        
        # Login to get token
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password123"}
        )
        self.token = response.get_json()["data"]["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        
        # Mock tape controller
        self.mock_tape_controller = mock.Mock()
        self.app.tape_controller = self.mock_tape_controller

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_tape_status_endpoint(self):
        self.mock_tape_controller.inventory.return_value = [
            {"slot": 1, "barcode": "T00001L6", "status": "available", "is_cleaning_tape": False},
            {"slot": 2, "barcode": "T00002L6", "status": "available", "is_cleaning_tape": False}
        ]
        self.mock_tape_controller.is_drive_only.return_value = False
        
        with mock.patch.object(self.app.db, 'get_tape', return_value=None):
            response = self.client.get("/api/setup/tape-status", headers=self.headers)
            print("ERROR BODY:", response.get_json())
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["data"]["count"], 2)
            self.assertEqual(data["data"]["initialized_count"], 0)
            self.assertTrue(data["data"]["has_library"])

    def test_tape_init_lifecycle(self):
        # 1. Start initialization
        self.mock_tape_controller.inventory.return_value = [
            {"slot": 1, "barcode": "T00001L6", "is_cleaning_tape": False}
        ]
        self.mock_tape_controller.is_drive_only.return_value = False
        
        with mock.patch('backend.routes.setup.threading.Thread'):
            response = self.client.post("/api/setup/tape-init", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["success"])

        # 2. Check status (mock _init_status directly in setup module)
        import backend.routes.setup as setup_mod
        setup_mod._init_status = {
            "running": True,
            "current": 1,
            "total": 5,
            "last_barcode": "T00001L6",
            "error": None,
            "complete": False
        }
        
        response = self.client.get("/api/setup/tape-init/status", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["current"], 1)
        self.assertTrue(data["data"]["running"])

if __name__ == "__main__":
    unittest.main()
