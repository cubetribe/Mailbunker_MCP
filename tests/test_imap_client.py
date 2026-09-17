"""Regression tests for AsyncImapClient UID enumeration.

Covers the Courier-IMAP (e.g. All-Inkl/KAS) case where the server rejects the
``UID SEARCH`` command and a bulk ``FETCH 1:*`` would overflow aioimaplib's
recursion on large mailboxes. The client must then fall back to a plain
``SEARCH`` plus batched ``FETCH (UID)``.
"""

from __future__ import annotations

import pytest

from mailbunker.config import AccountConfig
from mailbunker.imap.client import AsyncImapClient


def _account() -> AccountConfig:
    return AccountConfig(
        id="test",
        name="Test",
        host="v135276.kasserver.com",
        port=993,
        user="javis@example.com",
        password="secret",
    )


class _FakeUidSearchOk:
    """Server that supports UID SEARCH (preferred path)."""

    async def uid(self, command, *args):
        assert command == "SEARCH"
        return "OK", [b"101 102 103 104 105"]


class _FakeCourier:
    """Server that rejects UID SEARCH but supports plain SEARCH + FETCH (UID)."""

    def __init__(self, seq_to_uid: dict[int, int]):
        self._seq_to_uid = seq_to_uid
        self.fetch_batches: list[str] = []

    async def uid(self, command, *args):
        if command == "SEARCH":
            raise Exception("command UID only possible with COPY, FETCH, EXPUNGE (w/UIDPLUS) or STORE (was SEARCH)")
        raise AssertionError(f"unexpected uid command {command}")

    async def search(self, *criteria):
        seqs = " ".join(str(s) for s in sorted(self._seq_to_uid))
        return "OK", [seqs.encode()]

    async def fetch(self, seqset, parts):
        self.fetch_batches.append(seqset)
        lines = []
        for token in seqset.split(","):
            seq = int(token)
            uid = self._seq_to_uid[seq]
            lines.append(f"{seq} FETCH (UID {uid})".encode())
        return "OK", lines


def _wire(client: AsyncImapClient, fake) -> None:
    """Inject a fake low-level client and mark the connection live so that
    ``fetch_uids_since`` skips the real ``connect()``."""
    client._client = fake
    client._connected = True


@pytest.mark.asyncio
async def test_uid_search_preferred_path():
    client = AsyncImapClient(_account())
    _wire(client, _FakeUidSearchOk())
    uids = await client.fetch_uids_since(0)
    assert uids == [101, 102, 103, 104, 105]

    # since_uid filters the boundary out
    uids2 = await client.fetch_uids_since(103)
    assert uids2 == [104, 105]


@pytest.mark.asyncio
async def test_courier_fallback_search_plus_batched_fetch():
    # 5 messages, UID = 100 + sequence number
    seq_to_uid = {s: 100 + s for s in range(1, 6)}
    fake = _FakeCourier(seq_to_uid)
    client = AsyncImapClient(_account())
    _wire(client, fake)

    uids = await client.fetch_uids_since(0)
    assert uids == [101, 102, 103, 104, 105]
    # incremental
    assert await client.fetch_uids_since(103) == [104, 105]


@pytest.mark.asyncio
async def test_courier_fallback_batches_stay_small():
    # 700 messages must be fetched in >1 batch (batch_size=300) so aioimaplib's
    # per-untagged-response recursion never approaches Python's limit.
    seq_to_uid = {s: 1000 + s for s in range(1, 701)}
    fake = _FakeCourier(seq_to_uid)
    client = AsyncImapClient(_account())
    _wire(client, fake)

    uids = await client.fetch_uids_since(0)
    assert uids == sorted(seq_to_uid.values())
    assert len(uids) == 700
    # 700 / 300 -> 3 batches, each well under the recursion limit
    assert len(fake.fetch_batches) == 3
    assert all(len(b.split(",")) <= 300 for b in fake.fetch_batches)
