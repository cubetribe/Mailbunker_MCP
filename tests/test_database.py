import pytest
from pathlib import Path
from datetime import datetime, timezone

from mailbunker.crypto.engine import CryptoEngine
from mailbunker.crypto.vault import EncryptedFileVault
from mailbunker.storage.database import MailbunkerDatabase
from mailbunker.storage.models import EmailMessage, EmailAddress, AttachmentMeta, SearchQuery


@pytest.fixture
def test_db(tmp_path: Path):
    crypto = CryptoEngine("test-master-password")
    vault = EncryptedFileVault(tmp_path / "attachments", crypto)
    db = MailbunkerDatabase(tmp_path / "test.db", crypto, vault)
    return db, crypto, vault


def test_database_insert_and_get(test_db):
    db, crypto, vault = test_db

    msg = EmailMessage(
        id="email_1",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<msg-001@work.example>",
        subject="Confidential Budget 2026",
        sender=EmailAddress(name="Finance Team", email="finance@work.example"),
        to=[EmailAddress(name="User", email="user@work.example")],
        date=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        body_text="Here is the confidential quarterly budget outline.",
        body_markdown="Here is the **confidential quarterly budget** outline.",
        tags=["email", "account/work", "folder/inbox"],
    )

    attach_meta = AttachmentMeta(
        filename="budget_2026.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size=1024,
        sha256="abc123sha256fake",
    )
    raw_attach_bytes = b"fake-excel-data-bytes"

    inserted_id = db.insert_email(msg, [(attach_meta, raw_attach_bytes)])
    assert inserted_id == "email_1"

    # Test exists
    assert db.exists("Work", "INBOX", 1, "<msg-001@work.example>") is True
    assert db.exists("Work", "INBOX", 99, "<non-existent>") is False

    # Retrieve and decrypt
    retrieved = db.get_email("email_1")
    assert retrieved is not None
    assert retrieved.subject == "Confidential Budget 2026"
    assert retrieved.sender.email == "finance@work.example"
    assert retrieved.body_markdown == "Here is the **confidential quarterly budget** outline."
    assert len(retrieved.attachments) == 1
    assert retrieved.attachments[0].filename == "budget_2026.xlsx"


def test_database_fts5_search(test_db):
    db, crypto, vault = test_db

    # Insert two emails
    msg1 = EmailMessage(
        id="email_1",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<msg-001@work.example>",
        subject="Project Alpha Architecture",
        sender=EmailAddress(name="Architect", email="architect@work.example"),
        to=[EmailAddress(name="Recipient", email="user@work.example")],
        date=datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc),
        body_text="The system uses zero-trust security and SQLite FTS5 for fast searching.",
        body_markdown="The system uses zero-trust security and SQLite FTS5 for fast searching.",
    )

    msg2 = EmailMessage(
        id="email_2",
        account="Personal",
        folder="INBOX",
        uid=2,
        message_id="<msg-002@personal.example>",
        subject="Weekend Hiking Trip",
        sender=EmailAddress(name="Friend", email="friend@personal.example"),
        to=[EmailAddress(name="Recipient", email="user@personal.example")],
        date=datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc),
        body_text="Let's go hiking in the mountains this Saturday.",
        body_markdown="Let's go hiking in the mountains this Saturday.",
    )

    db.insert_email(msg1)
    db.insert_email(msg2)

    # Search for "zero-trust"
    res1 = db.search_emails(SearchQuery(query="zero-trust"))
    assert res1.total == 1
    assert res1.results[0].id == "email_1"

    # Search for "hiking"
    res2 = db.search_emails(SearchQuery(query="hiking"))
    assert res2.total == 1
    assert res2.results[0].id == "email_2"

    # Filter by account
    res3 = db.search_emails(SearchQuery(account="Work"))
    assert res3.total == 1
    assert res3.results[0].account == "Work"

    # Stats
    stats = db.get_stats()
    assert stats["total_emails"] == 2
