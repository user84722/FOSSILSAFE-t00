import pytest
from unittest.mock import MagicMock, patch
from backend.library_manager import LibraryManager
from backend.tape_controller import TapeLibraryController
from backend.services.tape_service import TapeService
from backend.lto_backend_main import PreflightChecker

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_tape_inventory.return_value = []
    return db

@pytest.fixture
def mock_controller():
    controller = MagicMock(spec=TapeLibraryController)
    controller.is_online.return_value = True
    controller.scan_barcodes.return_value = []
    controller.scan_library.return_value = []
    controller.get_drive_status.return_value = {'available': True}
    return controller

class TestLibraryManagerIntegration:
    
    def test_library_manager_initialization(self, mock_db):
        """Test LibraryManager initializes controllers from config."""
        with patch('backend.library_manager.load_config') as mock_load_config:
            mock_load_config.return_value = {
                'libraries': [
                    {'id': 'lib_1', 'device': '/dev/nst0', 'changer': '/dev/sg0'},
                    {'id': 'lib_2', 'device': '/dev/nst1', 'changer': '/dev/sg1'}
                ]
            }
            
            with patch('backend.library_manager.TapeLibraryController') as MockController:
                manager = LibraryManager(mock_db)
                manager.initialize()
                
                assert len(manager.controllers) == 2
                assert 'lib_1' in manager.controllers
                assert 'lib_2' in manager.controllers
                assert manager.default_library_id == 'lib_1'

    def test_find_controller_for_tape(self, mock_db):
        """Test finding a controller for a specific tape."""
        manager = LibraryManager(mock_db)
        
        c1 = MagicMock(spec=TapeLibraryController)
        c1.inventory.return_value = [{'barcode': 'TAPE_A', 'slot': 1}]
        
        c2 = MagicMock(spec=TapeLibraryController)
        c2.inventory.return_value = [{'barcode': 'TAPE_B', 'slot': 1}]
        
        manager.controllers = {'lib_1': c1, 'lib_2': c2}
        
        assert manager.find_controller_for_tape('TAPE_A') == c1
        assert manager.find_controller_for_tape('TAPE_B') == c2
        assert manager.find_controller_for_tape('TAPE_C') is None

    def test_tape_service_scan_updates_library_id(self, mock_db):
        """Test TapeService injects library_id into DB updates."""
        manager = LibraryManager(mock_db)
        c1 = MagicMock(spec=TapeLibraryController)
        c1.library_id = 'lib_1'
        c1.scan_library.return_value = [{'barcode': 'TAPE_A', 'slot': 1}]
        
        manager.controllers = {'lib_1': c1}
        manager.default_library_id = 'lib_1'
        
        service = TapeService(mock_db, library_manager=manager)
        
        # Helper to bypass _get_controller complexity if needed, 
        # but here we rely on manager.get_library(None) returning default.
        
        service.scan_library_and_update(mode='fast', library_id=None)
        
        # Verify db.update_tape_inventory called with library_id
        args = mock_db.update_tape_inventory.call_args[0][0]
        assert len(args) == 1
        assert args[0]['barcode'] == 'TAPE_A'
        assert args[0]['library_id'] == 'lib_1'

    def test_preflight_checker_multi_library(self, mock_db):
        """Test PreflightChecker checks all libraries."""
        manager = LibraryManager(mock_db)
        
        c1 = MagicMock(spec=TapeLibraryController)
        c1.is_online.return_value = True
        c1.get_drive_status.return_value = {'available': True}
        
        c2 = MagicMock(spec=TapeLibraryController)
        c2.is_online.return_value = False # Offline
        c2.get_drive_status.return_value = {'available': False}
        
        manager.controllers = {'lib_1': c1, 'lib_2': c2}
        
        checker = PreflightChecker(tape_controller=None, smb_client=None, db=mock_db, library_manager=manager)
        
        results = checker.run_all({'estimated_size': 0})
        
        # Check library status
        lib_checks = [c for c in results['checks'] if 'Library Status' in c['name']]
        assert len(lib_checks) == 2
        assert any(c['name'] == 'Library Status (lib_1)' and c['status'] == 'pass' for c in lib_checks)
        assert any(c['name'] == 'Library Status (lib_2)' and c['status'] == 'error' for c in lib_checks)
        
        # Check drive status
        drive_checks = [c for c in results['checks'] if 'Drive Status' in c['name']]
        assert len(drive_checks) == 2
        
        # Overall result should be failed if one is offline?
        # PreflightChecker.run_all logic: if any error, passed=False
        assert results['passed'] == False
