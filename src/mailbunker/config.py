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


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_list(key: str, default: List[str]) -> List[str]:
    raw = os.environ.get(key)
    if not raw:
        return list(default)
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or list(default)


# --- Sprint 03: hard-coded filter-layer thresholds, limits, and denylists --------------------
#
# Read once at module import time from the environment (consistent with the rest of this
# module's plain `os.environ` reads). These are deliberately module-level constants rather than
# fields on `MailbunkerConfig`/`AccountConfig`: `ingest.pipeline.filter_hook(email_msg)` is a
# fixed single-argument choke-point contract from Sprint 02 that must not change signature (no
# call-site changes in `sync_manager.py`/`idle_listener.py`), so the filter layer
# (`ingest/filters.py`) imports these directly from `mailbunker.config` instead of receiving a
# config object.
#
# Policy (per plans/v0.2.0/sprint-03-filter-layers.md "Risks"): False-positives must never
# silently delete mail. `SPAM_BLOCK_THRESHOLD` alone is NOT sufficient to reach `block` -- the
# filter layer additionally requires an explicit hard-evidence combination (provider Junk flag +
# auth failure, optionally + attachment denylist hit) before blocking. Score-only high-confidence
# spam without that provider evidence is quarantined, never blocked.
SPAM_QUARANTINE_THRESHOLD = _env_float("SPAM_QUARANTINE_THRESHOLD", 3.0)
SPAM_BLOCK_THRESHOLD = _env_float("SPAM_BLOCK_THRESHOLD", 7.0)

# Folders that are never synced at all (provider-side spam/trash folders -- syncing them would
# defeat the point of provider-evidence filtering by re-ingesting what the provider already
# flagged and the user already deleted).
FOLDER_DENYLIST = _env_list(
    "FOLDER_DENYLIST",
    ["Junk", "Spam", "Trash", "Deleted Items", "[Gmail]/Spam", "[Gmail]/Trash", "[Gmail]/Bin"],
)

# Resource/anomaly limits. Generous defaults (most legitimate mail is far below these) -- these
# are signals contributing to the score, not hard rejections, so an oversized-but-legitimate
# attachment is never silently dropped.
MAX_MESSAGE_SIZE_BYTES = _env_int("MAX_MESSAGE_SIZE_BYTES", 50_000_000)  # 50 MB raw RFC822 size
MAX_ATTACHMENT_SIZE_BYTES = _env_int("MAX_ATTACHMENT_SIZE_BYTES", 35_000_000)  # ~25MB w/ base64 overhead

# Attachment filename extensions treated as executable/dangerous. Checked against *every*
# dot-separated suffix of a filename (not just the last one) so an already-denylisted double
# extension like "invoice.pdf.exe" is caught even if a future caller only inspects the final
# suffix elsewhere.
ATTACHMENT_TYPE_DENYLIST = _env_list(
    "ATTACHMENT_TYPE_DENYLIST",
    [".exe", ".scr", ".js", ".vbs", ".vbe", ".bat", ".cmd", ".com", ".pif", ".jar", ".msi", ".ps1", ".wsf", ".jse", ".hta"],
)


# --- Sprint 04: Ollama classifier (Stufe 4) configuration -----------------------------------
#
# Read once at module import time from the environment, matching the Sprint 03 pattern above.
# `classify/client.py`/`classify/worker.py` import these directly instead of receiving a config
# object, for the same reason: they are called from the standalone `mailbunker classify` CLI
# command (never from the IDLE push path), so there is no single shared config-object call site
# to thread a new parameter through.
#
# `CLASSIFY_ENABLED` defaults to False: classification is an explicit opt-in, offline-optional
# feature -- a fresh install without Ollama running must never behave differently than before
# this sprint. `mailbunker classify` still works ad-hoc without flipping this flag (the CLI
# command itself is the explicit opt-in for a manual/scripted run); the flag exists for future
# scheduled/automatic invocations (e.g. a cron-style background worker) to check.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_CLASSIFIER_MODEL = os.environ.get("OLLAMA_CLASSIFIER_MODEL", "gemma3:4b")
CLASSIFY_BATCH_SIZE = _env_int("CLASSIFY_BATCH_SIZE", 25)
CLASSIFY_CONFIDENCE_THRESHOLD = _env_float("CLASSIFY_CONFIDENCE_THRESHOLD", 0.55)
CLASSIFY_ENABLED = os.environ.get("CLASSIFY_ENABLED", "false").lower() in ("true", "1", "yes")


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
