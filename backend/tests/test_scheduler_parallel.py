import pytest
from unittest.mock import MagicMock, patch
from backend.scheduler import BackupScheduler

@pytest.fixture
def scheduler():
    db = MagicMock()
    backup_engine = MagicMock()
    scheduler = BackupScheduler(db, backup_engine)
    return scheduler

def test_auto_drive_deferral(scheduler):
    """Test that Auto (-1) is passed directly to create_job"""
    # Setup
    scheduler.db.get_active_jobs.return_value = []
    
    schedule = {'id': 1, 'name': 'Test', 'drive': -1, 'source_id': 1, 'enabled': True}
    scheduler.db.get_schedule.return_value = schedule
    
    # Run
    scheduler._execute_scheduled_backup(1)
    
    # Verify created job has drive -1 (deferred)
    scheduler.db.create_job.assert_called()
    call_args = scheduler.db.create_job.call_args[1]
    assert call_args['drive'] == -1
