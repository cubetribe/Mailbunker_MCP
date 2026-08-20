"""Zero-Trust Cryptographic Engine using AES-256-GCM and Argon2id key derivation."""

from __future__ import annotations
import os
import json
import base64
from pathlib import Path
from typing import Optional, Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import argon2
from argon2.low_level import hash_secret_raw, Type


MAGIC_HEADER = b"MB01"  # Mailbunker v1 format
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16


class CryptoEngine:
    """Zero-Trust Cryptographic Engine for AES-256-GCM encryption & decryption."""

    def __init__(self, password: str, salt: Optional[bytes] = None):
        if not password:
            raise ValueError("Vault password cannot be empty.")
        self.password = password
        self.salt = salt if salt is not None else os.urandom(SALT_LEN)
        self._key = self._derive_key(self.password, self.salt)
        self._aesgcm = AESGCM(self._key)

    @classmethod
    def _derive_key(cls, password: str, salt: bytes) -> bytes:
        """Derive a 256-bit symmetric key from password and salt using Argon2id."""
        try:
            # Argon2id with 64MB memory, 3 iterations, 4 parallelism
            return hash_secret_raw(
                secret=password.encode("utf-8"),
                salt=salt,
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
                hash_len=32,
                type=Type.ID
            )
        except Exception:
            # Fallback to PBKDF2-HMAC-SHA256 (600,000 rounds) if Argon2id has C-level issue
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            return kdf.derive(password.encode("utf-8"))

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        """
        Encrypt raw bytes with AES-256-GCM.
        Format: MAGIC(4) + SALT(16) + NONCE(12) + CIPHERTEXT+TAG
        """
        nonce = os.urandom(NONCE_LEN)
        # We include MAGIC + SALT + NONCE in the output
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, associated_data=MAGIC_HEADER)
        return MAGIC_HEADER + self.salt + nonce + ciphertext

    def decrypt_bytes(self, data: bytes) -> bytes:
        """
        Decrypt raw bytes previously encrypted with encrypt_bytes.
        Verifies MAGIC header, salt, nonce, and AES-GCM auth tag.
        """
        if len(data) < len(MAGIC_HEADER) + SALT_LEN + NONCE_LEN + TAG_LEN:
            raise ValueError("Ciphertext too short to be valid Mailbunker encrypted data.")

        magic = data[:4]
        if magic != MAGIC_HEADER:
            raise ValueError(f"Invalid magic header: {magic!r}")

        salt = data[4:20]
        nonce = data[20:32]
        ciphertext = data[32:]

        # If stored salt differs from initialized salt, derive key with record salt
        if salt == self.salt:
            aes = self._aesgcm
        else:
            record_key = self._derive_key(self.password, salt)
            aes = AESGCM(record_key)

        return aes.decrypt(nonce, ciphertext, associated_data=MAGIC_HEADER)

    def encrypt_str(self, text: str) -> str:
        """Encrypt string and return Base64-encoded string."""
        raw_encrypted = self.encrypt_bytes(text.encode("utf-8"))
        return base64.b64encode(raw_encrypted).decode("ascii")

    def decrypt_str(self, encoded: str) -> str:
        """Decrypt Base64-encoded ciphertext string."""
        data = base64.b64decode(encoded.encode("ascii"))
        return self.decrypt_bytes(data).decode("utf-8")

    def encrypt_json(self, obj: Any) -> str:
        """Serialize object to JSON and encrypt."""
        json_str = json.dumps(obj, default=str)
        return self.encrypt_str(json_str)

    def decrypt_json(self, encoded: str) -> Any:
        """Decrypt ciphertext and parse as JSON."""
        decrypted_str = self.decrypt_str(encoded)
        return json.loads(decrypted_str)


class VaultSentinel:
    """Manages vault verification token to validate password without decrypting full data."""

    SENTINEL_FILE = ".vault_sentinel"
    SENTINEL_MAGIC_PHRASE = "MAILBUNKER_ZERO_TRUST_OK"

    @classmethod
    def initialize(cls, storage_dir: Path, crypto: CryptoEngine) -> None:
        """Create or verify sentinel file in storage directory."""
        storage_dir.mkdir(parents=True, exist_ok=True)
        sentinel_path = storage_dir / cls.SENTINEL_FILE
        if not sentinel_path.exists():
            encrypted = crypto.encrypt_str(cls.SENTINEL_MAGIC_PHRASE)
            sentinel_path.write_text(encrypted, encoding="utf-8")

    @classmethod
    def verify(cls, storage_dir: Path, crypto: CryptoEngine) -> bool:
        """Verify that the crypto engine's password can unlock the storage directory."""
        sentinel_path = storage_dir / cls.SENTINEL_FILE
        if not sentinel_path.exists():
            # Not initialized yet
            return True
        try:
            content = sentinel_path.read_text(encoding="utf-8").strip()
            decrypted = crypto.decrypt_str(content)
            return decrypted == cls.SENTINEL_MAGIC_PHRASE
        except Exception:
            return False
