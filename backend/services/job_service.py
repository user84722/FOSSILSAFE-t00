import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from backend.utils.validation import validate_job_name, validate_smb_path
from backend.utils.datetime import now_utc_iso
from backend.utils.formatting import format_bytes, format_duration
from backend.smb_client import SMBScanError

class JobService:
    def __init__(self, db, backup_engine, scheduler, preflight_checker, tape_controller, source_manager, smb_client=None):
        self.db = db
        self.backup_engine = backup_engine
        self.scheduler = scheduler
        self.preflight_checker = preflight_checker
        self.tape_controller = tape_controller
        self.source_manager = source_manager
        self.smb_client = smb_client
        self._internal_job_cancel_flags: Dict[int, threading.Event] = {}
        
    def cleanup_orphaned_jobs(self):
        """Mark 'running' jobs as failed if they don't have an active worker thread."""
        print(f"DEBUG: cleanup_orphaned_jobs starting...")
        try:
            active_jobs = self.db.get_active_jobs()
            running_ids = [j.get('id') for j in active_jobs if j.get('status') in ('running', 'pending')]
            print(f"DEBUG: found running_ids: {running_ids}")
            
            if not running_ids:
                return
                
            # If we just started, everything is orphaned
            for jid in running_ids:
                print(f"DEBUG: cleaning up job {jid}")
                if self.backup_engine and hasattr(self.backup_engine, 'active_jobs'):
                    if jid not in self.backup_engine.active_jobs:
                        print(f"DEBUG: job {jid} not in engine active_jobs, marking failed")
                        self.db.update_job_status(jid, 'failed', 'Job orphaned (engine restart or crash)')
                        self.db.add_job_log(jid, 'error', 'Job marked as failed due to being orphaned after restart/crash')
                else:
                    # Fallback cleanup
                    print(f"DEBUG: backup_engine not ready, marking failed as fallback for {jid}")
                    self.db.update_job_status(jid, 'failed', 'Job orphaned')
        except Exception as e:
            print(f"DEBUG: cleanup_orphaned_jobs failed: {e}")
            pass

    def _enrich_jobs(self, jobs: List[Dict]) -> List[Dict]:
        now = datetime.now(timezone.utc)
        for job in jobs:
            # Add progress percentage
            ts = job.get('total_size', 0)
            bw = job.get('bytes_written', 0)
            status = job.get('status', 'pending')
            
            if ts and ts > 0 and bw is not None:
                job['progress'] = min(100, int((bw / ts) * 100))
            elif status == 'completed':
                job['progress'] = 100
            else:
                job['progress'] = 0
                
            # Add formatted duration
            started = job.get('started_at')
            if started:
                try:
                    start_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                    completed = job.get('completed_at')
                    end_dt = datetime.fromisoformat(completed.replace('Z', '+00:00')) if completed else now
                    job['duration'] = format_duration(max(0.0, (end_dt - start_dt).total_seconds()))
                except (ValueError, TypeError):
                    job['duration'] = '--:--:--'
            else:
                job['duration'] = '--:--:--'
                
        return jobs

    def get_jobs(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        return self._enrich_jobs(self.db.get_all_jobs(limit=limit))

    def get_job(self, job_id: int) -> Optional[Dict]:
        job = self.db.get_job(job_id)
        return self._enrich_jobs([job])[0] if job else None

    def get_job_logs(self, job_id: int, limit: int = 200) -> List[Dict]:
        return self.db.get_job_logs(job_id, limit=limit)

    def create_job(self, data: Dict) -> Tuple[bool, Any]:
        """Validate and create a backup or health check job."""
        job_type = data.get('job_type', 'backup')
        
        # Validation
        valid, error = validate_job_name(data.get('name'))
        if not valid:
            return False, error
            
        backup_mode = (data.get('backup_mode') or 'full').lower()
        if backup_mode not in ('full', 'incremental'):
            return False, 'Invalid backup_mode'

        source_id = str(data.get('source_id') or '').strip()
        source_path = str(data.get('source_path') or '').strip()

        if job_type == 'backup':
            if not source_id and not source_path:
                return False, 'source_id or source_path is required for backups'
            
            if source_id:
                source = self.source_manager.get_source(source_id, include_password=False)
                if not source:
                    return False, f"Source '{source_id}' not found"
                
                source_type = source.get('source_type') or 'smb'
                source_path = source.get('source_path') or ''
                if source_type not in ('smb', 'local', 'nfs'):
                    return False, f"Source type '{source_type}' is not implemented for backups"
            else:
                # Manual path or browser upload
                if source_path.startswith('//browser/'):
                    source_type = 'local'
                    source_path = source_path.replace('//browser/', '', 1)
                elif source_path.startswith('//'):
                    source_type = 'smb'
                else:
                    source_type = 'local' # Default for paths provided directly
            
            if not source_path:
                return False, "Selected source has no path configured"
                
            if source_type == 'smb':
                valid, error = validate_smb_path(source_path)
                if not valid:
                    return False, error

        source_job_id = data.get('source_job_id')
        if job_type == 'health_check' and not source_job_id:
            return False, 'source_job_id is required for health checks'

        compression = data.get('compression')
        encryption = data.get('encryption', 'none') # 'none', 'software', or 'hardware'
        encryption_password = data.get('encryption_password')

        if not compression:
            compression = 'zstd' if data.get('compress') else 'none'
        
        drive = int(data.get('drive', 0) or 0)
        drive_count = int(self.db.get_setting('drive_count', 1))
        
        if drive < 0 or drive >= drive_count:
            return False, f'Invalid drive selection: {drive}'

        # F12: Mandatory Preflight for First Run
        # If no previous job with this name exists, or if specifically requested
        previous_jobs = self.db.get_jobs_by_name(data['name'], limit=1)
        is_first_run = len(previous_jobs) == 0
        
        if is_first_run and not data.get('preflight_passed'):
             return False, 'Mandatory preflight required for first-time job execution.'

        # Persistence
        job_id = self.db.create_job(
            name=data['name'],
            source_id=source_id,
            tapes=data.get('tapes', []),
            verify=self.db.get_bool_setting('verification_enabled', True),
            duplicate=data.get('duplicate', False),
            scheduled_time=data.get('scheduled_time'),
            drive=drive,
            job_type=job_type,
            source_job_id=source_job_id,
            compression=compression,
            encryption=encryption,
            backup_mode=backup_mode,
            archival_policy=data.get('archival_policy', 'none'),
            pre_job_hook=data.get('pre_job_hook'),
            post_job_hook=data.get('post_job_hook'),
            source_path=source_path
        )
        
        # Start if not scheduled
        if not data.get('scheduled_time'):
            if job_type == 'health_check':
                threading.Thread(
                    target=self.backup_engine.run_health_check, 
                    args=(job_id, source_job_id), 
                    daemon=True
                ).start()
            else:
                threading.Thread(
                    target=self.backup_engine.start_backup_job, 
                    args=(job_id, encryption_password), 
                    daemon=True
                ).start()
        
        return True, job_id

    def cancel_job(self, job_id: int) -> bool:
        job = self.db.get_job(job_id)
        if not job:
            return False
            
        if job.get("job_type") in ("library_load", "library_unload", "library_force_unload", "tape_wipe", "tape_move", "diagnostics_run"):
            flag = self._internal_job_cancel_flags.get(job_id)
            if flag:
                flag.set()
            self.mark_internal_job_cancel_requested(job_id, f"Job #{job_id} cancellation requested; waiting for safe stop")
        else:
            self.backup_engine.cancel_job(job_id)
            
        return True

    def create_internal_job(self, name: str, job_type: str, tapes: List, drive: int = 0, initial_status: str = "queued", total_files: int = None, total_size: int = None) -> int:
        job_id = self.db.create_job(
            name=name,
            source_id=None,
            tapes=tapes,
            verify=False,
            duplicate=False,
            scheduled_time=None,
            drive=drive,
            job_type=job_type,
        )
        status_message = f"{name} queued" if initial_status == "queued" else f"{name} started"
        self.db.update_job_status(job_id, initial_status, status_message)
        self.add_job_log(job_id, "info", status_message)
        self.set_job_progress(job_id, "queued", status_message)
        
        if total_files is not None or total_size is not None:
            info = {}
            if total_files is not None:
                info["total_files"] = total_files
            if total_size is not None:
                info["total_size"] = total_size
            self.db.update_job_info(job_id, info)
            
        self._internal_job_cancel_flags[job_id] = threading.Event()
        return job_id

    def is_internal_job_cancelled(self, job_id: int) -> bool:
        flag = self._internal_job_cancel_flags.get(job_id)
        return bool(flag and flag.is_set())

    def mark_internal_job_cancelled(self, job_id: int, message: str) -> None:
        self.db.update_job_status(job_id, "cancelled", message)
        self.add_job_log(job_id, "warning", message)

    def mark_internal_job_cancel_requested(self, job_id: int, message: str) -> None:
        self.db.update_job_status(job_id, "cancel_requested", message)
        self.set_job_progress(job_id, "cancel_requested", message, level="warning")

    def add_job_log(self, job_id: int, level: str, message: str, details: str = None) -> None:
        try:
            self.db.add_job_log(job_id, level, message, details)
        except Exception:
            pass

    def update_job_with_log(self, job_id: int, status: str, message: str, level: str = 'info') -> None:
        self.db.update_job_status(job_id, status, message)
        self.add_job_log(job_id, level, message)

    def set_job_progress(self, job_id: int, state: str, message: str = None, level: str = "info") -> None:
        info = {"progress_state": state}
        if message:
            info["progress_message"] = message
        self.db.update_job_info(job_id, info)
        if message:
            self.add_job_log(job_id, level, message)

    def update_job_progress_message(self, job_id: int, message: str) -> None:
        self.db.update_job_info(job_id, {"progress_message": message})

    def cleanup_job_flag(self, job_id: int):
        self._internal_job_cancel_flags.pop(job_id, None)

    def run_preflight(self, data: Dict) -> Dict:
        return self.preflight_checker.run_all(data)

    def dry_run(self, data: Dict) -> Tuple[bool, Any]:
        """Perform a dry-run to estimate job requirements."""
        if not self.smb_client:
            return False, {"code": "service_unavailable", "message": "SMB client unavailable"}

        source_id = str(data.get('source_id') or '').strip()
        smb_path = str(data.get('source_path') or data.get('smb_path') or data.get('share_path') or '').strip()
        username = data.get('username', '')
        password = data.get('password', '')
        domain = data.get('domain', '')
        scan_mode = str(data.get('scan_mode') or 'quick').strip().lower()
        scan_limit = data.get('scan_limit')
        
        source_type = data.get('source_type', 'smb')
        if source_id:
            source = self.source_manager.get_source(source_id, include_password=True)
            if source:
                source_type = source.get('source_type') or 'smb'
                smb_path = source.get('source_path', smb_path)
                username = source.get('username', username)
                password = source.get('password', password)
                domain = source.get('domain', domain)
                if not smb_path:
                    return False, {"code": "source_incomplete", "message": "Selected source has no path configured"}
            else:
                return False, {"code": "source_not_found", "message": f"Source '{source_id}' not found"}
        else:
            if smb_path.startswith('//browser/'):
                source_type = 'local'
                smb_path = smb_path.replace('//browser/', '', 1)
            elif smb_path.startswith('//'):
                source_type = 'smb'
            elif smb_path:
                source_type = 'local'

        if source_type == 'smb' and not source_id and not username and not password:
            return False, {"code": "source_required", "message": "Source authentication is required for SMB dry run"}

        scan_limit_value = None
        if scan_limit is not None:
            try:
                scan_limit_value = max(1, int(scan_limit))
            except (TypeError, ValueError):
                return False, {"code": "invalid_request", "message": "scan_limit must be a positive integer"}

        if source_type == 'smb':
            if not smb_path:
                return False, {"code": "source_required", "message": "Source path required"}
            scan_result = self.smb_client.scan_directory(
                smb_path, username, password, domain,
                scan_mode=scan_mode, max_files=scan_limit_value
            )
        else:
            local_path = smb_path
            if not local_path or not os.path.exists(local_path):
                 return False, {"code": "invalid_path", "message": f"Local path '{local_path}' does not exist"}
            try:
                 file_count = 0
                 total_size = 0
                 for root, dirs, files in os.walk(local_path):
                      for f in files:
                           file_count += 1
                           total_size += os.path.getsize(os.path.join(root, f))
                           if scan_limit_value and file_count >= scan_limit_value:
                                break
                      if scan_limit_value and file_count >= scan_limit_value:
                           break
                 scan_result = {
                      'file_count': file_count,
                      'total_size': total_size,
                      'duration_ms': 0,
                      'sample_paths': [],
                      'method': 'local_scan',
                      'dir_count': 0,
                      'warnings': [],
                      'partial': False,
                      'scan_mode': scan_mode
                 }
            except Exception as e:
                 return False, {"code": "scan_failed", "message": f"Local scan failed: {e}"}

        try:
            result = {
                'smb_path': smb_path,
                'timestamp': now_utc_iso(),
                'estimates': {},
                'enumeration': {
                    'files': scan_result.get('file_count', 0),
                    'bytes': scan_result.get('total_size', 0),
                    'duration_ms': scan_result.get('duration_ms', 0),
                    'sample_paths': scan_result.get('sample_paths', []),
                    'method': scan_result.get('method', 'find'),
                    'dir_count': scan_result.get('dir_count', 0),
                    'warnings': scan_result.get('warnings', []),
                    'partial': scan_result.get('partial', False),
                    'scan_mode': scan_result.get('scan_mode', scan_mode),
                }
            }
            
            total_size = result['enumeration']['bytes']
            result['estimates']['total_files'] = result['enumeration']['files']
            result['estimates']['total_size'] = total_size
            result['estimates']['total_size_formatted'] = format_bytes(total_size)
            
            available_tapes = self.db.get_available_tapes()
            generation_capacities = {
                'LTO-5': 1.5 * 1024**4, 'LTO-6': 2.5 * 1024**4, 'LTO-7': 6.0 * 1024**4,
                'LTO-8': 12.0 * 1024**4, 'LTO-9': 18.0 * 1024**4,
            }
            
            tape_capacity = 2.5 * 1024**4  # Default LTO-6
            detected_generation = 'LTO-6'
            if available_tapes:
                for tape in available_tapes:
                    gen = tape.get('generation', 'Unknown')
                    if gen in generation_capacities:
                        if generation_capacities[gen] < tape_capacity:
                            tape_capacity = generation_capacities[gen]
                            detected_generation = gen
            
            result['estimates']['detected_generation'] = detected_generation
            compressed_capacity = tape_capacity * 2.5
            result['estimates']['tapes_needed_native'] = max(1, int(total_size / tape_capacity) + 1)
            result['estimates']['tapes_needed_compressed'] = max(1, int(total_size / compressed_capacity) + 1)
            
            generation_speeds = {
                'LTO-5': 140 * 1024**2, 'LTO-6': 160 * 1024**2, 'LTO-7': 300 * 1024**2,
                'LTO-8': 360 * 1024**2, 'LTO-9': 400 * 1024**2,
            }
            write_speed = generation_speeds.get(detected_generation, 160 * 1024**2)
            estimated_seconds = total_size / write_speed if total_size > 0 else 0
            result['estimates']['duration_seconds'] = int(estimated_seconds)
            result['estimates']['duration_formatted'] = format_duration(estimated_seconds)
            result['estimates']['available_tapes'] = len(available_tapes)
            result['estimates']['sufficient_tapes'] = len(available_tapes) >= result['estimates']['tapes_needed_compressed']
            
            # F8: Spanning Forecast
            spanning = []
            remaining_bytes = total_size
            tape_idx = 0
            while remaining_bytes > 0:
                tape_label = f"Tape {chr(65 + tape_idx)}" if tape_idx < 26 else f"Tape {tape_idx}"
                current_tape_capacity = compressed_capacity
                
                # If we have actual tapes, use their specific capacity if possible
                if tape_idx < len(available_tapes):
                    tape_obj = available_tapes[tape_idx]
                    native_cap = tape_obj.get('capacity_bytes', tape_capacity)
                    current_tape_capacity = native_cap * 2.5 # Assuming 2.5:1 compression
                
                used_in_this_tape = min(remaining_bytes, current_tape_capacity)
                pct = (used_in_this_tape / current_tape_capacity) * 100
                
                spanning.append({
                    'label': tape_label,
                    'projected_usage_bytes': int(used_in_this_tape),
                    'projected_usage_percent': round(pct, 1),
                    'capacity_bytes': int(current_tape_capacity),
                    'is_estimate': True,
                    'warning': 'Final may vary based on compression/encryption'
                })
                
                remaining_bytes -= used_in_this_tape
                tape_idx += 1
                if tape_idx > 50: # Safety break
                    break
            
            result['estimates']['spanning'] = spanning
            
            result['warnings'] = list(result['enumeration'].get('warnings') or [])
            if not result['estimates']['sufficient_tapes']:
                result['warnings'].append(f"Insufficient tapes: need {result['estimates']['tapes_needed_compressed']}, have {len(available_tapes)}")
            if total_size > 10 * 1024**4:
                result['warnings'].append("Large backup - consider running during maintenance window")

            return True, result

        except SMBScanError as exc:
            return False, {"code": exc.code, "message": exc.message, "detail": exc.detail}
        except Exception as e:
            return False, {"code": "dry_run_failed", "message": str(e)}
