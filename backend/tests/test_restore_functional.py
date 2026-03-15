import pytest
import os
import shutil
import time
import hashlib
from unittest.mock import MagicMock, patch
from backend.database import Database
from backend.tape.vtl import VirtualTapeController
from backend.backup_engine import BackupEngine

# Re-use similar setup to E2E but strictly for restore logic
@pytest.fixture
def restore_env():
    data_dir = "/tmp/restore_test_data"
    vtl_root = "/tmp/restore_test_vtl"
    source_root = "/tmp/restore_test_source"
    restore_dest = "/tmp/restore_test_dest"

    for path in [data_dir, vtl_root, source_root, restore_dest]:
        if os.path.exists(path):
            shutil.rmtree(path)
        os.makedirs(path)

    # Create dummy file
    with open(os.path.join(source_root, "dataset.bin"), "wb") as f:
        f.write(os.urandom(1024 * 1024)) # 1MB

    os.environ['FOSSILSAFE_DATA_DIR'] = data_dir
    os.environ['VTL_ROOT'] = vtl_root
    
    yield {
        'data_dir': data_dir,
        'vtl_root': vtl_root,
        'source_root': source_root,
        'restore_dest': restore_dest,
        'db_path': os.path.join(data_dir, "lto.db")
    }

    # Cleanup
    # for path in [data_dir, vtl_root, source_root, restore_dest]:
    #     if os.path.exists(path):
    #         shutil.rmtree(path)

class MockSocketIO:
    def emit(self, event, data, **kwargs):
        print(f"SocketIO: {event} {data}")

class MockSmbClient:
    def __init__(self):
        pass
    def mount_share(self, share, **kwargs):
        return share if not share.startswith('//') else "/tmp/mock_mount"
    def unmount_share(self, share, mount):
        pass
    def list_files(self, path, **kwargs):
        results = []
        for root, dirs, files in os.walk(path):
            for name in files:
                full = os.path.join(root, name)
                results.append({
                    'name': name,
                    'path': os.path.relpath(full, path),
                    'size': os.path.getsize(full),
                    'mtime': os.path.getmtime(full),
                    'is_dir': False
                })
        return results
    def download_file(self, share, rel, dest, credentials=None, **kwargs):
        src = os.path.join(share, rel)
        print(f"DEBUG_MOCK: Downloading {src} to {dest}")
        if not os.path.exists(src):
            print(f"DEBUG_MOCK: Source missing! {src}")
        shutil.copy2(src, dest)

@pytest.fixture
def mock_config(restore_env):
    with patch('backend.backup_engine.get_data_dir') as mock_get:
        mock_get.return_value = restore_env['data_dir']
        # Also patch config_store in case other modules use it directly
        with patch('backend.config_store.get_data_dir') as mock_get_store:
            mock_get_store.return_value = restore_env['data_dir']
            yield

def test_restore_integrity(restore_env, mock_config):
    print("\n=== Testing Restore Integrity ===")
    vtl = VirtualTapeController() # Moved up so patches are active? No, fixture is active.
    
    # Reload components if needed, but patch handles it.
    
    db = Database(restore_env['db_path'])
    vtl = VirtualTapeController()
    vtl.initialize()
    
    # Setup Tape
    inventory = vtl.scan_barcodes()
    tape = next(i for i in inventory if i['type'] == 'slot')
    barcode = tape['barcode']
    
    vtl.load_tape(barcode, 0)
    vtl.format_tape(0, label="RESTORE01")
    vtl.unload_tape(0)
    
    db.add_tape(barcode, slot=0)
    
    # Run Backup
    source_mgr = MagicMock()
    source_mgr.get_source.return_value = {
        'id': 'src1', 'source_type': 'smb', 'source_path': restore_env['source_root']
    }
    
    engine = BackupEngine(db, vtl, MockSmbClient(), MockSocketIO(), source_mgr)
    
    job_id = db.create_job(
        name="Restore Test Job",
        source_id="src1",
        backup_mode="full",
        tapes=[barcode]
    )
    engine.start_backup_job(job_id)
    
    # Verify Backup
    job = db.get_job(job_id)
    assert job['status'] == 'completed'
    
    # Prepare Restore
    print("Starting Restore...")
    restore_id = db.create_restore_job([
        {'path': 'dataset.bin', 'source_id': 'src1'} # simplified logic for this test?
                                                     # verify_file logic might need job_id?
    ], restore_env['restore_dest'])
    
    # But wait, create_restore_job signature?
    # db.create_restore_job(files: List[Dict], destination: str)
    # File dict usually needs 'file_path', 'job_id' (to find it).
    
    # Let's find the file in DB
    files = db.search_files("dataset.bin")
    assert len(files) > 0
    target_file = files[0]
    
    restore_file_entry = {
         'file_path': target_file['file_path'], # relative path in archive
         'job_id': target_file['job_id']
    }
    
    # db.create_restore_job takes list of files.
    # Implementation of create_restore_job expects:
    # files: [{'path': str, 'tape_barcode': str, 'start_block': int, ...?}]
    # Let's check Database.create_restore_job signature briefly or assume dict passed is stored JSON.
    
    # Actually Database.create_restore_job checks nothing, just stores JSON.
    # But backup_engine.restore uses it.
    
    # We should pass what the UI passes.
    # UI passes: path, size, modification_time, tape_barcode, etc.
    
    restore_payload = [{
        'file_path': target_file['file_path_on_tape'], # Use file_path matching backend expectation
        'file_path_on_tape': target_file['file_path_on_tape'],
        'tape_barcode': target_file['tape_barcode'],
        'start_block': target_file['tape_position'] or 0,
        'file_size': target_file['file_size']
    }]
    
    restore_id = db.create_restore_job(restore_payload, restore_env['restore_dest'])
    
    # Execute Restore
    engine.start_restore_job(restore_id)
    
    # Verify
    restore_job = db.get_restore_job(restore_id)
    assert restore_job['status'] == 'completed', f"Restore failed: {restore_job.get('error')}"
    
    restored_path = os.path.join(restore_env['restore_dest'], "dataset.bin")
    assert os.path.exists(restored_path), "Restored file not found"
    
    # Compare Checksums
    def get_md5(p):
        v = hashlib.md5()
        with open(p, 'rb') as f:
            v.update(f.read())
        return v.hexdigest()
        
    orig_md5 = get_md5(os.path.join(restore_env['source_root'], "dataset.bin"))
    rest_md5 = get_md5(restored_path)
    
    assert orig_md5 == rest_md5, "Checksum mismatch"
    print("✅ Restore Integrity Verified")
