import hashlib
import json
import os
from typing import Optional, Dict, Any

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

class AuditSigner:
    """
    Handles Ed25519 signing and verification for audit logs.
    """
    def __init__(self, key_path: str = None):
        self.key_path = key_path or os.getenv('FOSSIL_SAFE_AUDIT_KEY_PATH', "/etc/fossilsafe/audit_key.pem")
        self._private_key = None
        self._public_key = None
        self._load_keys()

    def _load_keys(self):
        """Load or generate Ed25519 keys for signing."""
        try:
            if os.path.exists(self.key_path):
                with open(self.key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(f.read(), password=None)
                self._public_key = self._private_key.public_key()
            else:
                # Generate new key if it doesn't exist
                os.makedirs(os.path.dirname(self.key_path), exist_ok=True)
                self._private_key = ed25519.Ed25519PrivateKey.generate()
                self._public_key = self._private_key.public_key()
                
                with open(self.key_path, "wb") as f:
                    f.write(self._private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption()
                    ))
                os.chmod(self.key_path, 0o600)
        except Exception as e:
            print(f"Failed to load/generate audit keys: {e}")

    def sign(self, data: str) -> str:
        """Sign data and return hex signature."""
        if not self._private_key:
            return ""
        signature = self._private_key.sign(data.encode())
        return signature.hex()

    def verify(self, data: str, signature_hex: str) -> bool:
        """Verify signature for given data."""
        if not self._public_key:
            return False
        try:
            signature = bytes.fromhex(signature_hex)
            self._public_key.verify(signature, data.encode())
            return True
        except Exception:
            return False

class HashingManager:
    """
    Handles hashing for immutable audit logs.
    """
    
    @staticmethod
    def compute_log_hash(entry: Dict[str, Any], previous_hash: str) -> str:
        """
        Compute SHA-256 hash of a log entry combined with the previous hash.
        Fields included: timestamp, level, category, message, details, request_id, previous_hash.
        """
        # Ensure consistent serialization
        payload = {
            "timestamp": entry.get("timestamp"),
            "level": entry.get("level"),
            "category": entry.get("category"),
            "message": entry.get("message"),
            "details": entry.get("details"),
            "request_id": entry.get("request_id"),
            "previous_hash": previous_hash
        }
        
        # Sort keys to ensure deterministic JSON
        serialized = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

    @staticmethod
    def verify_chain(entries: list) -> bool:
        """
        Verify a list of log entries for hash integrity.
        Assumes entries are sorted by sequence/time.
        """
        return all(r['valid'] for r in HashingManager.verify_chain_detailed(entries))

    @staticmethod
    def verify_chain_detailed(entries: list) -> list:
        """
        Verify chain and return detailed status for each entry.
        Returns list of dicts: {'entry_id': id, 'valid': bool, 'error': str}
        """
        results = []
        if not entries:
            return results
            
        # First entry is always "valid" in isolation unless we check against external anchor
        results.append({'entry': entries[0], 'valid': True, 'error': None})
        
        for i in range(1, len(entries)):
            prev = entries[i-1]
            curr = entries[i]
            
            # Check 1: Link Integrity (curr.previous_hash MUST match prev.hash)
            # This detects deleted/missing rows or reordering
            if curr.get('previous_hash') != prev.get('hash'):
                results.append({
                    'entry': curr,
                    'valid': False, 
                    'error': f"Broken Link: previous_hash {curr.get('previous_hash')[:8]}... does not match prev entry hash {prev.get('hash')[:8]}..."
                })
                continue
            
            # Check 2: Content Integrity (curr.hash MUST match computed hash)
            # This detects tampering with the content of the current row
            expected_hash = HashingManager.compute_log_hash(curr, prev.get('hash', ''))
            if curr.get('hash') != expected_hash:
                results.append({
                    'entry': curr,
                    'valid': False, 
                    'error': "Integrity Failure: Content has been modified (Hash mismatch)"
                })
                continue
                
            results.append({'entry': curr, 'valid': True, 'error': None})
            
        return results
