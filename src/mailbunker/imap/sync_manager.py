"""Multi-account synchronization and IDLE push orchestrator."""

from __future__ import annotations
import asyncio
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from pathlib import Path

from .client import AsyncImapClient
from .idle_listener import IdleFolderListener
from .parser import parse_email_message
from ..config import MailbunkerConfig, AccountConfig
from ..storage.models import AccountSyncStatus
from ..storage.database import MailbunkerDatabase
from ..storage.obsidian import ObsidianVaultExporter

logger = logging.getLogger("mailbunker.imap.sync_manager")


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
                    raw_bytes = await client.fetch_raw_message(uid)
                    if not raw_bytes:
                        continue

                    msg, attachments = parse_email_message(
                        raw_bytes=raw_bytes,
                        account=account.name,
                        folder=folder,
                        uid=uid,
                    )
                    self.db.insert_email(msg, attachments)
                    ingested += 1
                    highest_uid = max(highest_uid, uid)

                    if self.config.obsidian_auto_export and self.obsidian_exporter:
                        self.obsidian_exporter.export_email(msg, self.config.obsidian_vault_path)
                except Exception as err:
                    logger.error(f"Error syncing UID {uid} on {account.name}/{folder}: {err}")

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
