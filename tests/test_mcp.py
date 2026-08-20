import pytest
from pathlib import Path
from datetime import datetime, timezone

from mailbunker.config import MailbunkerConfig, AccountConfig
from mailbunker.mcp.tools import (
    MailbunkerContext,
    search_emails_impl,
    get_email_impl,
    list_accounts_impl,
    list_mailboxes_impl,
    get_sync_status_impl,
    export_obsidian_vault_impl,
)
from mailbunker.storage.models import EmailMessage, EmailAddress


@pytest.fixture
def test_ctx(tmp_path: Path):
    cfg = MailbunkerConfig(
        vault_password="mcp-test-password",
        storage_path=tmp_path / "data",
        obsidian_vault_path=tmp_path / "vault",
        accounts=[
            AccountConfig(
                id="mail_1",
                name="Work",
                host="imap.work.com",
                user="user@work.com",
                password="pass",
                folders=["INBOX", "Sent"],
            )
        ]
    )
    ctx = MailbunkerContext(cfg)

    # Insert sample email
    msg = EmailMessage(
        id="mcp_test_1",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<test@mcp.com>",
        subject="AI Agent Integration Complete",
        sender=EmailAddress(name="Agent", email="agent@corp.com"),
        to=[EmailAddress(name="Dennis", email="dennis@corp.com")],
        date=datetime(2026, 8, 20, 16, 0, 0, tzinfo=timezone.utc),
        body_text="The FastMCP tools are now fully functional and searchable.",
        body_markdown="The **FastMCP tools** are now fully functional and searchable.",
    )
    ctx.db.insert_email(msg)
    return ctx


def test_mcp_tools(test_ctx: MailbunkerContext, tmp_path: Path):
    # 1. Search
    res = search_emails_impl(test_ctx, query="FastMCP")
    assert res["total"] == 1
    assert res["results"][0]["id"] == "mcp_test_1"

    # 2. Get email (markdown)
    get_res = get_email_impl(test_ctx, "mcp_test_1", format="markdown")
    assert "FastMCP tools" in get_res["content"]

    # 3. List accounts
    accs = list_accounts_impl(test_ctx)
    assert len(accs) == 1
    assert accs[0]["name"] == "Work"

    # 4. List mailboxes
    boxes = list_mailboxes_impl(test_ctx, "Work")
    assert "INBOX" in boxes["configured_folders"]

    # 5. Sync status
    status = get_sync_status_impl(test_ctx)
    assert status["database_stats"]["total_emails"] == 1

    # 6. Export vault
    out_dir = tmp_path / "mcp_exported_vault"
    exp_res = export_obsidian_vault_impl(test_ctx, target_path=str(out_dir))
    assert exp_res["status"] == "success"
    assert exp_res["exported_emails"] == 1
    assert out_dir.exists()
