import pytest
from pathlib import Path
from mailbunker.crypto.engine import CryptoEngine, VaultSentinel
from mailbunker.crypto.vault import EncryptedFileVault


def test_crypto_engine_encryption_roundtrip():
    password = "SuperSecretPassword123!"
    crypto = CryptoEngine(password)

    # Test raw bytes
    secret_bytes = b"Hello, Zero-Trust World! Top Secret Email Content."
    encrypted = crypto.encrypt_bytes(secret_bytes)
    assert encrypted != secret_bytes
    assert encrypted.startswith(b"MB01")

    decrypted = crypto.decrypt_bytes(encrypted)
    assert decrypted == secret_bytes

    # Test string
    secret_str = "Confidential meeting at 14:00 with Project Lead"
    enc_str = crypto.encrypt_str(secret_str)
    assert enc_str != secret_str
    assert crypto.decrypt_str(enc_str) == secret_str

    # Test JSON
    data = {"subject": "Q3 Report", "amount": 42000, "tags": ["finance", "confidential"]}
    enc_json = crypto.encrypt_json(data)
    dec_json = crypto.decrypt_json(enc_json)
    assert dec_json == data


def test_crypto_engine_wrong_password_fails():
    crypto1 = CryptoEngine("correct-password-1")
    crypto2 = CryptoEngine("wrong-password-2")

    secret = "Top Secret Information"
    encrypted = crypto1.encrypt_str(secret)

    with pytest.raises(Exception):
        crypto2.decrypt_str(encrypted)


def test_crypto_engine_tampered_ciphertext_fails():
    crypto = CryptoEngine("test-password")
    encrypted = bytearray(crypto.encrypt_bytes(b"Clean Data"))

    # Tamper with the ciphertext byte
    encrypted[-1] ^= 0xFF

    with pytest.raises(Exception):
        crypto.decrypt_bytes(bytes(encrypted))


def test_vault_sentinel(tmp_path: Path):
    crypto_correct = CryptoEngine("my-master-key")
    crypto_wrong = CryptoEngine("wrong-key")

    VaultSentinel.initialize(tmp_path, crypto_correct)
    assert VaultSentinel.verify(tmp_path, crypto_correct) is True
    assert VaultSentinel.verify(tmp_path, crypto_wrong) is False


def test_encrypted_file_vault(tmp_path: Path):
    crypto = CryptoEngine("vault-pass")
    vault = EncryptedFileVault(tmp_path / "vault_storage", crypto)

    rel_path = "subfolder/document.pdf"
    content = b"%PDF-1.4 Fake encrypted PDF content"

    target_file = vault.write_bytes(rel_path, content)
    assert target_file.exists()
    # Ensure physical file content on disk is encrypted
    assert target_file.read_bytes() != content

    # Read back decrypted
    decrypted_content = vault.read_bytes(rel_path)
    assert decrypted_content == content
    assert vault.exists(rel_path) is True

    # Test text
    txt_path = "notes/secret.txt"
    vault.write_text(txt_path, "Encrypted note text")
    assert vault.read_text(txt_path) == "Encrypted note text"

    # Test path traversal prevention
    with pytest.raises(ValueError):
        vault.write_bytes("../outside.txt", b"evil")
