import os
import hashlib
import json
import base64
from typing import Tuple, Optional, BinaryIO
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Constants
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for better throughput
MAX_CHUNK_SIZE = CHUNK_SIZE + 1024  # Sanity limit for decryption (DoS protection)
HEADER_v1 = b'FOSSILSAFE_v1:'

class EncryptionManager:
    """
    Handles zero-knowledge encryption using AES-256-GCM.
    
    Format:
    [Header][Salt (16)][Nonce (12)][Ciphertext_Chunk_1][Tag_1][Nonce_2]...
    
    Streaming GCM strategy:
    To support large files without loading them entirely into memory, we must
    encrypt/decrypt in chunks. Each chunk is effectively a separate GCM message 
    with its own distinct nonce (incremented or random).
    
    Structure v1:
    - Header: 'FOSSILSAFE_v1:' (UTF-8 bytes)
    - Master Salt: 16 bytes (used to derive Key from Passphrase)
    - For each chunk:
        - Nonce: 12 bytes
        - Ciphertext: N bytes
        - Tag: 16 bytes
    """
    
    def __init__(self, key: bytes, salt: bytes = None):
        """
        Initialize with a raw 32-byte key (AES-256).
        Optionally pass salt to be written to headers.
        """
        if len(key) != 32:
            raise ValueError("Key must be exactly 32 bytes for AES-256")
        self.key = key
        self.salt = salt
        self.aesgcm = AESGCM(key)

    @staticmethod
    def derive_key(passphrase: str, salt: Optional[bytes] = None) -> Tuple[bytes, bytes]:
        """
        Derive a 32-byte key from a passphrase using Scrypt.
        Returns (key, salt).
        """
        if salt is None:
            salt = os.urandom(SALT_SIZE)
            
        kdf = Scrypt(
            salt=salt,
            length=32,
            n=2**14,
            r=8,
            p=1,
        )
        key = kdf.derive(passphrase.encode('utf-8'))
        return key, salt

    def encrypt_file(self, input_path: str, output_path: str) -> None:
        """Encrypts a file and writes to output_path."""
        with open(input_path, 'rb') as fin, open(output_path, 'wb') as fout:
            self.encrypt_stream(fin, fout)

    def decrypt_file(self, input_path: str, output_path: str) -> None:
        """Decrypts a file and writes to output_path."""
        with open(input_path, 'rb') as fin, open(output_path, 'wb') as fout:
            self.decrypt_stream(fin, fout)

    def encrypt_stream(self, reader: BinaryIO, writer: BinaryIO, salt: bytes = None) -> None:
        """Encrypts data from reader and writes to writer."""
        return self._encrypt_stream_with_len(reader, writer, salt if salt else self.salt)

    def decrypt_stream(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Decrypts data from reader and writes to writer."""
        return self._decrypt_stream_with_len(reader, writer)
            
    def _encrypt_stream_with_len(self, reader: BinaryIO, writer: BinaryIO, salt: bytes = None) -> None:
        """
        Robust stream encryption with length framing and embedded salt.
        Structure: [Header][Salt(16)][ChunkLength][Nonce][Ciphertext]...
        """
        writer.write(HEADER_v1)
        
        if salt is None:
            salt = b'\x00' * SALT_SIZE
            
        if len(salt) != SALT_SIZE:
             raise ValueError(f"Salt must be {SALT_SIZE} bytes")
             
        writer.write(salt)
        
        while True:
            chunk = reader.read(CHUNK_SIZE)
            if not chunk:
                break
                
            nonce = os.urandom(NONCE_SIZE)
            ciphertext = self.aesgcm.encrypt(nonce, chunk, None) # len = chunk + 16
            
            # Format: [Length (4 bytes big endian)][Nonce (12)][Ciphertext (N + 16)]
            length = len(ciphertext)
            writer.write(length.to_bytes(4, byteorder='big'))
            writer.write(nonce)
            writer.write(ciphertext)

    def _decrypt_stream_with_len(self, reader: BinaryIO, writer: BinaryIO) -> Optional[bytes]:
        """
        Robust stream decryption. Returns the salt read from header.
        """
        # Read Header
        header = reader.read(len(HEADER_v1))
        if header != HEADER_v1:
             # Safety check: if empty, maybe empty file?
             if not header:
                 return None
             raise ValueError(f"Invalid header. Expected {HEADER_v1}, got {header[:20]}")
             
        # Read Salt
        file_salt = reader.read(SALT_SIZE)
        
        # We process the rest
        while True:
            # Read Length
            len_bytes = reader.read(4)
            if not len_bytes:
                break # EOF
            if len(len_bytes) < 4:
                raise ValueError("Truncated stream (length)")
                
            length = int.from_bytes(len_bytes, byteorder='big')
            
            # DoS Protection: Check if length is reasonable
            if length > MAX_CHUNK_SIZE:
                raise ValueError(f"Chunk size {length} exceeds maximum allowed {MAX_CHUNK_SIZE}. Possible corrupted file or attack.")
            
            # Read Nonce
            nonce = reader.read(NONCE_SIZE)
            if len(nonce) < NONCE_SIZE:
                 raise ValueError("Truncated stream (nonce)")
                 
            # Read Ciphertext
            ciphertext = reader.read(length)
            if len(ciphertext) < length:
                 raise ValueError("Truncated stream (ciphertext)")
                 
            # Decrypt
            plaintext = self.aesgcm.decrypt(nonce, ciphertext, None)
            writer.write(plaintext)
            
        return file_salt

    @staticmethod
    def read_salt(input_path: str) -> bytes:
        """Extract salt from encrypted file header."""
        with open(input_path, 'rb') as f:
            header = f.read(len(HEADER_v1))
            if header != HEADER_v1:
                raise ValueError(f"Invalid header. Expected {HEADER_v1}")
            salt = f.read(SALT_SIZE)
            if len(salt) < SALT_SIZE:
                raise ValueError("File too short")
            return salt
