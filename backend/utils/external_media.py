"""
External media detection and management.
Supports USB drives and external hard disks on Linux and macOS.
"""
import os
import json
import subprocess
import platform
from typing import List, Dict, Optional, Tuple


def _run_command(cmd: List[str]) -> Tuple[bool, str]:
    """Run a command and return success status and output."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0, result.stdout
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _list_drives_linux() -> List[Dict]:
    """List external drives on Linux using lsblk."""
    success, output = _run_command([
        'lsblk',
        '--json',
        '--output', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,LABEL,UUID,HOTPLUG',
        '--bytes'
    ])
    
    if not success:
        return []
    
    try:
        data = json.loads(output)
        drives = []
        
        for device in data.get('blockdevices', []):
            # Only include hotpluggable devices (USB, external)
            if device.get('hotplug') != '1':
                continue
                
            # Skip if it's a partition (we want the parent device)
            if device.get('type') != 'disk':
                continue
            
            drives.append({
                'device': f"/dev/{device['name']}",
                'name': device.get('label') or device['name'],
                'size': device.get('size', 0),
                'size_human': _format_size(device.get('size', 0)),
                'filesystem': device.get('fstype', 'unknown'),
                'mount_point': device.get('mountpoint'),
                'uuid': device.get('uuid'),
                'is_mounted': bool(device.get('mountpoint')),
                'platform': 'linux'
            })
        
        return drives
    except json.JSONDecodeError:
        return []


def _list_drives_macos() -> List[Dict]:
    """List external drives on macOS using diskutil."""
    success, output = _run_command(['diskutil', 'list', '-plist'])
    
    if not success:
        return []
    
    try:
        import plistlib
        data = plistlib.loads(output.encode())
        drives = []
        
        for disk_name in data.get('AllDisksAndPartitions', []):
            disk_id = disk_name.get('DeviceIdentifier')
            if not disk_id:
                continue
            
            # Get detailed info for this disk
            success, info_output = _run_command(['diskutil', 'info', '-plist', disk_id])
            if not success:
                continue
            
            try:
                disk_info = plistlib.loads(info_output.encode())
                
                # Only include external/removable drives
                if not disk_info.get('Internal', True) == False:
                    continue
                
                # Skip if it's a partition container
                if disk_info.get('Content') == 'Apple_APFS':
                    continue
                
                mount_point = disk_info.get('MountPoint')
                
                drives.append({
                    'device': f"/dev/{disk_id}",
                    'name': disk_info.get('VolumeName') or disk_id,
                    'size': disk_info.get('TotalSize', 0),
                    'size_human': _format_size(disk_info.get('TotalSize', 0)),
                    'filesystem': disk_info.get('FilesystemType', 'unknown'),
                    'mount_point': mount_point,
                    'uuid': disk_info.get('VolumeUUID'),
                    'is_mounted': bool(mount_point),
                    'platform': 'macos'
                })
            except plistlib.InvalidFileException:
                continue
        
        return drives
    except Exception:
        return []


def _format_size(size_bytes: int) -> str:
    """Format byte size to human readable string."""
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    return f"{size:.1f} {units[unit_index]}"


def list_external_drives() -> List[Dict]:
    """
    List all detected external/removable drives.
    Returns a list of drive dictionaries with device, name, size, etc.
    """
    system = platform.system()
    
    if system == 'Linux':
        return _list_drives_linux()
    elif system == 'Darwin':  # macOS
        return _list_drives_macos()
    else:
        return []


def get_drive_info(device: str) -> Optional[Dict]:
    """Get detailed information about a specific drive."""
    drives = list_external_drives()
    for drive in drives:
        if drive['device'] == device:
            return drive
    return None


def mount_drive(device: str, mount_point: Optional[str] = None) -> Tuple[bool, str]:
    """
    Mount an external drive.
    Returns (success, message/mount_point).
    """
    # Validate device exists
    drive_info = get_drive_info(device)
    if not drive_info:
        return False, "Drive not found"
    
    if drive_info['is_mounted']:
        return True, drive_info['mount_point']
    
    system = platform.system()
    
    if system == 'Linux':
        # Create mount point if not specified
        if not mount_point:
            safe_name = drive_info['name'].replace(' ', '_').replace('/', '_')
            mount_point = f"/mnt/fossilsafe/{safe_name}"
        
        # Ensure mount point directory exists
        try:
            os.makedirs(mount_point, exist_ok=True)
        except OSError as e:
            return False, f"Failed to create mount point: {e}"
        
        # Mount the drive
        success, output = _run_command(['sudo', 'mount', device, mount_point])
        if success:
            return True, mount_point
        else:
            return False, f"Mount failed: {output}"
    
    elif system == 'Darwin':  # macOS
        # macOS handles mounting automatically or via diskutil
        success, output = _run_command(['diskutil', 'mount', device])
        if success:
            # Get the mount point
            updated_info = get_drive_info(device)
            if updated_info and updated_info['mount_point']:
                return True, updated_info['mount_point']
            return True, "Mounted (location unknown)"
        else:
            return False, f"Mount failed: {output}"
    
    return False, "Unsupported platform"


def unmount_drive(device: str) -> Tuple[bool, str]:
    """
    Unmount an external drive.
    Returns (success, message).
    """
    drive_info = get_drive_info(device)
    if not drive_info:
        return False, "Drive not found"
    
    if not drive_info['is_mounted']:
        return True, "Drive already unmounted"
    
    system = platform.system()
    
    if system == 'Linux':
        success, output = _run_command(['sudo', 'umount', device])
        if success:
            return True, "Drive unmounted successfully"
        else:
            return False, f"Unmount failed: {output}"
    
    elif system == 'Darwin':  # macOS
        success, output = _run_command(['diskutil', 'unmount', device])
        if success:
            return True, "Drive unmounted successfully"
        else:
            return False, f"Unmount failed: {output}"
    
    return False, "Unsupported platform"


def is_safe_to_unmount(device: str) -> Tuple[bool, str]:
    """
    Check if a drive is safe to unmount (not in use).
    Returns (is_safe, reason).
    """
    drive_info = get_drive_info(device)
    if not drive_info:
        return False, "Drive not found"
    
    if not drive_info['is_mounted']:
        return True, "Drive not mounted"
    
    mount_point = drive_info['mount_point']
    
    # Check if any processes are using the mount point
    system = platform.system()
    
    if system == 'Linux':
        success, output = _run_command(['lsof', mount_point])
        if success and output.strip():
            return False, "Drive is in use by running processes"
    
    elif system == 'Darwin':  # macOS
        success, output = _run_command(['lsof', mount_point])
        if success and output.strip():
            return False, "Drive is in use by running processes"
    
    return True, "Safe to unmount"
