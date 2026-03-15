import subprocess
import os
import json
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from backend.config_store import get_data_dir

logger = logging.getLogger(__name__)

class RcloneSource:
    """
    Handler for remote object storage via rclone.
    Supports S3, Backblaze B2, SFTP, etc.
    """
    
    @classmethod
    def get_config_dir(cls) -> Path:
        config_dir = Path(get_data_dir()) / "rclone"
        config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return config_dir

    @classmethod
    def get_config_path(cls) -> Path:
        return cls.get_config_dir() / "rclone.conf"

    @classmethod
    def list_remotes(cls) -> List[str]:
        """Lists configured rclone remotes."""
        cmd = ["rclone", "--config", str(cls.get_config_path()), "listremotes"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return [line.strip().rstrip(':') for line in result.stdout.strip().split('\n') if line]
        except Exception as e:
            logger.error(f"Failed to list rclone remotes: {e}")
            return []

    @classmethod
    def list_files(cls, remote_name: str, path: str = "") -> Dict:
        """Lists files on a remote using rclone lsjson, including hashes."""
        full_remote_path = f"{remote_name}:{path}"
        # Request common hashes. Rclone will provide what the remote supports.
        cmd = ["rclone", "--config", str(cls.get_config_path()), "lsjson", "--hash", full_remote_path]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            raw_entries = json.loads(result.stdout)
            
            entries = []
            for entry in raw_entries:
                # FossilSafe prefers SHA256, but S3 often uses MD5 (ETag). 
                # Rclone might provide 'SHA-1', 'MD5', etc.
                hashes = entry.get('Hashes', {})
                checksum = hashes.get('sha1') or hashes.get('md5') or hashes.get('dropbox') or ""
                
                entries.append({
                    'name': entry['Name'],
                    'path': entry['Path'], # Use relative path from root
                    'is_dir': entry['IsDir'],
                    'size': entry.get('Size', 0),
                    'mtime': entry.get('ModTime'),
                    'checksum': checksum
                })
            
            # Sort: Directories first
            entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            
            return {
                'path': path,
                'entries': entries,
                'parent': os.path.dirname(path.rstrip('/')) if path not in ['', '/'] else None
            }
        except subprocess.CalledProcessError as e:
            error = e.stderr.strip() if e.stderr else str(e)
            raise Exception(f"Failed to list rclone remote: {error}")
        except Exception as e:
            raise Exception(f"Rclone list error: {str(e)}")

    @classmethod
    def download_single_file(cls, remote_name: str, remote_path: str, local_dest: str) -> bool:
        """Downloads a single file from rclone remote."""
        full_src = f"{remote_name}:{remote_path}"
        cmd = [
            "rclone", "--config", str(cls.get_config_path()),
            "copyto", full_src, local_dest
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            return True
        except Exception as e:
            logger.error(f"Rclone download failed: {e}")
            return False

    @classmethod
    def test_remote(cls, remote_name: str) -> Tuple[bool, str]:
        """Tests access to a configured remote."""
        cmd = ["rclone", "--config", str(cls.get_config_path()), "lsf", "--max-depth", "1", f"{remote_name}:"]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            return True, "Remote access successful"
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"
        except subprocess.CalledProcessError as e:
            error = e.stderr.strip() if e.stderr else str(e)
            return False, f"Access failed: {error}"

    @classmethod
    def test_connection_with_credentials(cls, provider: str, config: Dict[str, str]) -> Tuple[bool, str]:
        """
        Tests connection using explicit credentials (via env vars) without a config file.
        provider: s3, b2, etc. (rclone type)
        """
        env = os.environ.copy()
        # Map config to rclone env vars
        # RCLONE_CONFIG_MYREMOTE_TYPE=s3
        # RCLONE_CONFIG_MYREMOTE_ACCESS_KEY_ID=...
        
        remote_name = "test_temp"
        prefix = f"RCLONE_CONFIG_{remote_name.upper()}_"
        env[f"{prefix}TYPE"] = provider
        
        for key, value in config.items():
            # Rclone options are usually lower_case, env vars usually need to be specific.
            # We expect the caller to provide mapped keys like 'access_key_id' -> 'ACCESS_KEY_ID'
            # But rclone env loader is flexible. Let's try to map standard keys.
            
            # Common S3 mappings
            if provider == 's3':
                if key == 'access_key': env[f"{prefix}ACCESS_KEY_ID"] = value
                elif key == 'secret_key': env[f"{prefix}SECRET_ACCESS_KEY"] = value
                elif key == 'endpoint': env[f"{prefix}ENDPOINT"] = value
                elif key == 'region': env[f"{prefix}REGION"] = value
                # Add others as needed
            elif provider == 'b2':
                if key == 'account': env[f"{prefix}ACCOUNT"] = value
                elif key == 'key': env[f"{prefix}KEY"] = value
                
        # Run test
        cmd = ["rclone", "lsf", "--max-depth", "1", f"{remote_name}:"]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15, env=env)
            return True, "Connection successful"
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"
        except subprocess.CalledProcessError as e:
            error = e.stderr.strip() if e.stderr else str(e)
            return False, f"Connection failed: {error}"

    @classmethod
    def build_copy_command(cls, remote_name: str, remote_path: str, local_dest: str) -> List[str]:
        """Constructs an rclone copy command."""
        return [
            "rclone", "--config", str(cls.get_config_path()),
            "copy", "--progress",
            f"{remote_name}:{remote_path}",
            local_dest
        ]
