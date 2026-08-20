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
        message_id="<abc@def.com>",
        subject="Sprint Planning",
        sender=EmailAddress(name="Scrum Master", email="scrum@example.com"),
        to=[EmailAddress(name="Team", email="team@example.com")],
        date=datetime(2026, 8, 20, 9, 30, 0, tzinfo=timezone.utc),
        in_reply_to="<parent@def.com>",
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

    # Validate YAML frontmatter
    parts = md.split("---", 2)
    parsed_yaml = yaml.safe_load(parts[1])
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
        message_id="<test@work.com>",
        subject="Important Contract",
        sender=EmailAddress(name="Legal", email="legal@work.com"),
        to=[EmailAddress(name="Dennis", email="dennis@work.com")],
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
