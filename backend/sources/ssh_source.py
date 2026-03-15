import subprocess
import os
import logging
import re
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from backend.config_store import get_data_dir

logger = logging.getLogger(__name__)

class SSHSource:
    """
    Handler for remote sources via SSH and SFTP.
    Supports key-based authentication and rsync.
    """
    
    DEFAULT_KEY_NAME = "appliance_ssh_key"
    
    @classmethod
    def get_key_dir(cls) -> Path:
        key_dir = Path(get_data_dir()) / "ssh"
        key_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return key_dir

    @classmethod
    def get_private_key_path(cls) -> Path:
        return cls.get_key_dir() / cls.DEFAULT_KEY_NAME

    @classmethod
    def get_public_key_path(cls) -> Path:
        return cls.get_key_dir() / f"{cls.DEFAULT_KEY_NAME}.pub"

    @classmethod
    def ensure_ssh_key(cls) -> Tuple[bool, str]:
        """Ensures an appliance SSH key exists, generating one if necessary."""
        priv_path = cls.get_private_key_path()
        pub_path = cls.get_public_key_path()
        
        if priv_path.exists() and pub_path.exists():
            return True, "Key already exists"
            
        try:
            logger.info("Generating new appliance SSH key pair...")
            subprocess.run([
                "ssh-keygen", "-t", "ed25519", "-N", "", 
                "-f", str(priv_path), "-C", "fossilsafe-appliance"
            ], check=True, capture_output=True)
            priv_path.chmod(0o600)
            return True, "Key generated successfully"
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"Failed to generate SSH key: {error_msg}")
            return False, error_msg

    @classmethod
    def get_public_key(cls) -> Optional[str]:
        """Returns the public key content for display in the UI."""
        cls.ensure_ssh_key()
        pub_path = cls.get_public_key_path()
        if pub_path.exists():
            return pub_path.read_text().strip()
        return None

    @classmethod
    def test_connection(cls, host: str, user: str, port: Optional[int] = 22) -> Tuple[bool, str]:
        """Tests SSH connectivity using the appliance key."""
        cls.ensure_ssh_key()
        priv_path = cls.get_private_key_path()
        port = port or 22
        
        # Use BatchMode=yes to avoid interactive prompts
        # Use StrictHostKeyChecking=accept-new to handle first-time connections safely in a managed environment
        cmd = [
            "ssh", "-o", "BatchMode=yes", 
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", str(priv_path),
            "-p", str(port),
            f"{user}@{host}",
            "echo 'OK'"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if result.returncode == 0 and "OK" in result.stdout:
                return True, "Connection successful"
            else:
                error = result.stderr.strip() or f"Return code: {result.returncode}"
                return False, f"Connection failed: {error}"
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"

    @classmethod
    def list_remote_dir(cls, host: str, user: str, path: str, port: Optional[int] = 22) -> Dict:
        """Lists files in a remote directory via SSH."""
        cls.ensure_ssh_key()
        priv_path = cls.get_private_key_path()
        port = port or 22
        
        # We use a python one-liner on the remote side if possible for consistent JSON output,
        # but a simple 'ls -ap --full-time' fallback is safer for generic systems.
        # Here we'll start with a structured ls approach.
        cmd = [
            "ssh", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", str(priv_path),
            "-p", str(port),
            f"{user}@{host}",
            f"ls -ap {path}"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            lines = result.stdout.strip().split('\n')
            entries = []
            for line in lines:
                if not line or line in ['./', '../']:
                    continue
                is_dir = line.endswith('/')
                name = line.rstrip('/')
                entries.append({
                    'name': name,
                    'path': os.path.join(path, name),
                    'is_dir': is_dir,
                    'size': 0, # ls -ap doesn't give size easily without more flags
                })
            
            # Sort: Directories first
            entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            
            return {
                'path': path,
                'entries': entries,
                'parent': os.path.dirname(path.rstrip('/')) if path not in ['/', ''] else None
            }
        except subprocess.CalledProcessError as e:
            error = e.stderr.strip() if e.stderr else str(e)
            raise Exception(f"Failed to list remote directory: {error}")

    @classmethod
    def list_files_with_hashes(cls, host: str, user: str, path: str, port: Optional[int] = 22) -> List[Dict]:
        """Gathers file list with hashes from remote via SSH."""
        cls.ensure_ssh_key()
        priv_path = cls.get_private_key_path()
        port = port or 22
        
        # Use a combination of find and sha256sum for robustness
        # Format: <size>|<checksum>|<relative_path>
        cmd_str = (
            f"find {path} -type f -exec stat -c '%s' {{}} \\; -exec sha256sum {{}} \\; | "
            f"awk '{{ s=$1; getline; print s \"|\" $1 \"|\" $2 }}'"
        )
        
        # Actually, simpler and more reliable format:
        # find . -type f -printf "%s|" -exec sha256sum {} \;
        # But printf might not be on all platforms (like busybox).
        # Let's use a script approach.
        remote_script = (
            f"cd {path} && find . -type f | while read -r f; do "
            f"sz=$(stat -c %s \"$f\" 2>/dev/null || stat -f %z \"$f\"); "
            f"sum=$(sha256sum \"$f\" | awk '{{print $1}}'); "
            f"echo \"$sz|$sum|$f\"; done"
        )

        cmd = [
            "ssh", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", str(priv_path),
            "-p", str(port),
            f"{user}@{host}",
            remote_script
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            files = []
            for line in result.stdout.strip().split('\n'):
                if not line or '|' not in line:
                    continue
                parts = line.split('|', 2)
                if len(parts) < 3:
                    continue
                
                size = int(parts[0])
                checksum = parts[1]
                # parts[2] starts with ./ due to find .
                rel_path = parts[2].lstrip('./')
                
                files.append({
                    'path': rel_path,
                    'size': size,
                    'checksum': checksum
                })
            return files
        except Exception as e:
            error = getattr(e, 'stderr', str(e))
            logger.error(f"Failed remote hash scan: {error}")
            raise Exception(f"Remote scan failed: {error}")

    @classmethod
    def download_single_file(cls, host: str, user: str, remote_path: str, local_dest: str, port: Optional[int] = 22) -> bool:
        """Downloads a single file via scp."""
        cls.ensure_ssh_key()
        priv_path = cls.get_private_key_path()
        port = port or 22
        
        cmd = [
            "scp", "-P", str(port),
            "-i", str(priv_path),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{user}@{host}:{remote_path}",
            local_dest
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except Exception as e:
            logger.error(f"SCP failed: {e}")
            return False
