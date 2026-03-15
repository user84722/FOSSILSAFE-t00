"""
NFS (Network File System) source implementation.
Handles NFS mount/unmount operations and validation.
"""
import os
import subprocess
import re
import time
from typing import Tuple, Optional, List, Dict


def _run_command(cmd: List[str], timeout: int = 10) -> Tuple[bool, str]:
    """Run a command and return success status and output."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _run_with_retry(cmd: List[str], timeout: int = 10, max_retries: int = 3,
                    base_delay: float = 1.0) -> Tuple[bool, str]:
    """Run a command with exponential backoff retry."""
    last_error = ""
    for attempt in range(max_retries):
        success, output = _run_command(cmd, timeout)
        if success:
            return True, output
        last_error = output
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)  # 1s, 2s, 4s...
            time.sleep(delay)
    return False, f"Failed after {max_retries} attempts. Last error: {last_error}"


def validate_nfs_server(server: str) -> Tuple[bool, Optional[str]]:
    """
    Validate NFS server hostname or IP address.
    Returns (is_valid, error_message).
    """
    if not server or not server.strip():
        return False, "NFS server is required"
    
    server = server.strip()
    
    # Check for invalid characters
    if any(c in server for c in [' ', '\n', '\r', '\t', ';', '&', '|', '`']):
        return False, "NFS server contains invalid characters"
    
    # Basic hostname/IP validation
    # Allow: alphanumeric, dots, hyphens, colons (for IPv6)
    if not re.match(r'^[a-zA-Z0-9\.\-\:]+$', server):
        return False, "Invalid NFS server format"
    
    return True, None


def validate_nfs_export(export_path: str) -> Tuple[bool, Optional[str]]:
    """
    Validate NFS export path.
    Returns (is_valid, error_message).
    """
    if not export_path or not export_path.strip():
        return False, "NFS export path is required"
    
    export_path = export_path.strip()
    
    # Must start with /
    if not export_path.startswith('/'):
        return False, "NFS export path must start with /"
    
    # Check for dangerous characters
    if any(c in export_path for c in ['\n', '\r', '\t', ';', '&', '|', '`', '$']):
        return False, "NFS export path contains invalid characters"
    
    return True, None


def test_nfs_connection(server: str, export_path: str) -> Tuple[bool, str]:
    """
    Test if NFS export is accessible.
    Returns (is_accessible, message).
    """
    # Validate inputs
    valid_server, server_error = validate_nfs_server(server)
    if not valid_server:
        return False, server_error
    
    valid_export, export_error = validate_nfs_export(export_path)
    if not valid_export:
        return False, export_error
    
    # Try to query the NFS server for available exports
    success, output = _run_command(['showmount', '-e', server], timeout=15)
    
    if not success:
        return False, f"Cannot reach NFS server: {output}"
    
    # Check if the specific export is listed
    if export_path in output:
        return True, f"NFS export {export_path} is available on {server}"
    
    # Export might still be accessible even if not listed (permissions)
    return True, f"NFS server {server} is reachable (export {export_path} may require testing via mount)"


def list_nfs_exports(server: str) -> Tuple[bool, List[str], str]:
    """
    List available NFS exports from a server.
    Returns (success, list_of_exports, error_message).
    """
    valid_server, server_error = validate_nfs_server(server)
    if not valid_server:
        return False, [], server_error
    
    success, output = _run_command(['showmount', '-e', server], timeout=15)
    
    if not success:
        return False, [], f"Cannot query NFS server: {output}"
    
    # Parse showmount output
    # Format: "Export list for server:"
    #         "/export/path  client1,client2"
    exports = []
    for line in output.split('\n'):
        line = line.strip()
        if not line or line.startswith('Export') or line.startswith('Exports'):
            continue
        # Extract the export path (first field)
        parts = line.split()
        if parts and parts[0].startswith('/'):
            exports.append(parts[0])
    
    return True, exports, ""


def mount_nfs(server: str, export_path: str, mount_point: Optional[str] = None, 
              options: Optional[str] = None) -> Tuple[bool, str]:
    """
    Mount an NFS share.
    Returns (success, mount_point_or_error).
    """
    # Validate inputs
    valid_server, server_error = validate_nfs_server(server)
    if not valid_server:
        return False, server_error
    
    valid_export, export_error = validate_nfs_export(export_path)
    if not valid_export:
        return False, export_error
    
    # Create mount point if not specified
    if not mount_point:
        safe_name = f"{server}_{export_path.replace('/', '_')}"
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', safe_name)
        mount_point = f"/mnt/fossilsafe/nfs/{safe_name}"
    
    # Ensure mount point directory exists
    try:
        if not os.path.exists(mount_point):
            success, output = _run_command(['sudo', 'mkdir', '-p', mount_point], timeout=10)
            if not success:
                return False, f"Failed to create mount point {mount_point}: {output}"
            _run_command(['sudo', 'chown', 'fossilsafe:fossilsafe', mount_point], timeout=10)
    except Exception as e:
        return False, f"Failed to handle mount point: {e}"
    
    # Build mount command
    nfs_source = f"{server}:{export_path}"
    
    if options:
        cmd = ['sudo', 'mount', '-t', 'nfs', '-o', options, nfs_source, mount_point]
    else:
        # Default options: read-only, soft mount, timeout
        cmd = ['sudo', 'mount', '-t', 'nfs', '-o', 'ro,soft,timeo=10', nfs_source, mount_point]
    
    success, output = _run_with_retry(cmd, timeout=30, max_retries=3, base_delay=2.0)
    
    if success:
        return True, mount_point
    else:
        return False, f"Mount failed: {output}"


def unmount_nfs(mount_point: str) -> Tuple[bool, str]:
    """
    Unmount an NFS share.
    Returns (success, message).
    """
    if not mount_point or not mount_point.strip():
        return False, "Mount point is required"
    
    if not os.path.exists(mount_point):
        return True, "Mount point does not exist (already unmounted)"
    
    success, output = _run_command(['sudo', 'umount', mount_point], timeout=30)
    
    if success:
        return True, "NFS share unmounted successfully"
    else:
        # Try force unmount
        success, output = _run_command(['sudo', 'umount', '-f', mount_point], timeout=30)
        if success:
            return True, "NFS share force unmounted successfully"
        else:
            return False, f"Unmount failed: {output}"


class NFSSource:
    """Handler for NFS source operations."""
    
    @staticmethod
    def validate(server: str, export_path: str) -> Tuple[bool, Optional[str]]:
        """Validate NFS server and export path."""
        valid_server, error = validate_nfs_server(server)
        if not valid_server:
            return False, error
        
        valid_export, error = validate_nfs_export(export_path)
        if not valid_export:
            return False, error
        
        return True, None
    
    @staticmethod
    def test_connection(server: str, export_path: str) -> Dict:
        """Test NFS connection and return detailed result."""
        success, message = test_nfs_connection(server, export_path)
        return {
            'ok': success,
            'connected': success,
            'detail': message
        }
    
    @staticmethod
    def list_exports(server: str) -> Tuple[bool, List[str], str]:
        """List available exports from NFS server."""
        return list_nfs_exports(server)
    
    @staticmethod
    def mount(server: str, export_path: str, mount_point: Optional[str] = None,
              options: Optional[str] = None) -> Tuple[bool, str]:
        """Mount NFS share."""
        return mount_nfs(server, export_path, mount_point, options)
    
    @staticmethod
    def unmount(mount_point: str) -> Tuple[bool, str]:
        """Unmount NFS share."""
        return unmount_nfs(mount_point)
