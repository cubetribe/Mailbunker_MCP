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
To: "Dennis Westermann" <dennis@example.com>
Subject: Test Meeting
Date: Thu, 20 Aug 2026 14:30:00 +0200
Message-ID: <msg-001@example.com>
Content-Type: text/plain; charset="utf-8"

Hi Dennis,
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
    assert msg.to[0].email == "dennis@example.com"
    assert "tomorrow at 10 AM" in msg.body_text
    assert "tomorrow at 10 AM" in msg.body_markdown
    assert len(attachments) == 0


def test_parse_multipart_email_with_attachment():
    raw_email = b"""From: Billing <billing@service.com>
To: Dennis <dennis@example.com>
Subject: Invoice #2026-0891
Date: Thu, 20 Aug 2026 15:00:00 +0000
Message-ID: <inv-2026-0891@service.com>
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
