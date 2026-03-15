"""
Tape Encryption Module - Hardware-independent encryption for tape archives.
Uses GPG symmetric encryption with AES-256.
"""
import os
import subprocess
import hashlib
import secrets
import json
from pathlib import Path
from typing import Optional, Tuple, Dict
from datetime import datetime
from cryptography.fernet import Fernet


def get_key_store_path() -> Path:
    """Get path to encryption key store directory."""
    # Use same location pattern as credential keys
    for base_dir in ['/var/lib/fossilsafe', '/opt/fossilsafe', '.']:
        path = Path(base_dir) / 'encryption_keys'
        if path.parent.exists():
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            return path
    return Path('./encryption_keys')


class TapeEncryption:
    """
    Manages encryption for tape archives.
    
    Uses GPG symmetric encryption with AES-256 for the actual tape data.
    Keys are stored encrypted with Fernet (same as credential storage).
    """
    
    def __init__(self, fernet_key: bytes = None):
        """
        Initialize tape encryption.
        
        Args:
            fernet_key: Fernet key for encrypting stored tape keys.
                       If None, will generate or load from default location.
        """
        self.key_store_path = get_key_store_path()
        self._fernet = None
        
        if fernet_key:
            self._fernet = Fernet(fernet_key)
        else:
            self._initialize_fernet()
    
    def _initialize_fernet(self):
        """Initialize Fernet for key storage encryption."""
        key_file = self.key_store_path / '.fernet_key'
        
        if key_file.exists():
            self._fernet = Fernet(key_file.read_bytes())
        else:
            key = Fernet.generate_key()
            key_file.write_bytes(key)
            os.chmod(key_file, 0o600)
            self._fernet = Fernet(key)
    
    def generate_key(self) -> Tuple[str, str]:
        """
        Generate a new encryption key for a tape archive.
        
        Returns:
            Tuple of (key_id, passphrase) where passphrase is used for GPG
        """
        # Generate 256-bit key as passphrase
        passphrase = secrets.token_urlsafe(32)
        
        # Generate unique key ID
        key_id = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
        
        return key_id, passphrase
    
    def store_key(self, key_id: str, passphrase: str, metadata: Dict = None) -> bool:
        """
        Store an encryption key securely.
        
        Args:
            key_id: Unique identifier for the key
            passphrase: The encryption passphrase
            metadata: Optional metadata (job_id, tape_barcode, etc.)
        
        Returns:
            True if successful
        """
        try:
            key_data = {
                'passphrase': passphrase,
                'created_at': datetime.now().isoformat(),
                'metadata': metadata or {}
            }
            
            # Encrypt the key data
            encrypted = self._fernet.encrypt(json.dumps(key_data).encode())
            
            # Store to file
            key_file = self.key_store_path / f"{key_id}.key"
            key_file.write_bytes(encrypted)
            os.chmod(key_file, 0o600)
            
            return True
        except Exception as e:
            print(f"Failed to store key {key_id}: {e}")
            return False
    
    def get_key(self, key_id: str) -> Optional[str]:
        """
        Retrieve an encryption key passphrase.
        
        Args:
            key_id: Key identifier
            
        Returns:
            Passphrase string or None if not found
        """
        try:
            key_file = self.key_store_path / f"{key_id}.key"
            if not key_file.exists():
                return None
            
            encrypted = key_file.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            key_data = json.loads(decrypted.decode())
            
            return key_data.get('passphrase')
        except Exception as e:
            print(f"Failed to retrieve key {key_id}: {e}")
            return None
    
    def list_keys(self) -> list:
        """List all stored encryption keys."""
        keys = []
        for key_file in self.key_store_path.glob("*.key"):
            key_id = key_file.stem
            try:
                encrypted = key_file.read_bytes()
                decrypted = self._fernet.decrypt(encrypted)
                key_data = json.loads(decrypted.decode())
                keys.append({
                    'key_id': key_id,
                    'created_at': key_data.get('created_at'),
                    'metadata': key_data.get('metadata', {})
                })
            except Exception:
                keys.append({
                    'key_id': key_id,
                    'created_at': None,
                    'metadata': {},
                    'error': 'Could not decrypt key info'
                })
        return keys
    
    def delete_key(self, key_id: str) -> bool:
        """Delete an encryption key (use with caution!)."""
        try:
            key_file = self.key_store_path / f"{key_id}.key"
            if key_file.exists():
                key_file.unlink()
                return True
            return False
        except Exception as e:
            print(f"Failed to delete key {key_id}: {e}")
            return False
    
    @staticmethod
    def build_encrypt_command(passphrase_file: str, input_cmd: list = None) -> list:
        """
        Build GPG encryption command for piping.
        
        Args:
            passphrase_file: Path to file containing passphrase
            input_cmd: Optional input command to pipe from (e.g., tar command)
        
        Returns:
            List of command arguments for GPG encryption
        """
        gpg_cmd = [
            'gpg',
            '--batch',
            '--yes',
            '--symmetric',
            '--cipher-algo', 'AES256',
            '--passphrase-file', passphrase_file,
            '--compress-algo', 'none',  # Tape compression is more efficient
        ]
        return gpg_cmd
    
    @staticmethod
    def build_decrypt_command(passphrase_file: str) -> list:
        """
        Build GPG decryption command for piping.
        
        Args:
            passphrase_file: Path to file containing passphrase
        
        Returns:
            List of command arguments for GPG decryption
        """
        return [
            'gpg',
            '--batch',
            '--yes',
            '--decrypt',
            '--passphrase-file', passphrase_file,
        ]
    
    def create_passphrase_file(self, key_id: str) -> Optional[str]:
        """
        Create a temporary file with the passphrase for GPG.
        
        Args:
            key_id: Key identifier
            
        Returns:
            Path to passphrase file (caller must delete after use)
        """
        passphrase = self.get_key(key_id)
        if not passphrase:
            return None
        
        # Create temp file in key store (same secure location)
        passphrase_file = self.key_store_path / f".tmp_{key_id}_{secrets.token_hex(4)}"
        passphrase_file.write_text(passphrase)
        os.chmod(passphrase_file, 0o600)
        
        return str(passphrase_file)
    
    def cleanup_passphrase_file(self, path: str):
        """Securely delete a passphrase file."""
        try:
            p = Path(path)
            if p.exists():
                # Overwrite with random data before deletion
                p.write_bytes(secrets.token_bytes(64))
                p.unlink()
        except Exception:
            pass


