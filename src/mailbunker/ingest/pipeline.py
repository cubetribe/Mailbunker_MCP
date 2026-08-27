"""
Unified ingest choke-point (Sprint 02 - Ingest-Fundament).

Both ingest paths (one-shot `SyncManager.sync_folder` and the IDLE push listener
`IdleFolderListener._sync_pending_emails`) MUST call `ingest_raw()` instead of calling
`parse_email_message()` / `MailbunkerDatabase.insert_email()` directly. This is the single
place where Sprint 03 wires in the real filter layers (provider evidence, heuristics, HTML
sanitization) and Sprint 04 the async Ollama classifier -- without touching either call site
again.

In Sprint 02 `filter_hook()` is a deliberate No-Op: it always returns `store`. The pipeline
plumbing (parse -> filter -> store|quarantine|block, with `ingest_log` audit trail) exists and
is exercised by tests, but no actual filtering/scoring logic is implemented here yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, TYPE_CHECKING

from ..storage.models import EmailMessage, AttachmentMeta

if TYPE_CHECKING:
    from ..storage.database import MailbunkerDatabase


# Decision values. Kept as plain strings (not a strict enum) so Sprint 03/04 can extend the
# reason vocabulary freely without a model migration; the three decision values themselves are
# part of the sprint 02 contract and other sprints must not invent new ones.
DECISION_STORE = "store"
DECISION_QUARANTINE = "quarantine"
DECISION_BLOCK = "block"


@dataclass
class IngestDecision:
    """Result of running the filter hook against a parsed email."""

    decision: str  # one of DECISION_STORE | DECISION_QUARANTINE | DECISION_BLOCK
    reason: str = ""
    score: float = 0.0


def filter_hook(email_msg: EmailMessage) -> IngestDecision:
    """
    Filter hook -- Sprint 03: real Stufe 0-2 scoring (provider evidence, hard-coded heuristics;
    HTML sanitization/hidden_text extraction already happened in `imap/parser.py` before this is
    called, so this only scores its output).

    Delegates to `ingest/filters.py::evaluate()`. Imported lazily (not at module top) to avoid a
    circular import: `filters.py` imports the `DECISION_*`/`IngestDecision` constants defined
    above from this module, and `mailbunker.ingest.__init__` imports this module eagerly -- by
    calling `filter_hook` only at runtime (after this module has finished loading), `filters.py`
    can safely import back from an already-fully-initialized `pipeline` module.
    """
    from .filters import evaluate

    return evaluate(email_msg)


def ingest_raw(
    db: "MailbunkerDatabase",
    raw_bytes: bytes,
    account: str,
    folder: str,
    uid: int,
    flags: Optional[List[str]] = None,
) -> Tuple[IngestDecision, Optional[EmailMessage]]:
    """
    Single choke-point turning raw RFC822 bytes into a stored (or quarantined/blocked) email.

    Flow: parse_email_message -> filter_hook -> insert_email | quarantine | block.
    Every UID decision (store/quarantine/block) is recorded in `ingest_log`, regardless of
    outcome, so sync watermark advancement can be judged independently of filtering decisions
    (see the sync_manager/idle_listener watermark fix).

    Returns (decision, email_msg). `email_msg` is `None` when the decision is `block` (nothing
    was parsed into storage-worthy state); callers needing the parsed message for further
    processing (e.g. conditional Obsidian export) should only rely on it when
    `decision.decision == DECISION_STORE` per the "store + hide" quarantine semantics (quarantined
    mails are saved but never exported/surfaced).
    """
    # Imported lazily to avoid a circular import: `mailbunker.imap.__init__` eagerly imports
    # `sync_manager`/`idle_listener`, which import this module -- importing `imap.parser` at
    # module load time here would re-enter the still-initializing `mailbunker.imap` package.
    from ..imap.parser import parse_email_message

    email_msg, attachments_data = parse_email_message(
        raw_bytes=raw_bytes,
        account=account,
        folder=folder,
        uid=uid,
        flags=flags,
    )

    decision = filter_hook(email_msg)

    if decision.decision == DECISION_BLOCK:
        db.log_ingest_decision(account, folder, uid, decision.decision, decision.reason)
        return decision, None

    # spam_score/trust_level are set for every stored outcome, not just quarantine -- Sprint 03's
    # filter_hook (ingest/filters.py::evaluate) already set trust_level on email_msg directly;
    # spam_score is applied here so a single place governs it for both `store` and `quarantine`.
    email_msg.spam_score = decision.score
    if decision.decision == DECISION_QUARANTINE:
        email_msg.quarantined = True

    db.insert_email(email_msg, attachments_data)
    db.log_ingest_decision(account, folder, uid, decision.decision, decision.reason)

    return decision, email_msg
