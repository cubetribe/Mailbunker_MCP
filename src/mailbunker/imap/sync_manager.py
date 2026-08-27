"""Multi-account synchronization and IDLE push orchestrator."""

from __future__ import annotations
import asyncio
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from pathlib import Path

from .client import AsyncImapClient
from .idle_listener import IdleFolderListener
from ..ingest.pipeline import ingest_raw, DECISION_STORE
from ..config import MailbunkerConfig, AccountConfig, FOLDER_DENYLIST
from ..storage.models import AccountSyncStatus
from ..storage.database import MailbunkerDatabase
from ..storage.obsidian import ObsidianVaultExporter

logger = logging.getLogger("mailbunker.imap.sync_manager")


def _is_denylisted_folder(folder: str) -> bool:
    """
    Sprint 03: provider-side spam/trash folders are never synced at all -- re-ingesting mail the
    provider (or the user, via Trash) already discarded would defeat the point of trusting
    provider evidence in the first place. Comparison is case-insensitive and exact (folder names
    like "Junk"/"[Gmail]/Spam" vary by provider; `FOLDER_DENYLIST` in `config.py` lists the
    common ones and is configurable via the `FOLDER_DENYLIST` env var).
    """
    norm = folder.strip().lower()
    return any(norm == denied.strip().lower() for denied in FOLDER_DENYLIST)


def _short_error_reason(err: Exception, max_len: int = 80) -> str:
    """
    Build a short, non-sensitive `ingest_log.reason` value for an exception.

    `ingest_log` is unencrypted metadata storage; the full `str(err)` of a parse/fetch failure
    can contain fragments of message content (headers, addresses, etc.), which would violate
    the "nothing leaks from a filtered/failed mail" principle. Only the exception type plus a
    short, truncated message summary is kept.
    """
    msg = " ".join(str(err).split())  # collapse whitespace/newlines
    if len(msg) > max_len:
        msg = msg[:max_len] + "..."
    return f"{type(err).__name__}: {msg}" if msg else type(err).__name__


