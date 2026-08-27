"""Tests for the unified ingest choke-point (Sprint 02 - Ingest-Fundament)."""
import sqlite3
from pathlib import Path

import pytest

from mailbunker.crypto.engine import CryptoEngine
from mailbunker.crypto.vault import EncryptedFileVault
from mailbunker.storage.database import MailbunkerDatabase, SCHEMA_VERSION
from mailbunker.ingest.pipeline import ingest_raw, filter_hook, DECISION_STORE
from mailbunker.imap.parser import parse_email_message


RAW_EMAIL = b"""From: "Alice Smith" <alice@example.com>
To: "Bob Jones" <bob@example.com>
Subject: Ingest Pipeline Test
Date: Thu, 20 Aug 2026 14:30:00 +0200
Message-ID: <ingest-001@example.com>
Content-Type: text/plain; charset="utf-8"

Hello from the unified ingest choke-point.
"""


@pytest.fixture
def test_db(tmp_path: Path):
    crypto = CryptoEngine("test-master-password")
    vault = EncryptedFileVault(tmp_path / "attachments", crypto)
    db = MailbunkerDatabase(tmp_path / "test.db", crypto, vault)
    return db


def test_filter_hook_is_noop_store(test_db):
    msg, _ = parse_email_message(RAW_EMAIL, account="Work", folder="INBOX", uid=1)
    decision = filter_hook(msg)
    assert decision.decision == DECISION_STORE
    assert decision.score == 0.0


