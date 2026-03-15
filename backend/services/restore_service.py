import threading
from typing import Dict, List, Optional, Tuple, Any
from backend.utils.responses import success_response, error_response
from backend.utils.datetime import now_utc_iso

class RestoreService:
    def __init__(self, db, backup_engine, advanced_restore_engine, tape_controller):
        self.db = db
        self.backup_engine = backup_engine
        self.advanced_restore_engine = advanced_restore_engine
        self.tape_controller = tape_controller

    def initiate_restore(self, data: Dict) -> Tuple[bool, Any]:
        """Initiate a restore operation with full legacy logic."""
        files = data.get('files', [])
        destination = data.get('destination') or '/restore'
        restore_type = data.get('restoreType')
        encryption_password = data.get('encryption_password')
        
        
        # Validate destination path
        import os
        from pathlib import Path
        try:
            # Ensure path is absolute and canonicalized
            dest_path = Path(destination).resolve()
            
            # Blacklist critical system directories
            blocked_prefixes = [
                '/etc', '/bin', '/sbin', '/usr/bin', '/usr/sbin',
                '/var/lib/fossilsafe', '/boot', '/proc', '/sys', '/dev', '/root',
                '/private/etc', '/private/var'
            ]
            
            # Check for path traversal or blocked prefixes
            dest_str = str(dest_path)
            
            # --- NEW: Whitelist the recommended internal restore folder ---
            internal_restore_root = '/var/lib/fossilsafe/restore'
            is_internal_restore = (dest_str == internal_restore_root or dest_str.startswith(internal_restore_root + os.sep))
            
            if not is_internal_restore:
                for blocked in blocked_prefixes:
                    if dest_str == blocked or dest_str.startswith(blocked + os.sep):
                        return False, {"code": "forbidden", "message": f"Restore to system directory {blocked} is not allowed"}
            
            # Ensure it's not trying to restore to the root directly (very dangerous)
            if dest_str == '/':
                return False, {"code": "forbidden", "message": "Restore to root directory is not allowed"}
                    
        except Exception as e:
            return False, {"code": "invalid_request", "message": f"Invalid destination path: {str(e)}"}

        confirm = data.get('confirm', False)
        allow_overwrite = data.get('overwrite', False)
        simulation_mode = data.get('simulation_mode', False)
        
        if restore_type == 'system':
            # System restore logic placeholder
            # In a real implementation, this would likely trigger a separate workflow
            # For now, we allow it to proceed with empty files to simulate the API contract
            files = [{'id': 'system_restore', 'file_path': '/system/full_backup.tar'}]
        elif not files:
            return False, {"code": "invalid_request", "message": "No files specified"}
        
        if not confirm and restore_type != 'system':
             # System restore might use a different confirmation flow, but for parity we keep strictness
             pass
        if not confirm:
            return False, {"code": "confirmation_required", "message": "Restore confirmation required"}

        restore_files = []
        if restore_type == 'system':
             restore_files.append({
                'id': -1,
                'file_path': 'System Restore',
                'file_path_on_tape': 'System Restore',
                'file_size': 0,
                'checksum': 'SYSTEM',
                'tape_barcode': 'SYSTEM',
                'tape_position': 0
            })
        else:
            for file_entry in files:
                # Handle both dicts and simple strings (paths) for robustness
                if isinstance(file_entry, str):
                    file_id = None
                    file_path = file_entry
                else:
                    file_id = file_entry.get('id') or file_entry.get('file_id')
                    file_path = file_entry.get('file_path') or file_entry.get('path')
                
                record = None
                if file_id:
                    record = self.db.get_archived_file_by_id(file_id)
                elif file_path:
                    # Look up by path
                    results = self.db.search_archived_files(query=file_path)
                    if results and results[0].get('file_path') == file_path:
                        record = results[0]
                
                if not record:
                    return False, {"code": "not_found", "message": f"File {file_id or file_path} not found in catalog"}

                restore_files.append({
                    'id': record.get('id'),
                    'file_path': record.get('file_path'),
                    'file_path_on_tape': record.get('file_path_on_tape') or record.get('file_path'),
                    'file_size': record.get('file_size'),
                    'checksum': record.get('checksum'),
                    'tape_barcode': record.get('tape_barcode'),
                    'tape_position': record.get('tape_position')
                })

        restore_id = self.db.create_restore_job(
            files=restore_files,
            destination=destination,
            file_ids=[f.get('id') for f in restore_files if f.get('id') is not None],
            metadata={
                'allow_overwrite': bool(allow_overwrite)
            }
        )

        def run_restore():
            if self.advanced_restore_engine:
                self.advanced_restore_engine.execute_restore_with_prompts(restore_id, encryption_password=encryption_password, simulation_mode=simulation_mode)
            else:
                self.backup_engine.start_restore_job(restore_id, encryption_password=encryption_password)

        threading.Thread(target=run_restore, daemon=True).start()
        return True, {"restore_id": restore_id}

    def get_restore_jobs(self, limit: int = 50) -> List[Dict]:
        """Get all restore jobs."""
        return self.db.list_restore_jobs(limit)

    def get_restore_job(self, restore_id: int) -> Optional[Dict]:
        """Get a specific restore job."""
        return self.db.get_restore_job(restore_id)

    def confirm_tape(self, restore_id: int, barcode: str) -> Tuple[bool, str]:
        """Confirm a tape for a restore job with full legacy logic."""
        if self.advanced_restore_engine:
            try:
                self.tape_controller.load_tape(barcode)
            except Exception as e:
                # We don't return here as it might be manually loaded
                pass
            confirmed = self.advanced_restore_engine.confirm_tape_ready(restore_id, barcode)
            if not confirmed:
                return False, "Tape confirmation failed"
        
        return True, f"Tape {barcode} confirmed for restore {restore_id}"

    def verify_backup_tape(self, barcode: str, encryption_password: Optional[str] = None) -> Dict:
        """Trigger deep integrity verification of a tape."""
        if not self.advanced_restore_engine:
            raise Exception("Advanced Restore Engine required for deep verification")
        
        return self.advanced_restore_engine.verify_tape_integrity(barcode, encryption_password=encryption_password)
