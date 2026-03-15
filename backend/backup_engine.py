#!/usr/bin/env python3
"""
FossilSafe Backup Engine
Handles backup and restore operations with comprehensive sanity checks
for handling large numbers of files and large individual files.
"""

import os
import signal
import threading
from collections import defaultdict
import hashlib
import time
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Callable
from backend.smb_client import SMBClient
from backend.sources.nfs_source import NFSSource
from backend.sources.ssh_source import SSHSource
from backend.sources.rclone_source import RcloneSource
from backend.tape_controller import TapeLibraryController as TapeController
from backend.streaming_pipeline import is_streaming_enabled
from backend.config_store import get_data_dir
from backend.utils.encryption import EncryptionManager
from dataclasses import dataclass, field
from enum import Enum
import logging
import random
from backend.services.hook_service import hook_service

logger = logging.getLogger(__name__)

# =============================================================================
# Constants and Limits
# =============================================================================

# File handling limits
MAX_FILENAME_LENGTH = 255  # POSIX limit
MAX_PATH_LENGTH = 4096  # Linux limit
MAX_SINGLE_FILE_SIZE = 12 * 1024 * 1024 * 1024 * 1024  # 12 TB (LTO-8 capacity)
MIN_FREE_SPACE_BUFFER = 1024 * 1024 * 1024  # 1 GB buffer for tape overhead

# Chunk sizes for different operations
READ_CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB chunks for reading
WRITE_CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB chunks for writing
HASH_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks for hashing

# Progress update intervals
PROGRESS_UPDATE_INTERVAL = 2  # seconds
CHECKPOINT_INTERVAL = 100  # files between checkpoints

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# Incremental planning reasons
PLAN_REASON_NEW = "NEW"
PLAN_REASON_CHANGED = "CHANGED_HASH"
PLAN_REASON_MISSING = "MISSING_ON_TAPE"
PLAN_REASON_UNKNOWN = "UNKNOWN"
PLAN_REASON_SKIPPED_UNCHANGED = "SKIPPED_UNCHANGED"
PLAN_REASON_SKIPPED_PRESENT = "SKIPPED_PRESENT_SAME_HASH"