class SyncManager:
    """Orchestrates multi-account IMAP synchronization and real-time push listeners."""

    def __init__(
        self,
        config: MailbunkerConfig,
        db: MailbunkerDatabase,
        obsidian_exporter: Optional[ObsidianVaultExporter] = None,
    ):
        self.config = config
        self.db = db
        self.obsidian_exporter = obsidian_exporter
        self._listeners: Dict[str, IdleFolderListener] = {}
        self._periodic_task: Optional[asyncio.Task] = None
        self._running = False

    async def sync_folder(self, account: AccountConfig, folder: str) -> int:
        """Perform one-time synchronization for an account folder."""
        if _is_denylisted_folder(folder):
            logger.info(f"Skipping denylisted folder {account.name}/{folder} (FOLDER_DENYLIST) -- not connecting.")
            return 0

        logger.info(f"Starting one-time sync for {account.name} / {folder}")
        client = AsyncImapClient(account)
        try:
            await client.connect()
            msg_count, uid_validity = await client.select_folder(folder)
            last_uid, recorded_validity = self.db.get_sync_state(account.id, folder)

            if recorded_validity > 0 and uid_validity > 0 and recorded_validity != uid_validity:
                logger.warning(f"UIDVALIDITY changed for {account.name}/{folder}. Resetting last UID.")
                last_uid = 0

            new_uids = await client.fetch_uids_since(last_uid)
            if not new_uids:
                self.db.update_sync_state(account.id, folder, last_uid, uid_validity)
                return 0

            logger.info(f"Syncing {len(new_uids)} emails from {account.name}/{folder}...")
            highest_uid = last_uid
            ingested = 0

            for uid in sorted(new_uids):
                try:
                    raw_bytes, flags = await client.fetch_raw_message(uid)
                    if not raw_bytes:
                        # Message vanished from the server between SEARCH and FETCH (e.g.
                        # deleted/expunged concurrently). Not a processing error -- log it and
                        # still advance the watermark so we don't retry it forever.
                        self.db.log_ingest_decision(account.id, folder, uid, "empty", "no raw bytes returned")
                        highest_uid = max(highest_uid, uid)
                        continue

                    decision, msg = ingest_raw(
                        self.db,
                        raw_bytes=raw_bytes,
                        account=account.name,
                        folder=folder,
                        uid=uid,
                        flags=flags,
                    )
                    # Watermark fix: advance for EVERY UID decision (store/quarantine/block),
                    # not only successful inserts -- otherwise a filtered/quarantined mail with
                    # a lower UID than a later stored one is lost permanently. Only genuine
                    # exceptions (below) skip the advance, so they get retried next sync.
                    highest_uid = max(highest_uid, uid)

                    if decision.decision == DECISION_STORE:
                        ingested += 1
                        if self.config.obsidian_auto_export and self.obsidian_exporter and msg:
                            # Security review fix (Sprint 04 finalization, FIX B): a fresh
                            # `filter_hook` decision of `store` does not guarantee the row that
                            # actually landed in the DB is unquarantined -- `insert_email`'s
                            # `preserve_classification` merge can carry forward an EARLIER
                            # quarantine decision on a re-ingest of the same message id (e.g.
                            # IDLE redelivering after a reconnect), which the in-memory `msg`
                            # object built from THIS parse never reflects. Re-read the persisted,
                            # merged row before auto-exporting so a quarantined mail is never
                            # written to the plaintext vault.
                            stored_msg = self.db.get_email(msg.id)
                            if stored_msg and not stored_msg.quarantined:
                                self.obsidian_exporter.export_email(stored_msg, self.config.obsidian_vault_path)
                except Exception as err:
                    logger.error(f"Error syncing UID {uid} on {account.name}/{folder}: {err}")
                    # ingest_log is unencrypted metadata storage -- never persist the full
                    # exception text there (it can carry header/address fragments from the
                    # message being processed). Log type + a short, truncated summary instead.
                    self.db.log_ingest_decision(account.id, folder, uid, "error", _short_error_reason(err))

            self.db.update_sync_state(account.id, folder, highest_uid, uid_validity)
            return ingested
        finally:
            await client.disconnect()

    async def sync_account(self, account_name_or_id: str) -> int:
        """Sync all configured folders for a specific account."""
        acc = next((a for a in self.config.accounts if a.name.lower() == account_name_or_id.lower() or a.id == account_name_or_id), None)
        if not acc:
            raise ValueError(f"Account '{account_name_or_id}' not found in configuration.")

        total_ingested = 0
        for folder in acc.folders:
            total_ingested += await self.sync_folder(acc, folder)
        return total_ingested

    async def sync_all(self) -> Dict[str, int]:
        """Run one-off sync for all enabled accounts."""
        results = {}
        for acc in self.config.accounts:
            if not acc.enabled:
                continue
            count = 0
            for folder in acc.folders:
                try:
                    count += await self.sync_folder(acc, folder)
                except Exception as e:
                    logger.error(f"Failed sync on {acc.name}/{folder}: {e}")
            results[acc.name] = count
        return results

    async def start_idle_daemon(self) -> None:
        """Start real-time IDLE push listeners for all enabled accounts and folders."""
        if self._running:
            return
        self._running = True

        for acc in self.config.accounts:
            if not acc.enabled or not acc.idle_enabled:
                continue

            for folder in acc.folders:
                if _is_denylisted_folder(folder):
                    logger.info(f"Skipping IDLE listener for denylisted folder {acc.name}/{folder} (FOLDER_DENYLIST).")
                    continue

                listener_key = f"{acc.id}:{folder}"
                if listener_key not in self._listeners:
                    listener = IdleFolderListener(
                        account_config=acc,
                        folder=folder,
                        db=self.db,
                        obsidian_exporter=self.obsidian_exporter,
                        auto_export_obsidian=self.config.obsidian_auto_export,
                        obsidian_vault_path=str(self.config.obsidian_vault_path),
                    )
                    self._listeners[listener_key] = listener
                    await listener.start()

        # Also start periodic fallback ticker
        if self.config.sync_interval_minutes > 0:
            self._periodic_task = asyncio.create_task(self._run_periodic_sync())

        logger.info(f"Started {len(self._listeners)} IDLE push listeners.")

    async def stop_idle_daemon(self) -> None:
        """Stop all background IDLE listeners."""
        self._running = False
        if self._periodic_task and not self._periodic_task.done():
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass

        for listener in self._listeners.values():
            await listener.stop()
        self._listeners.clear()
        logger.info("Stopped all IDLE push listeners.")

    async def _run_periodic_sync(self) -> None:
        """Periodic fallback sync in case IDLE drops or mailboxes missed pushes."""
        interval_secs = self.config.sync_interval_minutes * 60
        while self._running:
            try:
                await asyncio.sleep(interval_secs)
                logger.info("Running periodic fallback sync check...")
                await self.sync_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in periodic sync: {e}")

    def get_statuses(self) -> List[AccountSyncStatus]:
        """Return status snapshot of all configured accounts."""
        statuses = []
        for acc in self.config.accounts:
            # Aggregate listener states
            is_connected = False
            is_idle = False
            last_sync = None
            last_err = None

            for folder in acc.folders:
                key = f"{acc.id}:{folder}"
                listener = self._listeners.get(key)
                if listener:
                    if listener._client and listener._client.is_connected:
                        is_connected = True
                    if listener.is_idle:
                        is_idle = True
                    if listener.last_sync:
                        last_sync = max(last_sync or listener.last_sync, listener.last_sync)
                    if listener.last_error:
                        last_err = listener.last_error

            # Count emails in DB for this account
            email_count = 0
            try:
                with self.db._get_conn() as conn:
                    email_count = conn.execute("SELECT COUNT(*) FROM emails WHERE account = ?", (acc.name,)).fetchone()[0]
            except Exception:
                pass

            statuses.append(AccountSyncStatus(
                name=acc.name,
                host=acc.host,
                user=acc.user,
                enabled=acc.enabled,
                connected=is_connected,
                idle_active=is_idle,
                last_sync=last_sync,
                total_emails=email_count,
                last_error=last_err,
            ))
        return statuses
