#!/usr/bin/env python3
"""
Tape Duplication Feature for FossilSafe
Implements A/B tape copying for redundancy
"""

import os
import json
import uuid
import logging
import threading
import tempfile
from typing import List, Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class TapeDuplicationEngine:
    """
    Manages tape duplication (A/B copies) for redundancy.
    
    Features:
    - Writes identical backup to two tapes
    - Catalog tracks both copies
    - Restore can use either copy
    - Copy sets are linked by copy_set_id
    """
    
    def __init__(self, db, tape_controller, smb_client, socketio, library_manager=None):
        self.db = db
        self.tape_controller = tape_controller
        self.library_manager = library_manager
        self.smb_client = smb_client
        self.socketio = socketio
    
    def execute_backup_with_duplication(self, job_id: int,
                                       source_path: str,
                                       tapes: List[str],
                                       credential: Optional[Dict] = None,
                                       drive: int = 0,
                                       file_list: Optional[List[Dict]] = None) -> bool:
        """
        Execute backup with A/B duplication.
        
        Args:
            job_id: Job ID
            source_path: Source SMB path
            tapes: List of tape barcodes [primary, secondary, ...]
            credential: SMB credentials if needed
            
        Returns:
            True if duplication succeeded
        """
        if len(tapes) < 2:
            logger.error("Duplication requires at least 2 tapes")
            self.db.update_job_status(
                job_id,
                'failed',
                error='Duplication requires at least 2 tapes'
            )
            return False
        
        primary_tape = tapes[0]
        secondary_tape = tapes[1]
        
        # Generate copy set ID
        copy_set_id = str(uuid.uuid4())
        
        logger.info(
            f"Starting duplicated backup: primary={primary_tape}, "
            f"secondary={secondary_tape}, copy_set={copy_set_id}"
        )
        
        try:
            # Phase 1: Write to primary tape
            self.db.update_job_status(
                job_id,
                'running',
                metadata={
                    'phase': 'primary',
                    'current_tape': primary_tape,
                    'copy_set_id': copy_set_id
                }
            )
            
            self._emit_progress(job_id, 'Writing to primary tape', 0)
            
            primary_files = self._execute_backup_to_tape(
                job_id=job_id,
                tape_barcode=primary_tape,
                source_path=source_path,
                copy_set_id=copy_set_id,
                copy_type='primary',
                credential=credential,
                drive=drive,
                file_list=file_list
            )
            
            if not primary_files:
                raise Exception("Primary backup failed - no files written")
            
            logger.info(f"Primary backup complete: {len(primary_files)} files")
            
            # Phase 2: Write to secondary tape (duplicate)
            self.db.update_job_status(
                job_id,
                'running',
                metadata={
                    'phase': 'duplicate',
                    'current_tape': secondary_tape,
                    'copy_set_id': copy_set_id
                }
            )
            
            self._emit_progress(job_id, 'Writing to secondary tape (duplicate)', 50)
            
            secondary_files = self._execute_backup_to_tape(
                job_id=job_id,
                tape_barcode=secondary_tape,
                source_path=source_path,
                copy_set_id=copy_set_id,
                copy_type='duplicate',
                credential=credential,
                drive=drive,
                file_list=file_list
            )
            
            if not secondary_files:
                raise Exception("Secondary backup failed - no files written")
            
            logger.info(f"Secondary backup complete: {len(secondary_files)} files")
            
            # Phase 3: Verify both copies match
            self._emit_progress(job_id, 'Verifying duplication', 90)
            
            if not self._verify_duplication(primary_files, secondary_files):
                raise Exception("Duplication verification failed - mismatch detected")
            
            # Mark complete
            self.db.update_job_status(
                job_id,
                'completed',
                metadata={
                    'duplicated': True,
                    'copy_set_id': copy_set_id,
                    'tapes': [primary_tape, secondary_tape],
                    'files_written': len(primary_files)
                }
            )
            
            self._emit_progress(job_id, 'Duplication complete', 100)
            
            logger.info(
                f"Duplication successful: {len(primary_files)} files on both tapes"
            )
            return True
            
        except Exception as e:
            logger.error(f"Duplication failed: {e}")
            self.db.update_job_status(
                job_id,
                'failed',
                error=f'Duplication error: {str(e)}'
            )
            return False
    
    def _execute_backup_to_tape(self, job_id: int, tape_barcode: str,
                                source_path: str, copy_set_id: str,
                                copy_type: str, credential: Optional[Dict],
                                drive: int = 0,
                                file_list: Optional[List[Dict]] = None) -> List[Dict]:
        """
        Execute backup to a single tape.
        
        Returns:
            List of file records written
        """
        files_written = []
        
        try:
            # Resolve controller
            controller = self.tape_controller
            if self.library_manager:
                found = self.library_manager.find_controller_for_tape(tape_barcode)
                if found: controller = found

            # Load tape
            logger.info(f"Loading tape {tape_barcode}")
            controller.load_tape(tape_barcode, drive)
            
            # Mount LTFS
            logger.info(f"Mounting tape {tape_barcode}")
            mount_point = controller.mount_ltfs(tape_barcode, drive)
            
            # Enumerate source files
            if file_list is not None:
                source_files = file_list
            elif source_path.startswith('//'):
                if not credential:
                    raise Exception("SMB credentials required for duplication")
                source_files = self.smb_client.list_files_recursive(source_path, credential)
            else:
                source_files = self._enumerate_local_files(source_path)
            
            # Copy files to tape
            for file_info in source_files:
                dest_path = self._copy_file_to_tape(file_info, mount_point, source_path, credential)
                
                if dest_path:
                    # Get tape position if available
                    tape_position = self._get_tape_position(dest_path)
                    
                    # Record in catalog
                    file_record = {
                        'job_id': job_id,
                        'tape_barcode': tape_barcode,
                        'file_path': os.path.join(source_path, file_info['path']) if source_path.startswith('//') else file_info['path'],
                        'file_path_on_tape': dest_path.replace(mount_point, ''),
                        'file_size': file_info['size'],
                        'checksum': file_info.get('checksum'),
                        'tape_position': tape_position,
                        'copy_set_id': copy_set_id,
                        'copy_type': copy_type,
                        'archived_at': datetime.now(timezone.utc).isoformat()
                    }
                    
                    self.db.add_archived_file(**file_record)
                    files_written.append(file_record)
                    
                    logger.debug(f"Wrote file: {file_info['path']}")
            
            # Unmount
            logger.info(f"Unmounting tape {tape_barcode}")
            controller.unmount_ltfs(mount_point, drive)
            
            # Unload tape
            logger.info(f"Unloading tape {tape_barcode}")
            controller.unload_tape(drive)
            
            return files_written
            
        except Exception as e:
            logger.error(f"Failed to backup to {tape_barcode}: {e}")
            # Try to clean up
            try:
                # We need to ensure we use the same controller for cleanup
                cleanup_controller = self.tape_controller
                if self.library_manager:
                     cleanup_found = self.library_manager.find_controller_for_tape(tape_barcode)
                     if cleanup_found: cleanup_controller = cleanup_found
                cleanup_controller.unmount_ltfs(drive=drive)
                cleanup_controller.unload_tape(drive)
            except Exception:
                pass
            raise
    
    def _enumerate_local_files(self, source_path: str) -> List[Dict]:
        """Enumerate files in local directory"""
        import os
        import hashlib
        
        files = []
        for root, dirs, filenames in os.walk(source_path):
            for filename in filenames:
                filepath = os.path.join(root, filename)
                try:
                    stat = os.stat(filepath)
                    
                    # Calculate checksum
                    checksum = self._calculate_checksum(filepath)
                    
                    files.append({
                        'path': filepath,
                        'relative_path': os.path.relpath(filepath, source_path),
                        'size': stat.st_size,
                        'checksum': checksum
                    })
                except Exception as e:
                    logger.warning(f"Could not process {filepath}: {e}")
        
        return files
    
    def _calculate_checksum(self, filepath: str) -> str:
        """Calculate SHA256 checksum of file"""
        import hashlib
        
        sha256 = hashlib.sha256()
        try:
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(8 * 1024 * 1024)  # 8MB chunks
                    if not chunk:
                        break
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.warning(f"Checksum failed for {filepath}: {e}")
            return ''
    
    def _copy_file_to_tape(self, file_info: Dict, mount_point: str,
                           source_path: str, credential: Optional[Dict]) -> Optional[str]:
        """
        Copy file to tape.
        
        Returns:
            Full path on tape if successful
        """
        import shutil
        import os
        
        try:
            # Destination path on tape
            relative_path = file_info.get('relative_path', file_info['path'])
            dest_path = os.path.join(mount_point, relative_path.lstrip('/'))

            # Create directories
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            if source_path.startswith('//'):
                if not credential:
                    raise Exception("Missing SMB credential")
                with tempfile.TemporaryDirectory() as staging_dir:
                    staged_file = os.path.join(staging_dir, os.path.basename(relative_path))
                    success = self.smb_client.download_file(
                        source_path,
                        credential.get('username', ''),
                        credential.get('password', ''),
                        relative_path,
                        staged_file,
                        credential.get('domain', '')
                    )
                    if not success:
                        raise Exception(f"SMB download failed: {relative_path}")
                    shutil.copy2(staged_file, dest_path)
            else:
                shutil.copy2(file_info['path'], dest_path)

            return dest_path

        except Exception as e:
            logger.error(f"Failed to copy {file_info['path']}: {e}")
            return None
    
    def _get_tape_position(self, file_path: str) -> Optional[int]:
        """
        Get tape position (startblock) from LTFS extended attributes.
        """
        import subprocess
        
        try:
            # Try to get ltfs.startblock xattr
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
    
    def _verify_duplication(self, primary_files: List[Dict], 
                           secondary_files: List[Dict]) -> bool:
        """
        Verify that primary and secondary copies match.
        
        Checks:
        - Same number of files
        - Same file paths
        - Same file sizes
        - Same checksums (if available)
        """
        if len(primary_files) != len(secondary_files):
            logger.error(
                f"File count mismatch: primary={len(primary_files)}, "
                f"secondary={len(secondary_files)}"
            )
            return False
        
        # Sort by file path for comparison
        primary_sorted = sorted(primary_files, key=lambda f: f['file_path'])
        secondary_sorted = sorted(secondary_files, key=lambda f: f['file_path'])
        
        for p, s in zip(primary_sorted, secondary_sorted):
            if p['file_path'] != s['file_path']:
                logger.error(f"Path mismatch: {p['file_path']} vs {s['file_path']}")
                return False
            
            if p['file_size'] != s['file_size']:
                logger.error(
                    f"Size mismatch for {p['file_path']}: "
                    f"{p['file_size']} vs {s['file_size']}"
                )
                return False
            
            if p.get('checksum') and s.get('checksum'):
                if p['checksum'] != s['checksum']:
                    logger.error(
                        f"Checksum mismatch for {p['file_path']}: "
                        f"{p['checksum']} vs {s['checksum']}"
                    )
                    return False
        
        logger.info("Duplication verification passed")
        return True
    
    def _emit_progress(self, job_id: int, message: str, percent: int):
        """Emit progress update via WebSocket"""
        if self.socketio:
            self.socketio.emit('job_progress', {
                'job_id': job_id,
                'message': message,
                'percent': percent
            })
    
    def get_duplicate_copy(self, copy_set_id: str) -> List[Dict]:
        """
        Get all files in a duplicate copy set.
        
        Returns:
            List of file records from both tapes
        """
        files = self.db.search_archived_files_by_copy_set(copy_set_id)
        return files
    
    def restore_from_any_copy(self, file_id: int) -> Optional[Dict]:
        """
        Find any available copy of a file (primary or duplicate).
        Prefers primary, falls back to duplicate if primary tape unavailable.
        
        Returns:
            File record to use for restore, or None if no copy available
        """
        # Get file record
        file_record = self.db.get_archived_file_by_id(file_id)
        if not file_record:
            return None
        
        copy_set_id = file_record.get('copy_set_id')
        if not copy_set_id:
            # No duplication, return as-is
            return file_record
        
        # Get all copies in this set
        all_copies = self.get_duplicate_copy(copy_set_id)
        
        # Filter to this specific file
        file_path = file_record['file_path']
        copies = [c for c in all_copies if c['file_path'] == file_path]
        
        if not copies:
            return file_record
        
        # Prefer primary copy
        primary = next((c for c in copies if c.get('copy_type') == 'primary'), None)
        if primary:
            return primary
        
        # Fall back to any available copy
        return copies[0]
