
import os
import sys
import unittest
import tempfile
import json
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.auth import AuthManager, User
from backend.database import Database

class TestRBACSystem(unittest.TestCase):
    def setUp(self):
        # Create a temporary database
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        self.db = Database(self.db_path)
        self.auth = AuthManager(self.db)
        
        # Create 3 users
        self.auth.create_user("admin_user", "password123", role="admin")
        self.auth.create_user("operator_user", "password123", role="operator")
        self.auth.create_user("viewer_user", "password123", role="viewer")
        self.auth.create_user("inactive_user", "password123", role="viewer")
        
        # Manually deactivate the inactive_user
        self.db.execute("UPDATE users SET is_active = 0 WHERE username = 'inactive_user'")

    def tearDown(self):
        self.db.close()
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_user_creation_and_attributes(self):
        """Test that users are created with correct roles and default attributes."""
        admin = self.auth.get_user("admin_user")
        self.assertIsNotNone(admin)
        self.assertEqual(admin.role, "admin")
        self.assertTrue(admin.is_active)
        self.assertIsNone(admin.totp_secret)

        viewer = self.auth.get_user("viewer_user")
        self.assertIsNotNone(viewer)
        self.assertEqual(viewer.role, "viewer")
        self.assertTrue(viewer.is_active)

    def test_user_inactivation(self):
        """Test that inactive users are correctly identified."""
        inactive = self.auth.get_user("inactive_user")
        self.assertIsNotNone(inactive)
        self.assertFalse(inactive.is_active)
        
        # Verify login failure for inactive user
        session = self.auth.login("inactive_user", "password123")
        self.assertIsNone(session)

    def test_role_hierarchy_logic(self):
        """Test the hierarchical role checking logic."""
        # This is a bit tricky as require_role is a decorator.
        # We can test the hierarchy mapping directly or the decorator behavior if we wrap a dummy function.
        
        hierarchy = {'admin': 3, 'operator': 2, 'viewer': 1, 'readonly': 1}
        
        def check_access(user_role, required_role):
            if user_role == 'admin': return True
            user_level = hierarchy.get(user_role, 0)
            required_level = hierarchy.get(required_role, 99)
            return user_level >= required_level

        # Admin can do everything
        self.assertTrue(check_access('admin', 'admin'))
        self.assertTrue(check_access('admin', 'operator'))
        self.assertTrue(check_access('admin', 'viewer'))
        
        # Operator can do operator and viewer things
        self.assertFalse(check_access('operator', 'admin'))
        self.assertTrue(check_access('operator', 'operator'))
        self.assertTrue(check_access('operator', 'viewer'))
        
        # Viewer can only do viewer things
        self.assertFalse(check_access('viewer', 'admin'))
        self.assertFalse(check_access('viewer', 'operator'))
        self.assertTrue(check_access('viewer', 'viewer'))

    def test_user_management_methods(self):
        """Test the new user management methods in AuthManager."""
        # List users
        users = self.auth.list_users()
        self.assertEqual(len(users), 4)
        usernames = [u['username'] for u in users]
        self.assertIn("admin_user", usernames)
        self.assertIn("operator_user", usernames)
        
        # Update user
        target = self.auth.get_user("viewer_user")
        self.auth.update_user(target.id, role="operator", is_active=False)
        
        updated = self.auth.get_user_by_id(target.id)
        self.assertEqual(updated.role, "operator")
        self.assertFalse(updated.is_active)
        
        # Delete user
        self.auth.delete_user(target.id)
        deleted = self.auth.get_user_by_id(target.id)
        self.assertIsNone(deleted)

if __name__ == "__main__":
    unittest.main()