def compute_backup_set_id(sources: List[str]) -> str:
    normalized = [str(source).strip() for source in sources if str(source).strip()]
    payload = json.dumps(sorted(normalized), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_incremental_plan(
    files: List[Dict],
    last_snapshot: Dict[str, str],
    catalog_index: Dict[str, List[str]],
    available_tapes: List[str],
) -> Dict[str, object]:
    available = set(available_tapes or [])
    to_backup: List[Dict] = []
    skipped: List[Dict] = []
    reason_counts: Dict[str, int] = defaultdict(int)
    bytes_counts: Dict[str, int] = defaultdict(int)

    for entry in files:
        path = entry.get("path")
        checksum = entry.get("checksum") or ""
        size = int(entry.get("size", 0) or 0)
        snapshot_checksum = last_snapshot.get(path)
        catalog_tapes = set(catalog_index.get(checksum, []))
        tape_present = bool(catalog_tapes & available)
        has_catalog = bool(catalog_tapes)

        if not checksum:
            reason = PLAN_REASON_UNKNOWN
            to_backup.append({**entry, "reason": reason})
        elif snapshot_checksum == checksum:
            if has_catalog and not tape_present:
                reason = PLAN_REASON_MISSING
                to_backup.append({**entry, "reason": reason})
            else:
                reason = PLAN_REASON_SKIPPED_UNCHANGED
                skipped.append({**entry, "reason": reason})
        elif snapshot_checksum and snapshot_checksum != checksum:
            reason = PLAN_REASON_CHANGED
            to_backup.append({**entry, "reason": reason})
        else:
            # File is totally new to this path
            reason = PLAN_REASON_NEW
            to_backup.append({**entry, "reason": reason})
        
        # DEBUG: Log every file reason for troubleshooting incremental issues
        try:
             # Using print because logger might be blocked or missing in this scope
             print(f"DEBUG_PLAN: path={path} size={size} snapshot_checksum={snapshot_checksum} checksum={checksum} reason={reason}")
        except Exception:
             pass

        reason_counts[reason] += 1
        bytes_counts[reason] += size

    total_files = len(files)
    total_bytes = sum(int(entry.get("size", 0) or 0) for entry in files)
    return {
        "to_backup": to_backup,
        "skipped": skipped,
        "summary": {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "to_backup_files": len(to_backup),
            "to_backup_bytes": sum(int(entry.get("size", 0) or 0) for entry in to_backup),
            "skipped_files": len(skipped),
            "skipped_bytes": sum(int(entry.get("size", 0) or 0) for entry in skipped),
            "reason_counts": dict(reason_counts),
            "reason_bytes": dict(bytes_counts),
        },
    }


class JobStatus(Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    PAUSED = 'paused'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


@dataclass
class FileEntry:
    """Represents a file to backup or restore"""
    path: str
    size: int
    mtime: float
    checksum: str = ''
    tape_barcode: str = ''
    tape_position: int = 0
    status: str = 'pending'  # pending, in_progress, completed, failed, skipped


@dataclass
class JobProgress:
    """Tracks progress of a backup/restore job"""
    total_files: int = 0
    processed_files: int = 0
    total_bytes: int = 0
    processed_bytes: int = 0
    current_file: str = ''
    current_file_progress: float = 0.0
    files_per_second: float = 0.0
    bytes_per_second: float = 0.0
    eta_seconds: int = 0
    errors: List[Dict] = field(default_factory=list)
    warnings: List[Dict] = field(default_factory=list)
    start_time: float = 0.0
    last_update: float = 0.0
    previous_processed_bytes: int = 0
    previous_processed_files: int = 0
    feeder_rate_bps: float = 0.0
    ingest_rate_bps: float = 0.0
    buffer_health: float = 0.0


class BackupEngine:
    """
    Main backup engine with comprehensive sanity checks.
    Handles backup and restore operations to/from LTO tape.
    """
    
    def __init__(self, db, tape_controller, smb_client=None, socketio=None, source_manager=None, library_manager=None, webhook_service=None, spanning_manager=None):
        """Initialize backup engine."""
        self.db = db
        self.tape_controller = tape_controller
        self.library_manager = library_manager
        self.smb_client = smb_client
        self.socketio = socketio
        self.source_manager = source_manager
        self.webhook_service = webhook_service
        self.spanning_manager = spanning_manager
        self.stop_requested = False
        self.pause_requested = False
        
        # Initialize streaming pipeline
        from backend.streaming_pipeline import get_streaming_config, StreamingBackupPipeline
        self.pipeline_config = get_streaming_config(db)
        self.streaming_pipeline = None
        if self.pipeline_config.enabled and smb_client and socketio:
            self.streaming_pipeline = StreamingBackupPipeline(db, tape_controller, smb_client, socketio, self.pipeline_config)
        
        self.active_jobs: Dict[int, dict] = {}
        self.job_locks: Dict[int, threading.Lock] = {}
        self.pause_flags: Dict[int, threading.Event] = {}
        self.cancel_flags: Dict[int, threading.Event] = {}
        self.drive_locks = defaultdict(threading.Lock)
        
        self._lock = threading.Lock()

    def _scan_source_with_hashes(
        self,
        smb_path: str,
        credentials: Dict[str, str],
        cancel_check: Callable[[], bool],
        source_type: str = 'smb',
        source: Optional[Dict] = None
    ) -> List[Dict]:
        files: List[Dict] = []
        mount_point = None
        base_path = smb_path

        try:
            if source_type == 'smb' and self.smb_client:
                smb_path = self.smb_client._normalize_path(smb_path)
            
            if source_type == 'smb' and smb_path.startswith('//'):
                mount_point = self.smb_client.mount_share(
                    share_path=smb_path,
                    username=credentials.get('username', ''),
                    password=credentials.get('password', ''),
                    domain=credentials.get('domain', '')
                )
                if not mount_point:
                    raise Exception("Failed to mount SMB share for hashing")
                base_path = mount_point
            elif source_type == 'nfs' and source:
                if smb_path and smb_path.startswith('/mnt/'):
                    base_path = smb_path
                else:
                    nfs_server = source.get('nfs_server')
                    nfs_export = source.get('nfs_export')
                    if not nfs_server or not nfs_export:
                        raise Exception("NFS server and export are required")
                    
                    success, mount_result = NFSSource.mount(nfs_server, nfs_export)
                    if not success:
                        raise Exception(f"Failed to mount NFS share: {mount_result}")
                    mount_point = mount_result
                    base_path = mount_point
            elif (source_type == 'rsync' or source_type == 'ssh') and source:
                rsync_host = source.get('host')
                rsync_user = source.get('username')
                rsync_port = source.get('port') or 22
                source_path = source.get('path', '')
                if not rsync_host or not rsync_user:
                    raise Exception("Rsync/SSH host and user are required")
                
                return SSHSource.list_files_with_hashes(rsync_host, rsync_user, source_path, rsync_port)
            elif source_type == 's3' and source:
                s3_bucket = source.get('s3_bucket')
                if not s3_bucket:
                    raise Exception("S3 bucket (remote name) is required")
                
                result = RcloneSource.list_files(s3_bucket, "")
                return result.get('entries', [])

            for root, _, filenames in os.walk(base_path):
                for filename in filenames:
                    if cancel_check():
                        raise RuntimeError("Backup cancelled during scan")
                    file_path = os.path.join(root, filename)
                    try:
                        stat = os.stat(file_path)
                    except Exception:
                        continue
                    checksum = self.calculate_checksum(file_path)
                    relative_path = os.path.relpath(file_path, base_path).replace('\\', '/')
                    files.append({
                        'path': relative_path,
                        'size': stat.st_size,
                        'checksum': checksum
                    })
        finally:
            if mount_point:
                if source_type == 'smb':
                    self.smb_client.unmount_share(share_path=smb_path, mount_point=mount_point)
                elif source_type == 'nfs':
                    NFSSource.unmount(mount_point)

        return files

    def _load_last_snapshot(self, backup_set_id: str) -> Dict[str, str]:
        snapshot = self.db.get_latest_backup_snapshot(backup_set_id)
        if not snapshot:
            return {}
        manifest_path = snapshot.get('manifest_path')
        if not manifest_path or not os.path.exists(manifest_path):
            return {}
        try:
            with open(manifest_path, 'r') as handle:
                manifest = json.load(handle)
            files = manifest.get('files') or []
            return {entry.get('path'): entry.get('checksum') for entry in files if entry.get('path')}
        except Exception:
            return {}

    def _write_snapshot_manifest(self, backup_set_id: str, job_id: int, manifest: Dict) -> str:
        base_dir = Path(get_data_dir()) / "backup-snapshots" / backup_set_id
        base_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = base_dir / f"job_{job_id}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
        with open(manifest_path, 'w') as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
        return str(manifest_path)

    def _build_tape_map(self, job_id: int) -> Dict[str, Dict[str, object]]:
        tape_map: Dict[str, Dict[str, object]] = {}
        for entry in self.db.get_archived_files_for_job(job_id):
            barcode = entry.get('tape_barcode') or 'unknown'
            tape_entry = tape_map.setdefault(barcode, {"files": [], "total_bytes": 0})
            tape_entry["files"].append({
                "path": entry.get("file_path"),
                "size": entry.get("file_size"),
                "checksum": entry.get("checksum"),
                "tape_position": entry.get("tape_position"),
            })
            tape_entry["total_bytes"] += int(entry.get("file_size", 0) or 0)
        return tape_map

    def _finalize_snapshot(
        self,
        job_id: int,
        backup_set_id: str,
        smb_path: str,
        backup_mode: str,
        files: List[Dict],
        plan_summary: Dict[str, object],
    ) -> None:
        tape_map = self._build_tape_map(job_id)
        manifest = {
            "job_id": job_id,
            "backup_set_id": backup_set_id,
            "sources": [smb_path],
            "backup_mode": backup_mode,
            "generated": datetime.utcnow().isoformat() + "Z",
            "total_files": plan_summary.get("total_files", len(files)),
            "total_bytes": plan_summary.get("total_bytes", 0),
            "files": files,
            "tape_map": tape_map,
        }
        manifest_path = self._write_snapshot_manifest(backup_set_id, job_id, manifest)
        self.db.add_backup_snapshot(
            backup_set_id,
            job_id,
            manifest_path,
            manifest.get("total_files", 0),
            manifest.get("total_bytes", 0),
            tape_map
        )
        self.db.add_job_log(
            job_id,
            "info",
            f"Snapshot recorded: {manifest_path}",
        )
        
        # Write catalog to tape for disaster recovery
        try:
            self._write_tape_catalog(job_id, manifest, tape_map)
        except Exception as e:
            logger.warning(f"Failed to write tape catalog: {e}")
            # Non-fatal - backup is still valid
    
    def _write_tape_catalog(self, job_id: int, manifest: Dict, tape_map: Dict) -> None:
        """
        Write FOSSILSAFE_CATALOG.json to tape for disaster recovery.
        Enables catalog rebuild from tapes alone.
        """
        from backend.catalog_security import sign_catalog
        
        job = self.db.get_job(job_id)
        if not job:
            return
        
        # Get primary tape barcode
        tape_barcode = job.get('tape_barcode') or list(tape_map.keys())[0] if tape_map else None
        if not tape_barcode:
            logger.warning("No tape barcode found for catalog write")
            return
        
        # Get mount point - ensure we use the correct controller
        controller = self.tape_controller
        if self.library_manager:
            found = self.library_manager.find_controller_for_tape(tape_barcode)
            if found:
                controller = found

        drive = job.get('drive', 0)
        try:
            mount_point = controller._get_mount_point(drive)
        except Exception:
            mount_point = None
        if not mount_point or not os.path.ismount(mount_point):
            logger.warning(f"Tape {tape_barcode} not mounted (drive {drive}), skipping catalog write")
            return
        
        # Build catalog structure
        catalog_data = {
            'version': '1.0',
            'tape_barcode': tape_barcode,
            'backup_set_id': manifest['backup_set_id'],
            'job_id': job_id,
            'created_at': manifest['generated'],
            'sources': manifest['sources'],
            'total_files': manifest['total_files'],
            'total_bytes': manifest['total_bytes'],
            'compression': job.get('compression', 'none'),
            'encryption': {
                'enabled': job.get('encrypt', False),
                'algorithm': 'gpg-aes256' if job.get('encrypt') else 'none'
            },
            'files': manifest['files'],
            'tape_sequence': {
                'volume_number': 1,  # TODO: Handle multi-volume
                'total_volumes': len(tape_map),
                'next_tape': None,
                'prev_tape': None
            }
        }
        
        # Sign catalog
        try:
            signed_catalog = sign_catalog(catalog_data)
        except Exception as e:
            logger.error(f"Failed to sign catalog: {e}")
            # Write unsigned catalog as fallback
            signed_catalog = catalog_data
        
        # Write to tape
        catalog_path = os.path.join(mount_point, 'FOSSILSAFE_CATALOG.json')
        try:
            with open(catalog_path, 'w') as f:
                json.dump(signed_catalog, f, indent=2)
            logger.info(f"Wrote catalog to tape {tape_barcode}")
        except Exception as e:
            logger.error(f"Failed to write catalog file: {e}")
            raise

    # =========================================================================
    # Health Check
    # =========================================================================

    def run_health_check(self, job_id: int, source_job_id: int) -> bool:
        """
        Validate archived files for a source job by verifying file checksums on tape.
        """
        self.db.update_job_status(job_id, 'running')
        failures = []
        checked = 0

        files = self.db.get_archived_files_for_health_check(source_job_id)
        source_job = self.db.get_job(source_job_id) or {}
        compression = source_job.get('compression', 'none')
        tapes = {}
        for file_entry in files:
            tape = file_entry.get('tape_barcode')
            if not tape:
                failures.append({'file': file_entry.get('file_path'), 'error': 'Missing tape barcode'})
                continue
            tapes.setdefault(tape, []).append(file_entry)

        try:
            for tape_barcode, tape_files in tapes.items():
                controller = self.tape_controller
                if self.library_manager:
                     found = self.library_manager.find_controller_for_tape(tape_barcode)
                     if found: controller = found

                controller.load_tape(tape_barcode)
                mount_point = controller.mount_ltfs(tape_barcode)
                try:
                    for file_entry in tape_files:
                        checked += 1
                        path_on_tape = file_entry.get('file_path_on_tape') or file_entry.get('file_path')
                        if not path_on_tape:
                            failures.append({'file': file_entry.get('file_path'), 'error': 'Missing tape path'})
                            continue
                        tape_path = os.path.join(mount_point, path_on_tape.lstrip('/'))
                        if not os.path.exists(tape_path):
                            failures.append({'file': file_entry.get('file_path'), 'error': 'Missing on tape'})
                            continue
                        expected_checksum = file_entry.get('checksum')
                        if not expected_checksum:
                            failures.append({'file': file_entry.get('file_path'), 'error': 'Missing checksum'})
                            continue
                        checksum_target = tape_path
                        if compression and compression != 'none':
                            try:
                                checksum_target = self._decompress_file(tape_path, compression)
                            except Exception as e:
                                failures.append({'file': file_entry.get('file_path'), 'error': f'Decompression failed: {e}'})
                                continue
                        actual_checksum = self.calculate_checksum(checksum_target)
                        if checksum_target != tape_path:
                            try:
                                os.remove(checksum_target)
                            except Exception:
                                pass
                        if actual_checksum != expected_checksum:
                            failures.append({
                                'file': file_entry.get('file_path'),
                                'error': 'Checksum mismatch',
                                'expected': expected_checksum,
                                'actual': actual_checksum
                            })
                finally:
                    try:
                        controller.unmount_ltfs(mount_point)
                    except Exception:
                        pass
                    controller.unload_tape()

            results = {
                'total_files': len(files),
                'checked_files': checked,
                'failed_files': len(failures),
                'failures': failures
            }
            self.db.store_health_check_results(job_id, results)
            message = 'Health check completed'
            if failures:
                message = f"Health check completed with {len(failures)} failures"
            self.db.update_job_status(job_id, 'completed', message)
            if self.webhook_service:
                self.webhook_service.trigger_event("JOB_COMPLETED", {"job_id": job_id, "message": message})
            return True
        except Exception as e:
            self.db.update_job_status(job_id, 'failed', str(e))
            if self.webhook_service:
                self.webhook_service.trigger_event("JOB_FAILED", {"job_id": job_id, "error": str(e)})
            return False
    
    # =========================================================================
    # Archival Policy (Intelligent Archival)
    # =========================================================================

    def execute_archival_policy(self, job_id: int) -> None:
        """
        Execute archival policy for a completed job.
        Strict Safety Checks:
        1. Job status must be 'completed' (no errors).
        2. Job must have 'archival_policy' set to 'delete_source'.
        3. Verify must be enabled and passed (implied by 'completed' status if verify=True).
        """
        job = self.db.get_job(job_id)
        if not job:
            return

        policy = job.get('archival_policy')
        if policy != 'delete_source':
            return
        
        status = job.get('status')
        if status != 'completed':
            self.db.log_entry('warning', 'archival', f"Skipping archival deletion for job {job_id}: status is {status}")
            return
            
        # Double check verification was enabled
        if not job.get('verify'):
             self.db.log_entry('warning', 'archival', f"Skipping archival deletion for job {job_id}: verification was disabled")
             return

        self.db.log_entry('info', 'archival', f"Executing archival deletion for job {job_id}")
        
        # Get files from snapshot
        snapshots = self.db.get_backup_snapshots(job_id)
        if not snapshots:
             self.db.log_entry('error', 'archival', f"No snapshot found for job {job_id}")
             return
             
        # Use first snapshot (usually only one per job)
        snapshot = snapshots[0]
        manifest_path = snapshot.get('manifest_path')
        if not manifest_path or not os.path.exists(manifest_path):
             self.db.log_entry('error', 'archival', f"Manifest missing for job {job_id}")
             return
             
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
        except Exception as e:
            self.db.log_entry('error', 'archival', f"Failed to load manifest: {e}")
            return

        source_path = manifest.get('sources', [''])[0]
        files = manifest.get('files', [])
        
        # Determine source type and credentials
        source_id = job.get('source_id')
        source = self.source_manager.get_source(source_id, include_password=True) if self.source_manager else None
        if not source:
             self.db.log_entry('error', 'archival', f"Source not found for archival deletion")
             return

        source_type = source.get('source_type', 'smb')
        deleted_count = 0
        failed_count = 0
        
        if source_type == 'smb':
            credentials = {
                'username': source.get('username'),
                'password': source.get('password'),
                'domain': source.get('domain', '')
            }
            
            for file_entry in files:
                # Only delete files that were actually backed up (not skipped)
                if file_entry.get('reason') and file_entry.get('reason') != PLAN_REASON_NEW and file_entry.get('reason') != PLAN_REASON_CHANGED and file_entry.get('reason') != PLAN_REASON_MISSING:
                     continue
                     
                rel_path = file_entry.get('path') 
                
                success = self.smb_client.delete_file(source_path, credentials['username'], credentials['password'], rel_path, credentials['domain'])
                if success:
                    deleted_count += 1
                else:
                    failed_count += 1
                    
        elif source_type == 'local' or source_type == 'nfs': # NFS mounted locally
             # Warning: NFS mounting logic is complex here as it might be unmounted.
             # Ideally we should remount or keep mounted. 
             # For now, implemented only for SMB as per plan.
             self.db.log_entry('warning', 'archival', f"Archival deletion not fully implemented for {source_type} yet")
             return

        self.db.log_entry('info', 'archival', f"Archival complete: {deleted_count} deleted, {failed_count} failed")
        
    # =========================================================================
    # Sanity Checks
    # =========================================================================
    
    def validate_file_path(self, path: str) -> Tuple[bool, str]:
        """Validate a file path for safety and compatibility"""
        if not path:
            return False, "Empty path"
        
        # Check path length
        if len(path) > MAX_PATH_LENGTH:
            return False, f"Path too long ({len(path)} > {MAX_PATH_LENGTH})"
        
        # Check filename length
        filename = os.path.basename(path)
        if len(filename) > MAX_FILENAME_LENGTH:
            return False, f"Filename too long ({len(filename)} > {MAX_FILENAME_LENGTH})"
        
        # Check for null bytes or other dangerous characters
        dangerous_chars = ['\x00', '\n', '\r']
        for char in dangerous_chars:
            if char in path:
                return False, f"Path contains dangerous character"
        
        # Normalize and check for path traversal
        normalized = os.path.normpath(path)
        if '..' in normalized.split(os.sep):
            return False, "Path traversal detected"
        
        return True, "OK"
    
    def validate_file_size(self, size: int) -> Tuple[bool, str]:
        """Validate file size is within acceptable limits"""
        if size < 0:
            return False, "Invalid negative file size"
        
        if size > MAX_SINGLE_FILE_SIZE:
            return False, f"File too large ({size} bytes > {MAX_SINGLE_FILE_SIZE})"
        
        return True, "OK"
    
    def validate_tape_capacity(self, required_bytes: int, tape_barcode: str) -> Tuple[bool, str]:
        """Check if tape has sufficient capacity"""
        tape = self.db.get_tape(tape_barcode)
        if not tape:
            return False, f"Tape {tape_barcode} not found"
        
        capacity = tape.get('capacity_bytes', 0)
        used = tape.get('used_bytes', 0)
        available = capacity - used - MIN_FREE_SPACE_BUFFER
        
        if required_bytes > available:
            return False, f"Insufficient tape capacity ({required_bytes} > {available} available)"
        
        return True, "OK"
    
    def validate_smb_connection(self, smb_path: str, credentials: dict) -> Tuple[bool, str]:
        """Validate SMB connection before starting job"""
        try:
            result = self.smb_client.test_connection(smb_path, credentials)
            if isinstance(result, dict):
                if not result.get('success'):
                    return False, result.get('error', 'Connection failed')
                return True, "OK"
            if not result:
                return False, "Connection failed"
            return True, "OK"
        except Exception as e:
            return False, str(e)
    
    def validate_tape_ready(self, barcode: str) -> Tuple[bool, str]:
        """Check if tape is ready for operation"""
        tape = self.db.get_tape(barcode)
        if not tape:
            return False, f"Tape {barcode} not found in database"
        
        if tape.get('status') not in ('available', 'in_use'):
            return False, f"Tape {barcode} not available (status: {tape.get('status')})"
        
        if tape.get('type') == 'cleaning':
            return False, f"Tape {barcode} is a cleaning tape"
        
        return True, "OK"
    
    def preflight_check(self, job_config: dict) -> Tuple[bool, List[str]]:
        """
        Run comprehensive preflight checks before starting a job.
        Returns (success, list of issues)
        """
        issues = []
        
        # Check job type
        job_type = job_config.get('job_type', 'backup')
        
        if job_type == 'backup':
            source_id = job_config.get('source_id')
            if not source_id:
                issues.append("No source selected")
            source = self.source_manager.get_source(source_id, include_password=True) if (source_id and self.source_manager) else None
            if not source:
                issues.append(f"Source '{source_id}' not found")
            else:
                source_type = source.get('source_type') or 'smb'
                source_path = source.get('source_path')
                if source_type == 'smb':
                    if not source_path:
                        issues.append("No SMB path specified")
                    else:
                        valid, msg = self.validate_smb_connection(source_path, source)
                        if not valid:
                            issues.append(f"SMB connection: {msg}")
            
            # Check tapes
            tapes = job_config.get('tapes', [])
            if not tapes:
                issues.append("No tapes specified for backup")
            
            for tape in tapes:
                valid, msg = self.validate_tape_ready(tape)
                if not valid:
                    issues.append(msg)
        
        elif job_type == 'restore':
            # Check restore targets
            files = job_config.get('files', [])
            if not files:
                issues.append("No files specified for restore")
            
            # Validate destination
            destination = job_config.get('destination')
            if destination:
                valid, msg = self.validate_file_path(destination)
                if not valid:
                    issues.append(f"Destination path: {msg}")
        
        # Check tape library status
        relevant_controllers = set()
        if self.library_manager:
            # Check libraries for specified tapes
            tapes = job_config.get('tapes', [])
            for barcode in tapes:
                c = self.library_manager.find_controller_for_tape(barcode)
                if c: relevant_controllers.add(c)
        
        # If no specific libraries found (or no tapes specified), check default
        if not relevant_controllers:
            relevant_controllers.add(self.tape_controller)

        for controller in relevant_controllers:
            try:
                library_info = controller.get_library_info()
                if not library_info.get('online'):
                    lib_id = getattr(controller, 'library_id', 'default')
                    issues.append(f"Tape library '{lib_id}' is offline")
            except Exception as e:
                issues.append(f"Cannot connect to tape library: {e}")
        
        # Check disk space for staging
        try:
            staging_path = Path(tempfile.gettempdir())
            statvfs = os.statvfs(staging_path)
            free_space = statvfs.f_frsize * statvfs.f_bavail
            if free_space < MIN_FREE_SPACE_BUFFER:
                issues.append(f"Low disk space for staging ({free_space} bytes)")
        except Exception as e:
            issues.append(f"Cannot check disk space: {e}")
        
        return len(issues) == 0, issues
    
    # =========================================================================
    # File Operations with Integrity Checks
    # =========================================================================
    
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
    
    def calculate_checksum_from_data(self, data: bytes, algorithm: str = 'sha256') -> str:
        """Calculate checksum from data in memory"""
        return hashlib.new(algorithm, data).hexdigest()
    
    def verify_file_integrity(self, file_path: str, expected_checksum: str) -> bool:
        """Verify file integrity by comparing checksums"""
        try:
            actual_checksum = self.calculate_checksum(file_path)
            return actual_checksum == expected_checksum
        except Exception:
            return False
    
    def safe_copy_file(self, src: str, dst: str, verify: bool = True) -> Tuple[bool, str]:
        """
        Copy a file with integrity verification.
        Handles large files with chunked copying.
        """
        try:
            src_path = Path(src)
            dst_path = Path(dst)
            
            # Validate paths
            valid, msg = self.validate_file_path(src)
            if not valid:
                return False, f"Source path invalid: {msg}"
            
            valid, msg = self.validate_file_path(dst)
            if not valid:
                return False, f"Destination path invalid: {msg}"
            
            # Check source exists
            if not src_path.exists():
                return False, f"Source file not found: {src}"
            
            # Validate size
            size = src_path.stat().st_size
            valid, msg = self.validate_file_size(size)
            if not valid:
                return False, msg
            
            # Ensure destination directory exists
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Calculate source checksum if verifying
            src_checksum = ''
            if verify:
                src_checksum = self.calculate_checksum(src)
            
            # Copy with chunked read/write for large files
            with open(src, 'rb') as src_file:
                with open(dst, 'wb') as dst_file:
                    while True:
                        chunk = src_file.read(WRITE_CHUNK_SIZE)
                        if not chunk:
                            break
                        dst_file.write(chunk)
            
            # Verify if requested
            if verify and src_checksum:
                if not self.verify_file_integrity(dst, src_checksum):
                    # Remove corrupt destination
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                    return False, "Integrity verification failed after copy"
            
            # Preserve timestamps
            src_stat = src_path.stat()
            os.utime(dst, (src_stat.st_atime, src_stat.st_mtime))
            
            return True, src_checksum
            
        except Exception as e:
            logger.error(f"File copy failed {src} -> {dst}: {e}")
            return False, str(e)
    
    # =========================================================================
    # Backup Operations
    # =========================================================================
    
    def start_backup_job(self, job_id: int, encryption_password: Optional[str] = None):
        """Start a backup job with comprehensive error handling"""
        job = self.db.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return
        
        drive = job.get('drive', 0) if job else 0

        # Initialize job tracking
        with self._lock:
            self.job_locks[job_id] = threading.Lock()
            self.pause_flags[job_id] = threading.Event()
            self.cancel_flags[job_id] = threading.Event()
            self.active_jobs[job_id] = {
                'progress': JobProgress(start_time=time.time()),
                'status': JobStatus.RUNNING
            }
        
        try:
            tapes_raw = job.get('tapes', [])
            if isinstance(tapes_raw, str):
                required_tapes = json.loads(tapes_raw)
            elif isinstance(tapes_raw, list):
                required_tapes = tapes_raw
        except Exception:
            required_tapes = []

        try:
            if drive == -1:
                # Auto-Queue Mode: Poll for ANY available drive
                self.db.update_job_status(job_id, 'queued')
                self._emit_job_update(job_id, 'queued', 'Waiting for available drive...')
                
                logger.info(f"Job {job_id} entering tape-aware selection loop. Required: {required_tapes}")

                selected_drive = None
                iteration = 0
                while selected_drive is None:
                    # Determine candidate drives
                    candidate_drives = list(self.drive_locks.keys())
                    if True: # Fully enabled in AGPL-3.0
                         candidate_drives = [0] # Restrict to Drive 0
                    
                    # Tape-Aware Optimization: Phase 1 - Try for OPTIMAL drives (matching tape already mounted)
                    # To minimize mechanical wear, we prefer a drive that already has one of our tapes.
                    optimal_drives = []
                    if required_tapes:
                        for d in candidate_drives:
                            if self.tape_controller and self.tape_controller._get_mounted_tape(d) in required_tapes:
                                optimal_drives.append(d)
                    
                    for candidate in optimal_drives:
                        lock = self.drive_locks.get(candidate)
                        if lock and lock.acquire(blocking=False):
                            selected_drive = candidate
                            logger.info(f"Tape-Aware Job {job_id}: Selected optimal drive {selected_drive} (matching media mounted)")
                            break
                    
                    if selected_drive: break

                    # Progress feedback
                    if iteration % 5 == 0:
                         self._emit_job_update(job_id, 'queued', 'Optimizing drive selection (searching for matching media)...')
                    
                    # Phase 2: If no optimal drive found, wait a tiny bit to allow an "optimal" job to grab it
                    # before we consider snatching a drive that might be 'warm' for another job.
                    # This 1-second grace period prevents random 'theft' of mounted tapes by unrelated jobs.
                    time.sleep(1.0) 
                    iteration += 1

                    # Phase 3: Try ANY available drive
                    random.shuffle(candidate_drives)
                    for candidate in candidate_drives:
                        lock = self.drive_locks.get(candidate)
                        if lock and lock.acquire(blocking=False):
                            selected_drive = candidate
                            break
                    
                    if selected_drive: break

                    # Phase 4: Long Wait and check cancel
                    for _ in range(50): # Wait 5s total in 0.1s chunks before retrying loop
                        if self.cancel_flags[job_id].is_set():
                            self.db.update_job_status(job_id, 'cancelled')
                            self._emit_job_update(job_id, 'cancelled')
                            if self.webhook_service:
                                self.webhook_service.trigger_event("JOB_CANCELLED", {"job_id": job_id})
                            return
                        time.sleep(0.1)
                
                drive = selected_drive
                # Update job definition in memory and DB so _execute_backup knows the drive
                job['drive'] = drive 
                self.db.update_job_drive(job_id, drive) 
            else:
                # Specific Drive Mode
                drive_lock = self.drive_locks[drive]
                if not drive_lock.acquire(blocking=False):
                    self.db.update_job_status(job_id, 'waiting_for_drive')
                    self._emit_job_update(job_id, 'waiting_for_drive', f'Waiting for drive {drive}')
                    while not drive_lock.acquire(timeout=5):
                        if self.cancel_flags[job_id].is_set():
                            self.db.update_job_status(job_id, 'cancelled')
                            self._emit_job_update(job_id, 'cancelled')
                            return

            self.db.update_job_status(job_id, 'running')
            self._emit_job_update(job_id, 'started')
            
            # Run Pre-Job Hook
            pre_hook = job.get('pre_job_hook')
            if pre_hook:
                self._emit_job_update(job_id, 'running', f"Executing pre-hook: {pre_hook}")
                hook_res = hook_service.execute_hook(pre_hook, job_id)
                if not hook_res['success']:
                    error_msg = f"Pre-hook {pre_hook} failed: {hook_res.get('error') or 'Non-zero exit'}"
                    logger.error(error_msg)
                    self.db.add_job_log(job_id, 'error', error_msg)
                    raise Exception(f"Job aborted: Pre-hook failed ({hook_res.get('exit_code')})")
                else:
                    self.db.add_job_log(job_id, 'info', f"Pre-hook {pre_hook} completed successfully")

            # Initialize spanning session if needed
            if self.spanning_manager and len(required_tapes) > 1:
                logger.info(f"Initializing spanning session for job {job_id} with {len(required_tapes)} tapes")
                initial_tape = required_tapes[0]
                self.spanning_manager.create_session(job_id, initial_tape)

            # Run the backup
            self._execute_backup(job_id, job, encryption_password=encryption_password)
            
            # Run Post-Job Hook
            post_hook = job.get('post_job_hook')
            if post_hook:
                self._emit_job_update(job_id, 'running', f"Executing post-hook: {post_hook}")
                hook_res = hook_service.execute_hook(post_hook, job_id)
                if not hook_res['success']:
                    logger.warning(f"Post-hook {post_hook} failed for job {job_id}: {hook_res.get('error') or 'Non-zero exit'}")
                    self.db.add_job_log(job_id, 'warning', f"Post-hook {post_hook} failed: {hook_res.get('stderr')}")
                else:
                    self.db.add_job_log(job_id, 'info', f"Post-hook {post_hook} completed successfully")

            # Execute archival policy if enabled (delete source files after verified backup)
            try:
                self.execute_archival_policy(job_id)
            except Exception as e:
                logger.warning(f"Archival policy execution failed for job {job_id}: {e}")
            
        except Exception as e:
            logger.exception(f"Backup job {job_id} failed with exception")
            self.db.update_job_status(job_id, 'failed')
            self.db.log_entry('error', 'backup', f"Job {job_id} failed: {e}")
            self._emit_job_update(job_id, 'failed', str(e))
            if self.webhook_service:
                self.webhook_service.trigger_event("JOB_FAILED", {"job_id": job_id, "error": str(e)})
        
        finally:
            # Cleanup
            with self._lock:
                self.active_jobs.pop(job_id, None)
                self.job_locks.pop(job_id, None)
                self.pause_flags.pop(job_id, None)
                self.cancel_flags.pop(job_id, None)
            drive_lock = self.drive_locks.get(drive)
            if drive_lock and drive_lock.locked():
                drive_lock.release()
    
    def _backup_with_tar(self, job_id: int, source_path: str, tapes: List[str],
                        credential: Optional[Dict], drive: int, file_list: List[Dict],
                        compression: str, encryption: str, encryption_password: Optional[str]) -> bool:
        """Perform a multi-tape backup using tar and TapeSpanningManager"""
        temp_list = None
        try:
            # 1. Prepare file list for tar
            with tempfile.NamedTemporaryFile(mode='w', suffix='.list', delete=False) as f:
                for file_info in file_list:
                    # In this mode, we expect source_path to be the local mount point of the source
                    f.write(file_info['path'] + '\n')
                temp_list = f.name
            
            # 2. Resolve drive device for tar
            device = "/dev/nst0" # Fallback
            if hasattr(self.tape_controller, 'get_drive_device'):
                device = self.tape_controller.get_drive_device(drive)
            
            # 3. Build tar command
            extra_args = []
            if compression == 'zstd': extra_args.append('-I zstd')
            elif compression == 'gzip': extra_args.append('-z')
            
            cmd = self.spanning_manager.build_tar_command(
                job_id, device, temp_list, extra_args
            )
            
            self.db.add_job_log(job_id, "info", f"Executing tar spanning command: {' '.join(cmd)}")
            
            # 4. Execute tar
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=source_path
            )
            
            # Monitor progress
            while process.poll() is None:
                # Check for cancellation
                if self.cancel_flags.get(job_id) and self.cancel_flags[job_id].is_set():
                    process.terminate()
                    self.spanning_manager.fail_session(job_id, "Cancelled by user")
                    return False
                
                # Check for pause
                if self.pause_flags.get(job_id) and self.pause_flags[job_id].is_set():
                    process.send_signal(signal.SIGSTOP)
                    while self.pause_flags[job_id].is_set() and not self.cancel_flags[job_id].is_set():
                        time.sleep(1)
                    process.send_signal(signal.SIGCONT)

                # Check spanning status
                status = self.spanning_manager.get_session_status(job_id)
                if status and status.get('state') == 'failed':
                    process.terminate()
                    return False
                
                time.sleep(1)
            
            if process.returncode != 0:
                stdout, stderr = process.communicate()
                error_msg = f"tar failed with code {process.returncode}: {stderr}"
                self.db.add_job_log(job_id, "error", error_msg)
                self.spanning_manager.fail_session(job_id, error_msg)
                return False
            
            # 5. Mark session complete
            self.spanning_manager.complete_session(job_id)
            self.db.add_job_log(job_id, "info", "Tar-based spanning backup completed successfully")
            
            # 6. Record processed files in database catalog
            try:
                archived_records = []
                primary_tape = tapes[0] if tapes else "UNKNOWN"
                for f_info in file_list:
                    f_path = f_info.get('path', '')
                    archived_records.append({
                        'job_id': job_id,
                        'tape_barcode': primary_tape,
                        'file_path': os.path.join(source_path, f_path),
                        'file_name': os.path.basename(f_path),
                        'file_size': f_info.get('size', 0),
                        'file_path_on_tape': f_path,
                        'checksum': '',
                        'tape_position': 0
                    })
                if archived_records:
                    self.db.batch_insert_archived_files(archived_records)
                    self.db.add_job_log(job_id, "info", f"Cataloged {len(archived_records)} files into database")
            except Exception as catalog_err:
                self.db.add_job_log(job_id, "warning", f"Failed to catalog files: {catalog_err}")

            return True
            
        except Exception as e:
            logger.exception(f"Error in _backup_with_tar for job {job_id}")
            self.db.add_job_log(job_id, "error", f"Spanning backup exception: {e}")
            if self.spanning_manager:
                self.spanning_manager.fail_session(job_id, str(e))
            return False
        finally:
            if temp_list and os.path.exists(temp_list):
                try: os.remove(temp_list)
                except: pass

    def _execute_backup(self, job_id: int, job: dict, resume_state: Optional[dict] = None, encryption_password: Optional[str] = None):
        """Execute the actual backup operation"""
        source_id = job.get('source_id')
        smb_path = ''
        source_type = 'smb'
        source = None

        if source_id and self.source_manager:
            source = self.source_manager.get_source(source_id, include_password=True)
            if not source:
                raise Exception(f"Source '{source_id}' not found for job {job_id}")
            source_type = source.get('source_type') or 'smb'
            smb_path = source.get('source_path', '')
        else:
            # Handle manual path or browser upload from DB
            smb_path = job.get('smb_path')
            if not smb_path:
                raise Exception("source_id or valid source_path is required for backups")
            
            # Simple heuristic for source_type if not provided
            if smb_path.startswith('//browser/'):
                source_type = 'local'
            elif smb_path.startswith('//'):
                source_type = 'smb'
            else:
                source_type = 'local'
        
        if source_type == 'smb' and self.smb_client:
             smb_path = self.smb_client._normalize_path(smb_path)
        
        tapes = json.loads(job.get('tapes', '[]')) if isinstance(job.get('tapes'), str) else job.get('tapes', [])
        verify = job.get('verify', True)
        compression = job.get('compression', 'none')
        encryption = job.get('encryption', 'none')
        # Use provided password (Zero-K) or fallback to DB if absolutely necessary (legacy/hardware)
        if not encryption_password:
            encryption_password = job.get('encryption_password')
        
        drive = job.get('drive', 0)
        backup_mode = (job.get('backup_mode') or 'full').lower()
        if backup_mode not in ('full', 'incremental'):
            backup_mode = 'full'
        
        # Determine logical path for backup set tracking
        logical_path = smb_path
        if source_type == 'rsync':
            logical_path = f"rsync://{source.get('rsync_host') if source else 'unknown'}/{source.get('source_path') if source else ''}"
        elif source_type == 's3':
            logical_path = f"s3://{source.get('s3_bucket') if source else 'unknown'}"
            
        backup_set_id = compute_backup_set_id([logical_path])
        self.db.add_backup_set(backup_set_id, [logical_path])
        try:
            self.db.update_job_info(job_id, {"backup_set_id": backup_set_id, "backup_mode": backup_mode})
        except Exception:
            pass
        
        progress = self.active_jobs[job_id]['progress']
        
        credentials = {}
        if source_type == 'smb':
            if not source:
                raise Exception(f"SMB source information missing for job {job_id}")
            credentials = {
                'username': source.get('username'),
                'password': source.get('password'),
                'domain': source.get('domain', '')
            }
        elif source_type == 'local':
            credentials = {}
        elif source_type == 'nfs':
            credentials = {} # NFS usually authenticated via IP/Host or mount options
        elif (source_type == 'rsync' or source_type == 'ssh'):
            credentials = {
                'username': source.get('username'),
                'host': source.get('host'),
                'port': source.get('port', 22),
                'path': source.get('path', '')
            }
        elif source_type == 's3':
            credentials = {
                's3_bucket': source.get('s3_bucket')
            }
        else:
            raise Exception(f"Source type '{source_type}' not supported for backups")
        
        main_mount_point = None
        try:
            # Handle NFS mounting for the whole job
            if source_type == 'nfs':
                success, mount_result = NFSSource.mount(
                    source.get('nfs_server', ''),
                    source.get('nfs_export', '')
                )
                if not success:
                    raise Exception(f"NFS mount failed: {mount_result}")
                main_mount_point = mount_result
                smb_path = main_mount_point # Override smb_path with local mount point for scanner & pipeline
            
            # Enumerate files with hashes (plan stage)
            self._emit_job_update(job_id, 'scanning', 'Scanning source files...')
            scan_start = time.time()
            try:
                files = self._scan_source_with_hashes(
                    smb_path,
                    credentials,
                    cancel_check=lambda: self.cancel_flags[job_id].is_set(),
                    source_type=source_type,
                    source=source
                )
                progress.total_files = len(files)
                progress.total_bytes = sum(f.get('size', 0) for f in files)
            except RuntimeError as e:
                if "cancelled" in str(e).lower():
                    self.db.update_job_status(job_id, 'cancelled')
                    self._emit_job_update(job_id, 'cancelled')
                    if self.webhook_service:
                        self.webhook_service.trigger_event("JOB_CANCELLED", {"job_id": job_id})
                    return
                raise Exception(f"Failed to scan source: {e}")
            except Exception as e:
                raise Exception(f"Failed to scan source: {e}")

            scan_duration = time.time() - scan_start
            self.db.add_job_log(
                job_id,
                "info",
                f"Scan complete: {len(files)} files ({progress.total_bytes} bytes) in {scan_duration:.1f}s",
            )

            if not files:
                self.db.update_job_status(job_id, 'completed')
                self._emit_job_update(job_id, 'completed', 'No files to backup')
                manifest = {
                    "job_id": job_id,
                    "backup_set_id": backup_set_id,
                    "sources": [smb_path],
                    "backup_mode": backup_mode,
                    "generated": datetime.utcnow().isoformat() + "Z",
                    "total_files": 0,
                    "total_bytes": 0,
                    "files": [],
                    "tape_map": {},
                }
                manifest_path = self._write_snapshot_manifest(backup_set_id, job_id, manifest)
                self.db.add_backup_snapshot(
                    backup_set_id,
                    job_id,
                    manifest_path,
                    0,
                    0,
                    {}
                )
                return

            plan = None
            if backup_mode == 'incremental':
                last_snapshot = self._load_last_snapshot(backup_set_id)
                if last_snapshot:
                    catalog_index = self.db.get_checksum_catalog()
                    available_tapes = [tape.get('barcode') for tape in self.db.get_tape_inventory()]
                    plan = compute_incremental_plan(files, last_snapshot, catalog_index, available_tapes)
                else:
                    self.db.add_job_log(
                        job_id,
                        "warning",
                        "No previous snapshot found; running incremental job as full backup",
                    )

            if plan is None:
                plan_summary = {
                    "total_files": len(files),
                    "total_bytes": sum(f.get('size', 0) for f in files),
                    "to_backup_files": len(files),
                    "to_backup_bytes": sum(f.get('size', 0) for f in files),
                    "skipped_files": 0,
                    "skipped_bytes": 0,
                    "reason_counts": {PLAN_REASON_NEW: len(files)},
                    "reason_bytes": {PLAN_REASON_NEW: sum(f.get('size', 0) for f in files)},
                }
                plan = {
                    "to_backup": [{**entry, "reason": PLAN_REASON_NEW} for entry in files],
                    "skipped": [],
                    "summary": plan_summary,
                }

            plan_summary = plan["summary"]
            self.db.update_job_info(job_id, {
                "total_files": plan_summary["to_backup_files"],
                "total_size": plan_summary["to_backup_bytes"],
                "plan_total_files": plan_summary["total_files"],
                "plan_total_size": plan_summary["total_bytes"],
                "plan_skipped_files": plan_summary["skipped_files"],
                "plan_skipped_size": plan_summary["skipped_bytes"],
            })
            self.db.add_job_log(
                job_id,
                "info",
                f"Plan: {plan_summary['to_backup_files']} to backup, "
                f"{plan_summary['skipped_files']} skipped "
                f"({plan_summary['total_files']} total)",
                details=json.dumps(plan_summary.get("reason_counts", {})),
            )

            files_to_backup = plan["to_backup"]
            progress.total_files = plan_summary["to_backup_files"]
            progress.total_bytes = plan_summary["to_backup_bytes"]

            if plan_summary["to_backup_files"] == 0:
                self.db.add_job_log(job_id, "info", "No new files to backup, but proceeding with catalog generation")

            self._emit_job_update(
                job_id,
                'running',
                f'Plan ready: {plan_summary["to_backup_files"]} files ({plan_summary["to_backup_bytes"]} bytes)'
            )

            is_smb_source = smb_path.startswith('//')
            files_for_external = []
            for entry in files_to_backup:
                relative_path = entry.get("path")
                if is_smb_source:
                    full_path = relative_path
                else:
                    full_path = os.path.join(smb_path, relative_path)
                files_for_external.append({
                    **entry,
                    "path": full_path,
                    "relative_path": relative_path,
                    "smb_path": smb_path if is_smb_source else None,
                })

            # Duplication flow
            if job.get('duplicate'):
                if not self.duplication_engine:
                    raise Exception("Duplication engine is not available")
                success = self.duplication_engine.execute_backup_with_duplication(
                    job_id=job_id,
                    source_path=smb_path,
                    tapes=tapes,
                    credential=credentials,
                    drive=drive,
                    file_list=files_for_external
                )
                if success:
                    self._finalize_snapshot(
                        job_id,
                        backup_set_id,
                        smb_path,
                        backup_mode,
                        files,
                        plan_summary
                    )
                return

            # Streaming pipeline flow
            if self.streaming_pipeline and is_streaming_enabled(self.db):
                if not tapes:
                    raise Exception("No tapes specified for streaming backup")
                if len(tapes) > 1:
                    self.db.log_entry('warning', 'backup',
                        f"Streaming pipeline supports one tape per job. Using {tapes[0]}.")
                success = self.streaming_pipeline.execute_streaming_backup(
                    job_id=job_id,
                    source_path=smb_path,
                    tape_barcode=tapes[0],
                    credential=credentials,
                    drive=drive,
                    file_list=files_for_external,
                    compression=compression,
                    encryption=encryption,
                    encryption_password=encryption_password
                )
                if success:
                    self._finalize_snapshot(
                        job_id,
                        backup_set_id,
                        smb_path,
                        backup_mode,
                        files,
                        plan_summary
                    )
                return

            # Sort files by size for better tape packing
            files_to_backup.sort(key=lambda f: f.get('size', 0), reverse=True)

            # Resume handling
            resume_state = resume_state or {}
            last_file_index = max(0, int(resume_state.get('last_file_index', 0) or 0))
            last_file_path = resume_state.get('last_file_path') or ''
            resume_current_tape = resume_state.get('current_tape') or ''

            if last_file_path:
                for idx, file_info in enumerate(files_to_backup):
                    if file_info.get('path') == last_file_path:
                        last_file_index = idx + 1
                        break

            file_idx = min(last_file_index, len(files_to_backup))
            current_tape_idx = 0
            if resume_current_tape and resume_current_tape in tapes:
                current_tape_idx = tapes.index(resume_current_tape)

            if resume_state:
                progress.processed_files = int(resume_state.get('files_completed', 0) or 0)
                progress.processed_bytes = int(resume_state.get('bytes_written', 0) or 0)
                state = resume_state.get('state', {})
                if isinstance(state, str):
                    try:
                        state = json.loads(state)
                    except Exception:
                        state = {}
                progress.current_file = state.get('current_file', '')
                progress.errors = state.get('errors', [])
            
            # Spanning flow (if multiple tapes and spanning_manager available)
            if self.spanning_manager and len(tapes) > 1 and not (job.get('duplicate') or is_streaming_enabled(self.db)):
                self.db.add_job_log(job_id, "info", "Using tar-based multi-tape spanning")
                success = self._backup_with_tar(
                    job_id=job_id,
                    source_path=smb_path,
                    tapes=tapes,
                    credential=credentials,
                    drive=drive,
                    file_list=files_for_external,
                    compression=compression,
                    encryption=encryption,
                    encryption_password=encryption_password
                )
                if success:
                    self._finalize_snapshot(
                        job_id,
                        backup_set_id,
                        smb_path,
                        backup_mode,
                        files,
                        plan_summary
                    )
                return

            # Process each tape
            
            while file_idx < len(files_to_backup) and current_tape_idx < len(tapes):
                # Check for cancellation
                if self.cancel_flags[job_id].is_set():
                    self.db.update_job_status(job_id, 'cancelled')
                    self._emit_job_update(job_id, 'cancelled')
                    return
                
                # Check for pause
                while self.pause_flags[job_id].is_set():
                    time.sleep(1)
                    if self.cancel_flags[job_id].is_set():
                        self.db.update_job_status(job_id, 'cancelled')
                        return
                
                tape_barcode = tapes[current_tape_idx]
                tape_loaded = False
                mount_point = None
                tape_bytes_written = 0
                tape_used = 0
                tape_capacity = 0

                try:
                    # Resolve controller for this tape
                    controller = self.tape_controller
                    if self.library_manager:
                        found = self.library_manager.find_controller_for_tape(tape_barcode)
                        if found: controller = found

                    # Load tape
                    try:
                        controller.load_tape(tape_barcode, drive)
                        tape_loaded = True
                        self.db.update_tape_status(tape_barcode, 'in_use')
                        
                        # Enable Hardware Encryption if requested
                        if encryption == 'hardware':
                             if True: # Fully enabled in AGPL-3.0
                                 self.db.add_job_log(job_id, "info", f"Enabling hardware encryption for tape {tape_barcode}")
                                 success = controller.enable_hardware_encryption(drive=drive)
                                 if not success:
                                     raise Exception(f"Failed to enable hardware encryption on drive {drive}")
                    except Exception as e:
                        self.db.add_job_log(job_id, "error", f"DEBUG: Tape load failed for {tape_barcode}: {e}")
                        progress.errors.append({
                            'type': 'tape_load',
                            'tape': tape_barcode,
                            'error': str(e)
                        })
                        current_tape_idx += 1
                        continue

                    # Mount LTFS
                    try:
                        mount_point = controller.mount_ltfs(tape_barcode, drive)
                    except Exception as e:
                        self.db.add_job_log(job_id, "error", f"DEBUG: Tape mount failed for {tape_barcode}: {e}")
                        progress.errors.append({
                            'type': 'mount',
                            'tape': tape_barcode,
                            'error': str(e)
                        })
                        current_tape_idx += 1
                        continue

                    # Get tape capacity
                    tape_info = self.db.get_tape(tape_barcode)
                    tape_capacity = tape_info.get('capacity_bytes', 0) if tape_info else 0
                    tape_used = tape_info.get('used_bytes', 0) if tape_info else 0
                    tape_available = tape_capacity - tape_used - MIN_FREE_SPACE_BUFFER
                    
                    self.db.add_job_log(job_id, "info", f"DEBUG: Tape info. barcode={tape_barcode}, info_found={bool(tape_info)}, capacity={tape_capacity}, used={tape_used}, available={tape_available}")

                    if resume_state and tape_barcode == resume_current_tape:
                        try:
                            tape_bytes_written = self.db.get_archived_size_for_job_tape(
                                job_id,
                                tape_barcode
                            )
                        except Exception as e:
                            logger.warning(f"Failed to read resume tape usage: {e}")

                    # Backup files to this tape
                    self.db.add_job_log(job_id, "info", f"DEBUG: Entering inner loop. tape_bytes_written={tape_bytes_written}, tape_available={tape_available}")
                    while file_idx < len(files_to_backup) and tape_bytes_written < tape_available:
                        file_info = files_to_backup[file_idx]
                        file_path = file_info.get('path', '')
                        file_size = file_info.get('size', 0)

                        # Check if file fits on current tape
                        if file_size > tape_available - tape_bytes_written:
                            # Try next file or move to next tape
                            if file_size > tape_available:
                                # File too large for any single tape? Split needed
                                progress.errors.append({
                                    'type': 'file_too_large',
                                    'file': file_path,
                                    'size': file_size
                                })
                                file_idx += 1
                                continue
                            else:
                                # Switch to next tape
                                break

                        # Check for cancel/pause
                        if self.cancel_flags[job_id].is_set():
                            break

                        while self.pause_flags[job_id].is_set():
                            time.sleep(1)

                        # Backup the file
                        progress.current_file = file_path
                        self.db.add_job_log(job_id, "info", f"DEBUG: Backing up file {file_idx+1}/{len(files_to_backup)}: {file_path} ({file_size} bytes)")
                        self._emit_job_update(job_id, 'running', f'Backing up: {file_path}')

                        try:
                            success, checksum, tape_position = self._backup_single_file(
                                job_id, file_info, smb_path, credentials,
                                mount_point, verify, compression, encryption, encryption_password, source_type
                            )

                            if success:
                                # Record in database
                                self.db.add_archived_file(
                                    job_id=job_id,
                                    tape_barcode=tape_barcode,
                                    file_path=os.path.join(smb_path, file_path),
                                    file_path_on_tape=file_path,
                                    file_size=file_size,
                                    checksum=checksum,
                                    tape_position=tape_position
                                )

                                self.db.add_job_log(job_id, "info", f"DEBUG: File {file_path} backed up successfully. Tape pos: {tape_position}")
                                tape_bytes_written += file_size
                                progress.processed_files += 1
                                progress.processed_bytes += file_size
                                
                                # Update job progress regularly
                                if progress.processed_files % 10 == 0:
                                    self._update_progress(job_id)
                            else:
                                self.db.add_job_log(job_id, "error", f"Backup failed for {file_path}: {checksum}")
                                progress.errors.append({
                                    'type': 'backup_failed',
                                    'file': file_path,
                                    'error': checksum
                                })

                        except Exception as e:
                            logger.exception(f"Exception during backup of {file_path}")
                            self.db.add_job_log(job_id, "error", f"Exception backing up {file_path}: {e}")
                            progress.errors.append({
                                'file': file_path,
                                'error': str(e)
                            })
                        
                        file_idx += 1

                        # Update progress
                        self._update_progress(job_id)

                        # Checkpoint periodically
                        if progress.processed_files % CHECKPOINT_INTERVAL == 0:
                            self._save_checkpoint(job_id, file_idx, tape_barcode, file_path)
                finally:
                    if mount_point:
                        try:
                            controller.unmount_ltfs(drive=drive)
                        except Exception as e:
                            logger.error(f"Failed to unmount tape: {e}")
                    if tape_loaded:
                        try:
                            controller.unload_tape(drive)
                        except Exception as e:
                            logger.error(f"Failed to unload tape: {e}")

                    if tape_loaded:
                        try:
                            self.db.update_tape_usage(tape_barcode, tape_used + tape_bytes_written)
                            self.db.update_tape_status(tape_barcode, 'available')
                        except Exception as e:
                            logger.error(f"Failed to update tape usage: {e}")

                current_tape_idx += 1
            
            # Final status
            if self.cancel_flags[job_id].is_set():
                self.db.update_job_status(job_id, 'cancelled')
                self._emit_job_update(job_id, 'cancelled')
                if self.webhook_service:
                    self.webhook_service.trigger_event("JOB_CANCELLED", {"job_id": job_id})
            elif progress.errors:
                self._update_progress(job_id, force=True)
                self.db.update_job_status(job_id, 'completed')  # Completed with errors
                self.db.log_entry('warning', 'backup', 
                    f"Job {job_id} completed with {len(progress.errors)} errors")
                self._emit_job_update(job_id, 'completed', 
                    f'Completed with {len(progress.errors)} errors')
                if self.webhook_service:
                    self.webhook_service.trigger_event("JOB_COMPLETED", {"job_id": job_id, "errors": len(progress.errors)})
                
                # Only finalize snapshot if at least some files were successfully backed up
                if progress.processed_files > 0:
                    self._finalize_snapshot(
                        job_id,
                        backup_set_id,
                        smb_path,
                        backup_mode,
                        files,
                        plan_summary
                    )
            else:
                self._update_progress(job_id, force=True)
                self.db.update_job_status(job_id, 'completed')
                self.db.update_job_progress(
                    job_id, 
                    files_written=progress.processed_files, 
                    bytes_written=progress.processed_bytes
                )
                self.db.log_entry('success', 'backup', 
                    f"Job {job_id} completed: {progress.processed_files} files, "
                    f"{progress.processed_bytes} bytes")
                self._emit_job_update(job_id, 'completed', 'Backup completed successfully')
                if self.webhook_service:
                    self.webhook_service.trigger_event("JOB_COMPLETED", {"job_id": job_id})
                
                # Finalize snapshot only if something was written or if it was a valid full/incremental run
                # (For full backups, we ALWAYS want a snapshot even if 0 files, but here we expect processed_files > 0 
                # because we already handled the 'no changes' Case above)
                self._finalize_snapshot(
                    job_id,
                    backup_set_id,
                    smb_path,
                    backup_mode,
                    files,
                    plan_summary
                )
                
                # Archival Policy Hook
                try:
                    self.execute_archival_policy(job_id)
                except Exception as e:
                    logger.error(f"Archival policy execution failed: {e}")
                    self.db.log_entry('error', 'archival', f"Archival policy execution failed: {e}")

            # Cleanup Tapes
            for barcode in tapes:
                try:
                    # Resolve controller for this tape
                    controller = self.tape_controller
                    if self.library_manager:
                        found = self.library_manager.find_controller_for_tape(barcode)
                        if found: controller = found
                    
                    if encryption == 'hardware':
                        self.db.add_job_log(job_id, "info", f"Disabling hardware encryption after job on drive {drive}")
                        controller.disable_hardware_encryption(drive=drive)

                    controller.unmount_ltfs(drive=drive)
                    controller.unload_tape(barcode, drive)
                    self.db.update_tape_status(barcode, 'available')
                except Exception as cleanup_e:
                    logger.error(f"Failed to cleanup tape {barcode}: {cleanup_e}")

        except Exception as e:
            logger.exception(f"Backup job {job_id} failed: {e}")
            self.db.update_job_status(job_id, 'failed', str(e))
            self._emit_job_update(job_id, 'failed', str(e))
            if self.webhook_service:
                self.webhook_service.trigger_event("JOB_FAILED", {"job_id": job_id, "error": str(e)})
        finally:
            if main_mount_point:
                try:
                    NFSSource.unmount(main_mount_point)
                except Exception as cleanup_e:
                    logger.error(f"Failed to unmount NFS source: {cleanup_e}")


    def _backup_single_file(self, job_id: int, file_info: dict, smb_path: str,
                           credentials: dict, mount_point: str, 
                           verify: bool, compression: str, encryption: str = 'none',
                           encryption_password: Optional[str] = None, 
                           source_type: str = 'smb') -> Tuple[bool, str, Optional[int]]:
        """Backup a single file with integrity checks"""
        file_path = file_info.get('path', '')
        file_size = file_info.get('size', 0)
        
        # Create staging area
        with tempfile.TemporaryDirectory() as staging_dir:
            staged_file = os.path.join(staging_dir, os.path.basename(file_path))
            
            # Download based on source type
            try:
                if source_type == 'smb':
                    success = self.smb_client.download_file(
                        share_path=smb_path,
                        username=credentials.get('username'),
                        password=credentials.get('password'),
                        remote_file=file_path,
                        local_file=staged_file,
                        domain=credentials.get('domain', '')
                    )
                    if not success:
                        raise Exception("SMB download failed")
                elif source_type == 'rsync' or source_type == 'ssh':
                    host = credentials.get('host')
                    user = credentials.get('username')
                    port = credentials.get('port', 22)
                    remote_full_path = os.path.join(smb_path, file_path)
                    success = SSHSource.download_single_file(host, user, remote_full_path, staged_file, port)
                    if not success:
                        raise Exception("SCP download failed")
                elif source_type == 's3':
                    bucket = credentials.get('s3_bucket')
                    success = RcloneSource.download_single_file(bucket, file_path, staged_file)
                    if not success:
                        raise Exception("Rclone download failed")
                else:
                    # Local or NFS
                    full_local_path = os.path.join(smb_path, file_path)
                    shutil.copy2(full_local_path, staged_file)
            except Exception as e:
                return False, f"Download failed: {e}", None
            
            # Calculate checksum of downloaded file
            try:
                checksum = self.calculate_checksum(staged_file)
            except Exception as e:
                return False, f"Checksum failed: {e}", None
            
            # Compress if requested
            final_file = staged_file
            if compression and compression != 'none':
                try:
                    final_file = self._compress_file(staged_file, compression)
                except Exception as e:
                    return False, f"Compression failed: {e}", None
                    
            # Encrypt if requested
            if encryption != 'none':
                if False: # Fully enabled and relaxed in AGPL-3.0
                    logger.warning(f"Encryption requested ({encryption}) but license missing 'encryption' capability. Skipping.")
                elif encryption == 'software':
                    try:
                        encrypted_path = final_file + '.enc'
                        key, salt = EncryptionManager.derive_key(encryption_password)
                        manager = EncryptionManager(key, salt=salt)
                        manager.encrypt_file(final_file, encrypted_path)
                        
                        if final_file != staged_file:
                            os.remove(final_file)
                        final_file = encrypted_path
                    except Exception as e:
                        return False, f"Software encryption failed: {e}", None
                elif encryption == 'hardware':
                    # Hardware encryption is handled at the drive level (TapeController)
                    # No file-level modification needed here, but we mark it for verification logic
                    pass
            
            # Copy to tape
            dest_path = os.path.join(mount_point, file_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            try:
                shutil.copy2(final_file, dest_path)
            except Exception as e:
                return False, f"Tape write failed: {e}", None
            
            # Verify on tape if requested
            if verify:
                try:
                    verify_path = dest_path
                    if encryption_password:
                        # Decrypt to temp
                        decrypted_temp = dest_path + '.dec.tmp'
                        try:
                            salt = EncryptionManager.read_salt(dest_path)
                            key, _ = EncryptionManager.derive_key(encryption_password, salt)
                            manager = EncryptionManager(key)
                            manager.decrypt_file(dest_path, decrypted_temp)
                            verify_path = decrypted_temp
                        except Exception as e:
                            return False, f"Verification decryption failed: {e}", None

                    if compression and compression != 'none':
                        # Decompress and verify
                        decompressed = self._decompress_file(verify_path, compression)
                        # If we decrypted, verify_path was a temp file, remove it
                        if verify_path != dest_path:
                            try:
                                os.remove(verify_path)
                            except OSError:
                                pass
                        verify_path = decompressed
                    
                    # Now calculate checksum of the plaintext result
                    reconstructed_checksum = self.calculate_checksum(verify_path)
                    
                    # Cleanup temp verify files
                    if verify_path != dest_path:
                        try:
                            os.remove(verify_path)
                        except OSError:
                            pass
                    
                    if reconstructed_checksum != checksum:
                         try:
                             os.remove(dest_path)
                         except OSError:
                             pass
                         return False, "Verification failed: checksum mismatch", None
                         
                except Exception as e:
                    return False, f"Verification failed: {e}", None


            tape_position = self._get_tape_position(dest_path)
            return True, checksum, tape_position

    def _get_tape_position(self, file_path: str) -> Optional[int]:
        """Get LTFS startblock for ordered restore."""
        try:
            result = subprocess.run(
                ['getfattr', '-n', 'user.ltfs.startblock', '--only-values', file_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip())
        except Exception:
            pass
        return None
    
    def _compress_file(self, file_path: str, compression: str) -> str:
        """Compress a file using specified algorithm"""
        output_path = file_path + '.compressed'
        
        if compression == 'zstd':
            subprocess.run(['zstd', '-f', '-q', file_path, '-o', output_path], check=True)
        elif compression == 'gzip':
            with open(output_path, 'wb') as out_file:
                subprocess.run(['gzip', '-c', file_path], stdout=out_file, check=True)
        elif compression == 'lz4':
            subprocess.run(['lz4', '-f', '-q', file_path, output_path], check=True)
        else:
            return file_path  # No compression
        
        return output_path
    
    def _decompress_file(self, file_path: str, compression: str) -> str:
        """Decompress a file"""
        output_path = file_path + '.decompressed'
        
        if compression == 'zstd':
            subprocess.run(['zstd', '-d', '-f', '-q', file_path, '-o', output_path], check=True)
        elif compression == 'gzip':
            with open(output_path, 'wb') as out_file:
                subprocess.run(['gzip', '-d', '-c', file_path], stdout=out_file, check=True)
        elif compression == 'lz4':
            subprocess.run(['lz4', '-d', '-f', '-q', file_path, output_path], check=True)
        
        return output_path
    
    # =========================================================================
    # Restore Operations
    # =========================================================================

    def _resolve_restore_destination(self, destination: Optional[str], relative_path: str) -> str:
        """Resolve a safe restore destination path."""
        if not relative_path:
            raise ValueError("Restore path is empty")

        rel_path = Path(relative_path)
        if rel_path.is_absolute():
            raise ValueError("Absolute paths are not allowed for restore")
        if '..' in rel_path.parts:
            raise ValueError("Path traversal is not allowed for restore")

        root = Path(destination) if destination else Path('/restore')
        if not root.is_absolute():
            raise ValueError("Restore destination must be an absolute path")
        root = root.resolve()
        dest_path = (root / rel_path).resolve()

        if root != dest_path and root not in dest_path.parents:
            raise ValueError("Restore path escapes destination root")

        return str(dest_path)
    
    def start_restore_job(self, restore_id: int, encryption_password: Optional[str] = None):
        """Start a restore job"""
        # NOTE: Restore jobs use restore_jobs table, NOT jobs table
        job = self.db.get_restore_job(restore_id)
        if not job:
            logger.error(f"Restore job {restore_id} not found")
            return
        
        with self._lock:
            self.job_locks[restore_id] = threading.Lock()
            self.pause_flags[restore_id] = threading.Event()
            self.cancel_flags[restore_id] = threading.Event()
            self.active_jobs[restore_id] = {
                'progress': JobProgress(start_time=time.time()),
                'status': JobStatus.RUNNING
            }
        
        try:
            self.db.update_restore_status(restore_id, 'running')
            self._emit_job_update(restore_id, 'started')
            
            self._execute_restore(restore_id, job, encryption_password=encryption_password)
            
        except Exception as e:
            logger.exception(f"Restore job {restore_id} failed")
            self.db.update_restore_status(restore_id, 'failed')
            self._emit_job_update(restore_id, 'failed', str(e))
        
        finally:
            with self._lock:
                self.active_jobs.pop(restore_id, None)
                self.job_locks.pop(restore_id, None)
                self.pause_flags.pop(restore_id, None)
                self.cancel_flags.pop(restore_id, None)
    
    def _execute_restore(self, restore_id: int, job: dict, encryption_password: Optional[str] = None):
        """Execute restore operation"""
        files_to_restore = job.get('files', [])
        if isinstance(files_to_restore, str):
            files_to_restore = json.loads(files_to_restore or '[]')
        destination = job.get('destination')
        verify = job.get('verify', True)
        allow_overwrite = job.get('metadata', {}).get('allow_overwrite', False)
        
        progress = self.active_jobs[restore_id]['progress']
        progress.total_files = len(files_to_restore)
        
        # Group files by tape for efficient restore
        files_by_tape = {}
        for file_info in files_to_restore:
            tape = file_info.get('tape_barcode', '')
            if tape not in files_by_tape:
                files_by_tape[tape] = []
            files_by_tape[tape].append(file_info)
        
        # Sort files within each tape by tape position if available (Milestone 4: ordered restore)
        for tape_barcode in files_by_tape:
            has_positions = all(f.get('tape_position') is not None for f in files_by_tape[tape_barcode])
            if not has_positions:
                self.db.log_entry(
                    'warning',
                    'restore',
                    f"Missing tape position metadata for {tape_barcode}; using path order"
                )
                files_by_tape[tape_barcode].sort(
                    key=lambda f: f.get('file_path', '')
                )
            else:
                files_by_tape[tape_barcode].sort(
                    key=lambda f: (f.get('tape_position', 0), f.get('file_path', ''))
                )
        
        # Restore from each tape
        for tape_barcode, tape_files in files_by_tape.items():
            if self.cancel_flags[restore_id].is_set():
                break
            
            # Load tape
            try:
                controller = self.tape_controller
                if self.library_manager:
                    found = self.library_manager.find_controller_for_tape(tape_barcode)
                    if found: controller = found

                controller.load_tape(tape_barcode)
                mount_point = controller.mount_ltfs(tape_barcode)
            except Exception as e:
                progress.errors.append({
                    'type': 'tape_load',
                    'tape': tape_barcode,
                    'error': str(e)
                })
                continue
            
            # Restore each file from this tape
            for file_info in tape_files:
                if self.cancel_flags[restore_id].is_set():
                    break
                
                while self.pause_flags[restore_id].is_set():
                    time.sleep(1)
                
                file_path = file_info.get('file_path', '')
                tape_path = file_info.get('file_path_on_tape') or file_path
                expected_checksum = file_info.get('checksum', '')
                compression = file_info.get('compression', '')
                
                progress.current_file = file_path
                self._emit_job_update(restore_id, 'running', f'Restoring: {file_path}')
                
                try:
                    success, msg = self._restore_single_file(
                        mount_point, tape_path, destination, 
                        expected_checksum, compression, verify, allow_overwrite,
                        encryption_password=encryption_password
                    )
                    
                    if success:
                        progress.processed_files += 1
                        progress.processed_bytes += file_info.get('file_size', 0)
                    else:
                        progress.errors.append({
                            'type': 'restore_failed',
                            'file': file_path,
                            'error': msg
                        })
                
                except Exception as e:
                    progress.errors.append({
                        'type': 'exception',
                        'file': file_path,
                        'error': str(e)
                    })
                
                self._update_progress(restore_id, force=True)
            
            # Unload tape
            try:
                controller.unmount_ltfs()
                controller.unload_tape()
            except Exception as e:
                logger.error(f"Failed to unload tape: {e}")
        
        # Final status - use restore-specific status update
        if self.cancel_flags[restore_id].is_set():
            self.db.update_restore_status(restore_id, 'cancelled')
        elif progress.errors:
            self.db.update_restore_status(restore_id, 'completed')
            self.db.log_entry('warning', 'restore', 
                f"Restore {restore_id} completed with {len(progress.errors)} errors")
        else:
            self.db.update_restore_status(restore_id, 'completed')
            self.db.log_entry('success', 'restore', 
                f"Restore {restore_id} completed: {progress.processed_files} files")
        
        self._emit_job_update(restore_id, 'completed')
    
    def _restore_single_file(self, mount_point: str, tape_path: str, 
                            destination: str, expected_checksum: str,
                            compression: str, verify: bool,
                            allow_overwrite: bool,
                            encryption_password: Optional[str] = None) -> Tuple[bool, str]:
        """Restore a single file with integrity verification"""
        source_path = os.path.join(mount_point, tape_path)
        
        if not os.path.exists(source_path):
            return False, "File not found on tape"
        
        # Determine destination
        try:
            dest_path = self._resolve_restore_destination(destination, tape_path)
        except ValueError as e:
            return False, str(e)
        
        # Create destination directory
        dest_dir = os.path.dirname(dest_path)
        os.makedirs(dest_dir, exist_ok=True)

        if os.path.exists(dest_path) and not allow_overwrite:
            return False, "Destination already exists (overwrite disabled)"
        
        # Copy from tape
        with tempfile.TemporaryDirectory() as staging_dir:
            staged_file = os.path.join(staging_dir, os.path.basename(tape_path))
            
            try:
                shutil.copy2(source_path, staged_file)
            except Exception as e:
                return False, f"Read from tape failed: {e}"
            
            # Decrypt if needed
            final_file = staged_file
            try:
                with open(staged_file, 'rb') as f:
                    header = f.read(len(HEADER_v1))
                    if header == HEADER_v1:
                        if not encryption_password:
                            return False, "File is encrypted but no passphrase provided"
                        
                        decrypted_path = staged_file + '.dec'
                        salt = EncryptionManager.read_salt(staged_file)
                        key, _ = EncryptionManager.derive_key(encryption_password, salt)
                        manager = EncryptionManager(key)
                        manager.decrypt_file(staged_file, decrypted_path)
                        final_file = decrypted_path
            except Exception as e:
                return False, f"Decryption failed: {e}"

            # Decompress if needed
            if compression and compression != 'none':
                try:
                    decompressed = self._decompress_file(final_file, compression)
                    if final_file != staged_file:
                        os.remove(final_file)
                    final_file = decompressed
                except Exception as e:
                    return False, f"Decompression failed: {e}"
            
            # Verify checksum
            if verify and expected_checksum:
                try:
                    actual_checksum = self.calculate_checksum(final_file)
                    if actual_checksum != expected_checksum:
                        return False, "Checksum verification failed"
                except Exception as e:
                    return False, f"Verification failed: {e}"
            
            # Copy to final destination (atomic write)
            temp_path = None
            try:
                temp_handle, temp_path = tempfile.mkstemp(
                    prefix=".restore_tmp_", dir=dest_dir
                )
                os.close(temp_handle)
                shutil.copy2(final_file, temp_path)
                if not allow_overwrite and os.path.exists(dest_path):
                    os.remove(temp_path)
                    return False, "Destination already exists (overwrite disabled)"
                os.replace(temp_path, dest_path)
            except Exception as e:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
                return False, f"Write to destination failed: {e}"
        
        return True, "OK"
    
    # =========================================================================
    # Job Control
    # =========================================================================
    
    def cancel_job(self, job_id: int):
        """Cancel a running job"""
        if job_id in self.cancel_flags:
            self.cancel_flags[job_id].set()
            self.db.log_entry('info', 'job', f"Job {job_id} cancellation requested")
    
    def request_pause(self, job_id: int):
        """Pause a running job"""
        if job_id in self.pause_flags:
            self.pause_flags[job_id].set()
            self.db.log_entry('info', 'job', f"Job {job_id} paused")
    
    def resume_job(self, job_id: int):
        """Resume a paused job"""
        if job_id in self.pause_flags:
            self.pause_flags[job_id].clear()
            self.db.log_entry('info', 'job', f"Job {job_id} resumed")
    
    def resume_from_checkpoint(self, job_id: int, checkpoint: dict):
        """Resume a job from a checkpoint"""
        job = self.db.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found for resume")
            return

        drive = job.get('drive', 0) if job else 0

        with self._lock:
            self.job_locks[job_id] = threading.Lock()
            self.pause_flags[job_id] = threading.Event()
            self.cancel_flags[job_id] = threading.Event()
            self.active_jobs[job_id] = {
                'progress': JobProgress(start_time=time.time()),
                'status': JobStatus.RUNNING
            }

        try:
            drive_lock = self.drive_locks[drive]
            if not drive_lock.acquire(blocking=False):
                self.db.update_job_status(job_id, 'waiting_for_drive')
                self._emit_job_update(
                    job_id,
                    'waiting_for_drive',
                    f'Resume waiting for drive {drive}'
                )
                while not drive_lock.acquire(timeout=5):
                    if self.cancel_flags[job_id].is_set():
                        self.db.update_job_status(job_id, 'cancelled')
                        self._emit_job_update(job_id, 'cancelled')
                        return

            self.db.update_job_status(job_id, 'running')
            self._emit_job_update(job_id, 'resuming', 'Resuming from checkpoint')

            self._execute_backup(job_id, job, resume_state=checkpoint, encryption_password=checkpoint.get('encryption_password'))

        except Exception as e:
            logger.exception(f"Resume for job {job_id} failed with exception")
            self.db.update_job_status(job_id, 'failed')
            self.db.log_entry('error', 'backup', f"Resume failed for job {job_id}: {e}")
            self._emit_job_update(job_id, 'failed', str(e))

        finally:
            with self._lock:
                self.active_jobs.pop(job_id, None)
                self.job_locks.pop(job_id, None)
                self.pause_flags.pop(job_id, None)
                self.cancel_flags.pop(job_id, None)
            drive_lock = self.drive_locks.get(drive)
            if drive_lock and drive_lock.locked():
                drive_lock.release()
    
    def get_job_progress(self, job_id: int) -> Optional[dict]:
        """Get current progress of a job"""
        if job_id not in self.active_jobs:
            return None
        
        progress = self.active_jobs[job_id]['progress']
        return {
            'total_files': progress.total_files,
            'processed_files': progress.processed_files,
            'total_bytes': progress.total_bytes,
            'processed_bytes': progress.processed_bytes,
            'current_file': progress.current_file,
            'percent': (progress.processed_bytes / progress.total_bytes * 100) 
                       if progress.total_bytes > 0 else 0,
            'errors': len(progress.errors),
            'eta_seconds': progress.eta_seconds,
            'feeder_rate_bps': progress.feeder_rate_bps,
            'ingest_rate_bps': progress.ingest_rate_bps,
            'buffer_health': progress.buffer_health
        }
    
    # =========================================================================
    # Internal Helpers
    # =========================================================================
    
    def _emit_job_update(self, job_id: int, status: str, message: str = ''):
        """Emit job status update via websocket"""
        try:
            self.socketio.emit('job_update', {
                'job_id': job_id,
                'status': status,
                'message': message,
                'progress': self.get_job_progress(job_id),
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            logger.warning(f"Failed to emit job update: {e}")
    
    def _update_progress(self, job_id: int, force: bool = False):
        """Update progress calculations and emit update"""
        if job_id not in self.active_jobs:
            return
        
        progress = self.active_jobs[job_id]['progress']
        now = time.time()
        
        # Only update at interval unless forced
        if not force and now - progress.last_update < PROGRESS_UPDATE_INTERVAL:
            return
        
        time_delta = now - progress.last_update
        progress.last_update = now
        
        if time_delta > 0:
            bytes_delta = progress.processed_bytes - progress.previous_processed_bytes
            files_delta = progress.processed_files - progress.previous_processed_files
            
            # Calculate instantaneous speed
            current_bytes_per_sec = bytes_delta / time_delta
            current_files_per_sec = files_delta / time_delta
            
            # Smooth with moving average (alpha=0.3)
            if progress.bytes_per_second == 0:
                progress.bytes_per_second = current_bytes_per_sec
            else:
                progress.bytes_per_second = (progress.bytes_per_second * 0.7) + (current_bytes_per_sec * 0.3)
                
            if progress.files_per_second == 0:
                progress.files_per_second = current_files_per_sec
            else:
                progress.files_per_second = (progress.files_per_second * 0.7) + (current_files_per_sec * 0.3)
                
            # Update previous values for next delta
            progress.previous_processed_bytes = progress.processed_bytes
            progress.previous_processed_files = progress.processed_files
            
            # Update ETA based on smoothed speed
            if progress.bytes_per_second > 0:
                remaining_bytes = max(0, progress.total_bytes - progress.processed_bytes)
                progress.eta_seconds = int(remaining_bytes / progress.bytes_per_second)
            else:
                progress.eta_seconds = 0
        
        self.db.update_job_progress(
            job_id,
            files_written=progress.processed_files,
            bytes_written=progress.processed_bytes,
            feeder_rate_bps=progress.bytes_per_second
        )
        self._emit_job_update(job_id, 'progress')
    
    def _save_checkpoint(self, job_id: int, file_idx: int, tape_barcode: str, file_path: str):
        """Save a checkpoint for job recovery"""
        checkpoint = {
            'job_id': job_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'last_file_index': file_idx,
            'last_file_path': file_path,
            'files_completed': 0,
            'bytes_written': 0,
            'current_tape': tape_barcode,
            'tape_position': 0,
            'state': json.dumps({})
        }

        if job_id in self.active_jobs:
            progress = self.active_jobs[job_id]['progress']
            checkpoint['files_completed'] = progress.processed_files
            checkpoint['bytes_written'] = progress.processed_bytes
            checkpoint['state'] = json.dumps({
                'current_file': progress.current_file,
                'errors': progress.errors
            })

        self.db.save_job_checkpoint(job_id, checkpoint)
