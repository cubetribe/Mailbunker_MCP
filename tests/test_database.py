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


def test_search_emails_hides_quarantined_by_default(test_db):
    db, crypto, vault = test_db

    visible = EmailMessage(
        id="email_visible",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<visible@work.example>",
        subject="Quarterly Report",
        sender=EmailAddress(name="Reports", email="reports@work.example"),
        date=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        body_text="Quarterly numbers attached.",
        body_markdown="Quarterly numbers attached.",
    )
    quarantined = EmailMessage(
        id="email_quarantined",
        account="Work",
        folder="INBOX",
        uid=2,
        message_id="<spam@work.example>",
        subject="Quarterly Winnings Notice",
        sender=EmailAddress(name="Prize", email="prize@spam.example"),
        date=datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc),
        body_text="You won a quarterly prize.",
        body_markdown="You won a quarterly prize.",
        quarantined=True,
        spam_score=0.95,
    )

    db.insert_email(visible)
    db.insert_email(quarantined)

    default_results = db.search_emails(SearchQuery(query="Quarterly"))
    assert default_results.total == 1
    assert default_results.results[0].id == "email_visible"

    included_results = db.search_emails(SearchQuery(query="Quarterly", include_quarantined=True))
    assert included_results.total == 2

    # Non-FTS structured filter must also respect the quarantine gate.
    structured = db.search_emails(SearchQuery(account="Work"))
    assert structured.total == 1
    assert structured.results[0].id == "email_visible"


def test_search_emails_snippet_highlights_actual_match(test_db):
    db, crypto, vault = test_db

    msg = EmailMessage(
        id="email_snippet",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<snippet@work.example>",
        subject="Weekly Update",
        sender=EmailAddress(name="Team", email="team@work.example"),
        date=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        body_text=(
            "This is a long padding preamble that fills more than twenty five tokens of body "
            "text before finally mentioning the unique needle term deep inside the message so "
            "that the old buggy snippet implementation would never surface it."
        ),
        body_markdown="padding",
    )
    db.insert_email(msg)

    results = db.search_emails(SearchQuery(query="needle"))
    assert results.total == 1
    snippet = results.results[0].snippet
    assert "»needle«" in snippet
    # Marker must not collide with Obsidian's own highlight syntax.
    assert "==" not in snippet


def test_insert_email_preserves_classification_on_reingest(test_db):
    db, crypto, vault = test_db

    msg = EmailMessage(
        id="email_reingest",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<reingest@work.example>",
        subject="Original Subject",
        sender=EmailAddress(name="Sender", email="sender@work.example"),
        date=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        body_text="body",
        body_markdown="body",
    )
    db.insert_email(msg)

    with db._get_conn() as conn:
        conn.execute(
            "UPDATE emails SET quarantined = 1, trust_level = 'low', classification_json = ? WHERE id = ?",
            ('{"category": "spam"}', "email_reingest"),
        )
        conn.commit()

    # Re-ingest a freshly-parsed EmailMessage (defaults: quarantined=False, trust_level=None).
    fresh_msg = EmailMessage(
        id="email_reingest",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<reingest@work.example>",
        subject="Original Subject",
        sender=EmailAddress(name="Sender", email="sender@work.example"),
        date=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        body_text="body",
        body_markdown="body",
    )
    db.insert_email(fresh_msg)

    stored = db.get_email("email_reingest")
    assert stored.quarantined is True
    assert stored.trust_level == "low"
    assert stored.classification_json == '{"category": "spam"}'

    # Explicit override path must still work when a caller wants to force a reset.
    db.insert_email(fresh_msg, preserve_classification=False)
    stored_after_override = db.get_email("email_reingest")
    assert stored_after_override.quarantined is False
    assert stored_after_override.trust_level is None


def test_insert_email_stores_and_decrypts_raw_header_blob(test_db):
    db, crypto, vault = test_db

    msg = EmailMessage(
        id="email_header_blob",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<header-blob@work.example>",
        subject="Header Blob Test",
        sender=EmailAddress(name="Sender", email="sender@work.example"),
        date=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        body_text="body",
        body_markdown="body",
        raw_header_blob="From: sender@work.example\r\nSubject: Header Blob Test\r\n",
    )
    db.insert_email(msg)

    # The DB column must not contain the plaintext header block.
    with db._get_conn() as conn:
        row = conn.execute("SELECT raw_header_blob FROM emails WHERE id = ?", ("email_header_blob",)).fetchone()
    assert row["raw_header_blob"] is not None
    assert "Header Blob Test" not in row["raw_header_blob"]

    stored = db.get_email("email_header_blob")
    assert stored.raw_header_blob == "From: sender@work.example\r\nSubject: Header Blob Test\r\n"
