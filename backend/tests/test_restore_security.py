import unittest
from unittest.mock import MagicMock
from backend.services.restore_service import RestoreService

class TestRestoreSecurity(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_backup = MagicMock()
        self.mock_adv_restore = MagicMock()
        self.mock_tape = MagicMock()
        self.service = RestoreService(
            self.mock_db, 
            self.mock_backup, 
            self.mock_adv_restore, 
            self.mock_tape
        )

    def test_imports_valid(self):
        """Verify the module can be imported and types are resolved."""
        from backend.services.restore_service import RestoreService
        self.assertIsNotNone(RestoreService)

    def test_initiate_restore_path_traversal(self):
        """Verify path traversal is blocked."""
        data = {
            'files': [{'id': 1}],
            'confirm': True,
            'destination': '/tmp/../../etc/shadow'
        }
        success, result = self.service.initiate_restore(data)
        self.assertFalse(success)
        self.assertIn("message", result)
        # Note: Depending on how abspath resolves, it might point to /etc/shadow directly which is blocked, 
        # or be caught by traversal check if implemented explicitly.
        # Our implementation blocks /etc, so this should fail.

    def test_initiate_restore_system_dir(self):
        """Verify restoring to system directories is blocked."""
        data = {
            'files': [{'id': 1}],
            'confirm': True,
            'destination': '/etc/passwd'
        }
        success, result = self.service.initiate_restore(data)
        self.assertFalse(success)
        self.assertIn("forbidden", str(result))

    def test_initiate_restore_valid_path(self):
        """Verify valid path is accepted."""
        # Mock DB response for file lookup
        self.mock_db.get_archived_file_by_id.return_value = {
            'id': 1, 'file_path': '/data/file1', 'file_size': 100
        }
        
        data = {
            'files': [{'id': 1}],
            'confirm': True,
            'destination': '/tmp/restore_test'
        }
        success, result = self.service.initiate_restore(data)
        self.assertTrue(success)
        self.assertIn("restore_id", result)

if __name__ == '__main__':
    unittest.main()
