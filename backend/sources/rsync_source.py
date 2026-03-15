import subprocess
import os
import logging
from typing import Dict, List, Optional, Tuple
from backend.sources.ssh_source import SSHSource

logger = logging.getLogger(__name__)

class RsyncSource:
    """
    Handler for remote sources via Rsync over SSH.
    Uses the same appliance SSH key as SSHSource.
    """
    
    @classmethod
    def test_connection(cls, host: str, user: str, port: Optional[int] = 22) -> Tuple[bool, str]:
        """Tests Rsync connectivity using the appliance key."""
        # Rsync test is basically an SSH test + checking if rsync is on the other side
        SSHSource.ensure_ssh_key()
        priv_path = SSHSource.get_private_key_path()
        port = port or 22
        
        # We try to run rsync --version on the remote side
        ssh_opts = f"-o BatchMode=yes -o StrictHostKeyChecking=accept-new -i {priv_path} -p {port}"
        cmd = [
            "rsync", "-e", f"ssh {ssh_opts}",
            f"{user}@{host}:", # Empty path to trigger listing or just connection check
            "--version" # This is a bit hacky, better to just check if rsync exists
        ]
        
        # Better: run 'rsync --version' via ssh
        cmd = [
            "ssh", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", str(priv_path),
            "-p", str(port),
            f"{user}@{host}",
            "rsync --version"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and "rsync  version" in result.stdout:
                return True, "Rsync connection successful"
            else:
                error = result.stderr.strip() or f"Return code: {result.returncode}"
                if "command not found" in error.lower():
                    return False, "Rsync is not installed on the remote host"
                return False, f"Rsync connection failed: {error}"
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"

    @classmethod
    def dry_run_sync(cls, host: str, user: str, remote_path: str, port: Optional[int] = 22) -> Dict:
        """
        Performs an rsync dry-run to estimate size and file count.
        """
        SSHSource.ensure_ssh_key()
        priv_path = SSHSource.get_private_key_path()
        port = port or 22
        
        ssh_opts = f"-o BatchMode=yes -o StrictHostKeyChecking=accept-new -i {priv_path} -p {port}"
        
        # rsync --dry-run --stats
        cmd = [
            "rsync", "-avz", "--dry-run", "--stats",
            "-e", f"ssh {ssh_opts}",
            f"{user}@{host}:{remote_path}",
            "/tmp/rsync_dry_run_unused" # Dest doesn't matter for dry-run --stats
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise Exception(f"Rsync dry-run failed: {result.stderr}")
            
            # Parse stats
            stats = {}
            for line in result.stdout.split('\n'):
                if ":" in line:
                    key, val = line.split(":", 1)
                    stats[key.strip()] = val.strip()
            
            return {
                'file_count': int(stats.get('Number of files', '0').replace(',', '')),
                'total_size': int(stats.get('Total file size', '0').split()[0].replace(',', '')),
                'duration_ms': 0 # Not relevant for dry-run
            }
        except Exception as e:
            logger.error(f"Rsync dry-run failed: {e}")
            raise
