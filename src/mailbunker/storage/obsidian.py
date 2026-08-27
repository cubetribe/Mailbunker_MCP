"""Obsidian Vault Generator and Exporter for Mailbunker."""

from __future__ import annotations
import json
import re
import yaml
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from .models import EmailMessage, AttachmentMeta
from .database import MailbunkerDatabase
from ..crypto.vault import EncryptedFileVault


def sanitize_path_component(text: str) -> str:
    """Sanitize strings for folder and file names, replacing invalid chars and spaces with underscores."""
    cleaned = re.sub(r'[\s\\/*?:"<>|#^\[\]]+', "_", text).strip("_")
    return cleaned[:100] or "unnamed"


def _neutralize_obsidian_meta(text: str) -> str:
    """Neutralize Obsidian/Markdown metacharacters in attacker-controlled values.

    Backtick code-span fencing alone is NOT sufficient: a display name containing its own
    backtick (e.g. `` `"x` [[Injected]] `y"` ``) terminates the surrounding code span early,
    letting the remainder be parsed as normal Markdown and render `[[Injected]]` as a live,
    clickable Obsidian wikilink. Backslash-escaping does not help either — CommonMark does
    not process backslash escapes inside/around code-span backtick runs. So instead:

    - Backticks are replaced outright (not merely escaped) so they can never terminate a
      surrounding code-span fence early.
    - `[[` / `]]` wikilink delimiters are broken by inserting a plain space, so the sequence
      can never resolve into a live wikilink — no zero-width/invisible characters are used,
      only a visible space, to avoid re-introducing the smuggling patterns this sprint just
      removed elsewhere.

    Visible characters other than these specific metacharacters are left untouched.
    """
    neutralized = text.replace("`", "'")
    neutralized = neutralized.replace("[[", "[ [").replace("]]", "] ]")
    return neutralized


def _extract_classification_fields(email_msg: EmailMessage) -> tuple[Optional[str], List[str]]:
    """Pull `summary`/`tags` out of `classification_json` (Sprint 04) for frontmatter enrichment.

    The classifier (`classify/classifier.py`) already neutralizes `summary`/`tags` via
    `security.sanitize.neutralize_untrusted` before persisting them, but that only strips
    invisible/control characters -- Obsidian-specific metacharacters (backticks, `[[wikilinks]]`)
    still need `_neutralize_obsidian_meta` at the point of frontmatter injection, exactly like
    every other attacker-influenced field in this module. Never raises: a malformed/missing
    `classification_json` (e.g. mail not classified yet) just yields no enrichment.
    """
    if not email_msg.classification_json:
        return None, []
    try:
        data = json.loads(email_msg.classification_json)
    except (ValueError, TypeError):
        return None, []
    summary = data.get("summary") or None
    tags = [t for t in (data.get("tags") or []) if isinstance(t, str) and t.strip()]
    return summary, tags


