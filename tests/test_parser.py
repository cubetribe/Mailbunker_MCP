from datetime import datetime, timezone
from mailbunker.imap.parser import parse_email_message, html_to_clean_markdown, decode_str_header


def test_decode_str_header():
    # RFC 2047 UTF-8 Base64 encoded: "Rechnung für März"
    encoded = "=?UTF-8?B?UmVjaG51bmcgZsO8ciBNw6Ryeg==?="
    assert decode_str_header(encoded) == "Rechnung für März"

    plain = "Simple Subject"
    assert decode_str_header(plain) == "Simple Subject"


def test_html_to_clean_markdown():
    html = """
    <html>
        <body>
            <h1>Project Update</h1>
            <p>Here is the <strong>important</strong> document link: <a href="https://example.com/doc">Doc</a></p>
            <ul>
                <li>Task 1</li>
                <li>Task 2</li>
            </ul>
        </body>
    </html>
    """
    md = html_to_clean_markdown(html)
    assert "# Project Update" in md
    assert "**important**" in md
    assert "[Doc](https://example.com/doc)" in md
    assert "- Task 1" in md or "* Task 1" in md


def test_parse_plain_text_email():
    raw_email = b"""From: "Alice Smith" <alice@example.com>
To: "Bob Jones" <bob@example.com>
Subject: Test Meeting
Date: Thu, 20 Aug 2026 14:30:00 +0200
Message-ID: <msg-001@example.com>
Content-Type: text/plain; charset="utf-8"

Hi Bob,
Let's meet tomorrow at 10 AM to discuss Mailbunker.

Best,
Alice
"""
    msg, attachments = parse_email_message(raw_email, account="Work", folder="INBOX", uid=101)

    assert msg.account == "Work"
    assert msg.folder == "INBOX"
    assert msg.uid == 101
    assert msg.subject == "Test Meeting"
    assert msg.sender.name == "Alice Smith"
    assert msg.sender.email == "alice@example.com"
    assert len(msg.to) == 1
    assert msg.to[0].email == "bob@example.com"
    assert "tomorrow at 10 AM" in msg.body_text
    assert "tomorrow at 10 AM" in msg.body_markdown
    assert len(attachments) == 0


def test_parse_multipart_email_with_attachment():
    raw_email = b"""From: Billing <billing@service.example>
To: Customer <customer@service.example>
Subject: Invoice #2026-0891
Date: Thu, 20 Aug 2026 15:00:00 +0000
Message-ID: <inv-2026-0891@service.example>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="BOUNDARY123"

--BOUNDARY123
Content-Type: text/html; charset="utf-8"

<p>Thank you for your business. Your <b>invoice</b> is attached.</p>

--BOUNDARY123
Content-Type: application/pdf; name="invoice_august.pdf"
Content-Disposition: attachment; filename="invoice_august.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQKJUZha2UgUERGIEJ5dGVzCg==
--BOUNDARY123--
"""
    msg, attachments = parse_email_message(raw_email, account="Personal", folder="INBOX", uid=202)

    assert msg.subject == "Invoice #2026-0891"
    assert "invoice" in msg.body_markdown.lower()
    assert len(attachments) == 1

    meta, payload = attachments[0]
    assert meta.filename == "invoice_august.pdf"
    assert meta.content_type == "application/pdf"
    assert payload.startswith(b"%PDF-1.4")
    assert meta.size > 0


def test_parse_preserves_trust_headers_and_raw_header_blob():
    raw_email = b"""From: "Alice Smith" <alice@example.com>
To: "Bob Jones" <bob@example.com>
Subject: Trust Header Test
Date: Thu, 20 Aug 2026 14:30:00 +0200
Message-ID: <trust-001@example.com>
Authentication-Results: mx.example.com; spf=pass smtp.mailfrom=example.com; dkim=pass
Received-SPF: pass (mx.example.com: domain of example.com designates 1.2.3.4 as permitted sender)
DKIM-Signature: v=1; a=rsa-sha256; d=example.com; s=selector1; bh=abc; b=def
ARC-Authentication-Results: i=1; mx.example.com; spf=pass
Return-Path: <alice@example.com>
List-Id: <bulk.example.com>
Precedence: bulk
Auto-Submitted: auto-generated
X-Mailer: SuperMailer 3.0
X-Spam-Score: 0.4
X-Spam-Flag: NO
X-Spam-Status: No, score=0.4
Content-Type: text/plain; charset="utf-8"

Trust header preservation test body.
"""
    msg, attachments = parse_email_message(raw_email, account="Work", folder="INBOX", uid=301)

    expected_headers = {
        "Authentication-Results": "mx.example.com; spf=pass smtp.mailfrom=example.com; dkim=pass",
        "Received-SPF": "pass (mx.example.com: domain of example.com designates 1.2.3.4 as permitted sender)",
        "Return-Path": "<alice@example.com>",
        "List-Id": "<bulk.example.com>",
        "Precedence": "bulk",
        "Auto-Submitted": "auto-generated",
        "X-Mailer": "SuperMailer 3.0",
    }
    for header, value in expected_headers.items():
        assert msg.raw_headers.get(header) == value

    assert "DKIM-Signature" in msg.raw_headers
    assert "ARC-Authentication-Results" in msg.raw_headers

    # All X-Spam-* headers captured regardless of exact suffix.
    assert msg.raw_headers.get("X-Spam-Score") == "0.4"
    assert msg.raw_headers.get("X-Spam-Flag") == "NO"
    assert "X-Spam-Status" in msg.raw_headers

    # Full raw header block persisted verbatim (up to first blank line), for later DKIM/SPF
    # re-verification -- not just the whitelisted subset.
    assert msg.raw_header_blob is not None
    assert "DKIM-Signature" in msg.raw_header_blob
    assert "Message-ID: <trust-001@example.com>" in msg.raw_header_blob
    assert "Trust header preservation test body." not in msg.raw_header_blob


def test_parse_email_message_populates_flags_when_provided():
    raw_email = b"""From: Alice <alice@example.com>
To: Bob <bob@example.com>
Subject: Flags Test
Date: Thu, 20 Aug 2026 14:30:00 +0200
Message-ID: <flags-001@example.com>
Content-Type: text/plain; charset="utf-8"

Flags test body.
"""
    msg, _ = parse_email_message(
        raw_email, account="Work", folder="INBOX", uid=401, flags=["\\Seen", "$Junk"]
    )
    assert msg.flags == ["\\Seen", "$Junk"]

    msg_no_flags, _ = parse_email_message(raw_email, account="Work", folder="INBOX", uid=402)
    assert msg_no_flags.flags == []
