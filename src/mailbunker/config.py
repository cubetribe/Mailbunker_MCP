"""Configuration management for Mailbunker_MCP."""

from __future__ import annotations
import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv


class AccountConfig(BaseModel):
    """Configuration for an individual email account."""
    id: str
    name: str
    host: str
    port: int = 993
    user: str
    password: str
    ssl: bool = True
    folders: List[str] = Field(default_factory=lambda: ["INBOX"])
    enabled: bool = True
    idle_enabled: bool = True


class MailbunkerConfig(BaseModel):
    """Root configuration for Mailbunker_MCP."""
    vault_password: str = Field(..., description="Master password for Zero-Trust encryption")
    storage_path: Path = Field(default=Path("./data"))
    obsidian_vault_path: Path = Field(default=Path("./obsidian_vault"))
    obsidian_auto_export: bool = False
    log_level: str = "INFO"
    sync_interval_minutes: int = 15
    accounts: List[AccountConfig] = Field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.storage_path / "mailbunker.db"

    @property
    def encrypted_vault_path(self) -> Path:
        return self.storage_path / "vault"

    @property
    def attachments_path(self) -> Path:
        return self.storage_path / "attachments"


def load_config(env_path: Optional[str | Path] = None) -> MailbunkerConfig:
    """Load configuration from environment variables and .env file."""
    if env_path:
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=False)

    vault_password = os.environ.get("VAULT_PASSWORD", "")
    if not vault_password:
        # Fallback for initial setup/testing if not set yet
        vault_password = os.environ.get("MAILBUNKER_VAULT_PASSWORD", "default-insecure-pass-please-change")

    storage_path = Path(os.environ.get("STORAGE_PATH", "./data")).resolve()
    obsidian_vault_path = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "./obsidian_vault")).resolve()
    obsidian_auto_export = os.environ.get("OBSIDIAN_AUTO_EXPORT", "false").lower() in ("true", "1", "yes")
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    sync_interval_minutes = int(os.environ.get("SYNC_INTERVAL_MINUTES", "15"))

    accounts: List[AccountConfig] = []

    # 1. Look for numbered accounts MAIL_1_*, MAIL_2_*, ..., MAIL_N_*
    account_indices = set()
    for key in os.environ:
        match = re.match(r"^MAIL_(\d+)_(?:HOST|USER|NAME)$", key, re.IGNORECASE)
        if match:
            account_indices.add(int(match.group(1)))

    for idx in sorted(account_indices):
        prefix = f"MAIL_{idx}_"
        host = os.environ.get(f"{prefix}HOST", "").strip()
        user = os.environ.get(f"{prefix}USER", "").strip()
        password = os.environ.get(f"{prefix}PASSWORD", "")
        if not host or not user:
            continue

        name = os.environ.get(f"{prefix}NAME", f"Account_{idx}").strip()
        port = int(os.environ.get(f"{prefix}PORT", "993"))
        ssl = os.environ.get(f"{prefix}SSL", "true").lower() in ("true", "1", "yes")
        enabled = os.environ.get(f"{prefix}ENABLED", "true").lower() in ("true", "1", "yes")
        
        folders_str = os.environ.get(f"{prefix}FOLDERS", "INBOX")
        folders = [f.strip() for f in folders_str.split(",") if f.strip()]
        if not folders:
            folders = ["INBOX"]

        accounts.append(
            AccountConfig(
                id=f"mail_{idx}",
                name=name,
                host=host,
                port=port,
                user=user,
                password=password,
                ssl=ssl,
                folders=folders,
                enabled=enabled,
            )
        )

    # 2. Also check single IMAP environment variables if present
    if not accounts and os.environ.get("IMAP_HOST") and os.environ.get("IMAP_USER"):
        accounts.append(
            AccountConfig(
                id="default",
                name=os.environ.get("IMAP_NAME", "Default"),
                host=os.environ.get("IMAP_HOST", ""),
                port=int(os.environ.get("IMAP_PORT", "993")),
                user=os.environ.get("IMAP_USER", ""),
                password=os.environ.get("IMAP_PASSWORD", ""),
                ssl=os.environ.get("IMAP_SSL", "true").lower() in ("true", "1", "yes"),
                folders=[f.strip() for f in os.environ.get("IMAP_FOLDERS", "INBOX").split(",") if f.strip()],
                enabled=True,
            )
        )

    return MailbunkerConfig(
        vault_password=vault_password,
        storage_path=storage_path,
        obsidian_vault_path=obsidian_vault_path,
        obsidian_auto_export=obsidian_auto_export,
        log_level=log_level,
        sync_interval_minutes=sync_interval_minutes,
        accounts=accounts,
    )
