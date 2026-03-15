import pytest
import time
import threading
import sys
import os
sys.path.append(os.getcwd())
from unittest.mock import MagicMock, patch, call

# Mock SMBClient module before importing BackupEngine
sys.modules['backend.sources.smb_client'] = MagicMock()

from backend.backup_engine import BackupEngine, JobStatus

@pytest.fixture
def backup_engine():
    db = MagicMock()
    tape_controller = MagicMock()
    # Mock drive locks for 2 drives
    drive_locks = {0: threading.Lock(), 1: threading.Lock()}
    
    # Use real threading.Lock
    engine = BackupEngine(db, tape_controller)
    engine.drive_locks = drive_locks
    return engine

def test_auto_queue_selection(backup_engine):
    """Test that start_backup_job with drive=-1 selects first available drive"""
    job_id = 99
    job = {'id': job_id, 'drive': -1, 'source_id': 1}
    backup_engine.db.get_job.return_value = job
    
    with patch('backend.services.license_service.license_service') as mock_license, \
         patch('backend.services.license_service.has_capability', return_value=True), \
         patch.object(backup_engine, '_execute_backup') as mock_exec:
         
        # Drive 0 is busy
        backup_engine.drive_locks[0].acquire()
        
        # Run in thread because it might block (though it shouldn't if Drive 1 is free)
        t = threading.Thread(target=backup_engine.start_backup_job, args=(job_id,))
        t.start()
        t.join(timeout=2)
        
        assert not t.is_alive()
        
        # Verify it picked Drive 1
        assert job['drive'] == 1
        backup_engine.db.update_job_drive.assert_called_with(job_id, 1)
        mock_exec.assert_called_once()
        
        # Cleanup
        backup_engine.drive_locks[0].release()

def test_active_queue_polling(backup_engine):
    """Test that it waits and picks up a drive when it becomes free"""
    job_id = 100
    job = {'id': job_id, 'drive': -1, 'source_id': 1}
    backup_engine.db.get_job.return_value = job
    
    with patch('backend.services.license_service.license_service') as mock_license, \
         patch('backend.services.license_service.has_capability', return_value=True), \
         patch.object(backup_engine, '_execute_backup') as mock_exec:
         
        # Both drives busy initially
        backup_engine.drive_locks[0].acquire()
        backup_engine.drive_locks[1].acquire()
        
        # Start job in thread (it should queue)
        t = threading.Thread(target=backup_engine.start_backup_job, args=(job_id,))
        t.start()
        
        # Wait a bit to ensure it entered queue loop
        time.sleep(0.5)
        backup_engine.db.update_job_status.assert_any_call(job_id, 'queued')
        
        # Release Drive 0
        backup_engine.drive_locks[0].release()
        
        # Wait for thread to finish
        t.join(timeout=2)
        assert not t.is_alive()
        
        # Verify it picked Drive 0
        assert job['drive'] == 0
        backup_engine.db.update_job_drive.assert_called_with(job_id, 0)
        
        # Cleanup
        backup_engine.drive_locks[1].release()

def test_load_balancing_shuffle(backup_engine):
    """Test that candidate drives are shuffled for load balancing"""
    job_id = 101
    job = {'id': job_id, 'drive': -1, 'source_id': 1}
    backup_engine.db.get_job.return_value = job
    
    with patch('backend.services.license_service.license_service') as mock_license, \
         patch('backend.services.license_service.has_capability', return_value=True), \
         patch('backend.backup_engine.random.shuffle') as mock_shuffle, \
         patch.object(backup_engine, '_execute_backup') as mock_exec:
         
        # Make drives available so loop doesn't block
        # start_backup_job will pick one and return
        backup_engine.start_backup_job(job_id)
        
        # Verify shuffle was called
        mock_shuffle.assert_called()
        
        # Verify it passed a list (candidate drives)
        args, _ = mock_shuffle.call_args
        assert isinstance(args[0], list)
        assert len(args[0]) == 2 # 0 and 1
