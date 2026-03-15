"""
Key Management Service Provider Abstraction.

Supports multiple key storage backends:
- LocalKeyProvider: File-based Fernet-encrypted storage (default)
- VaultKeyProvider: HashiCorp Vault integration
- AWSKMSProvider: AWS KMS integration (Future)
"""
import os
import json
import secrets
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from cryptography.fernet import Fernet


class KeyProvider(ABC):
    """Abstract base class for key management providers."""
    
    @abstractmethod
    def generate_key(self) -> Tuple[str, str]:
        """
        Generate a new encryption key.
        
        Returns:
            Tuple of (key_id, passphrase)
        """
        pass
    
    @abstractmethod
    def store_key(self, key_id: str, passphrase: str, metadata: Dict = None) -> bool:
        """
        Store an encryption key.
        
        Args:
            key_id: Unique identifier for the key
            passphrase: The encryption passphrase
            metadata: Optional metadata (job_id, tape_barcode, etc.)
        
        Returns:
            True if successful
        """
        pass
    
    @abstractmethod
    def get_key(self, key_id: str) -> Optional[str]:
        """
        Retrieve an encryption key passphrase.
        
        Args:
            key_id: Key identifier
            
        Returns:
            Passphrase string or None if not found
        """
        pass
    
    @abstractmethod
    def list_keys(self) -> List[Dict]:
        """List all stored encryption keys with metadata."""
        pass
    
    @abstractmethod
    def delete_key(self, key_id: str) -> bool:
        """Delete an encryption key."""
        pass
    
    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about this provider."""
        return {
            'type': self.__class__.__name__,
            'available': True
        }

    def get_active_key_hex(self) -> Optional[str]:
        """
        Get the hex representation of the active encryption key.
        If no key exists, generates one.
        """
        keys = self.list_keys()
        if not keys:
            # Generate a new default key if none exist
            key_id, passphrase = self.generate_key()
            self.store_key(key_id, passphrase, {'description': 'Default Hardware Encryption Key'})
            # Re-fetch to be sure
            keys = self.list_keys()
            if not keys: return None
        
        # In a real system, we might have a specific "active" flag.
        # For now, we use the most recent key.
        latest_key = sorted(keys, key=lambda k: k.get('created_at', ''), reverse=True)[0]
        passphrase = self.get_key(latest_key['key_id'])
        if not passphrase:
            return None
            
        # Convert passphrase to hex for LTO hardware (using SHA-256 to ensure 256-bit length)
        import hashlib
        return hashlib.sha256(passphrase.encode('utf-8')).hexdigest()


class LocalKeyProvider(KeyProvider):
    """
    Local file-based key storage with Fernet encryption.
    Default provider for standalone deployments.
    """
    
    def __init__(self, key_store_path: Path = None, fernet_key: bytes = None):
        """
        Initialize local key provider.
        
        Args:
            key_store_path: Directory to store keys
            fernet_key: Fernet key for encrypting stored keys
        """
        self.key_store_path = key_store_path or self._get_default_path()
        self._fernet = None
        
        if fernet_key:
            self._fernet = Fernet(fernet_key)
        else:
            self._initialize_fernet()
    
    def _get_default_path(self) -> Path:
        """Get default key storage path."""
        for base_dir in ['/var/lib/fossilsafe', '/opt/fossilsafe', '.']:
            path = Path(base_dir) / 'encryption_keys'
            if path.parent.exists():
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                return path
        return Path('./encryption_keys')
    
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
        """Generate a new encryption key."""
        passphrase = secrets.token_urlsafe(32)
        key_id = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
        return key_id, passphrase
    
    def store_key(self, key_id: str, passphrase: str, metadata: Dict = None) -> bool:
        """Store an encryption key securely."""
        try:
            key_data = {
                'passphrase': passphrase,
                'created_at': datetime.now().isoformat(),
                'metadata': metadata or {}
            }
            
            encrypted = self._fernet.encrypt(json.dumps(key_data).encode())
            key_file = self.key_store_path / f"{key_id}.key"
            key_file.write_bytes(encrypted)
            os.chmod(key_file, 0o600)
            
            return True
        except Exception as e:
            print(f"Failed to store key {key_id}: {e}")
            return False
    
    def get_key(self, key_id: str) -> Optional[str]:
        """Retrieve an encryption key passphrase."""
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
    
    def list_keys(self) -> List[Dict]:
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
    
    def rotate_master_key(self) -> Tuple[bool, str]:
        """
        Rotate the master Fernet key and re-encrypt all stored keys.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            old_fernet = self._fernet
            new_key = Fernet.generate_key()
            new_fernet = Fernet(new_key)
            
            # List all keys and re-encrypt
            key_files = list(self.key_store_path.glob("*.key"))
            re_encrypted_keys = []
            
            for key_file in key_files:
                encrypted = key_file.read_bytes()
                decrypted = old_fernet.decrypt(encrypted)
                re_encrypted = new_fernet.encrypt(decrypted)
                re_encrypted_keys.append((key_file, re_encrypted))
            
            # Atomic update (as much as possible)
            # 1. Write re-encrypted keys
            for key_file, encrypted_data in re_encrypted_keys:
                key_file.write_bytes(encrypted_data)
            
            # 2. Update fernet key file
            key_file = self.key_store_path / '.fernet_key'
            key_file.write_bytes(new_key)
            self._fernet = new_fernet
            
            return True, f"Successfully rotated master key and re-encrypted {len(key_files)} keys."
        except Exception as e:
            return False, f"Key rotation failed: {str(e)}"

    def get_provider_info(self) -> Dict[str, Any]:
        return {
            'type': 'LocalKeyProvider',
            'available': True,
            'key_store_path': str(self.key_store_path),
            'key_count': len(list(self.key_store_path.glob("*.key")))
        }