def encrypt_stream(tar_process, tape_device: str, passphrase_file: str) -> Tuple[bool, str]:
    """
    Encrypt tar output and write to tape.
    
    Args:
        tar_process: Subprocess with tar output on stdout
        tape_device: Tape device path (e.g., /dev/nst0)
        passphrase_file: Path to GPG passphrase file
    
    Returns:
        Tuple of (success, error_message)
    """
    try:
        # Build GPG command
        gpg_cmd = TapeEncryption.build_encrypt_command(passphrase_file)
        
        # Pipe tar -> gpg -> tape
        gpg_process = subprocess.Popen(
            gpg_cmd,
            stdin=tar_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Write encrypted data to tape
        with open(tape_device, 'wb') as tape:
            while True:
                chunk = gpg_process.stdout.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                tape.write(chunk)
        
        # Wait for processes to complete
        tar_process.wait()
        gpg_process.wait()
        
        if gpg_process.returncode != 0:
            stderr = gpg_process.stderr.read().decode()
            return False, f"GPG encryption failed: {stderr}"
        
        return True, ""
        
    except Exception as e:
        return False, f"Encryption error: {e}"


def decrypt_stream(tape_device: str, output_dir: str, passphrase_file: str) -> Tuple[bool, str]:
    """
    Read encrypted data from tape and decrypt.
    
    Args:
        tape_device: Tape device path
        output_dir: Directory to extract files to
        passphrase_file: Path to GPG passphrase file
    
    Returns:
        Tuple of (success, error_message)
    """
    try:
        # Build GPG decrypt command
        gpg_cmd = TapeEncryption.build_decrypt_command(passphrase_file)
        
        # Pipe tape -> gpg -> tar
        with open(tape_device, 'rb') as tape:
            gpg_process = subprocess.Popen(
                gpg_cmd,
                stdin=tape,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            tar_process = subprocess.Popen(
                ['tar', '-xvf', '-', '-C', output_dir],
                stdin=gpg_process.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            tar_process.wait()
            gpg_process.wait()
        
        if gpg_process.returncode != 0:
            stderr = gpg_process.stderr.read().decode()
            return False, f"GPG decryption failed: {stderr}"
        
        if tar_process.returncode != 0:
            stderr = tar_process.stderr.read().decode()
            return False, f"Tar extraction failed: {stderr}"
        
        return True, ""
        
    except Exception as e:
        return False, f"Decryption error: {e}"
