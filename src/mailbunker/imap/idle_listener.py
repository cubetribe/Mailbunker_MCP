"""Real-time IMAP IDLE push listener worker."""

from __future__ import annotations
import asyncio
import logging
from typing import Optional, Callable
from datetime import datetime, timezone

from .client import AsyncImapClient
from .parser import parse_email_message
from ..config import AccountConfig
from ..storage.database import MailbunkerDatabase
from ..storage.obsidian import ObsidianVaultExporter

logger = logging.getLogger("mailbunker.imap.idle")


class IdleFolderListener:
    """Listens for real-time push events on a single IMAP folder using RFC 2177 IDLE."""

    def __init__(
        self,
        account_config: AccountConfig,
        folder: str,
        db: MailbunkerDatabase,
        obsidian_exporter: Optional[ObsidianVaultExporter] = None,
        auto_export_obsidian: bool = False,
        obsidian_vault_path: Optional[str] = None,
    ):
        self.account_config = account_config
        self.folder = folder
        self.db = db
        self.obsidian_exporter = obsidian_exporter
        self.auto_export_obsidian = auto_export_obsidian
        self.obsidian_vault_path = obsidian_vault_path
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[AsyncImapClient] = None
        self.last_sync: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.is_idle = False

    async def start(self) -> None:
        """Start the background IDLE listener task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"idle_{self.account_config.name}_{self.folder}")

    async def stop(self) -> None:
        """Stop the background IDLE listener."""
        self._running = False
        if self._client and self.is_idle and self._client._client is not None:
            try:
                await self._client._client.idle_done()
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.disconnect()

    async def _run_loop(self) -> None:
        """Main resilient loop: connects, does initial sync, and maintains IDLE state."""
        backoff_seconds = 2.0

        while self._running:
            try:
                self._client = AsyncImapClient(self.account_config)
                await self._client.connect()
                msg_count, uid_validity = await self._client.select_folder(self.folder)

                # Reset backoff on successful connection
                backoff_seconds = 2.0
                self.last_error = None

                # Perform sync on initial connect or reconnect
                await self._sync_pending_emails(uid_validity)

                # Enter IDLE loop
                while self._running and self._client.is_connected:
                    logger.debug(f"Starting IDLE on {self.account_config.name} / {self.folder}")
                    assert self._client._client is not None
                    
                    idle_session = await self._client._client.idle_start()
                    self.is_idle = True

                    try:
                        # Wait up to 20 minutes (1200 seconds) for server push notification
                        # IMAP RFC recommends refreshing IDLE every ~20-29 mins to prevent NAT timeout
                        push_future = self._client._client.wait_server_push()
                        done, pending = await asyncio.wait([push_future], timeout=1200)

                        self.is_idle = False
                        await self._client._client.idle_done()

                        if push_future in done:
                            res = push_future.result()
                            logger.info(f"Push notification received on {self.account_config.name}/{self.folder}: {res}")
                            await self._sync_pending_emails(uid_validity)
                        else:
                            # Periodic refresh to keep connection alive
                            logger.debug(f"Refreshing IDLE keepalive on {self.account_config.name}/{self.folder}")
                    except Exception as e:
                        self.is_idle = False
                        try:
                            await self._client._client.idle_done()
                        except Exception:
                            pass
                        raise e

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning(f"IDLE listener error on {self.account_config.name}/{self.folder}: {exc}. Retrying in {backoff_seconds}s...")
                if self._client:
                    await self._client.disconnect()
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 1.5, 60.0)

    async def _sync_pending_emails(self, uid_validity: int) -> int:
        """Fetch and ingest all emails newer than recorded sync state."""
        if not self._client or not self._client.is_connected:
            return 0

        last_uid, recorded_validity = self.db.get_sync_state(self.account_config.id, self.folder)
        
        # If mailbox UIDVALIDITY changed, previous UIDs are invalid, reset to 0
        if recorded_validity > 0 and uid_validity > 0 and recorded_validity != uid_validity:
            logger.warning(f"UIDVALIDITY changed for {self.account_config.name}/{self.folder}. Resetting sync state.")
            last_uid = 0

        new_uids = await self._client.fetch_uids_since(last_uid)
        if not new_uids:
            self.last_sync = datetime.now(timezone.utc)
            self.db.update_sync_state(self.account_config.id, self.folder, last_uid, uid_validity)
            return 0

        logger.info(f"Found {len(new_uids)} new emails on {self.account_config.name}/{self.folder}")
        highest_uid = last_uid
        ingested = 0

        for uid in sorted(new_uids):
            if not self._running:
                break
            try:
                raw_bytes = await self._client.fetch_raw_message(uid)
                if not raw_bytes:
                    continue

                msg, attachments = parse_email_message(
                    raw_bytes=raw_bytes,
                    account=self.account_config.name,
                    folder=self.folder,
                    uid=uid,
                )

                self.db.insert_email(msg, attachments)
                ingested += 1
                highest_uid = max(highest_uid, uid)

                # Optional real-time Obsidian export
                if self.auto_export_obsidian and self.obsidian_exporter and self.obsidian_vault_path:
                    from pathlib import Path
                    self.obsidian_exporter.export_email(msg, Path(self.obsidian_vault_path))

            except Exception as err:
                logger.error(f"Failed to process email UID {uid} on {self.account_config.name}/{self.folder}: {err}")

        self.last_sync = datetime.now(timezone.utc)
        self.db.update_sync_state(self.account_config.id, self.folder, highest_uid, uid_validity)
        return ingested
