"""MIME email parser with HTML-to-Markdown conversion and attachment extraction."""

from __future__ import annotations
import email
from email import policy
from email.message import EmailMessage as PyEmailMessage
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr, getaddresses
import hashlib
import re
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict, Any
from bs4 import BeautifulSoup
import html2text

from ..storage.models import EmailMessage, EmailAddress, AttachmentMeta


def decode_str_header(header_val: Optional[str]) -> str:
    """Safely decode RFC 2047 encoded email headers."""
    if not header_val:
        return ""
    try:
        decoded_chunks = decode_header(header_val)
        parts = []
        for chunk, encoding in decoded_chunks:
            if isinstance(chunk, bytes):
                enc = encoding or "utf-8"
                try:
                    parts.append(chunk.decode(enc, errors="replace"))
                except LookupError:
                    parts.append(chunk.decode("utf-8", errors="replace"))
            else:
                parts.append(str(chunk))
        return " ".join(parts).strip()
    except Exception:
        return str(header_val)


def parse_address_list(header_val: Optional[str]) -> List[EmailAddress]:
    """Parse comma-separated address list into EmailAddress models."""
    if not header_val:
        return []
    addresses = getaddresses([header_val])
    result = []
    for name, addr in addresses:
        clean_name = decode_str_header(name)
        clean_addr = addr.strip()
        if clean_addr:
            result.append(EmailAddress(name=clean_name, email=clean_addr))
    return result


def html_to_clean_markdown(html_content: str) -> str:
    """Convert HTML email body into clean, readable Markdown."""
    if not html_content or not html_content.strip():
        return ""

    try:
        # Pre-clean with BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup(["script", "style", "meta", "noscript"]):
            tag.decompose()

        cleaned_html = str(soup)

        # Configure html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_emphasis = False
        h.body_width = 0  # Do not wrap lines
        h.protect_links = False
        h.unicode_snob = True
        h.skip_internal_links = True

        markdown = h.handle(cleaned_html)
        # Collapse multiple empty lines
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
        return markdown
    except Exception:
        # Fallback to stripped text
        return BeautifulSoup(html_content, "html.parser").get_text(separator="\n").strip()


def sanitize_filename(filename: str) -> str:
    """Sanitize attachment filename for safe filesystem storage."""
    clean = re.sub(r'[\\/*?:"<>|]', "_", filename)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or "unnamed_attachment"


def parse_email_message(
    raw_bytes: bytes,
    account: str,
    folder: str,
    uid: int,
) -> Tuple[EmailMessage, List[Tuple[AttachmentMeta, bytes]]]:
    """
    Parse raw RFC822 email bytes into EmailMessage and extracted attachments.
    """
    msg = email.message_from_bytes(raw_bytes, policy=policy.default)

    # 1. Subject & Message-ID
    subject = decode_str_header(msg.get("Subject", "(No Subject)"))
    message_id = msg.get("Message-ID", "").strip()
    if not message_id:
        # Generate stable fallback ID
        hash_seed = f"{account}_{folder}_{uid}_{subject}_{msg.get('Date', '')}"
        message_id = f"gen-{hashlib.sha256(hash_seed.encode('utf-8')).hexdigest()[:16]}@mailbunker"

    # Unique internal ID
    internal_id = hashlib.sha256(f"{account}:{folder}:{message_id}:{uid}".encode("utf-8")).hexdigest()[:24]

    # 2. Addresses
    sender_raw = msg.get("From", "")
    sender_parsed = parse_address_list(sender_raw)
    sender = sender_parsed[0] if sender_parsed else EmailAddress(name="", email="unknown@unknown.com")

    to_addrs = parse_address_list(msg.get("To", ""))
    cc_addrs = parse_address_list(msg.get("Cc", ""))
    bcc_addrs = parse_address_list(msg.get("Bcc", ""))

    # 3. Date
    date_val = msg.get("Date")
    parsed_date = None
    if date_val:
        try:
            parsed_date = parsedate_to_datetime(date_val)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    if parsed_date is None:
        parsed_date = datetime.now(timezone.utc)

    # 4. In-Reply-To and References
    in_reply_to = msg.get("In-Reply-To", "").strip() or None
    references_raw = msg.get("References", "")
    references = [r.strip() for r in re.findall(r"<[^>]+>", references_raw)] if references_raw else []

    # 5. Extract Body & Attachments
    body_text = ""
    body_html = ""
    attachments: List[Tuple[AttachmentMeta, bytes]] = []

    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))

        is_attachment = "attachment" in content_disposition or part.get_filename() is not None

        if is_attachment:
            raw_filename = part.get_filename() or "attachment"
            decoded_filename = decode_str_header(raw_filename)
            safe_fname = sanitize_filename(decoded_filename)
            payload = part.get_payload(decode=True) or b""
            size = len(payload)
            sha256_hash = hashlib.sha256(payload).hexdigest()
            content_id = part.get("Content-ID", "").strip("<>")

            meta = AttachmentMeta(
                filename=safe_fname,
                content_type=content_type,
                size=size,
                sha256=sha256_hash,
                is_inline="inline" in content_disposition,
                content_id=content_id if content_id else None,
            )
            attachments.append((meta, payload))
        else:
            if content_type == "text/plain" and not body_text:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body_text = payload.decode(charset, errors="replace")
                    except LookupError:
                        body_text = payload.decode("utf-8", errors="replace")
            elif content_type == "text/html" and not body_html:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body_html = payload.decode(charset, errors="replace")
                    except LookupError:
                        body_html = payload.decode("utf-8", errors="replace")

    # Generate Markdown
    if body_html:
        body_markdown = html_to_clean_markdown(body_html)
    else:
        body_markdown = body_text

    if not body_text and body_html:
        # Generate plain text from HTML
        body_text = BeautifulSoup(body_html, "html.parser").get_text(separator="\n").strip()

    # Raw Headers summary
    raw_headers = {}
    for h_name in ["Message-ID", "Subject", "From", "To", "Date", "Reply-To", "List-Unsubscribe"]:
        if msg.get(h_name):
            raw_headers[h_name] = decode_str_header(msg.get(h_name))

    # Tags
    tags = ["email", f"account/{account.lower().replace(' ', '_')}", f"folder/{folder.lower().replace(' ', '_')}"]
    if attachments:
        tags.append("has-attachment")

    email_model = EmailMessage(
        id=internal_id,
        account=account,
        folder=folder,
        uid=uid,
        message_id=message_id,
        subject=subject,
        sender=sender,
        to=to_addrs,
        cc=cc_addrs,
        bcc=bcc_addrs,
        date=parsed_date,
        in_reply_to=in_reply_to,
        references=references,
        body_text=body_text,
        body_markdown=body_markdown,
        body_html=body_html if body_html else None,
        attachments=[meta for meta, _ in attachments],
        size=len(raw_bytes),
        raw_headers=raw_headers,
        tags=tags,
    )

    return email_model, attachments