class VaultKeyProvider(KeyProvider):
    """
    HashiCorp Vault key storage provider.
    Enterprise feature for centralized key management.
    """
    
    def __init__(self, vault_addr: str, vault_token: str, mount_path: str = "secret"):
        """
        Initialize Vault key provider.
        
        Args:
            vault_addr: Vault server address (e.g., https://vault.example.com:8200)
            vault_token: Vault token for authentication
            mount_path: KV secrets engine mount path
        """
        self.vault_addr = vault_addr.rstrip('/')
        self.vault_token = vault_token
        self.mount_path = mount_path
        self.key_prefix = "fossilsafe/tape-keys"
        self._available = None
    
    def _make_request(self, method: str, path: str, data: Dict = None) -> Tuple[bool, Dict]:
        """Make authenticated request to Vault."""
        url = f"{self.vault_addr}/v1/{path}"
        
        headers = {
            'X-Vault-Token': self.vault_token,
            'Content-Type': 'application/json'
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode() if data else None,
                headers=headers,
                method=method
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read().decode()
                return True, json.loads(body) if body else {}
                
        except urllib.error.HTTPError as e:
            return False, {'error': f"HTTP {e.code}: {e.reason}"}
        except urllib.error.URLError as e:
            return False, {'error': f"Connection error: {e.reason}"}
        except Exception as e:
            return False, {'error': str(e)}
    
    def _check_availability(self) -> bool:
        """Check if Vault is reachable and authenticated."""
        success, _ = self._make_request('GET', 'sys/health')
        return success
    
    def generate_key(self) -> Tuple[str, str]:
        """Generate a new encryption key."""
        passphrase = secrets.token_urlsafe(32)
        key_id = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
        return key_id, passphrase
    
    def store_key(self, key_id: str, passphrase: str, metadata: Dict = None) -> bool:
        """Store encryption key in Vault."""
        path = f"{self.mount_path}/data/{self.key_prefix}/{key_id}"
        
        data = {
            'data': {
                'passphrase': passphrase,
                'created_at': datetime.now().isoformat(),
                'metadata': metadata or {}
            }
        }
        
        success, response = self._make_request('POST', path, data)
        if not success:
            print(f"Failed to store key in Vault: {response.get('error')}")
        return success
    
    def get_key(self, key_id: str) -> Optional[str]:
        """Retrieve encryption key from Vault."""
        path = f"{self.mount_path}/data/{self.key_prefix}/{key_id}"
        
        success, response = self._make_request('GET', path)
        if not success:
            return None
        
        try:
            data = response.get('data', {}).get('data', {})
            return data.get('passphrase')
        except Exception:
            return None
    
    def list_keys(self) -> List[Dict]:
        """List all keys stored in Vault."""
        path = f"{self.mount_path}/metadata/{self.key_prefix}"
        
        success, response = self._make_request('LIST', path)
        if not success:
            return []
        
        keys = []
        for key_id in response.get('data', {}).get('keys', []):
            # Get key metadata
            key_path = f"{self.mount_path}/data/{self.key_prefix}/{key_id}"
            key_success, key_response = self._make_request('GET', key_path)
            
            if key_success:
                data = key_response.get('data', {}).get('data', {})
                keys.append({
                    'key_id': key_id,
                    'created_at': data.get('created_at'),
                    'metadata': data.get('metadata', {})
                })
            else:
                keys.append({
                    'key_id': key_id,
                    'created_at': None,
                    'metadata': {},
                    'error': 'Could not retrieve key info'
                })
        
        return keys
    
    def delete_key(self, key_id: str) -> bool:
        """Delete encryption key from Vault."""
        # Permanently destroy all versions
        path = f"{self.mount_path}/metadata/{self.key_prefix}/{key_id}"
        success, _ = self._make_request('DELETE', path)
        return success
    
    def get_provider_info(self) -> Dict[str, Any]:
        if self._available is None:
            self._available = self._check_availability()
        
        return {
            'type': 'VaultKeyProvider',
            'available': self._available,
            'vault_addr': self.vault_addr,
            'mount_path': self.mount_path
        }


def create_key_provider(config: Dict = None) -> KeyProvider:
    """
    Factory function to create appropriate key provider based on configuration.
    
    Args:
        config: Configuration dictionary with provider settings
        
    Returns:
        Configured KeyProvider instance
    """
    if not config:
        return LocalKeyProvider()
    
    provider_type = config.get('type', 'local').lower()
    
    if provider_type == 'vault':
        vault_addr = config.get('vault_addr', os.environ.get('VAULT_ADDR', ''))
        vault_token = config.get('vault_token', os.environ.get('VAULT_TOKEN', ''))
        mount_path = config.get('mount_path', 'secret')
        
        if vault_addr and vault_token:
            return VaultKeyProvider(vault_addr, vault_token, mount_path)
        else:
            print("Vault configuration incomplete, falling back to local provider")
            return LocalKeyProvider()
    
# Default to local
    return LocalKeyProvider()


class KMSProvider:
    """
    High-level KMS wrapper for the rest of the application.
    Automatically initializes the correct provider based on system settings.
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(KMSProvider, cls).__new__(cls)
            cls._instance.provider = create_key_provider()
        return cls._instance

    def get_active_key_hex(self) -> Optional[str]:
        """Proxy to the underlying provider's get_active_key_hex."""
        return self.provider.get_active_key_hex()
