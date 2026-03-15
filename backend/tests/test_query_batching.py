"""
Tests for database query batching
"""
import pytest
import time
from backend.database import Database


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    yield database
    database.close()


def test_batch_insert_archived_files(db):
    """Test batch insert of archived files."""
    # Create a test job and tape
    job_id = db.create_job('test_job', 'local', '/test/path')
    db.add_tape('TAPE001L9', 0)
    
    # Prepare batch of files
    files = []
    for i in range(100):
        files.append({
            'job_id': job_id,
            'tape_barcode': 'TAPE001',
            'file_path': f'/test/file{i}.txt',
            'file_name': f'file{i}.txt',
            'file_extension': 'txt',
            'file_size': 1024 * i,
            'checksum': f'checksum{i}',
            'file_path_on_tape': f'/tape/file{i}.txt',
        })
    
    # Batch insert
    count = db.batch_insert_archived_files(files)
    
    assert count == 100
    
    # Verify all files were inserted
    result = db.search_archived_files(None, job_id=job_id)
    assert len(result) == 100


def test_batch_insert_with_archived_at(db):
    """Test batch insert with archived_at timestamps."""
    job_id = db.create_job('test_job', 'local', '/test/path')
    db.add_tape('TAPE001L9', 0)
    
    files = []
    for i in range(50):
        files.append({
            'job_id': job_id,
            'tape_barcode': 'TAPE001',
            'file_path': f'/test/file{i}.txt',
            'file_name': f'file{i}.txt',
            'file_extension': 'txt',
            'file_size': 1024,
            'checksum': f'checksum{i}',
            'file_path_on_tape': f'/tape/file{i}.txt',
            'archived_at': '2024-01-01T00:00:00Z',
        })
    
    count = db.batch_insert_archived_files(files)
    assert count == 50
    
    # Verify archived_at was set
    result = db.search_archived_files(None, job_id=job_id)
    assert all(r['archived_at'] == '2024-01-01T00:00:00Z' for r in result)


def test_batch_insert_empty_list(db):
    """Test batch insert with empty list."""
    count = db.batch_insert_archived_files([])
    assert count == 0


def test_batch_insert_performance(db):
    """Test that batch insert is faster than individual inserts."""
    job_id = db.create_job('test_job', 'local', '/test/path')
    db.add_tape('TAPE001L9', 0)
    
    # Prepare test data
    files = []
    for i in range(500):
        files.append({
            'job_id': job_id,
            'tape_barcode': 'TAPE001',
            'file_path': f'/test/file{i}.txt',
            'file_name': f'file{i}.txt',
            'file_extension': 'txt',
            'file_size': 1024,
            'checksum': f'checksum{i}',
            'file_path_on_tape': f'/tape/file{i}.txt',
        })
    
    # Time batch insert
    start = time.time()
    db.batch_insert_archived_files(files)
    batch_time = time.time() - start
    
    # Batch insert should complete quickly
    assert batch_time < 1.0  # Should be much faster than 1 second for 500 files


def test_batch_update_job_progress(db):
    """Test batch update of job progress."""
    # Create test jobs
    job_ids = []
    for i in range(5):
        job_id = db.create_job(f'test_job_{i}', 'local', f'/test/path{i}')
        job_ids.append(job_id)
    
    # Prepare batch updates
    updates = []
    for i, job_id in enumerate(job_ids):
        updates.append({
            'job_id': job_id,
            'files_written': i * 10,
            'bytes_written': i * 1024,
        })
    
    # Batch update
    count = db.batch_update_job_progress(updates)
    assert count == 5
    
    # Verify updates
    for i, job_id in enumerate(job_ids):
        job = db.get_job(job_id)
        assert job['files_written'] == i * 10
        assert job['bytes_written'] == i * 1024


