"""Data models for Mailbunker_MCP."""

from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class AttachmentMeta(BaseModel):
    """Metadata for an email attachment."""
    filename: str
    content_type: str
    size: int
    sha256: str
    storage_path: Optional[str] = None
    is_inline: bool = False
    content_id: Optional[str] = None


class EmailAddress(BaseModel):
    """Parsed email address with display name."""
    name: str = ""
    email: str

    def __str__(self) -> str:
        if self.name:
            escaped_name = self.name.replace('"', '\\"')
            return f'"{escaped_name}" <{self.email}>'
        return self.email


class EmailMessage(BaseModel):
    """Complete parsed email message representation."""
    id: str  # internal unique ID (e.g. SHA256 of account + message_id or UUID)
    account: str
    folder: str
    uid: int
    message_id: str
    subject: str = "(No Subject)"
    sender: EmailAddress
    to: List[EmailAddress] = Field(default_factory=list)
    cc: List[EmailAddress] = Field(default_factory=list)
    bcc: List[EmailAddress] = Field(default_factory=list)
    date: datetime
    in_reply_to: Optional[str] = None
    references: List[str] = Field(default_factory=list)
    body_text: str = ""
    body_markdown: str = ""
    body_html: Optional[str] = None
    attachments: List[AttachmentMeta] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)
    size: int = 0
    raw_headers: Dict[str, str] = Field(default_factory=dict)
    raw_header_blob: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    # Sprint 03: HTML-sanitization (Stufe 2) output. `hidden_text` holds text extracted from
    # elements detected as hidden (display:none, visibility:hidden, zero-size, aria-hidden,
    # off-screen positioning, color==background) -- it MUST NEVER be copied into `body_text`,
    # `body_markdown`, the FTS index, or any future embedding input; it exists purely as an
    # injection/spam scoring signal and an optional forensic view, and is persisted encrypted
    # like other sensitive payload fields. `tracking_domains` collects the distinct external
    # image/link domains found in the HTML body (extracted as a feature instead of being left
    # inline in the markdown).
    hidden_text: Optional[str] = None
    tracking_domains: List[str] = Field(default_factory=list)

    # Sprint 02: ingest-fundament columns. Sprint 03 fills quarantined/spam_score via the
    # filter hook; Sprint 04 fills trust_level/classification_json/classified_at via the
    # Ollama classifier. In Sprint 02 these carry safe defaults only (No-Op filter hook).
    quarantined: bool = False
    spam_score: float = 0.0
    trust_level: Optional[str] = None
    classification_json: Optional[str] = None
    classified_at: Optional[str] = None
    embedding_state: str = "pending"

    # Sprint 04: Ollama classifier (Stufe 4) result columns, extracted out of
    # `classification_json` into dedicated columns for fast structured filtering in
    # `search_emails`/MCP. Always the neutralized (`security/sanitize.neutralize_untrusted`)
    # values from `classify.classifier.ClassificationResult` -- never raw model output.
    spam_verdict: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None

    @property
    def sender_str(self) -> str:
        return str(self.sender)

    @property
    def to_str(self) -> str:
        return ", ".join(str(addr) for addr in self.to)


class SearchQuery(BaseModel):
    """Parameters for email search."""
    query: str = ""
    account: Optional[str] = None
    folder: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    has_attachments: Optional[bool] = None
    limit: int = 20
    offset: int = 0
    include_quarantined: bool = False
    # Sprint 04: additive structured filters over the Ollama classifier's output columns.
    category: Optional[str] = None
    spam_verdict: Optional[str] = None


class SearchResultItem(BaseModel):
    """Single item returned in search results."""
    id: str
    account: str
    folder: str
    message_id: str
    subject: str
    sender: str
    date: datetime
    snippet: str
    has_attachments: bool
    attachment_count: int = 0
    tags: List[str] = Field(default_factory=list)


class SearchResults(BaseModel):
    """Aggregated search response."""
    total: int
    limit: int
    offset: int
    results: List[SearchResultItem]


class AccountSyncStatus(BaseModel):
    """Real-time status of an account's sync engine."""
    name: str
    host: str
    user: str
    enabled: bool
    connected: bool
    idle_active: bool
    last_sync: Optional[datetime] = None
    total_emails: int = 0
    last_error: Optional[str] = None
