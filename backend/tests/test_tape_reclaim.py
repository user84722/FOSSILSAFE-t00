
import pytest
import json
from unittest.mock import MagicMock, patch
from backend.services.tape_reclaim_service import TapeReclaimService
from backend.database import Database
from backend.tape_controller import TapeLibraryController

@pytest.fixture
def mock_db():
    db = MagicMock(spec=Database)
    db.get_tapes_by_utilization.return_value = [
        {'barcode': 'A001', 'capacity_bytes': 1000, 'used_bytes': 100, 'utilization': 10.0},
        {'barcode': 'A002', 'capacity_bytes': 1000, 'used_bytes': 200, 'utilization': 20.0}
    ]
    db._get_conn.return_value.cursor.return_value.lastrowid = 123
    return db

@pytest.fixture
def mock_tape_controller():
    tc = MagicMock(spec=TapeLibraryController)
    tc.mount_ltfs.return_value = '/tmp/mount_point'
    return tc

@pytest.fixture
def service(mock_db, mock_tape_controller):
    with patch('os.makedirs'):
        return TapeReclaimService(mock_db, mock_tape_controller)

def test_identify_reclaimable_tapes(service, mock_db):
    candidates = service.identify_reclaimable_tapes(threshold_percent=30.0)
    mock_db.get_tapes_by_utilization.assert_called_with(30.0, 100)
    assert len(candidates) == 2
    assert candidates[0]['barcode'] == 'A001'

def test_calculate_reclaim_stats(service):
    tapes = [
        {'barcode': 'A001', 'capacity_bytes': 1000, 'used_bytes': 100},
        {'barcode': 'A002', 'capacity_bytes': 1000, 'used_bytes': 200}
    ]
    stats = service.calculate_reclaim_stats(tapes)
    assert stats['tape_count'] == 2
    assert stats['total_used_bytes'] == 300
    assert stats['total_capacity_bytes'] == 2000

@patch('threading.Thread')
def test_start_reclaim_job(mock_thread, service, mock_db):
    source_barcodes = ['A001', 'A002']
    dest_barcode = 'B001'
    
    job_id = service.start_reclaim_job(source_barcodes, dest_barcode)
    
    assert job_id == 123
    mock_thread.return_value.start.assert_called_once()
    
    # Verify DB job creation
    conn = mock_db._get_conn.return_value
    cursor = conn.cursor.return_value
    cursor.execute.assert_called()
    args = cursor.execute.call_args
    assert 'INSERT INTO jobs' in args[0][0]
    assert json.dumps(['B001', 'A001', 'A002']) in str(args[0][1])

@patch('shutil.copy2')
@patch('os.walk')
@patch('os.makedirs')
@patch('shutil.rmtree')
def test_execute_reclaim_logic(mock_rmtree, mock_makedirs, mock_walk, mock_copy, service, mock_db, mock_tape_controller):
    # Setup
    job_id = 100
    source_barcodes = ['A001']
    dest_barcode = 'B001'
    
    # Mock OS walk to return one file
    mock_walk.return_value = [
        ('/tmp/mount_point', [], ['file1.txt'])
    ]
    
    with patch('os.path.getsize', return_value=500):
        # Execute (directly calling private method to test logic synchronously)
        service._execute_reclaim_logic(job_id, source_barcodes, dest_barcode)
    
    # Verify mounting/unmounting
    mock_tape_controller.load_tape.assert_any_call('A001')
    mock_tape_controller.load_tape.assert_any_call('B001')
    mock_tape_controller.mount_ltfs.assert_any_call('A001')
    mock_tape_controller.mount_ltfs.assert_any_call('B001')
    mock_tape_controller.unmount_ltfs.assert_called()
    mock_tape_controller.unload_tape.assert_called()
    
    # Verify Copying
    assert mock_copy.call_count == 2 # Once to stage, once to dest
    
    # Verify DB Updates
    mock_db.update_job_status.assert_any_call(job_id, 'running')
    mock_db.update_job_status.assert_any_call(job_id, 'completed', metadata={'files_reclaimed': 1})
    
    # Verify Catalog Update
    mock_db.add_archived_file.assert_called_once()
    call_kwargs = mock_db.add_archived_file.call_args[1]
    assert call_kwargs['tape_barcode'] == dest_barcode
    assert 'RECLAIM/A001/file1.txt' in call_kwargs['file_path']