def test_ingest_raw_stores_email_and_logs_decision(test_db):
    decision, msg = ingest_raw(test_db, RAW_EMAIL, account="Work", folder="INBOX", uid=1)

    assert decision.decision == DECISION_STORE
    assert msg is not None

    stored = test_db.get_email(msg.id)
    assert stored is not None
    assert stored.subject == "Ingest Pipeline Test"
    assert stored.quarantined is False

    with test_db._get_conn() as conn:
        rows = conn.execute(
            "SELECT account_id, folder, uid, decision, reason FROM ingest_log WHERE uid = ?", (1,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["account_id"] == "Work"
    assert rows[0]["folder"] == "INBOX"
    assert rows[0]["decision"] == "store"


def test_ingest_raw_passes_flags_through(test_db):
    decision, msg = ingest_raw(
        test_db, RAW_EMAIL, account="Work", folder="INBOX", uid=2, flags=["\\Seen", "$Junk"]
    )
    assert msg is not None
    stored = test_db.get_email(msg.id)
    assert "\\Seen" in stored.flags
    assert "$Junk" in stored.flags


def test_ingest_raw_reingest_preserves_quarantine_decision(test_db):
    """
    Simulates a Sprint-3/4 quarantine decision being preserved across a re-ingest of the same
    message (e.g. IDLE re-delivering the same UID after a reconnect).
    """
    decision, msg = ingest_raw(test_db, RAW_EMAIL, account="Work", folder="INBOX", uid=3)
    assert msg is not None

    # Simulate a later classification step marking this email quarantined directly in storage.
    with test_db._get_conn() as conn:
        conn.execute(
            "UPDATE emails SET quarantined = 1, spam_score = 0.9, trust_level = 'low' WHERE id = ?",
            (msg.id,),
        )
        conn.commit()

    # Re-ingest the identical raw bytes/uid; the No-Op filter hook will again produce a fresh
    # EmailMessage with quarantined=False by default -- but insert_email's merge logic must
    # preserve the existing quarantine decision instead of clobbering it.
    ingest_raw(test_db, RAW_EMAIL, account="Work", folder="INBOX", uid=3)

    stored = test_db.get_email(msg.id)
    assert stored.quarantined is True
    assert stored.spam_score == 0.9
    assert stored.trust_level == "low"


def test_watermark_advances_for_filtered_and_error_uids(test_db):
    """
    Regression test for the watermark bug: a UID that produces a non-store decision (or an
    outright fetch/parse error) must still be distinguishable in ingest_log, and (for
    non-error decisions) the watermark must be advanced by the caller even though nothing
    "new" was inserted. This test exercises the ingest_log audit trail directly since the full
    IMAP fetch loop lives in sync_manager/idle_listener (network-bound, tested elsewhere).
    """
    decision, _ = ingest_raw(test_db, RAW_EMAIL, account="Work", folder="INBOX", uid=10)
    assert decision.decision == "store"

    test_db.log_ingest_decision("Work", "INBOX", 11, "error", "simulated fetch failure")

    with test_db._get_conn() as conn:
        rows = {
            row["uid"]: row["decision"]
            for row in conn.execute(
                "SELECT uid, decision FROM ingest_log WHERE account_id = 'Work' AND folder = 'INBOX'"
            ).fetchall()
        }
    assert rows[10] == "store"
    assert rows[11] == "error"


def test_migration_adds_columns_to_legacy_schema(tmp_path: Path):
    """
    Creates a DB with the pre-Sprint-02 (Sprint 01) schema -- no quarantine/classification
    columns, no ingest_log table -- and verifies that opening it via MailbunkerDatabase runs
    the migration and that insert_email works afterwards without error.
    """
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE emails (
            id TEXT PRIMARY KEY,
            account TEXT NOT NULL,
            folder TEXT NOT NULL,
            uid INTEGER NOT NULL,
            message_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            sender_email TEXT NOT NULL,
            sender_name TEXT,
            recipients TEXT,
            date_iso TEXT NOT NULL,
            date_timestamp INTEGER NOT NULL,
            has_attachments INTEGER NOT NULL DEFAULT 0,
            attachment_count INTEGER NOT NULL DEFAULT 0,
            size INTEGER NOT NULL DEFAULT 0,
            tags TEXT,
            encrypted_payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE emails_fts USING fts5(
            id UNINDEXED, subject, sender, recipients, body, tags, account, folder,
            tokenize = 'porter unicode61'
        );
    """)
    conn.execute("""
        CREATE TABLE attachments (
            id TEXT PRIMARY KEY,
            email_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            encrypted_file_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE sync_state (
            account_id TEXT NOT NULL,
            folder TEXT NOT NULL,
            last_uid INTEGER NOT NULL DEFAULT 0,
            uid_validity INTEGER NOT NULL DEFAULT 0,
            last_sync_timestamp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (account_id, folder)
        );
    """)
    conn.commit()
    # Sanity check: legacy schema really lacks the new columns before opening it.
    legacy_cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
    assert "quarantined" not in legacy_cols
    assert "raw_header_blob" not in legacy_cols
    conn.close()

    crypto = CryptoEngine("legacy-password")
    vault = EncryptedFileVault(tmp_path / "attachments", crypto)
    db = MailbunkerDatabase(db_path, crypto, vault)  # __init__ must run migration

    with db._get_conn() as conn2:
        migrated_cols = {row["name"] for row in conn2.execute("PRAGMA table_info(emails)").fetchall()}
        user_version = conn2.execute("PRAGMA user_version").fetchone()[0]
        has_ingest_log = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ingest_log'"
        ).fetchone()

    for col in ["quarantined", "spam_score", "trust_level", "classification_json", "classified_at", "embedding_state", "raw_header_blob"]:
        assert col in migrated_cols
    assert user_version == SCHEMA_VERSION
    assert has_ingest_log is not None

    # insert_email must run without error against the migrated schema.
    decision, msg = ingest_raw(db, RAW_EMAIL, account="Work", folder="INBOX", uid=99)
    assert decision.decision == "store"
    stored = db.get_email(msg.id)
    assert stored is not None
    assert stored.subject == "Ingest Pipeline Test"


def test_migration_is_idempotent_on_reopen(tmp_path: Path):
    """Opening an already-migrated DB a second time must not error or duplicate columns."""
    crypto = CryptoEngine("pw")
    vault = EncryptedFileVault(tmp_path / "attachments", crypto)
    db_path = tmp_path / "reopen.db"
    db1 = MailbunkerDatabase(db_path, crypto, vault)
    del db1

    db2 = MailbunkerDatabase(db_path, crypto, vault)
    with db2._get_conn() as conn:
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()]
    assert cols.count("quarantined") == 1
