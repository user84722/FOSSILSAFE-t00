#!/usr/bin/env python3
"""
SMB Client Module - Handles SMB/CIFS file operations
"""

import os
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Optional
import tempfile
import shutil
import logging
import re

logger = logging.getLogger(__name__)



class SMBScanError(RuntimeError):
    def __init__(self, code: str, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class SMBMountError(RuntimeError):
    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.detail = detail

class SMBClient:
    """
    SMB/CIFS client for accessing network shares.
    Uses smbclient command-line tool for reliability.
    """
    
    def __init__(self):
        self.mounted_shares = {}

    def _normalize_path(self, path: str) -> str:
        """Normalize SMB path by ensuring it uses UNC format (//server/share)."""
        if not path:
            return path
        if path.startswith('smb://'):
            path = path.replace('smb://', '//', 1)
        
        # Remove trailing slash
        if path.endswith('/'):
            path = path.rstrip('/')
            
        return path

    def _escape_smb_path(self, path: str) -> str:
        """Escape a path for use in smbclient -c commands."""
        # Check for injection attempts within smbclient (like ! command breakout)
        if re.search(r'[;!|`$<>(){}\[\]\\]', path):
             raise ValueError("SMB path contains invalid characters")
        
        # smbclient uses double quotes for paths. 
        # We wrap in double quotes and escape any existing double quotes.
        return '"' + path.replace('"', '""') + '"'
    
    def connect(self, share_path: str, username: str, password: str, domain: str = None) -> bool:
        """
        Test connection to SMB share.
        
        Args:
            share_path: UNC path like //server/share
            username: SMB username
            password: SMB password
            domain: Optional domain/workgroup
            
        Returns:
            True if connection successful
        """
        share_path = self._normalize_path(share_path)
        try:
            creds_file = self._write_credentials_file(username, password, domain)
            cmd = ['smbclient', share_path, '-A', creds_file, '-c', 'ls']
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                text=True
            )
            return result.returncode == 0
        except Exception as e:
            print(f"SMB connection test failed: {e}")
            return False
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)

    def test_connection(self, share_path: str, username=None, password: str = None,
                        domain: str = None):
        """
        Test SMB connectivity.

        Accepts either a credentials dict or explicit username/password.

        Returns:
            True if connection successful, False otherwise.
        """
        if isinstance(username, dict):
            credentials = username
            return self.connect(
                share_path,
                credentials.get('username', ''),
                credentials.get('password', ''),
                credentials.get('domain', '')
            )
        return self.connect(share_path, username or '', password or '', domain)

    def test_connection_detailed(self, share_path: str, username: str, password: str,
                                 domain: str = None) -> Dict[str, object]:
        """
        Test SMB connectivity with structured error details.

        Returns:
            dict with ok (bool) and optional error_code/message/detail.
        """
        share_path = self._normalize_path(share_path)
        if not share_path:
            return {
                "ok": False,
                "error_code": "invalid_request",
                "message": "SMB path is required",
                "detail": None,
            }
        try:
            creds_file = self._write_credentials_file(username or '', password or '', domain)
            cmd = ['smbclient', share_path, '-A', creds_file, '-c', 'ls']
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                text=True
            )
            output = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
            if result.returncode == 0:
                return {"ok": True, "detail": output or None}
            if self._is_auth_failure(output):
                return {
                    "ok": False,
                    "error_code": "auth_failed",
                    "message": "SMB authentication failed",
                    "detail": output or "Authentication failed",
                }
            if self._is_share_not_found(output):
                return {
                    "ok": False,
                    "error_code": "share_not_found",
                    "message": "SMB share not found",
                    "detail": output or "Share not found",
                }
            if self._is_host_unreachable(output):
                return {
                    "ok": False,
                    "error_code": "host_unreachable",
                    "message": "SMB host unreachable",
                    "detail": output or "Host unreachable",
                }
            return {
                "ok": False,
                "error_code": "connection_failed",
                "message": "SMB connection failed",
                "detail": output or f"SMB client exited with code {result.returncode}",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error_code": "timeout",
                "message": "SMB connection timed out",
                "detail": "SMB client timed out",
            }
        except FileNotFoundError:
            return {
                "ok": False,
                "error_code": "smb_tool_missing",
                "message": "SMB client not available",
                "detail": "smbclient command not found",
            }
        except Exception as e:
            return {
                "ok": False,
                "error_code": "connection_failed",
                "message": "SMB connection failed",
                "detail": str(e),
            }
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)
    
    def mount_share(self, share_path: str, username: str, password: str,
                    domain: str = None, mount_point: str = None,
                    raise_on_error: bool = False, port: Optional[int] = None) -> Optional[str]:
        """
        Mount SMB share using mount.cifs.
        
        Args:
            share_path: UNC path like //server/share
            username: SMB username
            password: SMB password
            domain: Optional domain/workgroup
            mount_point: Optional mount point (auto-created if None)
            
        Returns:
            Mount point path or None on failure
        """
        share_path = self._normalize_path(share_path)
        try:
            # Create mount point if not provided
            if mount_point is None:
                mount_point = tempfile.mkdtemp(prefix='smb_mount_')
            else:
                os.makedirs(mount_point, exist_ok=True)
            
            # Build mount options
            creds_file = self._write_credentials_file(username, password, domain)
            options = [
                f'credentials={creds_file}',
                'rw',
                'file_mode=0755',
                'dir_mode=0755'
            ]
            if port is not None:
                options.append(f'port={port}')
            options_str = ','.join(options)
            
            # Mount the share
            cmd = [
                'sudo', 'mount', '-t', 'cifs',
                share_path,
                mount_point,
                '-o', options_str
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                text=True
            )
            
            if result.returncode == 0:
                self.mounted_shares[share_path] = mount_point
                return mount_point
            print(f"Mount failed: {result.stderr}")
            # Clean up mount point
            try:
                os.rmdir(mount_point)
            except Exception:
                pass
            if raise_on_error:
                raise SMBMountError("SMB mount failed", result.stderr.strip() or None)
            return None
                
        except Exception as e:
            print(f"SMB mount failed: {e}")
            if raise_on_error:
                raise SMBMountError("SMB mount failed", str(e))
            return None
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)
    
    def unmount_share(self, share_path: str = None, mount_point: str = None) -> bool:
        """
        Unmount SMB share.
        
        Args:
            share_path: UNC path (used to lookup mount point)
            mount_point: Direct mount point path
            
        Returns:
            True if unmounted successfully
        """
        try:
            # Determine mount point
            if mount_point is None and share_path:
                mount_point = self.mounted_shares.get(share_path)
            
            if not mount_point:
                return False
            
            # Unmount
            result = subprocess.run(
                ['sudo', 'umount', mount_point],
                capture_output=True,
                timeout=30,
                text=True
            )
            
            if result.returncode == 0:
                # Remove from tracking
                if share_path and share_path in self.mounted_shares:
                    del self.mounted_shares[share_path]
                
                # Clean up mount point directory
                try:
                    os.rmdir(mount_point)
                except OSError:
                    pass
                
                return True
            else:
                print(f"Unmount failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"SMB unmount failed: {e}")
            return False
    
    def list_files(self, share_path: str, username: str, password: str,
                   remote_path: str = '', domain: str = None, limit: int = None) -> List[Dict]:
        """
        List files in SMB share without mounting.
        
        Args:
            share_path: UNC path like //server/share
            username: SMB username
            password: SMB password
            remote_path: Path within share
            domain: Optional domain
            
        Returns:
            List of file info dicts
        """
        share_path = self._normalize_path(share_path)
        try:
            creds_file = self._write_credentials_file(username, password, domain)
            cmd = ['smbclient', share_path, '-A', creds_file]
            # Build ls command
            escaped_remote = self._escape_smb_path(remote_path)
            ls_cmd = f'cd {escaped_remote}; ls' if remote_path else 'ls'
            cmd.extend(['-c', ls_cmd])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=60,
                text=True
            )
            
            if result.returncode != 0:
                return []
            
            # Parse output
            files = []
            for line in result.stdout.split('\n'):
                line = line.strip()
                if not line or line.startswith('.') or 'blocks of size' in line:
                    continue
                
                parts = line.split()
                if len(parts) >= 2:
                    name = parts[0]
                    if name not in ['.', '..']:
                        files.append({
                            'name': name,
                            'is_dir': 'D' in line,
                            'size': int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                        })
            
            if limit:
                return files[:limit]
            return files
            
        except Exception as e:
            print(f"SMB list failed: {e}")
            return []
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)
    
    def upload_file(self, share_path: str, username: str, password: str,
                   local_file: str, remote_file: str, domain: str = None) -> bool:
        """
        Upload a single file to SMB share.
        
        Args:
            share_path: UNC path like //server/share
            username: SMB username
            password: SMB password
            local_file: Local source path
            remote_file: Path to file on share
            domain: Optional domain
            
        Returns:
            True if upload successful
        """
        share_path = self._normalize_path(share_path)
        try:
            creds_file = self._write_credentials_file(username, password, domain)
            # smbclient format: put local_file remote_file
            # Escape paths to handle spaces and quotes
            escaped_local = self._escape_smb_path(local_file)
            escaped_remote = self._escape_smb_path(remote_file)
            cmd = ['smbclient', share_path, '-A', creds_file, '-c', f'put {escaped_local} {escaped_remote}']
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,  # 5 min timeout
                text=True
            )
            
            if result.returncode != 0:
                logger.warning(f"smbclient UPLOAD failed for {local_file} with rc={result.returncode}: {result.stderr}")
                logger.warning(f"Command attempted: {' '.join(cmd)}")
                return False
                
            return True
            
        except Exception as e:
            logger.warning(f"SMB upload failed: {e}")
            return False
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)

    def download_file(self, share_path: str, username: str, password: str,
                     remote_file: str, local_file: str, domain: str = None) -> bool:
        """
        Download a single file from SMB share.
        
        Args:
            share_path: UNC path like //server/share
            username: SMB username
            password: SMB password
            remote_file: Path to file on share
            local_file: Local destination path
            domain: Optional domain
            
        Returns:
            True if download successful
        """
        share_path = self._normalize_path(share_path)
        try:
            creds_file = self._write_credentials_file(username, password, domain)
            escaped_remote = self._escape_smb_path(remote_file)
            escaped_local = self._escape_smb_path(local_file)
            cmd = ['smbclient', share_path, '-A', creds_file, '-c', f'get {escaped_remote} {escaped_local}']
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,  # 5 min timeout for large files
                text=True
            )
            
            if result.returncode != 0:
                logger.warning(f"smbclient DOWNLOAD failed for {remote_file} with rc={result.returncode}: {result.stderr}")
                logger.warning(f"Command attempted: {' '.join(cmd)}")
            
            return result.returncode == 0 and os.path.exists(local_file)
            
        except Exception as e:
            logger.warning(f"SMB download failed: {e}")
            return False
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)
    
    def delete_file(self, share_path: str, username: str, password: str,
                   remote_path: str, domain: str = None) -> bool:
        """
        Delete a file from the SMB share.
        
        Args:
            share_path: UNC path like //server/share
            username: SMB username
            password: SMB password
            remote_path: Path to file on share
            domain: Optional domain
            
        Returns:
            True if deletion successful or file doesn't exist
        """
        share_path = self._normalize_path(share_path)
        try:
            creds_file = self._write_credentials_file(username, password, domain)
            # Use del command
            escaped_path = self._escape_smb_path(remote_path)
            cmd = ['smbclient', share_path, '-A', creds_file, '-c', f'del {escaped_path}']
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                text=True
            )
            
            # success if 0, or if file not found (already gone)
            success = result.returncode == 0
            if not success:
               # specific check for 'NT_STATUS_NO_SUCH_FILE' or similar if needed
               # but smbclient might return non-zero.
               if "NT_STATUS_NO_SUCH_FILE" in result.stdout or "NT_STATUS_OBJECT_NAME_NOT_FOUND" in result.stdout:
                   return True
            
            return success
            
        except Exception as e:
            print(f"SMB delete failed: {e}")
            return False
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)

    def delete_empty_dirs(self, share_path: str, username: str, password: str,
                         remote_path: str, domain: str = None) -> bool:
        """
        Recursively delete empty directories starting from a path.
        Note: smbclient doesn't have a direct 'rmdir -p', so we might need to rely on 
        cleanup scripts or leave empty dirs. This is a best-effort implementation
        using 'rmdir' on the specific path.
        """
        try:
            creds_file = self._write_credentials_file(username, password, domain)
            # Use rmdir command
            escaped_path = self._escape_smb_path(remote_path)
            cmd = ['smbclient', share_path, '-A', creds_file, '-c', f'rmdir {escaped_path}']
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                text=True
            )
            
            return result.returncode == 0
        except Exception:
            return False
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)

    def cleanup(self):
        """Unmount all tracked shares"""
        for share_path in list(self.mounted_shares.keys()):
            self.unmount_share(share_path=share_path)

    def _is_auth_failure(self, output: str) -> bool:
        if not output:
            return False
        upper = output.upper()
        lower = output.lower()
        return (
            "NT_STATUS_LOGON_FAILURE" in upper
            or "NT_STATUS_ACCESS_DENIED" in upper
            or "session setup failed" in lower
            or "logon failure" in lower
            or "access denied" in lower
        )

    def _is_share_not_found(self, output: str) -> bool:
        if not output:
            return False
        upper = output.upper()
        lower = output.lower()
        return (
            "NT_STATUS_BAD_NETWORK_NAME" in upper
            or "NT_STATUS_BAD_NETWORK_PATH" in upper
            or "NT_STATUS_OBJECT_NAME_NOT_FOUND" in upper
            or "does not exist" in lower
            or "share not found" in lower
        )

    def _is_host_unreachable(self, output: str) -> bool:
        if not output:
            return False
        upper = output.upper()
        lower = output.lower()
        return (
            "NT_STATUS_HOST_UNREACHABLE" in upper
            or "NT_STATUS_NETWORK_UNREACHABLE" in upper
            or "connection to" in lower and "failed" in lower
            or "no route to host" in lower
            or "name or service not known" in lower
            or "could not resolve" in lower
        )

    def _write_credentials_file(self, username: str, password: str, domain: str = None) -> str:
        # Use a more secure directory if available, otherwise fallback to system temp
        # FossilSafe usually runs in /opt/fossilsafe or /var/lib/fossilsafe
        base_dir = os.environ.get('FOSSILSAFE_VAR_DIR', '/var/lib/fossilsafe/tmp')
        if not os.path.exists(base_dir):
             os.makedirs(base_dir, mode=0o700, exist_ok=True)
             
        handle = tempfile.NamedTemporaryFile(mode='w', dir=base_dir, delete=False)
        handle.write(f"username={username}\n")
        handle.write(f"password={password}\n")
        if domain:
            handle.write(f"domain={domain}\n")
        handle.flush()
        handle.close()
        os.chmod(handle.name, 0o600)
        return handle.name

    def _cleanup_credentials_file(self, path: str) -> None:
        try:
            os.remove(path)
        except Exception:
            pass

    def can_read(self, share_path: str, username: str, password: str, domain: str = None) -> bool:
        """Check read access on a share."""
        try:
            files = self.list_files(share_path, username, password, domain=domain, limit=1)
            return isinstance(files, list)
        except Exception:
            return False

    def scan_directory(self, share_path: str, username: str, password: str,
                       domain: str = None, scan_mode: str = "quick",
                       max_files: Optional[int] = None,
                       port: Optional[int] = None) -> Dict:
        """
        Scan a share and return file count and total size.
        Raises SMBScanError on failures.
        """
        share_path = self._normalize_path(share_path)
        start_time = time.monotonic()
        method = "find"
        sample_paths: List[str] = []
        warnings: List[str] = []
        file_count = 0
        total_size = 0
        dir_count = 0
        mount_point = None
        base_path = share_path
        partial = False
        normalized_mode = scan_mode if scan_mode in {"quick", "full"} else "quick"
        file_limit = max_files if max_files is not None else (5000 if normalized_mode == "quick" else None)

        def _record_warning(message: str) -> None:
            if message and len(warnings) < 20:
                warnings.append(message)

        try:
            if share_path.startswith('//'):
                try:
                    mount_point = self.mount_share(
                        share_path=share_path,
                        username=username,
                        password=password,
                        domain=domain or '',
                        raise_on_error=True,
                        port=port,
                    )
                except SMBMountError as exc:
                    return self._scan_with_smbclient(
                        share_path=share_path,
                        username=username,
                        password=password,
                        domain=domain or '',
                        mount_error=exc.detail,
                        port=port,
                    )
                if not mount_point:
                    return self._scan_with_smbclient(
                        share_path=share_path,
                        username=username,
                        password=password,
                        domain=domain or '',
                        mount_error="No mount point returned",
                        port=port,
                    )
                base_path = mount_point
                method = "mount"
            else:
                if not os.path.exists(base_path):
                    raise SMBScanError("path_not_found", "Source path not found", base_path)
                if not os.access(base_path, os.R_OK):
                    raise SMBScanError("permission_denied", "Source path is not readable", base_path)

                def _on_walk_error(err: Exception) -> None:
                    _record_warning(f"Unreadable path skipped: {err}")

                for root, dirnames, filenames in os.walk(base_path, onerror=_on_walk_error):
                    dir_count += len(dirnames)
                    for filename in filenames:
                        if file_limit is not None and file_count >= file_limit:
                            partial = True
                            _record_warning(f"Quick scan stopped after {file_limit} files.")
                            break
                        filepath = os.path.join(root, filename)
                        try:
                            stat = os.stat(filepath)
                        except FileNotFoundError:
                            _record_warning(f"Broken symlink skipped: {filepath}")
                            continue
                        except PermissionError:
                            _record_warning(f"Permission denied: {filepath}")
                            continue
                        except Exception:
                            _record_warning(f"Unreadable file skipped: {filepath}")
                            continue
                        file_count += 1
                        total_size += stat.st_size
                        if len(sample_paths) < 5:
                            relative_path = os.path.relpath(filepath, base_path)
                            sample_paths.append(relative_path.replace('\\', '/'))
                    if partial:
                        break
        finally:
            if mount_point:
                self.unmount_share(share_path=share_path, mount_point=mount_point)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        return {
            'file_count': file_count,
            'total_size': total_size,
            'duration_ms': duration_ms,
            'sample_paths': sample_paths,
            'method': method,
            'dir_count': dir_count,
            'warnings': warnings,
            'partial': partial,
            'scan_mode': normalized_mode,
        }

    def _scan_with_smbclient(self, share_path: str, username: str, password: str,
                             domain: str = None, mount_error: Optional[str] = None,
                             port: Optional[int] = None) -> Dict:
        start_time = time.monotonic()
        sample_paths: List[str] = []
        file_count = 0
        total_size = 0
        try:
            creds_file = self._write_credentials_file(username, password, domain)
            cmd = ['smbclient']
            if port is not None:
                cmd.extend(['-p', str(port)])
            cmd.extend([share_path, '-A', creds_file, '-c', 'recurse; ls'])
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=120,
                text=True
            )
            output = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
            if result.returncode != 0:
                detail = self._tail_output(output)
                if mount_error:
                    detail = "\n".join(filter(None, [mount_error, detail]))
                if self._is_auth_failure(output):
                    raise SMBScanError("auth_failed", "SMB authentication failed", detail)
                if self._is_share_not_found(output):
                    raise SMBScanError("share_not_found", "SMB share not found", detail)
                if self._is_host_unreachable(output):
                    raise SMBScanError("host_unreachable", "SMB host unreachable", detail)
                if mount_error:
                    raise SMBScanError("smb_mount_failed", "SMB mount failed", detail)
                raise SMBScanError("connection_failed", "SMB enumeration failed", detail)

            for line in result.stdout.split('\n'):
                line = line.strip()
                if not line or line.startswith('.') or 'blocks of size' in line:
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                name = parts[0]
                if name in ['.', '..']:
                    continue
                attributes = parts[1]
                size_token = parts[2]
                if 'D' in attributes:
                    continue
                if not size_token.isdigit():
                    continue
                size = int(size_token)
                file_count += 1
                total_size += size
                if len(sample_paths) < 5:
                    sample_paths.append(name)
        except subprocess.TimeoutExpired:
            raise SMBScanError("timeout", "SMB enumeration timed out", "SMB client timed out")
        except FileNotFoundError:
            raise SMBScanError("smb_tool_missing", "SMB client not available", "smbclient command not found")
        finally:
            if 'creds_file' in locals():
                self._cleanup_credentials_file(creds_file)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        return {
            'file_count': file_count,
            'total_size': total_size,
            'duration_ms': duration_ms,
            'sample_paths': sample_paths,
            'method': 'smbclient',
        }

    def _tail_output(self, output: str, lines: int = 12) -> Optional[str]:
        if not output:
            return None
        trimmed = "\n".join(output.strip().splitlines()[-lines:])
        return trimmed or None

    def reconnect(self, share_path: str, username: str, password: str, domain: str = None) -> bool:
        """Attempt a reconnect by unmounting any existing share and retesting."""
        try:
            self.unmount_share(share_path=share_path)
        except Exception:
            pass
        return self.connect(share_path, username, password, domain)

    def list_files_recursive(self, share_path: str, credentials: Dict[str, str]) -> List[Dict]:
        """
        Recursively list files from an SMB share or local path.

        Returns:
            List of file info dicts with relative path and size.
        """
        share_path = self._normalize_path(share_path)
        files: List[Dict] = []
        mount_point = None
        base_path = share_path

        try:
            if share_path.startswith('//'):
                mount_point = self.mount_share(
                    share_path=share_path,
                    username=credentials.get('username', ''),
                    password=credentials.get('password', ''),
                    domain=credentials.get('domain', '')
                )
                if not mount_point:
                    return []
                base_path = mount_point

            for root, _, filenames in os.walk(base_path):
                for filename in filenames:
                    filepath = os.path.join(root, filename)
                    try:
                        stat = os.stat(filepath)
                        relative_path = os.path.relpath(filepath, base_path)
                        files.append({
                            'path': relative_path.replace('\\', '/'),
                            'size': stat.st_size
                        })
                    except Exception:
                        continue
        finally:
            if mount_point:
                self.unmount_share(share_path=share_path, mount_point=mount_point)

        return files
