#!/usr/bin/env python3
"""
Streaming Backup Pipeline for FossilSafe
Implements high-performance backup with prefetch queue

Feature-flagged for safety - defaults to OFF
"""

import os
import queue
import threading
import logging
import shutil
import tempfile
import time
import subprocess
from typing import Dict, List, Optional, Callable
from pathlib import Path
from dataclasses import dataclass, field

from backend.config_store import get_default_staging_dir
from backend.sources.ssh_source import SSHSource
from backend.sources.rclone_source import RcloneSource
from backend.utils.encryption import EncryptionManager

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for streaming pipeline"""
    enabled: bool = False
    max_queue_size_gb: int = 10
    max_queue_files: int = 1000
    staging_dir: str = field(default_factory=get_default_staging_dir)
    producer_threads: int = 2  # Parallel SMB fetchers
    prefetch_enabled: bool = True
    safety_threshold_gb: int = 2 # Start writing only after 2GB staged
    io_window_seconds: int = 5   # Window for rate calculation


class IOStatTracker:
    """Tracks byte rates over a sliding window"""
    def __init__(self, window_seconds: int = 5):
        self.window = window_seconds
        self.history = [] # list of (timestamp, bytes)
        self.lock = threading.Lock()

    def record(self, bytes_count: int):
        with self.lock:
            self.history.append((time.time(), bytes_count))
            self._prune()

    def get_rate(self) -> float:
        """Returns bytes per second over window"""
        with self.lock:
            self._prune()
            if not self.history: return 0.0
            total_bytes = sum(b for t, b in self.history)
            # Use actual time span if history is shorter than window
            span = max(float(self.window), 0.1) 
            return total_bytes / span

    def _prune(self):
        cutoff = time.time() - self.window
        self.history = [item for item in self.history if item[0] > cutoff]


class StreamingBackupPipeline:
    """
    High-performance backup with staging queue.
    
    Architecture:
    - Producer threads: Enumerate + fetch from SMB to local staging
    - Consumer thread: Copy from staging to tape
    - Bounded queue prevents unbounded disk usage
    
    Benefits:
    - Keeps tape streaming (no start/stop)
    - Parallel SMB fetching
    """
    
    def __init__(self, db, tape_controller, smb_client, socketio, config: PipelineConfig, library_manager=None, engine=None):
        self.db = db
        self.tape_controller = tape_controller
        self.smb_client = smb_client
        self.socketio = socketio
        self.config = config
        self.library_manager = library_manager
        self.engine = engine # Reference to BackupEngine for progress updates
        
        self.staging_dir = Path(config.staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        
        # Queue for staged files
        self.queue = queue.Queue(maxsize=config.max_queue_files)
        self.queue_size_bytes = 0
        self.queue_lock = threading.Lock()
        
        # Control flags
        self.stop_requested: bool = False
        self.producer_error: Optional[Exception] = None
        self.consumer_error: Optional[Exception] = None
        self.producers_done: bool = False
        
        # Stats & Performance
        self.stats: Dict[str, float] = {
            'files_staged': 0.0,
            'files_written': 0.0,
            'bytes_staged': 0.0,
            'bytes_written': 0.0,
            'producer_wait_time': 0.0,
            'consumer_wait_time': 0.0
        }
        self.feeder_tracker = IOStatTracker(config.io_window_seconds)
        self.ingest_tracker = IOStatTracker(config.io_window_seconds)
        
        # Database Batching (Added)
        self.db_buffer = []
        self.db_buffer_limit = 1000
    
    def execute_streaming_backup(self, job_id: int, source_path: str,
                                 tape_barcode: str, credential: Optional[Dict] = None,
                                 drive: int = 0, file_list: Optional[List[Dict]] = None,
                                 source_type: str = 'smb',
                                 compression: str = 'none',
                                 encryption: str = 'none',
                                 encryption_password: Optional[str] = None) -> bool:
        """
        Execute backup with streaming pipeline.
        
        Returns:
            True if backup succeeded
        """
        if not self.config.enabled:
            raise Exception("Streaming pipeline is not enabled")
        
        logger.info(f"Starting streaming backup: job={job_id}, tape={tape_barcode}")
        
        self.db.update_job_status(job_id, 'running')
        
        try:
            # Clean staging area
            self._cleanup_staging()
            self.producers_done = False
            
            # Start producer threads
            producer_threads = []
            for i in range(self.config.producer_threads):
                t = threading.Thread(
                    target=self._producer_thread,
                    args=(job_id, source_path, credential, i, file_list, source_type, compression, encryption, encryption_password),
                    name=f"Producer-{i}"
                )
                t.start()
                producer_threads.append(t)
            
            # Start consumer thread (support for multiple drives)
            drives = [drive] if isinstance(drive, int) else drive
            consumer_threads = []
            for d in drives:
                ct = threading.Thread(
                    target=self._consumer_thread,
                    args=(job_id, tape_barcode, d, encryption),
                    name=f"Consumer-Drive-{d}"
                )
                ct.start()
                consumer_threads.append(ct)
            
            # Wait for producers to finish
            for t in producer_threads:
                t.join()
                
            self.producers_done = True
            
            # Signal all consumers that producers are done (one None per consumer)
            for _ in consumer_threads:
                self.queue.put(None)
            
            # Wait for all consumers to finish
            for t in consumer_threads:
                t.join()
            
            # Check for errors
            if self.producer_error is not None:
                raise self.producer_error
            if self.consumer_error is not None:
                raise self.consumer_error
            
            # Mark complete
            self.db.update_job_status(
                job_id,
                'completed',
                metadata={
                    'streaming_pipeline': True,
                    'files_written': self.stats['files_written'],
                    'bytes_written': self.stats['bytes_written']
                }
            )
            
            logger.info(
                f"Streaming backup complete: {self.stats['files_written']} files, "
                f"{self.stats['bytes_written']} bytes"
            )
            return True
            
        except Exception as e:
            logger.error(f"Streaming backup failed: {e}")
            self.db.update_job_status(job_id, 'failed', error=str(e))
            return False
        finally:
            self._cleanup_staging()
    
    def _producer_thread(self, job_id: int, source_path: str,
                        credential: Optional[Dict], thread_id: int,
                        file_list: Optional[List[Dict]] = None,
                        source_type: str = 'smb',
                        compression: str = 'none',
                        encryption: str = 'none',
                        encryption_password: Optional[str] = None):
        """
        Producer thread: enumerate SMB and stage files to local disk.
        """
        try:
            # Enumerate files
            # For simplicity, assume source_path is local or mounted
            # In production, would use smb_client to enumerate
            files = list(file_list) if file_list is not None else list(self._enumerate_source_files(source_path, credential))
            
            # Divide files among producer threads
            my_files = [f for i, f in enumerate(files) if i % self.config.producer_threads == thread_id]
            
            logger.info(f"Producer {thread_id}: processing {len(my_files)} files")
            
            for file_info in my_files:
                if self.stop_requested:
                    break
                
                # Wait if queue is full
                wait_start = time.time()
                while self._is_queue_full(file_info['size']):
                    if self.stop_requested:
                        return
                    time.sleep(0.1)
                wait_time = time.time() - wait_start
                self.stats['producer_wait_time'] += wait_time
                
                # Stage file to local disk (includes compression/encryption)
                staged_path = self._stage_file(file_info, credential, source_path, source_type, compression, encryption, encryption_password)
                
                if staged_path:
                    # Add to queue
                    with self.queue_lock:
                        self.queue.put({
                            'file_info': file_info,
                            'staged_path': staged_path
                        })
                        self.queue_size_bytes += file_info['size']
                        self.stats['files_staged'] += 1
                        self.stats['bytes_staged'] += file_info['size']
                        self.feeder_tracker.record(file_info['size'])
                    
                    logger.debug(f"Producer {thread_id}: staged {file_info['path']}")
            
            logger.info(f"Producer {thread_id}: finished")
            
        except Exception as e:
            logger.error(f"Producer {thread_id} error: {e}")
            self.producer_error = e
            # Signal error to consumer
            self.queue.put(None)
    
    def _consumer_thread(self, job_id: int, tape_barcode: str, drive: int, encryption: str = 'none'):
        """
        Consumer thread: write staged files to tape.
        """
        try:
            safety_limit = self.config.safety_threshold_gb * 1024 * 1024 * 1024
            while self.queue_size_bytes < safety_limit and not self.stop_requested:
                if self.producer_error or self.producers_done: # If producers done or error, proceed anyway
                    break
                time.sleep(1.0)

            # Resolve controller
            controller = self.tape_controller
            if self.library_manager:
                found = self.library_manager.find_controller_for_tape(tape_barcode)
                if found: controller = found

            # Load and mount tape
            logger.info(f"Consumer (Drive {drive}): loading tape {tape_barcode}")
            controller.load_tape(tape_barcode, drive)
            
            # --- NEW: Enable Hardware Encryption ---
            if encryption == 'hardware':
                try:
                    logger.info(f"Consumer: Enabling hardware encryption for drive {drive}")
                    controller.enable_hardware_encryption(drive)
                except Exception as e:
                    logger.error(f"Failed to enable hardware encryption: {e}")
                    raise Exception(f"Hardware encryption setup failed: {e}")

            mount_point = controller.mount_ltfs(tape_barcode, drive)
            
            logger.info(f"Consumer: tape mounted at {mount_point}")
            
            try:
                while not self.stop_requested:
                    # Get next file from queue
                    try:
                        item = self.queue.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    
                    if item is None:
                        # Producers finished or error
                        break
                    
                    # Copy from staging to tape
                    try:
                        self._copy_staged_to_tape(
                            item['staged_path'],
                            item['file_info'],
                            mount_point,
                            job_id,
                            tape_barcode
                        )
                        
                        self.stats['files_written'] += 1
                        self.stats['bytes_written'] += item['file_info']['size']
                        self.ingest_tracker.record(item['file_info']['size'])
                        
                        # Flush DB buffer if full
                        if len(self.db_buffer) >= self.db_buffer_limit:
                            self._flush_db_buffer(job_id)
                        
                    except Exception as e:
                        logger.error(f"Failed to write {item['file_info']['path']}: {e}")
                    
                    # Remove from staging
                    try:
                        os.unlink(item['staged_path'])
                    except OSError:
                        pass
                    
                    # Update queue size
                    with self.queue_lock:
                        self.queue_size_bytes -= item['file_info']['size']
                
                # Final flush
                self._flush_db_buffer(job_id, final=True)
                
                logger.info("Consumer: finished writing files")
                
            finally:
                # Unmount tape
                logger.info(f"Consumer: unmounting tape")
                controller.unmount_ltfs(mount_point, drive)
                controller.unload_tape(drive)
            
        except Exception as e:
            logger.error(f"Consumer error: {e}")
            self.consumer_error = e
    
    def _enumerate_source_files(self, source_path: str, credential: Optional[Dict]):
        """Enumerate files from source"""
        import os

        if source_path.startswith('//'):
            if not credential:
                raise Exception("SMB credentials required for streaming pipeline")
            files = self.smb_client.list_files_recursive(source_path, credential)
            for file_info in files:
                yield {
                    'path': file_info['path'],
                    'relative_path': file_info['path'],
                    'size': file_info['size'],
                    'smb_path': source_path
                }
            return

        for root, _, files in os.walk(source_path):
            for filename in files:
                filepath = os.path.join(root, filename)
                try:
                    stat = os.stat(filepath)
                    yield {
                        'path': filepath,
                        'relative_path': os.path.relpath(filepath, source_path),
                        'size': stat.st_size,
                        'mtime': stat.st_mtime
                    }
                except Exception as e:
                    logger.warning(f"Could not stat {filepath}: {e}")
    
    def _is_queue_full(self, next_file_size: int) -> bool:
        """
        Check if adding next file would exceed limits.
        
        Logic:
        1. Soft Limit: Check configured max queue size (GB).
           - OVERRIDE: If queue is empty, allow file even if it exceeds max size (to handle single large files).
        2. Physical Limit: Check actual free disk space.
           - MUST FAIL if not enough space (plus safety margin).
        """
        import shutil
        
        # 1. Physical Space Check (Hard Limit)
        try:
            usage = shutil.disk_usage(self.staging_dir)
            # Require at least 500MB headroom + file size
            required_space = next_file_size + (500 * 1024 * 1024) 
            if usage.free < required_space:
                # DEADLOCK CHECK:
                # If the queue is empty, the consumer has nothing to process.
                # Therefore, no space will be freed by the pipeline.
                # We are stuck and must abort.
                if self.queue.qsize() == 0:
                     raise Exception(f"Staging disk is full ({usage.free // 1024 // 1024} MB free) and queue is empty. Cannot proceed.")
                
                # Otherwise, Consumer is working, space might free up. We wait.
                return True
        except Exception as e:
            # If it's our deadlock exception, let it bubble up
            if "Staging disk is full" in str(e):
                raise
            logger.error(f"Failed to check disk usage: {e}")
            # Fail safe
            return True

        with self.queue_lock:
            # 2. Soft Limit Check
            max_size_bytes = self.config.max_queue_size_gb * 1024 * 1024 * 1024
            
            # If queue is empty, we MUST allow the file if it fits physically (handled above),
            # even if it exceeds the soft limit. Otherwise we stall on large files.
            if self.queue.qsize() == 0 and self.queue_size_bytes == 0:
                return False
                
            would_exceed_size = (self.queue_size_bytes + next_file_size) > max_size_bytes
            would_exceed_count = self.queue.qsize() >= self.config.max_queue_files
            
            return would_exceed_size or would_exceed_count
    
    def _stage_file(self, file_info: Dict, credential: Optional[Dict], 
                   source_path: str, source_type: str = 'smb',
                   compression: str = 'none', encryption: str = 'none',
                   encryption_password: Optional[str] = None) -> Optional[str]:
        """
        Stage file to local disk.
        
        Returns:
            Path to staged file, or None if failed
        """
        try:
            # Create staging file
            staged_path = self.staging_dir / f"staged_{os.getpid()}_{time.time()}.tmp"

            # Copy to staging
            if file_info.get('smb_path'):
                if not credential:
                    raise Exception("SMB credentials required for staging")
                success = self.smb_client.download_file(
                    file_info['smb_path'],
                    credential.get('username', ''),
                    credential.get('password', ''),
                    file_info['path'],
                    str(staged_path),
                    credential.get('domain', '')
                )
                if not success:
                    raise Exception(f"SMB download failed: {file_info['path']}")
            elif source_type == 'rsync':
                if not credential:
                    raise Exception("SSH credentials required for staging")
                # Use SSHSource to download single file
                host = credential.get('rsync_host')
                user = credential.get('rsync_user')
                port = credential.get('rsync_port', 22)
                # In streaming, file_info['path'] is relative to source_path
                remote_full_path = os.path.join(source_path, file_info['path'])
                success = SSHSource.download_single_file(host, user, remote_full_path, str(staged_path), port)
                if not success:
                    raise Exception(f"SSH download failed: {file_info['path']}")
            elif source_type == 's3':
                if not credential:
                    raise Exception("S3 credentials required for staging")
                # Use RcloneSource to download single file
                bucket = credential.get('s3_bucket')
                remote_full_path = file_info['path'] # rclone uses relative to bucket
                success = RcloneSource.download_single_file(bucket, remote_full_path, str(staged_path))
                if not success:
                    raise Exception(f"Rclone download failed: {file_info['path']}")
            else:
                shutil.copy2(file_info['path'], staged_path)
            
            # --- NEW: Compression ---
            final_path = str(staged_path)
            if compression and compression != 'none':
                try:
                    compressed_path = self._compress_file(final_path, compression)
                    if compressed_path != final_path:
                        if os.path.exists(final_path):
                            os.remove(final_path)
                        final_path = compressed_path
                except Exception as e:
                    logger.error(f"Compression failed for {file_info['path']}: {e}")
                    return None
            
            # --- NEW: Software Encryption ---
            if encryption == 'software' and encryption_password:
                try:
                    encrypted_path = final_path + '.enc'
                    key, salt = EncryptionManager.derive_key(encryption_password)
                    manager = EncryptionManager(key, salt=salt)
                    manager.encrypt_file(final_path, encrypted_path)
                    if os.path.exists(final_path):
                        os.remove(final_path)
                    final_path = encrypted_path
                except Exception as e:
                    logger.error(f"Encryption failed for {file_info['path']}: {e}")
                    return None
            
            # Update file_info with the actual size of the staged (compressed/encrypted) file
            # This is important for the consumer thread to track honest bytes written to tape
            actual_size = os.path.getsize(final_path)
            file_info['original_size'] = file_info['size'] # Keep original for reference if needed
            file_info['size'] = actual_size
            
            return final_path
            
        except Exception as e:
            logger.error(f"Failed to stage {file_info['path']}: {e}")
            return None
    
    def _copy_staged_to_tape(self, staged_path: str, file_info: Dict,
                            mount_point: str, job_id: int, tape_barcode: str):
        """Copy staged file to tape and catalog it"""
        # Destination path on tape
        relative_path = file_info['relative_path']
        dest_path = os.path.join(mount_point, relative_path.lstrip('/'))
        
        # Create directories
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        # Copy file
        shutil.copy2(staged_path, dest_path)
        
        # Get tape position
        tape_position = self._get_tape_position(dest_path)
        
        # Add to catalog
        file_path = file_info['path']
        if file_info.get('smb_path'):
            file_path = os.path.join(file_info['smb_path'], relative_path)

        # Buffer for batch insert
        record = {
            'job_id': job_id,
            'tape_barcode': tape_barcode,
            'file_path': file_path,
            'file_size': file_info['size'],
            'checksum': file_info.get('checksum'),
            'file_path_on_tape': relative_path,
            'tape_position': tape_position
        }
        self.db_buffer.append(record)
    
    def _flush_db_buffer(self, job_id: int, final: bool = False):
        """Flush buffered database records"""
        if not self.db_buffer and not final:
            return
            
        try:
            if self.db_buffer:
                self.db.batch_add_archived_files(self.db_buffer)
                self.db_buffer = []  # Clear buffer
            
            # Get latest stats for update
            stats = self.get_stats()
            
            # Update progress
            self.db.update_job_progress(
                job_id,
                files_written=self.stats['files_written'],
                bytes_written=self.stats['bytes_written'],
                feeder_rate_bps=stats['feeder_rate_bps'],
                ingest_rate_bps=stats['ingest_rate_bps'],
                buffer_health=stats['buffer_health']
            )
            
            # Update BackupEngine active_jobs if present
            if self.engine and job_id in self.engine.active_jobs:
                progress = self.engine.active_jobs[job_id]['progress']
                progress.processed_files = self.stats['files_written']
                progress.processed_bytes = self.stats['bytes_written']
                progress.feeder_rate_bps = stats['feeder_rate_bps']
                progress.ingest_rate_bps = stats['ingest_rate_bps']
                progress.buffer_health = stats['buffer_health']
                progress.bytes_per_second = stats['ingest_rate_bps'] # For compatibility with old ThroughputWidget
        except Exception as e:
            logger.error(f"Failed to flush DB buffer: {e}")
            # If batch fails, we might lose records in the index, 
            # but the data is on tape. 
            # In a robust system, we would retry or handle this better.
    
    def _get_tape_position(self, file_path: str) -> Optional[int]:
        """Get LTFS startblock"""
        import subprocess
        
        try:
            result = subprocess.run(
                ['getfattr', '-n', 'user.ltfs.startblock', '--only-values', file_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip())
        except (Exception, subprocess.SubprocessError):
            pass
        return None
    
    def _cleanup_staging(self):
        """Clean up staging directory"""
        try:
            for file in self.staging_dir.glob("staged_*.tmp"):
                try:
                    file.unlink()
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"Staging cleanup failed: {e}")
    
    def get_stats(self) -> Dict:
        """Get pipeline statistics"""
        feeder_rate = self.feeder_tracker.get_rate()
        ingest_rate = self.ingest_tracker.get_rate()
        
        # Buffer health (0.0 to 1.0)
        max_bytes = self.config.max_queue_size_gb * 1024 * 1024 * 1024
        buffer_percentage = (self.queue_size_bytes / max_bytes) if max_bytes > 0 else 0
        
        return {
            **self.stats,
            'queue_size_bytes': self.queue_size_bytes,
            'queue_length': self.queue.qsize(),
            'feeder_rate_bps': feeder_rate,
            'ingest_rate_bps': ingest_rate,
            'buffer_health': buffer_percentage
        }

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


def is_streaming_enabled(db) -> bool:
    """Check if streaming pipeline is enabled in settings"""
    if db is None:
        return False
    enabled = db.get_setting('streaming_backup_enabled', 'false')
    return enabled.lower() == 'true'


def get_streaming_config(db) -> PipelineConfig:
    """Get streaming pipeline configuration from database"""
    if db is None:
        return PipelineConfig(
            enabled=False,
            max_queue_size_gb=10,
            max_queue_files=1000,
            staging_dir=get_default_staging_dir(),
            producer_threads=2,
            prefetch_enabled=True
        )
    return PipelineConfig(
        enabled=is_streaming_enabled(db),
        max_queue_size_gb=int(db.get_setting('streaming_max_queue_gb', '10')),
        max_queue_files=int(db.get_setting('streaming_max_queue_files', '1000')),
        staging_dir=db.get_setting('streaming_staging_dir', get_default_staging_dir()),
        producer_threads=int(db.get_setting('streaming_producer_threads', '2')),
        prefetch_enabled=db.get_setting('streaming_prefetch_enabled', 'true').lower() == 'true',
        safety_threshold_gb=int(db.get_setting('streaming_safety_threshold_gb', '2')),
        io_window_seconds=int(db.get_setting('streaming_io_window_seconds', '5'))
    )
