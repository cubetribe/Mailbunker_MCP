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
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import html2text

from ..storage.models import EmailMessage, EmailAddress, AttachmentMeta
from ..security.sanitize import neutralize_untrusted

# Trust/provider-evidence headers preserved verbatim (Sprint 03/04 heuristics and the Ollama
# classifier read these; discarding them today would make later DKIM/SPF re-verification and
# spam-signal scoring impossible). X-Spam-* headers are additionally captured via prefix match
# below since provider-specific header names vary (X-Spam-Score, X-Spam-Flag, X-Spam-Status, ...).
# X-ICL-Score doesn't follow that "x-spam-" prefix convention (AOL's spam-score header), so it is
# whitelisted explicitly for Sprint 03's Stufe-0 provider-evidence scoring.
TRUST_HEADER_WHITELIST = [
    "Message-ID",
    "Subject",
    "From",
    "To",
    "Date",
    "Reply-To",
    "List-Unsubscribe",
    "Authentication-Results",
    "Received-SPF",
    "DKIM-Signature",
    "ARC-Authentication-Results",
    "Return-Path",
    "List-Id",
    "Precedence",
    "Auto-Submitted",
    "X-Mailer",
    "X-ICL-Score",
]

# --- Sprint 03 Stufe 2: HTML hidden-content / dangerous-scheme sanitization -------------------
#
# Hidden-element detection heuristics. None of these are exotic CSS -- they are the handful of
# techniques actually used to smuggle invisible text into an HTML email (prompt-injection payloads
# and old-school spam keyword stuffing alike): zero size, zero opacity, display/visibility off,
# color-on-color, and off-screen positioning.
_HIDDEN_STYLE_RES = (
    re.compile(r"display\s*:\s*none", re.I),
    re.compile(r"visibility\s*:\s*hidden", re.I),
    re.compile(r"font-size\s*:\s*0(?:\.0*)?(?:px|pt|em|%)?\b", re.I),
    re.compile(r"font-size\s*:\s*1px\b", re.I),
    re.compile(r"width\s*:\s*0(?:px)?\b", re.I),
    re.compile(r"height\s*:\s*0(?:px)?\b", re.I),
    re.compile(r"opacity\s*:\s*0(?:\.0+)?\b", re.I),
    re.compile(r"(?:left|margin-left|text-indent)\s*:\s*-\d{3,}px", re.I),  # off-screen positioning
)

_DANGEROUS_HREF_RE = re.compile(r"^\s*(javascript|vbscript)\s*:", re.I)
_DATA_URI_RE = re.compile(r"^\s*data\s*:", re.I)

