"""
Core Unit Test Suite
Consolidated suite covering Encryption, Immutable Logs, and External Catalog Sync.
"""
import unittest
import os
import json
import time
import tempfile
import shutil
import hashlib
from unittest.mock import MagicMock, patch

# FossilSafe Imports
from backend.utils.encryption import EncryptionManager, HEADER_v1
from backend.log_manager import LogManager
from backend.utils.hashing import HashingManager
from backend.external_catalog_backup import ExternalCatalogBackup

class TestEncryptionCore(unittest.TestCase):
    """Unit tests for the EncryptionManager (Logic from test_encryption.py)"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.passphrase = "correct horse battery staple"
        self.key, self.salt = EncryptionManager.derive_key(self.passphrase)
        self.manager = EncryptionManager(self.key)
        
    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_encrypt_decrypt_small_file(self):
        input_path = os.path.join(self.test_dir, "input.txt")
        enc_path = os.path.join(self.test_dir, "input.enc")
        dec_path = os.path.join(self.test_dir, "input.dec")
        
        content = b"Hello World! This is a test of AES-256-GCM encryption."
        with open(input_path, 'wb') as f:
            f.write(content)
            
        self.manager.encrypt_file(input_path, enc_path)
        self.manager.decrypt_file(enc_path, dec_path)
        
        with open(dec_path, 'rb') as f:
            self.assertEqual(content, f.read())

    def test_dos_protection(self):
        """Verify that huge chunk lengths trigger ValueError."""
        enc_path = os.path.join(self.test_dir, "dos_attack.enc")
        with open(enc_path, 'wb') as f:
            f.write(HEADER_v1)
            f.write(os.urandom(16)) # Salt
            huge_length = 100 * 1024 * 1024 
            f.write(huge_length.to_bytes(4, byteorder='big'))
            f.write(os.urandom(12)) # Nonce
            
        with self.assertRaises(ValueError):
            self.manager.decrypt_file(enc_path, os.path.join(self.test_dir, "dec.out"))


class TestImmutableLogsCore(unittest.TestCase):
    """Unit tests for log chaining (Logic from test_immutable_logs.py)"""
    
    def setUp(self):
        self.mock_db = MagicMock()
        self.log_manager = LogManager(db=self.mock_db)
        self.log_manager.logs = []
        
    def test_hash_chaining(self):
        entry1 = self.log_manager.add('info', 'First valid log')
        entry2 = self.log_manager.add('info', 'Second valid log')
        self.assertEqual(entry2.get('previous_hash'), entry1.get('hash'))
        self.assertTrue(HashingManager.verify_chain([entry1, entry2]))

    @patch('backend.log_manager.has_capability')
    def test_worm_compliance_cleanup(self, mock_has_capability):
        mock_has_capability.return_value = True
        self.log_manager.add('info', 'Old log')
        deleted = self.log_manager.cleanup_old_logs(retention_days=1)
        self.assertEqual(deleted, 0)


class TestCloudSyncMockCore(unittest.TestCase):
    """Unit tests for ExternalCatalogBackup (Logic from test_cloud_sync_mock.py)"""
    
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_tape = MagicMock()
        self.backup = ExternalCatalogBackup(self.mock_db, self.mock_tape)
        
    @patch('backend.external_catalog_backup.config_store.load_config')
    @patch('backend.external_catalog_backup.boto3')
    @patch('backend.external_catalog_backup.SMBClient')
    @patch('backend.external_catalog_backup.has_capability', return_value=True)
    def test_sync_catalog_to_cloud(self, mock_cap, mask_smb, mock_boto, mock_config):
        mock_config.return_value = {
            'cloud_sync': {'enabled': True, 's3': {'enabled': True, 'bucket': 'b'}, 'smb': {'enabled': True}}
        }
        with open('test_export_core.json', 'w') as f: f.write('{}')
        try:
            results = self.backup.sync_catalog_to_cloud('test_export_core.json')
            self.assertIn("Uploaded", str(results))
        finally:
            if os.path.exists('test_export_core.json'): os.remove('test_export_core.json')

if __name__ == '__main__':
    unittest.main()
