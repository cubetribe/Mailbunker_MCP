"""Tool definitions for the Mailbunker Model Context Protocol (MCP) server."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from ..config import MailbunkerConfig, load_config
from ..crypto.engine import CryptoEngine, VaultSentinel
from ..crypto.vault import EncryptedFileVault
from ..storage.database import MailbunkerDatabase
from ..storage.obsidian import ObsidianVaultExporter, format_email_markdown
from ..storage.models import SearchQuery
from ..imap.sync_manager import SyncManager


class MailbunkerContext:
    """Shared application context holding initialized database, crypto engine, and sync manager."""

    def __init__(self, config: Optional[MailbunkerConfig] = None):
        self.config = config or load_config()
        self.crypto = CryptoEngine(self.config.vault_password)
        VaultSentinel.initialize(self.config.storage_path, self.crypto)
        self.vault_files = EncryptedFileVault(self.config.attachments_path, self.crypto)
        self.db = MailbunkerDatabase(self.config.db_path, self.crypto, self.vault_files)
        self.obsidian_exporter = ObsidianVaultExporter(self.db, self.vault_files)
        self.sync_manager = SyncManager(self.config, self.db, self.obsidian_exporter)


def search_emails_impl(
    ctx: MailbunkerContext,
    query: str = "",
    account: Optional[str] = None,
    folder: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """Execute full-text and filtered search across stored emails."""
    parsed_start = datetime.fromisoformat(start_date) if start_date else None
    parsed_end = datetime.fromisoformat(end_date) if end_date else None

    search_req = SearchQuery(
        query=query,
        account=account,
        folder=folder,
        start_date=parsed_start,
        end_date=parsed_end,
        has_attachments=has_attachments,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
    )

    results = ctx.db.search_emails(search_req)
    return {
        "total": results.total,
        "limit": results.limit,
        "offset": results.offset,
        "count": len(results.results),
        "results": [r.model_dump() for r in results.results],
    }


def get_email_impl(
    ctx: MailbunkerContext,
    email_id: str,
    format: str = "markdown",
) -> Dict[str, Any]:
    """Retrieve full decrypted email by internal ID."""
    msg = ctx.db.get_email(email_id)
    if not msg:
        return {"error": f"Email with ID '{email_id}' not found."}

    if format.lower() == "markdown":
        return {
            "id": msg.id,
            "subject": msg.subject,
            "sender": str(msg.sender),
            "date": msg.date.isoformat(),
            "account": msg.account,
            "folder": msg.folder,
            "content": format_email_markdown(msg),
            "attachments": [a.model_dump() for a in msg.attachments],
        }
    elif format.lower() == "text":
        return {
            "id": msg.id,
            "subject": msg.subject,
            "sender": str(msg.sender),
            "date": msg.date.isoformat(),
            "account": msg.account,
            "folder": msg.folder,
            "content": msg.body_text or msg.body_markdown,
            "attachments": [a.model_dump() for a in msg.attachments],
        }
    else:  # json
        return {
            "id": msg.id,
            "email": msg.model_dump(),
        }


def list_accounts_impl(ctx: MailbunkerContext) -> List[Dict[str, Any]]:
    """List all configured email accounts and their statuses."""
    statuses = ctx.sync_manager.get_statuses()
    return [s.model_dump() for s in statuses]


def list_mailboxes_impl(ctx: MailbunkerContext, account_name: str) -> Dict[str, Any]:
    """List mailboxes for an account."""
    acc = next((a for a in ctx.config.accounts if a.name.lower() == account_name.lower()), None)
    if not acc:
        return {"error": f"Account '{account_name}' not found."}
    return {
        "account": acc.name,
        "configured_folders": acc.folders,
        "host": acc.host,
    }


async def sync_now_impl(
    ctx: MailbunkerContext,
    account: Optional[str] = None,
    folder: Optional[str] = None,
) -> Dict[str, Any]:
    """Trigger immediate synchronization of email accounts."""
    if account:
        acc = next((a for a in ctx.config.accounts if a.name.lower() == account.lower()), None)
        if not acc:
            return {"error": f"Account '{account}' not found."}
        if folder:
            count = await ctx.sync_manager.sync_folder(acc, folder)
            return {"account": acc.name, "folder": folder, "ingested_emails": count}
        else:
            count = await ctx.sync_manager.sync_account(acc.name)
            return {"account": acc.name, "ingested_emails": count}
    else:
        results = await ctx.sync_manager.sync_all()
        total = sum(results.values())
        return {"total_ingested": total, "accounts": results}


def get_sync_status_impl(ctx: MailbunkerContext) -> Dict[str, Any]:
    """Return real-time sync status and database stats."""
    stats = ctx.db.get_stats()
    accounts_status = [s.model_dump() for s in ctx.sync_manager.get_statuses()]
    return {
        "database_stats": stats,
        "accounts": accounts_status,
        "obsidian_vault_path": str(ctx.config.obsidian_vault_path),
        "obsidian_auto_export": ctx.config.obsidian_auto_export,
    }


def export_obsidian_vault_impl(
    ctx: MailbunkerContext,
    target_path: str,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Export all decrypted emails and attachments to an Obsidian Vault directory."""
    if password and password != ctx.config.vault_password:
        return {"error": "Invalid vault master password."}

    dest = Path(target_path).resolve()
    count = ctx.obsidian_exporter.export_all(dest)
    return {
        "status": "success",
        "target_path": str(dest),
        "exported_emails": count,
    }
