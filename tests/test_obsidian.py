import re
import yaml
from pathlib import Path
from datetime import datetime, timezone

from mailbunker.crypto.engine import CryptoEngine
from mailbunker.crypto.vault import EncryptedFileVault
from mailbunker.storage.database import MailbunkerDatabase
from mailbunker.storage.models import EmailMessage, EmailAddress, AttachmentMeta
from mailbunker.storage.obsidian import format_email_markdown, ObsidianVaultExporter


def test_format_email_markdown():
    msg = EmailMessage(
        id="msg123",
        account="Work",
        folder="INBOX",
        uid=55,
        message_id="<abc@def.example>",
        subject="Sprint Planning",
        sender=EmailAddress(name="Scrum Master", email="scrum@example.com"),
        to=[EmailAddress(name="Team", email="team@example.com")],
        date=datetime(2026, 8, 20, 9, 30, 0, tzinfo=timezone.utc),
        in_reply_to="<parent@def.example>",
        body_markdown="### Agenda\n- Goal 1\n- Goal 2",
        attachments=[AttachmentMeta(filename="sprint.pdf", content_type="application/pdf", size=5000, sha256="123")],
    )

    md = format_email_markdown(msg)
    assert md.startswith("---\n")
    assert "Sprint Planning" in md
    assert "scrum@example.com" in md
    assert "In Reply To:" in md
    assert "sprint.pdf" in md
    assert "### Agenda" in md

    # Validate YAML frontmatter using strict leading-delimiter extraction (a subject or body
    # containing a literal "---" line must not be mistaken for the frontmatter boundary).
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", md, re.DOTALL)
    assert frontmatter_match is not None
    parsed_yaml = yaml.safe_load(frontmatter_match.group(1))
    assert parsed_yaml["id"] == "msg123"
    assert parsed_yaml["subject"] == "Sprint Planning"
    assert parsed_yaml["has_attachments"] is True


def test_obsidian_vault_exporter(tmp_path: Path):
    crypto = CryptoEngine("vault-pass")
    vault = EncryptedFileVault(tmp_path / "enc_vault", crypto)
    db = MailbunkerDatabase(tmp_path / "mail.db", crypto, vault)

    msg = EmailMessage(
        id="test_mail_1",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<test@work.example>",
        subject="Important Contract",
        sender=EmailAddress(name="Legal", email="legal@work.example"),
        to=[EmailAddress(name="Client", email="client@work.example")],
        date=datetime(2026, 8, 20, 11, 0, 0, tzinfo=timezone.utc),
        body_markdown="Please find the attached agreement.",
    )
    attach_meta = AttachmentMeta(
        filename="contract.pdf",
        content_type="application/pdf",
        size=100,
        sha256="hash123",
    )
    db.insert_email(msg, [(attach_meta, b"%PDF-Agreement-Data")])

    exporter = ObsidianVaultExporter(db, vault)
    out_dir = tmp_path / "DecryptedVault"
    count = exporter.export_all(out_dir)

    assert count == 1
    # Check that directory layout was created: Accounts/Work/2026-08/
    work_dir = out_dir / "Accounts" / "Work" / "2026-08"
    assert work_dir.exists()

    md_files = list(work_dir.glob("*.md"))
    assert len(md_files) == 1
    assert "Important_Contract" in md_files[0].name

    # Check exported attachment
    attach_file = out_dir / "Attachments" / "test_mail_1" / "contract.pdf"
    assert attach_file.exists()
    assert attach_file.read_bytes() == b"%PDF-Agreement-Data"


def test_export_all_excludes_quarantined_by_default(tmp_path: Path):
    """
    Security review regression test (Sprint 04 finalization, FIX B): `export_all` must never
    write a quarantined email's plaintext content into the vault by default -- a bulk
    `mailbunker export-vault` run would otherwise leak held spam/phishing in the clear, directly
    contradicting the "store + hide" quarantine semantics already enforced in `search_emails`.
    """
    crypto = CryptoEngine("vault-pass")
    vault = EncryptedFileVault(tmp_path / "enc_vault", crypto)
    db = MailbunkerDatabase(tmp_path / "mail.db", crypto, vault)

    visible = EmailMessage(
        id="visible_mail",
        account="Work",
        folder="INBOX",
        uid=1,
        message_id="<visible@work.example>",
        subject="Legit Report",
        sender=EmailAddress(name="Reports", email="reports@work.example"),
        date=datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc),
        body_markdown="Legit content.",
    )
    quarantined = EmailMessage(
        id="quarantined_mail",
        account="Work",
        folder="INBOX",
        uid=2,
        message_id="<spam@work.example>",
        subject="You Won A Prize",
        sender=EmailAddress(name="Prize", email="prize@spam.example"),
        date=datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc),
        body_markdown="Click here to claim your prize.",
        quarantined=True,
        spam_score=0.95,
    )
    db.insert_email(visible)
    db.insert_email(quarantined)

    exporter = ObsidianVaultExporter(db, vault)
    out_dir = tmp_path / "DecryptedVault"
    count = exporter.export_all(out_dir)

    assert count == 1
    exported_text = "\n".join(p.read_text() for p in out_dir.rglob("*.md"))
    assert "Legit Report" in exported_text
    assert "You Won A Prize" not in exported_text
    assert "Click here to claim your prize" not in exported_text

    # Explicit opt-in must still be able to include quarantined mail (e.g. a manual review flow).
    out_dir_all = tmp_path / "DecryptedVaultAll"
    count_all = exporter.export_all(out_dir_all, include_quarantined=True)
    assert count_all == 2
