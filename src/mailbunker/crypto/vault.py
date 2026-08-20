"""Encrypted file and attachment storage manager."""

from __future__ import annotations
from pathlib import Path
from typing import List, Optional
from .engine import CryptoEngine


class EncryptedFileVault:
    """Manages encrypted at-rest file storage for attachments and vault notes."""

    def __init__(self, base_dir: Path, crypto: CryptoEngine):
        self.base_dir = base_dir.resolve()
        self.crypto = crypto
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str | Path) -> Path:
        target = (self.base_dir / relative_path).resolve()
        if not str(target).startswith(str(self.base_dir)):
            raise ValueError(f"Path traversal detected: {relative_path}")
        return target

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        """Encrypt and write raw bytes to file."""
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        encrypted_data = self.crypto.encrypt_bytes(data)
        target.write_bytes(encrypted_data)
        return target

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """Read and decrypt file from disk."""
        target = self._resolve(relative_path)
        if not target.exists():
            raise FileNotFoundError(f"Encrypted file not found: {relative_path}")
        encrypted_data = target.read_bytes()
        return self.crypto.decrypt_bytes(encrypted_data)

    def write_text(self, relative_path: str | Path, text: str) -> Path:
        """Encrypt and write UTF-8 text to file."""
        return self.write_bytes(relative_path, text.encode("utf-8"))

    def read_text(self, relative_path: str | Path) -> str:
        """Read and decrypt text file."""
        return self.read_bytes(relative_path).decode("utf-8")

    def exists(self, relative_path: str | Path) -> bool:
        """Check if an encrypted file exists."""
        return self._resolve(relative_path).exists()

    def list_files(self, subfolder: str = "") -> List[Path]:
        """List all encrypted files under a subfolder."""
        target_dir = self._resolve(subfolder) if subfolder else self.base_dir
        if not target_dir.exists():
            return []
        return [p.relative_to(self.base_dir) for p in target_dir.rglob("*") if p.is_file() and not p.name.startswith(".")]
