#!/usr/bin/env python3
"""
Advanced Restore Features for FossilSafe
Implements:
- Multi-tape restore with prompting
- Ordered restore to minimize seek time
- Tape change workflow
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from backend.utils.encryption import EncryptionManager, HEADER_v1

logger = logging.getLogger(__name__)


@dataclass
class RestorePlan:
    """Plan for executing a restore operation"""
    files: List[Dict]
    tapes_needed: List[str]
    tape_order: List[str]
    files_per_tape: Dict[str, List[Dict]]
    estimated_time_seconds: int
    warnings: List[str]


class AdvancedRestoreEngine:
    """
    Advanced restore engine with multi-tape support and prompting
    """
    
    def __init__(self, db, tape_controller, socketio, duplication_engine=None, library_manager=None):
        self.db = db
        self.tape_controller = tape_controller
        self.socketio = socketio
        self.duplication_engine = duplication_engine
        self.library_manager = library_manager
        self.active_prompts = {}  # restore_id -> required_tape
    
    def plan_restore(self, file_ids: List[int]) -> RestorePlan:
        """
        Analyze restore request and create optimal execution plan.
        
        Returns:
            RestorePlan with ordered files and tape sequence
        """
        # Get file details from catalog
        files = []
        for file_id in file_ids:
            if self.duplication_engine:
                file_record = self.duplication_engine.restore_from_any_copy(file_id)
            else:
                file_record = self.db.get_archived_file_by_id(file_id)
            if file_record:
                files.append(file_record)
        
        if not files:
            return RestorePlan(
                files=[],
                tapes_needed=[],
                tape_order=[],
                files_per_tape={},
                estimated_time_seconds=0,
                warnings=['No files found to restore']
            )
        
        # Group files by tape
        tapes_needed = {}
        missing_positions = set()
        for f in files:
            tape = f.get('tape_barcode')
            if not tape:
                continue
            if tape not in tapes_needed:
                tapes_needed[tape] = []
            tapes_needed[tape].append(f)
            if f.get('tape_position') is None:
                missing_positions.add(tape)
        
        # Sort files within each tape by position (if available)
        for tape in tapes_needed:
            if missing_positions and tape in missing_positions:
                tapes_needed[tape].sort(
                    key=lambda f: f.get('file_path', '')
                )
            else:
                tapes_needed[tape].sort(
                    key=lambda f: (f.get('tape_position', 0), f.get('file_path', ''))
                )
        
        # Determine optimal tape order
        # Start with deterministic order, then prefer the currently loaded tape
        tape_order = sorted(tapes_needed.keys())
        current_tape = None
        if self.tape_controller:
            try:
                current_tape = self.tape_controller.get_current_tape()
            except Exception as exc:
                logger.debug(f"Unable to read current tape for ordering: {exc}")

        if current_tape:
            current_barcode = current_tape.get('barcode')
            if current_barcode in tape_order:
                tape_order.remove(current_barcode)
                tape_order.insert(0, current_barcode)
        
        # Calculate estimated time
        # Rough estimate: 2 min per tape change + 1MB/sec transfer rate
        total_size = sum(f.get('file_size', 0) for f in files)
        tape_changes = max(0, len(tape_order) - 1)
        estimated_time = (tape_changes * 120) + (total_size / (1024 * 1024))
        
        warnings = []
        if len(tape_order) > 5:
            warnings.append(f'Restore requires {len(tape_order)} tape changes')
        if missing_positions:
            warnings.append(
                'Missing tape position metadata; restore order will fallback to path sorting'
            )
        
        return RestorePlan(
            files=files,
            tapes_needed=list(tapes_needed.keys()),
            tape_order=tape_order,
            files_per_tape={t: tapes_needed[t] for t in tape_order},
            estimated_time_seconds=int(estimated_time),
            warnings=warnings
        )
    
    def execute_restore_with_prompts(self, restore_job_id: int, encryption_password: Optional[str] = None, simulation_mode: bool = False) -> bool:
        """
        Execute restore with user prompts for tape changes.
        
        Returns:
            True if restore completed successfully
        """
        # Get restore job details
        job = self.db.get_restore_job(restore_job_id)
        if not job:
            logger.error(f"Restore job {restore_job_id} not found")
            return False
        
        # Parse file IDs
        file_ids = job.get('file_ids', [])
        if isinstance(file_ids, str):
            try:
                file_ids = json.loads(file_ids)
            except (ValueError, TypeError, json.JSONDecodeError):
                file_ids = []
        if not file_ids:
            files = job.get('files', [])
            if isinstance(files, str):
                try:
                    files = json.loads(files)
                except (ValueError, TypeError, json.JSONDecodeError):
                    files = []
            file_ids = [f.get('id') for f in files if f.get('id') is not None]
        
        # Create restore plan
        plan = self.plan_restore(file_ids)
        
        if not plan.files:
            self.db.update_restore_job_status(
                restore_job_id,
                'failed',
                error='No files to restore'
            )
            return False
        
        # Update job with plan
        self.db.update_restore_job(
            restore_job_id,
            metadata={'plan': {
                'tapes_needed': plan.tapes_needed,
                'tape_order': plan.tape_order,
                'estimated_time': plan.estimated_time_seconds,
                'total_files': len(plan.files),
                'simulation_mode': simulation_mode
            }}
        )
        
        self.db.update_restore_job_status(restore_job_id, 'running')
        
        try:
            # Process each tape in order
            for tape_barcode in plan.tape_order:
                # Check if tape is loaded
                if not self._ensure_tape_loaded(restore_job_id, tape_barcode):
                    # Set waiting_for_tape status
                    self.db.update_restore_job_status(
                        restore_job_id,
                        'waiting_for_tape',
                        metadata={'required_tape': tape_barcode}
                    )
                    
                    # Emit socket event for UI prompt
                    self._emit_tape_needed(restore_job_id, tape_barcode, job)
                    
                    # Wait for tape to be ready
                    if not self._wait_for_tape_ready(restore_job_id, tape_barcode):
                        raise Exception(f"Tape {tape_barcode} was not loaded")
                
                # Resume running status
                self.db.update_restore_job_status(restore_job_id, 'running')
                
                # Restore files from this tape
                files_on_tape = plan.files_per_tape[tape_barcode]
                self._restore_files_from_tape(
                    restore_job_id,
                    tape_barcode,
                    files_on_tape,
                    job.get('destination') or '/restore',
                    job.get('metadata', {}).get('allow_overwrite', False),
                    encryption_password=encryption_password,
                    simulation_mode=simulation_mode
                )
            
                latest_job = self.db.get_restore_job(restore_job_id)
                if latest_job and latest_job.get('status') == 'cancelled':
                    self.db.update_restore_job_status(restore_job_id, 'cancelled')
                    return False

            # Mark complete
            self.db.update_restore_job_status(restore_job_id, 'completed')
            return True
            
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            self.db.update_restore_job_status(
                restore_job_id,
                'failed',
                error=str(e)
            )
            return False
    
    def _ensure_tape_loaded(self, restore_job_id: int, tape_barcode: str) -> bool:
        """
        Check if required tape is loaded in drive.
        
        Returns:
            True if tape is loaded, False if needs user action
        """
        try:
            # Resolve controller
            controller = self.tape_controller
            if self.library_manager:
                found = self.library_manager.find_controller_for_tape(tape_barcode)
                if found: controller = found

            # Check current tape in drive
            current_tape = controller.get_current_tape()
            if current_tape and current_tape.get('barcode') == tape_barcode:
                return True
            
            # If library has changer, try to load automatically
            if not controller.drive_only_mode:
                try:
                    controller.load_tape(tape_barcode)
                    return True
                except Exception:
                    pass
            
            # Needs user action
            return False
            
        except Exception as e:
            logger.warning(f"Error checking tape: {e}")
            return False
    
    def _emit_tape_needed(self, restore_job_id: int, tape_barcode: str, job: dict):
        """Emit WebSocket event that tape change is needed"""
        if self.socketio:
            self.socketio.emit('restore_tape_needed', {
                'job_id': restore_job_id,
                'tape_barcode': tape_barcode,
                'job_name': job.get('name', 'Restore'),
                'reason': f'Need tape {tape_barcode} to continue restore'
            })
        
        logger.info(f"Restore {restore_job_id} waiting for tape {tape_barcode}")
    
    def _wait_for_tape_ready(self, restore_job_id: int, 
                            tape_barcode: str, 
                            timeout: int = 3600) -> bool:
        """
        Wait for tape to be ready (either auto-loaded or user-confirmed).
        
        Args:
            timeout: Max seconds to wait (default 1 hour)
            
        Returns:
            True if tape is ready, False if timeout
        """
        import time
        
        start_time = time.time()
        self.active_prompts[restore_job_id] = tape_barcode
        
        while (time.time() - start_time) < timeout:
            # Check if tape is loaded
            try:
                current_tape = self.tape_controller.get_current_tape()
                if current_tape and current_tape.get('barcode') == tape_barcode:
                    # Tape is ready!
                    if restore_job_id in self.active_prompts:
                        del self.active_prompts[restore_job_id]
                    return True
            except Exception:
                pass
            
            # Check if job was cancelled
            job = self.db.get_restore_job(restore_job_id)
            if job and job.get('status') == 'cancelled':
                if restore_job_id in self.active_prompts:
                    del self.active_prompts[restore_job_id]
                return False
            
            # Wait a bit before checking again
            time.sleep(2)
        
        # Timeout
        if restore_job_id in self.active_prompts:
            del self.active_prompts[restore_job_id]
        return False
    
    def confirm_tape_ready(self, restore_job_id: int, tape_barcode: str) -> bool:
        """
        Called by API when user confirms tape is loaded.
        This is mainly for drive-only mode.
        """
        # Verify job is waiting for this tape
        required_tape = self.active_prompts.get(restore_job_id)
        if required_tape != tape_barcode:
            logger.warning(
                f"Tape mismatch: expected {required_tape}, got {tape_barcode}"
            )
            return False
        
        # The wait loop will detect tape is ready
        logger.info(f"Tape {tape_barcode} confirmed ready for restore {restore_job_id}")
        return True
    
    def _restore_files_from_tape(self, restore_job_id: int,
                                 tape_barcode: str,
                                 files: List[Dict],
                                 destination: str,
                                 allow_overwrite: bool,
                                 encryption_password: Optional[str] = None,
                                 simulation_mode: bool = False):
        """Restore files from a single tape"""
        # Mount tape (LTFS)
        try:
            controller = self.tape_controller
            if self.library_manager:
                found = self.library_manager.find_controller_for_tape(tape_barcode)
                if found: controller = found

            mount_point = controller.mount_ltfs(tape_barcode)
        except Exception as e:
            raise Exception(f"Failed to mount tape {tape_barcode}: {e}")
        
        try:
            if not simulation_mode:
                os.makedirs(destination, exist_ok=True)
            
            # --- Epic 4: Verify Catalog Signature ---
            catalog_path = os.path.join(mount_point, 'FOSSILSAFE_CATALOG.json')
            if os.path.exists(catalog_path):
                try:
                    from backend.catalog_security import verify_catalog
                    with open(catalog_path, 'r') as f:
                        catalog_data = json.load(f)
                    is_valid, msg = verify_catalog(catalog_data)
                    logger.info(f"Catalog verification: {is_valid}, {msg}")
                    self.db.add_job_log(restore_job_id, 'info' if is_valid else 'warning', f"Catalog Signature: {msg}")
                except Exception as e:
                    logger.warning(f"Catalog verification failed: {e}")

            restored_count = 0
            for file_record in files:
                latest_job = self.db.get_restore_job(restore_job_id)
                if latest_job and latest_job.get('status') == 'cancelled':
                    logger.info(f"Restore {restore_job_id} cancelled")
                    return

                file_path_on_tape = file_record.get('file_path_on_tape')
                if not file_path_on_tape:
                    continue
                
                # Full path on mounted tape
                source_path = os.path.join(mount_point, file_path_on_tape.lstrip('/'))
                
                # Destination path (safe)
                dest_path = self._get_safe_destination(destination, file_path_on_tape)
                
                # Copy file
                try:
                    dest_dir = os.path.dirname(dest_path)
                    os.makedirs(dest_dir, exist_ok=True)
                    if os.path.exists(dest_path) and not allow_overwrite:
                        raise FileExistsError("Destination already exists (overwrite disabled)")
                    temp_handle, temp_path = tempfile.mkstemp(
                        prefix=".restore_tmp_", dir=dest_dir
                    )
                    os.close(temp_handle)
                    try:
                        # Copy from tape to temp first
                        shutil.copy2(source_path, temp_path)

                        # Check for encryption header
                        is_encrypted = False
                        try:
                            with open(temp_path, 'rb') as f:
                                header = f.read(len(HEADER_v1))
                                if header == HEADER_v1:
                                    is_encrypted = True
                        except Exception:
                            pass

                        # Decrypt if needed
                        if is_encrypted:
                            if not encryption_password:
                                # Fallback to metadata for hardware encryption/legacy (if any), but Zero-K requires explicit password
                                decryption_password = job.get('metadata', {}).get('encryption_password') or job.get('metadata', {}).get('password')
                            else:
                                decryption_password = encryption_password

                            if not decryption_password:
                                raise Exception(f"File {file_path_on_tape} is encrypted but no password provided")
                            
                            try:
                                decrypted_path = temp_path + '.dec'
                                salt = EncryptionManager.read_salt(temp_path)
                                key, _ = EncryptionManager.derive_key(decryption_password, salt)
                                manager = EncryptionManager(key)
                                manager.decrypt_file(temp_path, decrypted_path)
                                
                                # Replace encrypted temp with decrypted
                                os.remove(temp_path)
                                temp_path = decrypted_path
                            except Exception as e:
                                raise Exception(f"Decryption failed for {file_path_on_tape}: {e}")

                        # Replace destination or copy
                        if not simulation_mode:
                            if not allow_overwrite and os.path.exists(dest_path):
                                os.remove(temp_path)
                                raise FileExistsError("Destination already exists (overwrite disabled)")
                            os.replace(temp_path, dest_path)
                        else:
                            # In simulation, we just verify the hash of what we "would" have restored
                            import hashlib
                            hasher = hashlib.sha256()
                            with open(temp_path, 'rb') as f:
                                for chunk in iter(lambda: f.read(8192), b""):
                                    hasher.update(chunk)
                            actual_hash = hasher.hexdigest()
                            expected_hash = file_record.get('checksum')
                            
                            if expected_hash and actual_hash != expected_hash:
                                logger.error(f"Simulation Hash Mismatch for {file_path_on_tape}: expected {expected_hash}, got {actual_hash}")
                                raise Exception(f"Integrity verification failed for {file_path_on_tape}")
                            
                            logger.info(f"Simulation Verified: {file_path_on_tape}")
                            os.remove(temp_path)
                    except Exception:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                        raise
                    restored_count += 1
                    
                    # Update progress
                    self.db.update_restore_job(
                        restore_job_id,
                        files_restored=restored_count
                    )
                    
                except Exception as e:
                    logger.error(f"Failed to restore {file_path_on_tape}: {e}")
            
            logger.info(
                f"Restored {restored_count}/{len(files)} files from {tape_barcode}"
            )
            
        finally:
            # Unmount tape
            try:
                controller.unmount_ltfs()
            except Exception as e:
                logger.warning(f"Failed to unmount tape: {e}")
    
    def _get_safe_destination(self, base_dest: str, file_path: str) -> str:
        """
        Generate safe destination path (prevent path traversal).
        """
        rel_path = Path(file_path)
        if rel_path.is_absolute():
            raise ValueError("Absolute paths are not allowed for restore")
        if '..' in rel_path.parts:
            raise ValueError("Path traversal is not allowed for restore")

        root = Path(base_dest)
        if not root.is_absolute():
            raise ValueError("Restore destination must be an absolute path")
        root = root.resolve()
        dest_path = (root / rel_path).resolve()

        if root != dest_path and root not in dest_path.parents:
            raise ValueError(f"Path traversal attempt: {file_path}")

        return str(dest_path)

    def verify_tape_integrity(self, tape_barcode: str, encryption_password: Optional[str] = None) -> Dict:
        """
        Epic 4: Verify tape integrity using the on-tape catalog (DR Simulator).
        This does not require the tape to be in the local database.
        """
        results = {
            'barcode': tape_barcode,
            'status': 'starting',
            'catalog_verified': False,
            'files_verified': 0,
            'files_failed': 0,
            'errors': []
        }
        
        try:
            import json
            import hashlib
            # Resolve controller
            controller = self.tape_controller
            if self.library_manager:
                found = self.library_manager.find_controller_for_tape(tape_barcode)
                if found: controller = found

            # Mount tape
            mount_point = controller.mount_ltfs(tape_barcode)
            
            try:
                # 1. Load and Verify Catalog
                catalog_path = os.path.join(mount_point, 'FOSSILSAFE_CATALOG.json')
                if not os.path.exists(catalog_path):
                    raise Exception("No catalog found on tape. Tape may not have been formatted by FossilSafe.")
                
                with open(catalog_path, 'r') as f:
                    catalog_data = json.load(f)
                
                from backend.catalog_security import verify_catalog
                is_valid, msg = verify_catalog(catalog_data)
                results['catalog_verified'] = is_valid
                results['catalog_message'] = msg
                
                if not is_valid:
                    results['errors'].append(f"Catalog Signature Error: {msg}")
                
                # 2. Iterate through files and verify
                files = catalog_data.get('files', [])
                for file_info in files:
                    file_path_on_tape = file_info.get('file_path_on_tape')
                    if not file_path_on_tape:
                        continue
                        
                    full_tape_path = os.path.join(mount_point, file_path_on_tape)
                    if not os.path.exists(full_tape_path):
                        results['files_failed'] += 1
                        results['errors'].append(f"File missing on tape: {file_path_on_tape}")
                        continue
                        
                    try:
                        # Verify hash
                        hasher = hashlib.sha256()
                        with open(full_tape_path, 'rb') as f:
                            for chunk in iter(lambda: f.read(1024*1024), b""):
                                hasher.update(chunk)
                        
                        actual_hash = hasher.hexdigest()
                        expected_hash = file_info.get('checksum')
                        
                        if expected_hash and actual_hash != expected_hash:
                            results['files_failed'] += 1
                            results['errors'].append(f"Hash mismatch for {file_path_on_tape}")
                        else:
                            results['files_verified'] += 1
                            
                    except Exception as e:
                        results['files_failed'] += 1
                        results['errors'].append(f"Error verifying {file_path_on_tape}: {e}")
                
                results['status'] = 'completed' if not results['errors'] else 'warning'
                
                # --- NEW: Generate Text Summary for Logs ---
                try:
                    from backend.utils.dr_reporting import DRReportGenerator
                    summary_text = DRReportGenerator.generate_text_summary(results)
                    results['summary_text'] = summary_text
                    
                    # Log summary to the "internal" audit system or job logs
                    logger.info("DR Verification Summary:\n" + summary_text)
                    
                    # If this was part of a specific job (optional), we could link it
                except Exception as e:
                    logger.warning(f"Failed to generate DR text summary: {e}")

                return results
                
            finally:
                controller.unmount_ltfs(mount_point)
                
        except Exception as e:
            results['status'] = 'failed'
            results['errors'].append(str(e))
            return results
