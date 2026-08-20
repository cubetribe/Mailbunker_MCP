"""FastMCP Server for Mailbunker."""

from __future__ import annotations
import os
import sys
import logging
from typing import Optional, Dict, Any, List
from fastmcp import FastMCP

from .tools import (
    MailbunkerContext,
    search_emails_impl,
    get_email_impl,
    list_accounts_impl,
    list_mailboxes_impl,
    sync_now_impl,
    get_sync_status_impl,
    export_obsidian_vault_impl,
)

logger = logging.getLogger("mailbunker.mcp")

# Initialize FastMCP Server
mcp = FastMCP("Mailbunker", instructions="Zero-Trust Encrypted Email Archive & Search MCP Server")

_context: Optional[MailbunkerContext] = None


def get_context() -> MailbunkerContext:
    global _context
    if _context is None:
        _context = MailbunkerContext()
    return _context


@mcp.tool()
def search_emails(
    query: str = "",
    account: Optional[str] = None,
    folder: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Search indexed emails using full-text search (FTS5) and structured filters.

    Args:
        query: Free text or boolean search query (e.g. 'invoice', 'taxes AND 2025', 'project report')
        account: Filter by specific account name (e.g. 'Work', 'Personal')
        folder: Filter by mailbox folder (e.g. 'INBOX', 'Sent')
        start_date: Filter emails on or after ISO8601 date (e.g. '2025-01-01')
        end_date: Filter emails on or before ISO8601 date (e.g. '2025-12-31')
        has_attachments: Filter for emails with/without attachments
        limit: Max results to return (default 20, max 100)
        offset: Pagination offset
    """
    ctx = get_context()
    return search_emails_impl(
        ctx=ctx,
        query=query,
        account=account,
        folder=folder,
        start_date=start_date,
        end_date=end_date,
        has_attachments=has_attachments,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def get_email(
    email_id: str,
    format: str = "markdown",
) -> Dict[str, Any]:
    """
    Retrieve full decrypted content of an email by its unique internal ID.

    Args:
        email_id: The unique email ID returned from search_emails
        format: Return format - 'markdown' (formatted note), 'json' (raw model), or 'text' (plain body)
    """
    ctx = get_context()
    return get_email_impl(ctx, email_id, format=format)


@mcp.tool()
def list_accounts() -> List[Dict[str, Any]]:
    """
    List all configured email accounts, their connection statuses, and total stored emails.
    """
    ctx = get_context()
    return list_accounts_impl(ctx)


@mcp.tool()
def list_mailboxes(account_name: str) -> Dict[str, Any]:
    """
    List configured mailboxes and folders for an account.

    Args:
        account_name: Name of the account (e.g. 'Work')
    """
    ctx = get_context()
    return list_mailboxes_impl(ctx, account_name)


@mcp.tool()
async def sync_now(
    account: Optional[str] = None,
    folder: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Trigger an immediate sync with IMAP mailboxes to ingest new emails into the bunker.

    Args:
        account: Optional account name to sync. If omitted, all enabled accounts are synced.
        folder: Optional folder to sync (e.g. 'INBOX').
    """
    ctx = get_context()
    return await sync_now_impl(ctx, account=account, folder=folder)


@mcp.tool()
def get_sync_status() -> Dict[str, Any]:
    """
    Get real-time status of IMAP push IDLE listeners, total emails stored, and database statistics.
    """
    ctx = get_context()
    return get_sync_status_impl(ctx)


@mcp.tool()
def export_obsidian_vault(
    target_path: str,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Decrypt and export all stored emails and attachments into an organized Obsidian Vault directory.

    Args:
        target_path: Absolute or relative directory path where the Obsidian Vault should be created.
        password: Vault master password for validation.
    """
    ctx = get_context()
    return export_obsidian_vault_impl(ctx, target_path=target_path, password=password)


@mcp.resource("mailbunker://status")
def get_status_resource() -> str:
    """Read the current status and statistics of Mailbunker."""
    ctx = get_context()
    status_data = get_sync_status_impl(ctx)
    import json
    return json.dumps(status_data, indent=2)


def run_server():
    """Entry point for running the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