def format_email_markdown(email_msg: EmailMessage, attachment_base_rel: str = "../../Attachments") -> str:
    """Format an EmailMessage into an Obsidian-compatible Markdown note with frontmatter."""

    classifier_summary, classifier_tags = _extract_classification_fields(email_msg)
    safe_summary = _neutralize_obsidian_meta(classifier_summary) if classifier_summary else None
    # Merge classifier tags into the existing tag list (e.g. the Sprint 03 "newsletter" label),
    # de-duplicated, each metachar-neutralized so a classifier-produced tag can't smuggle a live
    # wikilink/code-span break into the YAML frontmatter.
    merged_tags: List[str] = list(email_msg.tags)
    for t in classifier_tags:
        safe_tag = _neutralize_obsidian_meta(t)
        if safe_tag not in merged_tags:
            merged_tags.append(safe_tag)

    # 1. Frontmatter metadata
    frontmatter_dict = {
        "id": email_msg.id,
        "subject": email_msg.subject,
        "from": str(email_msg.sender),
        "to": [str(a) for a in email_msg.to],
        "cc": [str(a) for a in email_msg.cc] if email_msg.cc else None,
        "date": email_msg.date.isoformat(),
        "account": email_msg.account,
        "folder": email_msg.folder,
        "message_id": email_msg.message_id,
        "in_reply_to": email_msg.in_reply_to,
        "tags": merged_tags,
        "category": email_msg.category,
        "priority": email_msg.priority,
        "summary": safe_summary,
        "has_attachments": bool(email_msg.attachments),
        "attachments": [a.filename for a in email_msg.attachments] if email_msg.attachments else None,
    }

    # Clean out None values
    clean_frontmatter = {k: v for k, v in frontmatter_dict.items() if v is not None}
    yaml_header = yaml.dump(clean_frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # 2. Note Header
    date_formatted = email_msg.date.strftime("%Y-%m-%d %H:%M:%S %Z")
    recipients_str = ", ".join(str(a) for a in email_msg.to)

    # Fold subject onto a single line: a subject smuggling embedded newlines could otherwise
    # break out of the H1 heading and inject arbitrary markdown/HTML into the rendered note.
    # Also neutralize wikilink/backtick metacharacters directly in the heading text (a plain
    # H1 has no fence to break out of, but a raw `[[...]]` in the subject would still render
    # as a live wikilink).
    safe_subject_line = _neutralize_obsidian_meta(" ".join(email_msg.subject.split()))

    # From/To are rendered in backticks: these come from attacker-controlled display names and
    # must not be interpreted as Markdown (e.g. wikilinks `[[...]]`, emphasis, HTML) by Obsidian.
    # Backtick fencing alone is breakable if the value itself contains a backtick, so the value
    # is metachar-neutralized BEFORE being wrapped in the fence.
    safe_sender = f"`{_neutralize_obsidian_meta(str(email_msg.sender))}`"
    safe_recipients = f"`{_neutralize_obsidian_meta(recipients_str)}`" if recipients_str else ""

    lines = [
        "---",
        yaml_header.strip(),
        "---",
        "",
        f"# {safe_subject_line}",
        "",
        f"> **From:** {safe_sender}  ",
        f"> **To:** {safe_recipients}  ",
        f"> **Date:** {date_formatted}  ",
        f"> **Folder:** `{email_msg.account} / {email_msg.folder}`",
    ]

    if email_msg.in_reply_to:
        lines.append(f"> **In Reply To:** `{email_msg.in_reply_to}`")

    lines.append("")

    # 3. Attachments section if present
    if email_msg.attachments:
        lines.append("## 📎 Attachments")
        for attach in email_msg.attachments:
            # Obsidian file link format. Escape Markdown link-breaking characters in the
            # attacker-controlled filename so it cannot terminate the label/target early
            # and inject an arbitrary link target or trailing markup, and neutralize
            # backtick/wikilink metacharacters (the label sits outside any code span here,
            # so a raw `[[...]]` filename would otherwise still render as a live wikilink).
            safe_name = _neutralize_obsidian_meta(attach.filename)
            safe_label = safe_name.replace("]", "\\]").replace("(", "\\(").replace(")", "\\)")
            safe_link_name = safe_name.replace(")", "%29").replace("(", "%28")
            attach_link = f"{attachment_base_rel}/{email_msg.id}/{safe_link_name}"
            size_kb = round(attach.size / 1024, 1)
            lines.append(f"- [{safe_label}]({attach_link}) *({size_kb} KB, `{attach.content_type}`)*")
        lines.append("")
        lines.append("---")
        lines.append("")

    # 4. Email Body Content
    lines.append("## ✉️ Message")
    lines.append("")
    body_content = email_msg.body_markdown or email_msg.body_text or "*(No text content)*"
    lines.append(body_content)
    lines.append("")

    return "\n".join(lines)


class ObsidianVaultExporter:
    """Exports decrypted emails and attachments into an organized Obsidian Vault directory."""

    def __init__(self, db: MailbunkerDatabase, vault_file_storage: EncryptedFileVault):
        self.db = db
        self.vault_file_storage = vault_file_storage

    def export_all(self, target_dir: Path, include_quarantined: bool = False) -> int:
        """Export all emails and attachments from the database to target_dir.

        Security review fix (Sprint 04 finalization, FIX B): quarantined mail (spam/phishing
        held per the "store + hide" quarantine semantics, Plan v0.2.0) is excluded by default,
        exactly like `search_emails`/MCP already exclude it -- a bulk `export-vault` run must
        never write quarantined content into the plaintext vault on disk. Pass
        `include_quarantined=True` for an explicit, deliberate full export (e.g. a manual
        quarantine-review workflow), never as a default.
        """
        target_dir = target_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        with self.db._get_conn() as conn:
            if include_quarantined:
                cur = conn.execute("SELECT id FROM emails ORDER BY date_timestamp ASC")
            else:
                cur = conn.execute(
                    "SELECT id FROM emails WHERE COALESCE(quarantined, 0) = 0 ORDER BY date_timestamp ASC"
                )
            email_ids = [row["id"] for row in cur.fetchall()]

        exported_count = 0
        for email_id in email_ids:
            msg = self.db.get_email(email_id)
            if not msg:
                continue

            self.export_email(msg, target_dir)
            exported_count += 1

        return exported_count

    def export_email(self, msg: EmailMessage, vault_root: Path) -> Path:
        """Export a single EmailMessage and its attachments to the Obsidian vault."""
        # Folder layout: vault_root/Accounts/<Account>/<YYYY-MM>/
        acc_folder = sanitize_path_component(msg.account)
        date_folder = msg.date.strftime("%Y-%m")
        target_subfolder = vault_root / "Accounts" / acc_folder / date_folder
        target_subfolder.mkdir(parents=True, exist_ok=True)

        safe_subject = sanitize_path_component(msg.subject)
        date_prefix = msg.date.strftime("%Y%m%d_%H%M")
        note_filename = f"{date_prefix}_{safe_subject}_{msg.id[:6]}.md"
        note_path = target_subfolder / note_filename

        # Write markdown note
        markdown_text = format_email_markdown(msg, attachment_base_rel="../../Attachments")
        note_path.write_text(markdown_text, encoding="utf-8")

        # Export attachments
        if msg.attachments:
            attach_dir = vault_root / "Attachments" / msg.id
            attach_dir.mkdir(parents=True, exist_ok=True)

            for attach in msg.attachments:
                if attach.storage_path and self.vault_file_storage.exists(attach.storage_path):
                    try:
                        raw_data = self.vault_file_storage.read_bytes(attach.storage_path)
                        attach_dest = attach_dir / attach.filename
                        attach_dest.write_bytes(raw_data)
                    except Exception:
                        pass

        return note_path
