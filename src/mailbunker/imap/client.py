"""Asynchronous IMAP client using aioimaplib."""

from __future__ import annotations
import re
import asyncio
import logging
from typing import List, Optional, Tuple
import aioimaplib

from ..config import AccountConfig

logger = logging.getLogger("mailbunker.imap.client")


class AsyncImapClient:
    """Async IMAP Client for querying and streaming emails."""

    def __init__(self, config: AccountConfig):
        self.config = config
        self._client: Optional[aioimaplib.IMAP4_SSL | aioimaplib.IMAP4] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def connect(self) -> None:
        """Establish IMAP connection and authenticate."""
        if self.is_connected:
            return

        logger.info(f"Connecting to IMAP {self.config.host}:{self.config.port} for user {self.config.user}")
        if self.config.ssl:
            client = aioimaplib.IMAP4_SSL(host=self.config.host, port=self.config.port, timeout=30)
        else:
            client = aioimaplib.IMAP4(host=self.config.host, port=self.config.port, timeout=30)

        await client.wait_hello_from_server()
        res, data = await client.login(self.config.user, self.config.password)
        if res != "OK":
            raise ConnectionError(f"IMAP login failed for {self.config.user} on {self.config.host}: {data}")

        self._client = client
        self._connected = True
        logger.info(f"Successfully connected and logged in to {self.config.name} ({self.config.user})")

    async def disconnect(self) -> None:
        """Gracefully logout and close connection."""
        if self._client:
            try:
                await self._client.logout()
            except Exception:
                pass
            finally:
                self._client = None
                self._connected = False

    async def list_mailboxes(self) -> List[str]:
        """List all available mailboxes/folders."""
        await self.connect()
        assert self._client is not None

        res, data = await self._client.list('""', "*")
        if res != "OK":
            raise RuntimeError(f"Failed to list mailboxes: {data}")

        folders = []
        for line in data:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            # Parse IMAP list line: e.g. (\HasNoChildren) "/" "INBOX"
            match = re.search(r'"([^"]+)"$', line.strip()) or re.search(r'([^\s"]+)$', line.strip())
            if match:
                folder_name = match.group(1)
                folders.append(folder_name)
        return folders or ["INBOX"]

    async def select_folder(self, folder: str = "INBOX") -> Tuple[int, int]:
        """
        Select a folder and return (message_count, uid_validity).
        """
        await self.connect()
        assert self._client is not None

        # Quote folder name if it has spaces or special chars
        quoted_folder = f'"{folder}"' if " " in folder or "/" in folder else folder
        res, data = await self._client.select(quoted_folder)
        if res != "OK":
            raise RuntimeError(f"Failed to select folder {folder}: {data}")

        msg_count = 0
        if data and data[0]:
            try:
                val = data[0].decode("utf-8") if isinstance(data[0], bytes) else str(data[0])
                msg_count = int(val.strip())
            except Exception:
                pass

        # Fetch UIDVALIDITY
        uid_validity = 0
        status_res, status_data = await self._client.status(quoted_folder, "(UIDVALIDITY)")
        if status_res == "OK" and status_data:
            val_str = status_data[0].decode("utf-8") if isinstance(status_data[0], bytes) else str(status_data[0])
            m = re.search(r"UIDVALIDITY\s+(\d+)", val_str)
            if m:
                uid_validity = int(m.group(1))

        return msg_count, uid_validity

    async def fetch_uids_since(self, since_uid: int = 0) -> List[int]:
        """Fetch all message UIDs greater than since_uid."""
        await self.connect()
        assert self._client is not None

        if since_uid > 0:
            search_crit = f"UID {since_uid + 1}:*"
        else:
            search_crit = "ALL"

        res, data = await self._client.uid("SEARCH", search_crit)
        if res != "OK" or not data:
            return []

        uids_raw = data[0].decode("utf-8") if isinstance(data[0], bytes) else str(data[0])
        uids = [int(u) for u in uids_raw.split() if u.isdigit()]
        # Filter out uids <= since_uid in case server returned since_uid on range matching
        return [u for u in uids if u > since_uid]

    async def fetch_raw_message(self, uid: int) -> Optional[bytes]:
        """Fetch RFC822 raw message bytes for a specific UID."""
        await self.connect()
        assert self._client is not None

        res, data = await self._client.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if res != "OK" or not data:
            return None

        # Data contains tuples or alternating strings and bytes
        for item in data:
            if isinstance(item, (bytes, bytearray)) and len(item) > 10:
                # Discard FETCH headers if returned as single block
                return bytes(item)
            if isinstance(item, tuple) and len(item) > 1:
                return bytes(item[1])

        return None
