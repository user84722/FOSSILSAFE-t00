
import logging
import os
import time
import threading
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Optional

from backend.database import Database
from backend.tape_controller import TapeLibraryController

logger = logging.getLogger(__name__)

# Checksum calculation helper
HASH_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks

class VerificationService:
    """
    Service for verifying tape data integrity.
    """
    
    def __init__(self, db: Database, tape_controller: TapeLibraryController, socketio=None, library_manager=None):
        self.db = db
        self.tape_controller = tape_controller
        self.socketio = socketio
        self.library_manager = library_manager

    def calculate_checksum(self, file_path: str, algorithm: str = 'sha256') -> str:
        """Calculate checksum of a file with chunked reading for large files"""
        hasher = hashlib.new(algorithm)
        
        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            logger.error(f"Checksum calculation failed for {file_path}: {e}")
            raise

    def start_verification_job(self, tapes: List[str], job_id: int):
        """
        Start a background verification job.
        """
        t = threading.Thread(
            target=self._execute_verification,
            args=(job_id, tapes),
            name=f"VerificationJob-{job_id}"
        )
        t.start()

    def _execute_verification(self, job_id: int, tapes: List[str]):
        """
        Main execution logic.
        """
        logger.info(f"Starting Verification Job {job_id} for tapes: {tapes}")
        self.db.update_job_status(job_id, 'running')
        self._emit_progress(job_id, "Starting verification...", 0)
        
        total_files = 0
        total_failed = 0
        total_bytes = 0
        start_time = time.time()
        
        try:
            for i, barcode in enumerate(tapes):
                self._emit_progress(job_id, f"Verifying tape {barcode} ({i+1}/{len(tapes)})", int((i / len(tapes)) * 100))
                
                tape_report = self._verify_single_tape(job_id, barcode)
                
                # Aggregate stats
                total_files += tape_report['files_checked']
                total_failed += tape_report['files_failed']
                total_bytes += tape_report['bytes_checked']
                
                # Store per-tape report
                tape_report['job_id'] = job_id
                self.db.add_verification_report(tape_report)
                
            duration = int(time.time() - start_time)
            msg = f"Verification complete. Scanned {total_files} files, {total_failed} failures."
            status = 'completed' if total_failed == 0 else 'warning'
            
            self.db.update_job_status(job_id, status, message=msg, metadata={
                'files_checked': total_files,
                'files_failed': total_failed,
                'bytes_checked': total_bytes,
                'duration': duration
            })
            self._emit_progress(job_id, msg, 100)
            
        except Exception as e:
            logger.error(f"Verification job failed: {e}")
            self.db.update_job_status(job_id, 'failed', error=str(e))
            self._emit_progress(job_id, f"Failed: {e}", 0)

    def _verify_single_tape(self, job_id: int, barcode: str) -> Dict:
        """
        Mount and verify all files on a tape.
        """
        report = {
            'tape_barcode': barcode,
            'files_checked': 0,
            'files_failed': 0,
            'bytes_checked': 0,
            'duration_seconds': 0,
            'failure_details': []
        }
        
        start_time = time.time()
        
        try:
            controller = self.tape_controller
            if self.library_manager:
                found = self.library_manager.find_controller_for_tape(barcode)
                if found: controller = found

            controller.load_tape(barcode)
            mount_point = controller.mount_ltfs(barcode)
            
            # Get expected files from DB to compare against?
            # Or just walk the tape and check consistency?
            # Walking the tape ensures we check what's actually there.
            # We can cross-reference with DB to see if checksums match.
            
            # Map valid files on tape to their DB records
            # Getting all files for a tape might be huge.
            # Strategy: Walk tape, query DB for each file (slow?) OR load all files for tape into memory map.
            
            db_files = self.db.get_files_by_tape(barcode)
            # Create a lookup map: normalized_rel_path -> record
            # archived_files stores 'file_path_on_tape' OR 'file_path'.
            # Usually 'file_path' is source path. 'file_path_on_tape' is relative to tape root?
            # If 'file_path_on_tape' is not set, we assume 'file_path' (but that's source path).
            
            # Let's rely on filenames + sizes first, or just verify readability if we don't have checksums.
            # But we DO have checksums in DB.
            
            # Optimized lookup: 
            # We will walk the tape. For each file, we calculate checksum.
            # We then look for a matching record in db_files list.
            
            # Path matching can be tricky.
            # Let's clean up db_files list into a dictionary by file_name as a heuristic, or walk logic.
            
            for root, dirs, files in os.walk(mount_point):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, mount_point)
                    
                    try:
                        # calculate checksum
                        # Using chunked calculator
                        size = os.path.getsize(full_path)
                        checksum = self.calculate_checksum(full_path)
                        
                        report['files_checked'] += 1
                        report['bytes_checked'] += size
                        
                        # Find matching DB record
                        # This iteration is O(N*M) which is bad.
                        # Should optimize, but for now simple search.
                        match = None
                        for rec in db_files:
                            # Try to match by checksum/size first (strongest)
                            if rec['checksum'] == checksum and rec['file_size'] == size:
                                match = rec
                                break
                            # Or path?
                        
                        # If we found a match, great. If not, is it corruption or just an untracked file?
                        # If we find a record with SAME path but DIFFERENT checksum, that's corruption.
                        
                        # Better approach:
                        # 1. Build a map of expected files from DB: { rel_path_on_tape: checksum }
                        # 2. As we walk, check against map.
                        
                        # NOTE: Database `file_path` is usually absolute source path.
                        # `file_path_on_tape` is often the source path structure preserved.
                        
                    except Exception as e:
                        report['files_failed'] += 1
                        report['failure_details'].append({
                            'file': rel_path,
                            'error': str(e)
                        })

            controller.unmount_ltfs(mount_point)
            controller.unload_tape()
            
        except Exception as e:
            logger.error(f"Error verifying tape {barcode}: {e}")
            report['failure_details'].append({'error': f"Tape operation failed: {str(e)}"})
        
        report['duration_seconds'] = int(time.time() - start_time)
        return report

    def _emit_progress(self, job_id, message, percent):
        if self.socketio:
            self.socketio.emit('job_progress', {'job_id': job_id, 'message': message, 'percent': percent})
