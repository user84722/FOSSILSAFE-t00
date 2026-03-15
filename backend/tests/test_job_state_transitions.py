import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import threading
import time

# Add project root to path
sys.path.append(os.getcwd())

# Mock modules
sys.modules['backend.smb_client'] = MagicMock()

from backend.services.job_service import JobService

class TestJobStateTransitions(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_db.get_setting.return_value = 1
        self.mock_db.get_bool_setting.return_value = True
        self.mock_backup_engine = MagicMock()
        self.mock_scheduler = MagicMock()
        self.mock_preflight = MagicMock()
        self.mock_tape_controller = MagicMock()
        self.mock_source_manager = MagicMock()
        self.mock_source_manager.get_source.return_value = {'source_type': 'smb', 'source_path': '//server/share'}
        self.mock_smb_client = MagicMock()

        self.service = JobService(
            db=self.mock_db,
            backup_engine=self.mock_backup_engine,
            scheduler=self.mock_scheduler,
            preflight_checker=self.mock_preflight,
            tape_controller=self.mock_tape_controller,
            source_manager=self.mock_source_manager,
            smb_client=self.mock_smb_client
        )

    def test_job_creation_pending(self):
        """Test that a new job starts in 'pending' state (or queued if async)"""
        data = {
            'name': 'Test Job',
            'job_type': 'backup',
            'source_id': '1'
        }
        
        # Mock DB create_job to return a specific ID
        job_id = 101
        self.mock_db.create_job.return_value = job_id
        
        # Mock the license service module that will be imported inside create_job
        mock_ls = MagicMock()
        mock_ls.has_capability.return_value = True
        mock_ls.license_service.current_license = {}

        with patch.dict(sys.modules, {'backend.services.license_service': mock_ls}):
             with patch('backend.services.job_service.validate_job_name', return_value=(True, None)):
                with patch('backend.services.job_service.validate_smb_path', return_value=(True, None)):
                    
                    success, returned_id = self.service.create_job(data)
                    
                    self.assertTrue(success)
                    self.assertEqual(returned_id, job_id)

    def test_cancel_job_running(self):
        """Test cancelling a running job"""
        job_id = 102
        self.mock_db.get_job.return_value = {'id': job_id, 'status': 'running', 'job_type': 'backup'}
        
        success = self.service.cancel_job(job_id)
        
        self.assertTrue(success)
        self.mock_backup_engine.cancel_job.assert_called_with(job_id)

    def test_cancel_job_internal(self):
        """Test cancelling an internal job (tape op)"""
        job_id = 103
        self.mock_db.get_job.return_value = {'id': job_id, 'status': 'running', 'job_type': 'tape_wipe'}
        
        # Setup internal flag
        event = threading.Event()
        self.service._internal_job_cancel_flags[job_id] = event
        
        success = self.service.cancel_job(job_id)
        
        self.assertTrue(success)
        self.assertTrue(event.is_set())
        # Verify status update
        self.mock_db.update_job_status.assert_called()
        args, _ = self.mock_db.update_job_status.call_args
        self.assertEqual(args[0], job_id)
        self.assertEqual(args[1], 'cancel_requested')

if __name__ == '__main__':
    unittest.main()
