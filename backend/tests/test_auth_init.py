import unittest
from unittest.mock import MagicMock, patch
import backend.lto_backend_main as app_main
from backend.auth import AuthManager

class TestAuthInit(unittest.TestCase):
    @patch('backend.lto_backend_main.init_auth')
    @patch('backend.lto_backend_main.Database')
    @patch('backend.lto_backend_main.LogManager')
    def test_auth_initialized(self, mock_log_manager, mock_db, mock_init_auth):
        """Verify init_auth is called with the database instance."""
        # Setup mocks to avoid side effects
        app_main.db = MagicMock()
        app_main.log_manager = None
        
        # Run initialization
        app_main.initialize_app()
        
        # Verify init_auth was called
        mock_init_auth.assert_called_once_with(app_main.db)

if __name__ == '__main__':
    unittest.main()