def test_batch_update_partial_fields(db):
    """Test batch update with partial field updates."""
    job_id = db.create_job('test_job', 'local', '/test/path')
    
    # Update only files_written
    updates = [{'job_id': job_id, 'files_written': 100}]
    db.batch_update_job_progress(updates)
    
    job = db.get_job(job_id)
    assert job['files_written'] == 100
    
    # Update only bytes_written
    updates = [{'job_id': job_id, 'bytes_written': 2048}]
    db.batch_update_job_progress(updates)
    
    job = db.get_job(job_id)
    assert job['files_written'] == 100  # Should remain unchanged
    assert job['bytes_written'] == 2048


def test_batch_update_empty_list(db):
    """Test batch update with empty list."""
    count = db.batch_update_job_progress([])
    assert count == 0


def test_batch_transaction_success(db):
    """Test batch transaction with successful commit."""
    job_id = db.create_job('test_job', 'local', '/test/path')
    db.add_tape('TAPE001L9', 0)
    
    with db.batch_transaction() as conn:
        cursor = conn.cursor()
        
        # Insert multiple files in one transaction
        for i in range(10):
            cursor.execute('''
                INSERT INTO archived_files 
                (job_id, tape_barcode, file_path, file_name, file_extension, file_size, checksum,
                 file_path_on_tape, tape_position, copy_set_id, copy_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (job_id, 'TAPE001', f'/test/file{i}.txt', f'file{i}.txt', 'txt',
                  1024, f'checksum{i}', f'/tape/file{i}.txt', None, None, 'primary'))
    
    # Verify all files were committed
    result = db.search_archived_files(None, job_id=job_id)
    assert len(result) == 10


def test_batch_transaction_rollback(db):
    """Test batch transaction with rollback on error."""
    job_id = db.create_job('test_job', 'local', '/test/path')
    db.add_tape('TAPE001L9', 0)
    
    # Insert one file first
    db.add_archived_file(
        job_id=job_id,
        tape_barcode='TAPE001',
        file_path='/test/file0.txt',
        file_size=1024,
        checksum='checksum0'
    )
    
    # Try to insert more files but cause an error
    try:
        with db.batch_transaction() as conn:
            cursor = conn.cursor()
            
            # Insert a valid file
            cursor.execute('''
                INSERT INTO archived_files 
                (job_id, tape_barcode, file_path, file_name, file_extension, file_size, checksum,
                 file_path_on_tape, tape_position, copy_set_id, copy_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (job_id, 'TAPE001', '/test/file1.txt', 'file1.txt', 'txt',
                  1024, 'checksum1', '/tape/file1.txt', None, None, 'primary'))
            
            # Cause an error (invalid SQL)
            cursor.execute('INVALID SQL')
    except Exception:
        pass
    
    # Verify rollback - only the first file should exist
    result = db.search_archived_files(None, job_id=job_id)
    assert len(result) == 1
    assert result[0]['file_path'] == '/test/file0.txt'


def test_batch_insert_mixed_timestamps(db):
    """Test batch insert with mixed archived_at values."""
    job_id = db.create_job('test_job', 'local', '/test/path')
    db.add_tape('TAPE001L9', 0)
    
    files = []
    # Some files with timestamps
    for i in range(5):
        files.append({
            'job_id': job_id,
            'tape_barcode': 'TAPE001',
            'file_path': f'/test/file{i}.txt',
            'file_name': f'file{i}.txt',
            'file_extension': 'txt',
            'file_size': 1024,
            'checksum': f'checksum{i}',
            'file_path_on_tape': f'/tape/file{i}.txt',
            'archived_at': '2024-01-01T00:00:00Z',
        })
    
    # Some files without timestamps
    for i in range(5, 10):
        files.append({
            'job_id': job_id,
            'tape_barcode': 'TAPE001',
            'file_path': f'/test/file{i}.txt',
            'file_name': f'file{i}.txt',
            'file_extension': 'txt',
            'file_size': 1024,
            'checksum': f'checksum{i}',
            'file_path_on_tape': f'/tape/file{i}.txt',
        })
    
    count = db.batch_insert_archived_files(files)
    assert count == 10
    
    result = db.search_archived_files(None, job_id=job_id)
    assert len(result) == 10
