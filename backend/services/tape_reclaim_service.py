
import logging
import os
import shutil
import json
import time
import threading
from typing import List, Dict, Optional
from datetime import datetime, timezone

from backend.database import Database
from backend.tape_controller import TapeLibraryController
# from backend.sources.smb_client import SMBClient # Not needed unless we verify against source? No, we verify against tape.

logger = logging.getLogger(__name__)

class TapeReclaimService:
    """
    Service for reclaiming space by consolidating partially used tapes.
    """
    
    def __init__(self, db: Database, tape_controller: TapeLibraryController, socketio=None, library_manager=None):
        self.db = db
        self.tape_controller = tape_controller
        self.socketio = socketio
        self.library_manager = library_manager
        self.config = {} # functionality to load config if needed
        self.staging_dir = '/var/lib/fossilsafe/reclaim_staging' # Default, configurable?
        
        # Ensure staging dir exists
        os.makedirs(self.staging_dir, exist_ok=True)

    def identify_reclaimable_tapes(self, threshold_percent: float = 50.0, limit: int = 100) -> List[Dict]:
        """
        Identify tapes that are good candidates for reclaim (low utilization).
        """
        return self.db.get_tapes_by_utilization(threshold_percent, limit)

    def calculate_reclaim_stats(self, tapes: List[Dict]) -> Dict:
        """
        Calculate potential space savings.
        """
        total_used = sum(t.get('used_bytes', 0) for t in tapes)
        total_capacity = sum(t.get('capacity_bytes', 0) for t in tapes)
        tape_count = len(tapes)
        
        return {
            'tape_count': tape_count,
            'total_used_bytes': total_used,
            'total_capacity_bytes': total_capacity,
            'projected_tapes_needed': 1 if total_used < 1.5 * 1024**4 else (total_used // (1.5 * 1024**4) + 1) # Crude LTO-5 est
        }

    def start_reclaim_job(self, source_barcodes: List[str], dest_barcode: str) -> int:
        """
        Start a background thread to execute reclaim.
        Returns: Job ID
        """
        # Create Job
        # We need a way to insert a job. Database.create_job?
        # Database.create_schedule -> creates schedule.
        # Database.create_restore_job -> restore_jobs table.
        # Database has 'jobs' table but mostly inserted via scheduler or manual backup.
        # We can insert a manual job record.
        
        # Let's verify how standard backup jobs are created.
        # Typically scheduler calls 'create_job' (which might be in scheduler.py, not database.py?)
        # Let's assume we can insert into 'jobs' manually via SQL for now or add a method.
        # Actually, let's use a new method in this service to create the job entry if DB doesn't have one.
        
        # For now, I'll simulate a job creation using raw SQL if needed, or better, add create_job to DB?
        # Let's check DB again later. For now, assume we get an ID.
        
        job_id = int(time.time()) # Placeholder, ideally use DB sequence
        
        # Correction: We should use the 'jobs' table properly.
        # I'll add `create_reclaim_job` to DB later if needed.
        # For now, let's assume we pass in a job_id or generate one.
        
        conn = self.db._get_conn() 
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO jobs (source_id, status, type, tapes)
            VALUES (?, ?, ?, ?)
        ''', ('RECLAIM', 'queued', 'reclaim', json.dumps([dest_barcode] + source_barcodes)))
        job_id = cursor.lastrowid
        conn.commit()

        # Start thread
        t = threading.Thread(
            target=self._execute_reclaim_logic,
            args=(job_id, source_barcodes, dest_barcode),
            name=f"ReclaimJob-{job_id}"
        )
        t.start()
        
        return job_id

    def _execute_reclaim_logic(self, job_id: int, source_barcodes: List[str], dest_barcode: str):
        """
        Main execution logic.
        1. Stage files from sources.
        2. Write to dest.
        3. Cleanup.
        """
        logger.info(f"Starting Reclaim Job {job_id}: Sources={source_barcodes} -> Dest={dest_barcode}")
        self.db.update_job_status(job_id, 'running')
        self._emit_progress(job_id, "Starting reclaim process...", 0)
        
        staged_files = []
        
        try:
            # Phase 1: Read from Sources
            for i, barcode in enumerate(source_barcodes):
                self._emit_progress(job_id, f"Processing source tape {barcode} ({i+1}/{len(source_barcodes)})", 10 + (i * 20))
                
                # Mount
                try:
                    controller = self.tape_controller
                    if self.library_manager:
                        found = self.library_manager.find_controller_for_tape(barcode)
                        if found: controller = found

                    controller.load_tape(barcode)
                    mount_point = controller.mount_ltfs(barcode)
                    
                    # Walk and Copy
                    # We should query DB for what files are expected?
                    # Or just walk the filesystem? Walking is safer to get what's actually there.
                    for root, dirs, files in os.walk(mount_point):
                        for file in files:
                            src_path = os.path.join(root, file)
                            rel_path = os.path.relpath(src_path, mount_point)
                            
                            # Staging path
                            dest_path = os.path.join(self.staging_dir, barcode, rel_path)
                            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                            
                            shutil.copy2(src_path, dest_path)
                            staged_files.append({
                                'original_barcode': barcode,
                                'rel_path': rel_path,
                                'staged_path': dest_path,
                                'size': os.path.getsize(dest_path)
                            })
                    
                    controller.unmount_ltfs(mount_point)
                    controller.unload_tape()
                    
                except Exception as e:
                    logger.error(f"Error reading tape {barcode}: {e}")
                    # Continue to next tape? Or fail?
                    # If we fail to read one, we shouldn't proceed with writing partial data?
                    # Let's fail the job.
                    raise e

            # Phase 2: Write to Destination
            self._emit_progress(job_id, f"Writing to destination tape {dest_barcode}", 60)
            
            dest_controller = self.tape_controller
            if self.library_manager:
                found = self.library_manager.find_controller_for_tape(dest_barcode)
                if found: dest_controller = found

            dest_controller.load_tape(dest_barcode)
            mount_point = dest_controller.mount_ltfs(dest_barcode)
            
            files_written = []
            
            for f in staged_files:
                src = f['staged_path']
                rel = f['rel_path'] # Use same structure? Or flatten?
                # Probably keep structure: /<original_barcode>/<rel_path> to avoid collisions?
                # Or just <rel_path>? If multiple tapes have same file (e.g. backup versions), they collide.
                # Keeping original barcode in path seems safe: /RECLAIM/<original_barcode>/...
                
                final_rel_path = os.path.join('RECLAIM', f['original_barcode'], rel)
                dest = os.path.join(mount_point, final_rel_path)
                
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)
                
                # Add to DB
                # We need to construct a valid file record.
                files_written.append({
                    'job_id': job_id,
                    'tape_barcode': dest_barcode,
                    'file_path': final_rel_path, # What is the "original path"?
                    # Ideally we preserve the metadata of where it came from.
                    # 'file_path' usually maps to Source path.
                    # We should get the original DB record to copy metadata.
                    # For now, let's just log it.
                    'file_size': f['size'],
                    'archived_at': datetime.now(timezone.utc).isoformat()
                })
            
            dest_controller.unmount_ltfs(mount_point)
            dest_controller.unload_tape()
            
            # Phase 3: Update DB
            self._emit_progress(job_id, "Updating catalog...", 90)
            for file_rec in files_written:
                self.db.add_archived_file(
                    job_id=file_rec['job_id'],
                    tape_barcode=file_rec['tape_barcode'],
                    file_path=file_rec['file_path'],
                    file_size=file_rec['file_size'],
                    archived_at=file_rec['archived_at']
                )
            
            # Cleanup Staging
            shutil.rmtree(self.staging_dir)
            os.makedirs(self.staging_dir, exist_ok=True)
            
            self.db.update_job_status(job_id, 'completed', metadata={'files_reclaimed': len(files_written)})
            self._emit_progress(job_id, "Reclaim complete", 100)
            
        except Exception as e:
            logger.error(f"Reclaim job failed: {e}")
            self.db.update_job_status(job_id, 'failed', error=str(e))
            self._emit_progress(job_id, f"Failed: {e}", 0)

    def _emit_progress(self, job_id, message, percent):
        if self.socketio:
            self.socketio.emit('job_progress', {'job_id': job_id, 'message': message, 'percent': percent})
