"""Tool definitions for the Mailbunker Model Context Protocol (MCP) server."""

from __future__ import annotations
import hmac
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
from ..security.sanitize import neutralize_untrusted, UNTRUSTED_CONTENT_NOTICE


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
    category: Optional[str] = None,
    spam_verdict: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute full-text and filtered search across stored emails.

    `category`/`spam_verdict` (Sprint 04) are additive, optional structured filters over the
    Ollama classifier's output columns -- omitting them preserves the exact pre-Sprint-04
    behavior.
    """
    try:
        parsed_start = datetime.fromisoformat(start_date) if start_date else None
        parsed_end = datetime.fromisoformat(end_date) if end_date else None
    except ValueError as e:
        return {"error": f"Invalid date format: {e}"}

    search_req = SearchQuery(
        query=query,
        account=account,
        folder=folder,
        start_date=parsed_start,
        end_date=parsed_end,
        has_attachments=has_attachments,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
        category=category,
        spam_verdict=spam_verdict,
    )

    try:
        results = ctx.db.search_emails(search_req)
    except Exception as e:
        return {"error": f"Search failed: {e}"}

    sanitized_results = []
    for r in results.results:
        item = r.model_dump()
        subject, subject_truncated = neutralize_untrusted(item.get("subject"), return_truncated=True)
        sender, sender_truncated = neutralize_untrusted(item.get("sender"), return_truncated=True)
        snippet, snippet_truncated = neutralize_untrusted(item.get("snippet"), return_truncated=True)
        item["subject"] = subject
        item["sender"] = sender
        item["snippet"] = snippet
        item["tags"] = [neutralize_untrusted(t) for t in item.get("tags", [])]
        if subject_truncated or sender_truncated or snippet_truncated:
            item["truncated"] = True
        sanitized_results.append(item)

    return {
        "total": results.total,
        "limit": results.limit,
        "offset": results.offset,
        "count": len(results.results),
        "results": sanitized_results,
        "_notice": UNTRUSTED_CONTENT_NOTICE,
    }


def get_email_impl(
    ctx: MailbunkerContext,
    email_id: str,
    format: str = "markdown",
) -> Dict[str, Any]:
    """Retrieve full decrypted email by internal ID."""
    try:
        msg = ctx.db.get_email(email_id)
    except Exception as e:
        return {"error": f"Failed to retrieve email '{email_id}': {e}"}

    if not msg:
        return {"error": f"Email with ID '{email_id}' not found."}

    if format.lower() == "markdown":
        subject, subject_trunc = neutralize_untrusted(msg.subject, return_truncated=True)
        sender, sender_trunc = neutralize_untrusted(str(msg.sender), return_truncated=True)
        content, content_trunc = neutralize_untrusted(format_email_markdown(msg), return_truncated=True)
        resp = {
            "id": msg.id,
            "subject": subject,
            "sender": sender,
            "date": msg.date.isoformat(),
            "account": msg.account,
            "folder": msg.folder,
            "content": content,
            "attachments": [a.model_dump() for a in msg.attachments],
            "_notice": UNTRUSTED_CONTENT_NOTICE,
        }
        if subject_trunc or sender_trunc or content_trunc:
            resp["truncated"] = True
        return resp
    elif format.lower() == "text":
        subject, subject_trunc = neutralize_untrusted(msg.subject, return_truncated=True)
        sender, sender_trunc = neutralize_untrusted(str(msg.sender), return_truncated=True)
        content, content_trunc = neutralize_untrusted(
            msg.body_text or msg.body_markdown, return_truncated=True
        )
        resp = {
            "id": msg.id,
            "subject": subject,
            "sender": sender,
            "date": msg.date.isoformat(),
            "account": msg.account,
            "folder": msg.folder,
            "content": content,
            "attachments": [a.model_dump() for a in msg.attachments],
            "_notice": UNTRUSTED_CONTENT_NOTICE,
        }
        if subject_trunc or sender_trunc or content_trunc:
            resp["truncated"] = True
        return resp
    else:  # json
        # raw_header_blob is unsanitized, attacker-influenced raw MIME header text (Sprint 02)
        # and must never leave the vault via MCP -- excluding it here preserves the Sprint 01
        # hardening. embedding_state is purely internal worker bookkeeping, not user-facing data.
        # hidden_text (Sprint 03) is the deliberately-isolated, human-invisible HTML-injection
        # payload split out by imap/parser.py -- the whole point of isolating it is that it must
        # never reach an LLM/consumer, so it must never leave the vault via MCP either.
        email_dict = msg.model_dump(
            exclude={"body_html", "raw_headers", "raw_header_blob", "embedding_state", "hidden_text"}
        )
        subject, subject_trunc = neutralize_untrusted(email_dict.get("subject"), return_truncated=True)
        body_text, body_text_trunc = neutralize_untrusted(email_dict.get("body_text"), return_truncated=True)
        body_markdown, body_markdown_trunc = neutralize_untrusted(
            email_dict.get("body_markdown"), return_truncated=True
        )
        email_dict["subject"] = subject
        email_dict["body_text"] = body_text
        email_dict["body_markdown"] = body_markdown

        # Sprint 04: surface the classifier's one-sentence summary as a first-class field.
        # `summary` only lives inside `classification_json` (no dedicated column) -- it was
        # already neutralized by classify/classifier.py before being persisted, so this is a
        # convenience extraction, not a new trust boundary.
        summary = None
        if msg.classification_json:
            try:
                summary = json.loads(msg.classification_json).get("summary") or None
            except (ValueError, TypeError):
                summary = None
        email_dict["summary"] = summary
        resp = {
            "id": msg.id,
            "email": email_dict,
            "_notice": UNTRUSTED_CONTENT_NOTICE,
        }
        if subject_trunc or body_text_trunc or body_markdown_trunc:
            resp["truncated"] = True
        return resp


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
    """Export all decrypted emails and attachments to an Obsidian Vault directory.

    Password is mandatory: this operation decrypts the entire archive to plaintext on
    disk, so an unauthenticated/absent-password call must never proceed. The target
    path is additionally constrained to a configured allowlist of roots to prevent
    exfiltration to arbitrary filesystem locations (e.g. triggered by an injected
    instruction embedded in email content).
    """
    if not password:
        return {"error": "Vault master password is required to export decrypted content."}

    # Fail closed: compare as UTF-8 bytes and never let a comparison error (e.g. from an
    # unexpected type/encoding) crash the tool call or fall through to a successful export.
    try:
        password_matches = hmac.compare_digest(
            password.encode("utf-8"), ctx.config.vault_password.encode("utf-8")
        )
    except Exception:
        password_matches = False

    if not password_matches:
        return {"error": "Invalid vault master password."}

    allowed_roots = getattr(ctx.config, "export_allowed_roots", None) or [ctx.config.obsidian_vault_path]
    resolved_roots = [Path(r).resolve() for r in allowed_roots]

    dest = Path(target_path).resolve()
    is_allowed = any(dest == root or root in dest.parents for root in resolved_roots)
    if not is_allowed:
        return {
            "error": (
                f"Target path '{dest}' is outside the allowed export roots "
                f"({[str(r) for r in resolved_roots]})."
            )
        }

    count = ctx.obsidian_exporter.export_all(dest)
    return {
        "status": "success",
        "target_path": str(dest),
        "exported_emails": count,
    }