# Sprint 03 security-review FIX 2: class/ID-based CSS hiding (`<style>.x{display:none}</style>
# <div class="x">payload</div>`) bypasses inline-style detection entirely and is the most common
# real-world hiding technique -- more common than inline `style="display:none"`. `_CSS_RULE_RE`
# extracts `selector-group { declarations }` blocks from a <style> tag's raw text;
# `_SIMPLE_SELECTOR_RE` validates that each comma-separated selector in the group is a plain
# tag/class/id selector (optionally chained, e.g. `div.x`) -- deliberately no combinators
# (whitespace/`>`/`+`/`~`), no pseudo-classes/elements, no at-rules, per the sprint's explicit
# "keep the CSS parser simple, this narrow attack surface is all that needs covering" scope.
# Selectors that don't validate are skipped rather than guessed at -- guessing wrong on a complex
# selector risks over-hiding legitimate content across the whole document, which is worse than
# under-hiding for this specific narrow feature (the broader inline-style/aria-hidden/off-screen
# heuristics above still catch the same content if it's also inline-hidden).
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_SELECTOR_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9]*)?(?:[.#][a-zA-Z0-9_-]+)*$")


def _css_rule_hides(declaration_block: str) -> bool:
    """Conservative match: if a CSS declaration block contains any of the same hidden-content
    patterns used for inline `style=""` attributes, treat it as a hiding rule. On ambiguity this
    over-matches rather than under-matches (e.g. a comment containing "display:none" inside the
    block would still trigger it) -- deliberately conservative per sprint policy: prefer to split
    content off into `hidden_text` over letting a real hiding rule through unflagged.
    """
    return any(pattern.search(declaration_block) for pattern in _HIDDEN_STYLE_RES)


def _apply_style_block_hiding(soup: Any) -> None:
    """
    Resolve simple class/ID/tag CSS selectors from `<style>` blocks that set a hidden-content
    property, and mark every matching element with `data-mb-hidden="css"` so the main
    `_is_hidden_element` pass treats them exactly like inline-hidden elements.

    Uses BeautifulSoup's own `.select()` (backed by `soupsieve`, already an installed
    `beautifulsoup4` dependency -- not a new one) to resolve the validated-simple selectors
    against the actual document tree, rather than reimplementing a CSS selector engine.
    """
    for style_tag in soup.find_all("style"):
        css_text = style_tag.string
        if css_text is None:
            css_text = style_tag.get_text()
        if not css_text:
            continue

        for selector_group, declarations in _CSS_RULE_RE.findall(css_text):
            if not _css_rule_hides(declarations):
                continue
            for raw_selector in selector_group.split(","):
                selector = raw_selector.strip()
                if not selector or not _SIMPLE_SELECTOR_RE.match(selector):
                    # Combinator/pseudo-class/at-rule selectors are out of scope -- skip.
                    continue
                try:
                    matched = soup.select(selector)
                except Exception:
                    continue
                for el in matched:
                    el["data-mb-hidden"] = "css"


def _parse_style(style_attr: str) -> Dict[str, str]:
    """Parse an inline `style="..."` attribute into a lowercased property->value dict."""
    result: Dict[str, str] = {}
    for decl in (style_attr or "").split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        result[prop.strip().lower()] = val.strip().lower()
    return result


def _is_hidden_element(tag: Any) -> bool:
    """Detect whether a BeautifulSoup tag is a hidden-content injection vector.

    Covers: display:none, visibility:hidden, font-size 0/1px, width/height 0, opacity 0,
    off-screen positioning (large negative left/margin-left/text-indent), color==background(-color),
    aria-hidden="true" / the bare `hidden` attribute, and class/ID-based `<style>` block hiding
    (marked upstream by `_apply_style_block_hiding` via the `data-mb-hidden` attribute).
    """
    if tag.get("data-mb-hidden") == "css":
        return True

    style_attr = tag.get("style", "") or ""
    if any(pattern.search(style_attr) for pattern in _HIDDEN_STYLE_RES):
        return True

    style = _parse_style(style_attr)
    color = style.get("color")
    background = style.get("background-color") or style.get("background")
    if color and background and color == background:
        return True

    aria_hidden = (tag.get("aria-hidden") or "").strip().lower()
    if aria_hidden == "true":
        return True
    if tag.has_attr("hidden"):
        return True

    return False


def sanitize_html_body(html_content: str) -> Tuple[str, Optional[str], List[str]]:
    """
    Sanitize an untrusted HTML email body before it is converted to Markdown/plain text.

    Returns `(cleaned_html, hidden_text, tracking_domains)`:
    - `cleaned_html`: the HTML with hidden elements removed, `data:` URIs stripped (replaced by
      nothing -- no unbounded inlining), and `javascript:`/`vbscript:` hrefs neutralized. Safe to
      feed into `html_to_clean_markdown`.
    - `hidden_text`: the concatenated visible text of every element that was detected as hidden
      (display:none, visibility:hidden, zero-size, aria-hidden, off-screen, color-on-color), or
      `None` if nothing was hidden. Callers MUST NOT merge this into body_text/body_markdown --
      it exists only as an injection/spam scoring signal (see `ingest/filters.py`).
    - `tracking_domains`: sorted list of distinct external http(s) hostnames referenced by `<img
      src>` or `<a href>` in the body (tracking pixels / outbound links), extracted as a feature
      instead of being left inline in the converted Markdown.
    """
    if not html_content or not html_content.strip():
        return html_content, None, []

    try:
        soup = BeautifulSoup(html_content, "html.parser")
    except Exception:
        return html_content, None, []

    # 0. Class/ID/tag-based CSS hiding via <style> blocks (security-review FIX 2) -- must run
    # before the hidden-element pass below so matched elements are treated identically to
    # inline-hidden ones.
    _apply_style_block_hiding(soup)

    # 1. Hidden-element extraction: walk a static snapshot of all tags (decompose() mutates the
    # tree while we iterate, so `.parent is None` guards against re-processing an already-removed
    # descendant of a previously decomposed hidden ancestor).
    hidden_parts: List[str] = []
    for tag in list(soup.find_all(True)):
        if tag.parent is None:
            continue
        try:
            if _is_hidden_element(tag):
                text = tag.get_text(separator=" ", strip=True)
                if text:
                    hidden_parts.append(text)
                tag.decompose()
        except Exception:
            continue

    # 2. Tracking-domain extraction (before hrefs/srcs are neutralized) + data:/dangerous-scheme
    # neutralization, in one remaining pass over the (now hidden-content-free) tree.
    tracking_domains: set[str] = set()

    for tag in soup.find_all(src=True):
        src = (tag.get("src") or "").strip()
        if _DATA_URI_RE.match(src):
            del tag["src"]
            continue
        host = _extract_http_host(src)
        if host:
            tracking_domains.add(host)

    for tag in soup.find_all(href=True):
        href = (tag.get("href") or "").strip()
        if _DANGEROUS_HREF_RE.match(href):
            tag["href"] = "#blocked-unsafe-link"
            continue
        if _DATA_URI_RE.match(href):
            tag["href"] = "#blocked-data-uri"
            continue
        host = _extract_http_host(href)
        if host:
            tracking_domains.add(host)

    cleaned_html = str(soup)
    hidden_text = "\n".join(hidden_parts).strip() or None
    if hidden_text:
        # hidden_text is untrusted extracted content persisted to storage; strip zero-width/bidi
        # smuggling characters the same way visible body text is neutralized below.
        hidden_text = neutralize_untrusted(hidden_text)

    return cleaned_html, hidden_text, sorted(tracking_domains)


def _extract_http_host(url: str) -> Optional[str]:
    """Return the lowercased hostname of an http(s) URL, or None for anything else (cid:, mailto:, relative, data:, etc.)."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def extract_raw_header_block(raw_bytes: bytes) -> str:
    """
    Extract the complete raw RFC822 header block (everything up to the first blank line) as
    text, so later trust verification (DKIM/SPF re-check) is possible even though only a
    whitelisted subset of headers is kept in the structured `raw_headers` dict.
    """
    header_bytes = raw_bytes
    for sep in (b"\r\n\r\n", b"\n\n"):
        idx = raw_bytes.find(sep)
        if idx != -1:
            header_bytes = raw_bytes[:idx]
            break
    try:
        return header_bytes.decode("utf-8", errors="replace")
    except Exception:
        return header_bytes.decode("latin-1", errors="replace")


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
    flags: Optional[List[str]] = None,
) -> Tuple[EmailMessage, List[Tuple[AttachmentMeta, bytes]]]:
    """
    Parse raw RFC822 email bytes into EmailMessage and extracted attachments.

    `flags` are the IMAP flags fetched alongside the message body (e.g. `$Junk`, `\\Seen`);
    they are optional since not every caller/server provides them.
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

    # Sprint 03 Stufe 2: strip hidden-content injection vectors and dangerous URL schemes from
    # the HTML body BEFORE it is converted to Markdown/plain text, so hidden text never reaches
    # body_text/body_markdown/FTS, data: URIs are never inlined raw, and javascript:/vbscript:
    # hrefs never survive into the Markdown link. Must run before `html_to_clean_markdown`.
    hidden_text: Optional[str] = None
    tracking_domains: List[str] = []
    if body_html:
        body_html, hidden_text, tracking_domains = sanitize_html_body(body_html)

    # Generate Markdown
    if body_html:
        body_markdown = html_to_clean_markdown(body_html)
    else:
        body_markdown = body_text

    if not body_text and body_html:
        # Generate plain text from HTML
        body_text = BeautifulSoup(body_html, "html.parser").get_text(separator="\n").strip()

    # Strip zero-width/bidi smuggling characters from the visible body (Sprint 01's
    # `security/sanitize.py`, reused here rather than reinventing the stripping logic).
    # `max_len=None`: this is archival storage, not an MCP response -- `neutralize_untrusted`'s
    # 20k default truncation is meant for the output-side context-budget use case in
    # `mcp/tools.py`/`security/sanitize.py`, not for what we persist here.
    body_text = neutralize_untrusted(body_text, max_len=None)
    body_markdown = neutralize_untrusted(body_markdown, max_len=None)

    # Raw Headers summary: whitelisted trust/provider headers...
    raw_headers = {}
    for h_name in TRUST_HEADER_WHITELIST:
        if msg.get(h_name):
            raw_headers[h_name] = decode_str_header(msg.get(h_name))

    # ...plus ALL X-Spam-* headers (provider-specific names/casing vary).
    for h_name, h_val in msg.items():
        if h_name.lower().startswith("x-spam-") and h_name not in raw_headers:
            raw_headers[h_name] = decode_str_header(h_val)

    # Complete raw header block (verbatim, pre-first-blank-line) for later DKIM/SPF
    # re-verification; persisted encrypted by the storage layer (see database.py).
    raw_header_blob = extract_raw_header_block(raw_bytes)

    # Tags
    tags = ["email", f"account/{account.lower().replace(' ', '_')}", f"folder/{folder.lower().replace(' ', '_')}"]
    if attachments:
        tags.append("has-attachment")
    if tracking_domains:
        tags.append("has-tracking-links")

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
        flags=list(flags) if flags else [],
        size=len(raw_bytes),
        raw_headers=raw_headers,
        raw_header_blob=raw_header_blob,
        hidden_text=hidden_text,
        tracking_domains=tracking_domains,
        tags=tags,
    )

    return email_model, attachments
