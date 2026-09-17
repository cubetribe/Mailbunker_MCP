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
        """Fetch all message UIDs greater than since_uid.

        Prefers ``UID SEARCH`` (returns UIDs directly and is cheap). Some IMAP
        servers -- notably certain Courier-IMAP setups (e.g. All-Inkl/KAS) --
        reject ``UID SEARCH`` with "command UID only possible with COPY, FETCH,
        EXPUNGE (w/UIDPLUS) or STORE". For those we fall back to a plain
        ``FETCH 1:* (UID)`` and filter client-side; ``UID FETCH`` (used by
        :meth:`fetch_raw_message`) is still accepted by such servers.
        """
        await self.connect()
        assert self._client is not None

        if since_uid > 0:
            search_crit = f"UID {since_uid + 1}:*"
        else:
            search_crit = "ALL"

        # Preferred path: UID SEARCH.
        try:
            res, data = await self._client.uid("SEARCH", search_crit)
            if res == "OK":
                if not data:
                    return []
                uids_raw = data[0].decode("utf-8") if isinstance(data[0], bytes) else str(data[0])
                uids = [int(u) for u in uids_raw.split() if u.isdigit()]
                # Filter uids <= since_uid in case the server included the boundary UID.
                return [u for u in uids if u > since_uid]
            logger.debug(
                f"UID SEARCH rejected on {self.config.host} (res={res}); "
                f"falling back to FETCH (UID)."
            )
        except Exception as e:
            logger.debug(
                f"UID SEARCH not supported on {self.config.host} "
                f"({type(e).__name__}); falling back to FETCH (UID)."
            )

        return await self._fetch_uids_via_search_fetch(since_uid)

    async def _fetch_uids_via_search_fetch(self, since_uid: int = 0, batch_size: int = 300) -> List[int]:
        """Fallback UID enumeration for servers that reject ``UID SEARCH``.

        Some Courier-IMAP servers (e.g. All-Inkl/KAS) reject the ``UID SEARCH``
        command outright. A bulk ``FETCH 1:* (UID)`` is not a safe alternative
        either: aioimaplib appends one untagged response per message on a
        recursive code path, so a large mailbox (thousands of messages)
        overflows Python's recursion limit and desyncs the connection. Instead we
        issue a plain ``SEARCH`` (returns sequence numbers -- accepted by these
        servers) and map sequence numbers to UIDs with ``FETCH (UID)`` in bounded
        batches that stay well under the recursion limit. ``UID FETCH`` for the
        individual message bodies (see :meth:`fetch_raw_message`) is unaffected.
        """
        assert self._client is not None

        # Plain SEARCH returns sequence numbers (works where UID SEARCH is refused).
        try:
            res, data = await self._client.search("ALL")
        except Exception as e:
            logger.warning(f"Fallback SEARCH failed on {self.config.host}: {type(e).__name__}")
            return []
        if res != "OK" or not data:
            return []

        seq_raw = data[0].decode("utf-8") if isinstance(data[0], (bytes, bytearray)) else str(data[0])
        seqs = [int(s) for s in seq_raw.split() if s.isdigit()]
        if not seqs:
            return []

        uids: List[int] = []
        for i in range(0, len(seqs), batch_size):
            batch = seqs[i : i + batch_size]
            seqset = ",".join(str(s) for s in batch)
            try:
                fres, fdata = await self._client.fetch(seqset, "(UID)")
            except Exception as e:
                logger.warning(
                    f"Fallback FETCH (UID) batch failed on {self.config.host}: {type(e).__name__}"
                )
                continue
            if fres != "OK" or not fdata:
                continue
            for line in fdata:
                text = (
                    line.decode("utf-8", errors="ignore")
                    if isinstance(line, (bytes, bytearray))
                    else str(line)
                )
                m = re.search(r"\bUID\s+(\d+)", text)
                if m:
                    uids.append(int(m.group(1)))
        return sorted(u for u in uids if u > since_uid)

    async def fetch_raw_message(self, uid: int) -> Tuple[Optional[bytes], List[str]]:
        """
        Fetch RFC822 raw message bytes plus IMAP flags for a specific UID.

        Returns (raw_bytes, flags). `$Junk`/`\\Seen` etc. are valuable provider spam/trust
        signals (see PLAN v0.2.0). Falls back to an empty flags list if the server does not
        return a FLAGS item alongside BODY.PEEK[] (some servers omit it depending on fetch
        item ordering/quirks) -- the raw message itself is unaffected by that fallback.
        """
        await self.connect()
        assert self._client is not None

        res, data = await self._client.uid("FETCH", str(uid), "(FLAGS BODY.PEEK[])")
        if res != "OK" or not data:
            return None, []

        raw_bytes: Optional[bytes] = None
        flags: List[str] = []

        def _try_extract_flags(text: str) -> None:
            nonlocal flags
            match = re.search(r"FLAGS\s*\(([^)]*)\)", text)
            if match:
                flags = [f for f in match.group(1).split() if f]

        # aioimaplib returns a mix of plain response lines (bytes) and (header_line, body_bytes)
        # tuples for literal fetch items. FLAGS usually rides on a short header line/tuple head;
        # the actual RFC822 body is either the tuple's second element or a large standalone
        # bytes blob.
        for item in data:
            if isinstance(item, tuple) and len(item) > 1:
                header_part = item[0]
                if isinstance(header_part, (bytes, bytearray)):
                    _try_extract_flags(header_part.decode("utf-8", errors="ignore"))
                if isinstance(item[1], (bytes, bytearray)):
                    raw_bytes = bytes(item[1])
            elif isinstance(item, (bytes, bytearray)):
                if len(item) <= 512:
                    # Short line: most likely the FETCH response header/trailer, may carry FLAGS.
                    _try_extract_flags(item.decode("utf-8", errors="ignore"))
                elif raw_bytes is None:
                    # Discard FETCH headers if returned as a single unsplit block.
                    raw_bytes = bytes(item)

        return raw_bytes, flags
