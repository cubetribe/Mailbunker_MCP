"""Async classification worker (Sprint 04).

Processes emails with `classified_at IS NULL` (or the whole non-quarantined backlog when
`backfill=True`) in batches, writes the classifier's verdict columns, and optionally triggers a
vault re-export of each affected note so the Obsidian frontmatter picks up the new
category/priority/summary/tags without requiring a full `export-vault` re-run.

Never imported by `imap/idle_listener.py` or `imap/sync_manager.py` -- this module is only ever
driven by the standalone `mailbunker classify` CLI command, by design (Core principle: async,
never in the IDLE push path).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING

from ..config import CLASSIFY_CONFIDENCE_THRESHOLD
from .classifier import classify_email
from .client import OllamaClient

if TYPE_CHECKING:
    from ..storage.database import MailbunkerDatabase
    from ..storage.obsidian import ObsidianVaultExporter
    from pathlib import Path

UNCERTAIN_TAG = "uncertain"


@dataclass
class WorkerStats:
    """Outcome summary of one `run_classification_batch` call."""

    processed: int = 0
    classified: int = 0
    fallback: int = 0
    skipped: int = 0
    ollama_unavailable: bool = False
    errors: List[str] = field(default_factory=list)


def run_classification_batch(
    db: "MailbunkerDatabase",
    client: OllamaClient,
    batch_size: int,
    account: Optional[str] = None,
    backfill: bool = False,
    confidence_threshold: float = CLASSIFY_CONFIDENCE_THRESHOLD,
    obsidian_exporter: Optional["ObsidianVaultExporter"] = None,
    vault_root: Optional["Path"] = None,
) -> WorkerStats:
    """Classify up to `batch_size` eligible emails and persist the results.

    Stops early (without raising) the moment Ollama proves unreachable -- retrying every
    remaining email in the batch against a backend that just failed would only add latency for
    an outcome we already know (graceful degradation, not a crash).
    """
    stats = WorkerStats()

    email_ids = db.get_unclassified_email_ids(limit=batch_size, account=account, backfill=backfill)

    for email_id in email_ids:
        msg = db.get_email(email_id)
        if msg is None or msg.quarantined:
            # Quarantined mail is deliberately excluded from classification -- it is already
            # hidden from search/MCP/vault export and does not need enrichment. A vanished
            # message (deleted between listing and fetch) is just skipped.
            stats.skipped += 1
            continue

        stats.processed += 1
        result = classify_email(client, msg)

        if result is None:
            stats.ollama_unavailable = True
            break

        if result.confidence < confidence_threshold and UNCERTAIN_TAG not in result.tags:
            result.tags.append(UNCERTAIN_TAG)

        # A fallback result always has category "unknown" (schema.py's dedicated sentinel) --
        # count it separately from a genuine, schema-conforming classification for reporting.
        if result.category == "unknown" and result.confidence == 0.0 and not result.summary:
            stats.fallback += 1

        classified_at = datetime.now(timezone.utc).isoformat()
        db.save_classification(
            email_id=email_id,
            spam_verdict=result.spam_verdict,
            category=result.category,
            priority=result.priority,
            classification_json=result.model_dump_json(),
            classified_at=classified_at,
        )
        stats.classified += 1

        if obsidian_exporter is not None and vault_root is not None:
            try:
                updated_msg = db.get_email(email_id)
                if updated_msg is not None:
                    obsidian_exporter.export_email(updated_msg, vault_root)
            except Exception as e:  # re-export is best-effort enrichment, never fatal to the run
                stats.errors.append(f"vault re-export failed for {email_id}: {e}")

    return stats
