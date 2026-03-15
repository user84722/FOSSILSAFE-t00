import re
import os
from typing import Tuple, Optional

def validate_job_name(name: str) -> Tuple[bool, Optional[str]]:
    """Validate job name"""
    if not name:
        return False, "Job name is required"
    if len(name) > 255:
        return False, "Job name is too long (max 255 characters)"
    if len(name) < 2:
        return False, "Job name is too short (min 2 characters)"
    return True, None

def validate_barcode(barcode: str) -> Tuple[bool, Optional[str]]:
    """Validate tape barcode"""
    if not barcode:
        return False, "Barcode is required"
    # Relaxed validation for manual naming (appliance usage)
    if not re.match(r'^[A-Z0-9_-]{1,32}$', barcode):
        return False, "Invalid barcode (must be 1-32 chars, A-Z, 0-9, -, _)"
    return True, None

def validate_smb_path(path: str) -> Tuple[bool, Optional[str]]:
    """Validate SMB path format"""
    if not path:
        return False, "SMB path is required"
    if not path.startswith('smb://'):
        return False, "SMB path must start with smb://"
    
    # Check for command injection characters
    if re.search(r'[;&|`$<>(){}\[\]\\]', path):
        return False, "SMB path contains invalid characters"
        
    parts = path.replace('smb://', '').split('/')
    if len(parts) < 2 or not parts[0]:
        return False, "SMB path must include host and share (e.g. smb://host/share)"
        
    return True, None

def validate_tape_identifier(barcode: str, tape_controller=None, db=None) -> Tuple[bool, Optional[str]]:
    """Validate a tape identifier (barcode or manual name)."""
    if not barcode:
        return False, "Barcode or tape name is required"
    
    # Allow any non-empty identifier in drive-only mode
    if tape_controller and tape_controller.is_drive_only():
        return True, None
        
    # Allow identifiers already registered in the database
    if db:
        try:
            if db.get_tape(barcode):
                return True, None
        except Exception:
            pass
            
    return validate_barcode(barcode)

def validate_local_path(path_str: str) -> Tuple[bool, Optional[str]]:
    """
    Validate a local filesystem path for safety.
    Prevents access to sensitive system directories.
    """
    if not path_str:
        return False, "Path is required"
    
    try:
        path = os.path.abspath(os.path.normpath(path_str))
        
        # Deny root
        if path == '/':
            return False, "Root directory backup is not allowed"
            
        # Deny sensitive system directories
        # We check if the path starts with these prefixes
        restricted_prefixes = [
            '/proc', '/sys', '/dev', '/run', '/var/run', '/boot', '/etc', '/bin', '/sbin', '/usr/bin', '/usr/sbin'
        ]
        
        for prefix in restricted_prefixes:
            if path == prefix or path.startswith(prefix + os.sep):
                return False, f"Access to restricted system path '{prefix}' is not allowed"
        
        # Check existence (optional, but good for source validation)
        # Note: We might want separate 'exists' check vs 'safe path' check
        # For this validator, we just check safety syntax/location.
        
        return True, None
        
    except Exception as e:
        return False, f"Invalid path format: {e}"

def validate_nfs_server(server: str) -> Tuple[bool, Optional[str]]:
    """Validate NFS server hostname or IP."""
    from backend.sources.nfs_source import validate_nfs_server as _validate_nfs_server
    return _validate_nfs_server(server)

def validate_nfs_export(export_path: str) -> Tuple[bool, Optional[str]]:
    """Validate NFS export path."""
    from backend.sources.nfs_source import validate_nfs_export as _validate_nfs_export
    return _validate_nfs_export(export_path)

def validate_key_format(key: str) -> Tuple[bool, Optional[str]]:
    """Validate format of a key (e.g. API key)"""
    if not key:
        return False, "Key is required"
    if not re.match(r'^[A-Za-z0-9_-]{16,128}$', key):
        return False, "Invalid key format"
    return True, None

def validate_slot(slot: object) -> Tuple[bool, Optional[str]]:
    """Validate tape library slot number"""
    try:
        s = int(str(slot))
        if s < 0 or s > 10000:
            return False, f"Invalid slot number: {s}"
        return True, None
    except (TypeError, ValueError):
        return False, f"Invalid slot: {slot}"

def validate_drive(drive: object) -> Tuple[bool, Optional[str]]:
    """Validate tape drive number"""
    try:
        d = int(str(drive))
        if d < 0 or d > 32:
            return False, f"Invalid drive number: {d}"
        return True, None
    except (TypeError, ValueError):
        return False, f"Invalid drive: {drive}"
