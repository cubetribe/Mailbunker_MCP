"""SQLite Database with FTS5 Full-Text Search and Zero-Trust Encrypted Storage."""

from __future__ import annotations
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any

from .models import EmailMessage, EmailAddress, AttachmentMeta, SearchQuery, SearchResultItem, SearchResults
from ..crypto.engine import CryptoEngine
from ..crypto.vault import EncryptedFileVault


# Current schema/migration version. Bump when adding a new _migrate_vN step below.
SCHEMA_VERSION = 2


class MailbunkerDatabase:
    """Manages email storage, FTS5 full-text indexing, and encrypted payload retrieval."""

    def __init__(self, db_path: Path, crypto: CryptoEngine, vault: EncryptedFileVault):
        self.db_path = db_path.resolve()
        self.crypto = crypto
        self.vault = vault
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Initialize SQLite tables and FTS5 search index."""
        with self._get_conn() as conn:
            # 1. Main emails table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    uid INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    sender_name TEXT,
                    recipients TEXT,
                    date_iso TEXT NOT NULL,
                    date_timestamp INTEGER NOT NULL,
                    has_attachments INTEGER NOT NULL DEFAULT 0,
                    attachment_count INTEGER NOT NULL DEFAULT 0,
                    size INTEGER NOT NULL DEFAULT 0,
                    tags TEXT,
                    encrypted_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # Indices on structured fields for fast filtering
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_acc_folder ON emails(account, folder);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_timestamp DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_acc_uid ON emails(account, folder, uid);")

            # 2. FTS5 Virtual Table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
                    id UNINDEXED,
                    subject,
                    sender,
                    recipients,
                    body,
                    tags,
                    account,
                    folder,
                    tokenize = 'porter unicode61'
                );
            """)

            # 3. Attachments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    email_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    encrypted_file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (email_id) REFERENCES emails(id) ON DELETE CASCADE
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_attach_email ON attachments(email_id);")

            # 4. Sync State tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    account_id TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    last_uid INTEGER NOT NULL DEFAULT 0,
                    uid_validity INTEGER NOT NULL DEFAULT 0,
                    last_sync_timestamp INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (account_id, folder)
                );
            """)

            # 5. Ingest decision log (new table, brand-new so CREATE IF NOT EXISTS is
            # sufficient for idempotency without going through _migrate()).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ingest_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT,
                    folder TEXT,
                    uid INTEGER,
                    decision TEXT,
                    reason TEXT,
                    ts TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ingest_log_acc_folder_uid ON ingest_log(account_id, folder, uid);")
            conn.commit()

        # Run idempotent schema migrations (adds columns to a pre-existing `emails` table,
        # whether it came from an old install or was just created above with the base schema).
        self._migrate()

    def _migrate(self) -> None:
        """Idempotent schema migration runner, tracked via PRAGMA user_version.

        Safe to call on a fresh DB (columns already present after CREATE TABLE would just be
        skipped) and on a pre-existing DB in the old (Sprint 01) schema, which lacks the new
        ingest/classification columns entirely.
        """
        with self._get_conn() as conn:
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]

            if current_version < 1:
                self._migrate_v1_add_ingest_columns(conn)
                conn.execute("PRAGMA user_version = 1")
                current_version = 1

            if current_version < 2:
                self._migrate_v2_add_classifier_columns(conn)
                conn.execute("PRAGMA user_version = 2")
                current_version = 2

            conn.commit()

    def _migrate_v1_add_ingest_columns(self, conn: sqlite3.Connection) -> None:
        """Migration step 1: add quarantine/classification columns via ALTER TABLE, only if
        missing (checked via PRAGMA table_info). No FTS5 schema changes are needed for this
        step, so no drop+reindex is required here.
        """
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
        new_columns: Dict[str, str] = {
            "quarantined": "INTEGER DEFAULT 0",
            "spam_score": "REAL DEFAULT 0",
            "trust_level": "TEXT",
            "classification_json": "TEXT",
            "classified_at": "TEXT",
            "embedding_state": "TEXT DEFAULT 'pending'",
            "raw_header_blob": "TEXT",
        }
        for col_name, col_decl in new_columns.items():
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE emails ADD COLUMN {col_name} {col_decl}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_quarantined ON emails(quarantined);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_classified ON emails(classified_at);")

    def _migrate_v2_add_classifier_columns(self, conn: sqlite3.Connection) -> None:
        """Migration step 2 (Sprint 04): add the Ollama classifier's structured-filter columns.

        `classification_json`/`classified_at` already exist from migration v1 (Sprint 02 reserved
        them for this sprint); this step only adds the three fast-filter columns
        (`spam_verdict`/`category`/`priority`) extracted out of that JSON blob. Idempotent via the
        same existence-guard pattern as v1, and `PRAGMA user_version` is only advanced by the
        caller (`_migrate`) after this returns without error.
        """
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
        new_columns: Dict[str, str] = {
            "spam_verdict": "TEXT",
            "category": "TEXT",
            "priority": "TEXT",
        }
        for col_name, col_decl in new_columns.items():
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE emails ADD COLUMN {col_name} {col_decl}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_category ON emails(category);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_spam_verdict ON emails(spam_verdict);")

    def exists(self, account: str, folder: str, uid: int, message_id: str) -> bool:
        """Check if an email is already indexed."""
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT 1 FROM emails WHERE (account = ? AND folder = ? AND uid = ?) OR (account = ? AND message_id = ?) LIMIT 1",
                (account, folder, uid, account, message_id)
            )
            return cur.fetchone() is not None

    def insert_email(
        self,
        email_msg: EmailMessage,
        attachments_data: Optional[List[Tuple[AttachmentMeta, bytes]]] = None,
        preserve_classification: bool = True,
    ) -> str:
        """
        Store email with Zero-Trust encryption:
        - Attachments encrypted on disk
        - Full payload (body markdown, html, raw headers, attachments) encrypted with AES-256-GCM
        - Raw header block encrypted separately (same AES-256-GCM engine) for later trust
          verification (DKIM/SPF) without needing full payload access
        - Searchable text indexed into FTS5

        Re-ingest safety: `INSERT OR REPLACE` would otherwise clobber a prior quarantine or
        classification decision on the same email id. When `preserve_classification` is True
        (the default) and a row with this id already exists, the existing
        quarantined/spam_score/trust_level/classification_json/classified_at/embedding_state
        values are carried forward instead of being reset to `email_msg`'s (usually default)
        values. Pass `preserve_classification=False` to force an explicit overwrite (e.g. when
        the caller -- such as the future classifier worker -- deliberately wants to (re)apply a
        new decision).
        """
        attachments_data = attachments_data or []
        saved_attach_metas = []

        # 1. Save and encrypt attachments
        for meta, raw_bytes in attachments_data:
            rel_path = f"attachments/{email_msg.id}/{meta.sha256[:12]}_{meta.filename}"
            self.vault.write_bytes(rel_path, raw_bytes)
            meta.storage_path = rel_path
            saved_attach_metas.append(meta)

        email_msg.attachments = saved_attach_metas

        # 2. Encrypt full payload
        payload = {
            "body_markdown": email_msg.body_markdown,
            "body_text": email_msg.body_text,
            "body_html": email_msg.body_html,
            "in_reply_to": email_msg.in_reply_to,
            "references": email_msg.references,
            "raw_headers": email_msg.raw_headers,
            "attachments": [a.model_dump() for a in email_msg.attachments],
            "to": [a.model_dump() for a in email_msg.to],
            "cc": [a.model_dump() for a in email_msg.cc],
            "bcc": [a.model_dump() for a in email_msg.bcc],
            "flags": email_msg.flags,
            # Sprint 03: hidden_text/tracking_domains live inside the same encrypted payload
            # blob as body_html/raw_headers (not a new column) -- they are already "sensitive
            # field" material by the existing payload-encryption boundary, and hidden_text in
            # particular must never appear in body_text/body_markdown/FTS/embedding input,
            # which this placement guarantees for free (the FTS insert below only ever reads
            # email_msg.body_text).
            "hidden_text": email_msg.hidden_text,
            "tracking_domains": email_msg.tracking_domains,
        }
        encrypted_payload = self.crypto.encrypt_json(payload)

        # 2b. Encrypt raw header block separately (same AES-256-GCM engine as other sensitive
        # fields), so DKIM/SPF re-verification never needs to touch the full body payload.
        encrypted_header_blob = (
            self.crypto.encrypt_str(email_msg.raw_header_blob) if email_msg.raw_header_blob else None
        )

        # 3. Prepare structured fields
        recipients_str = ", ".join(f"{a.name} <{a.email}>" if a.name else a.email for a in email_msg.to)
        sender_str = f"{email_msg.sender.name} <{email_msg.sender.email}>" if email_msg.sender.name else email_msg.sender.email
        date_iso = email_msg.date.isoformat()
        date_ts = int(email_msg.date.timestamp())
        now_iso = datetime.now(timezone.utc).isoformat()
        tags_str = " ".join(email_msg.tags)

        # 3b. Re-ingest merge: preserve an existing quarantine/classification decision instead
        # of letting INSERT OR REPLACE reset it to email_msg's (usually default) values.
        quarantined = 1 if email_msg.quarantined else 0
        spam_score = email_msg.spam_score
        trust_level = email_msg.trust_level
        classification_json = email_msg.classification_json
        classified_at = email_msg.classified_at
        embedding_state = email_msg.embedding_state
        # Sprint 04: same re-ingest preservation contract as the Sprint 02/03 columns above --
        # a re-ingest of the same message (e.g. IDLE redelivery) must not clobber a classifier
        # verdict that already ran.
        spam_verdict = email_msg.spam_verdict
        category = email_msg.category
        priority = email_msg.priority

        with self._get_conn() as conn:
            if preserve_classification:
                existing = conn.execute(
                    """SELECT quarantined, spam_score, trust_level, classification_json,
                              classified_at, embedding_state, spam_verdict, category, priority
                       FROM emails WHERE id = ? LIMIT 1""",
                    (email_msg.id,),
                ).fetchone()
                if existing is not None:
                    quarantined = existing["quarantined"]
                    spam_score = existing["spam_score"]
                    trust_level = existing["trust_level"]
                    classification_json = existing["classification_json"]
                    classified_at = existing["classified_at"]
                    embedding_state = existing["embedding_state"]
                    spam_verdict = existing["spam_verdict"]
                    category = existing["category"]
                    priority = existing["priority"]

            # Insert or replace in emails table
            conn.execute("""
                INSERT OR REPLACE INTO emails (
                    id, account, folder, uid, message_id, subject, sender_email, sender_name,
                    recipients, date_iso, date_timestamp, has_attachments, attachment_count,
                    size, tags, encrypted_payload, created_at,
                    quarantined, spam_score, trust_level, classification_json, classified_at,
                    embedding_state, raw_header_blob, spam_verdict, category, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                email_msg.id,
                email_msg.account,
                email_msg.folder,
                email_msg.uid,
                email_msg.message_id,
                email_msg.subject,
                email_msg.sender.email,
                email_msg.sender.name,
                recipients_str,
                date_iso,
                date_ts,
                1 if email_msg.attachments else 0,
                len(email_msg.attachments),
                email_msg.size,
                tags_str,
                encrypted_payload,
                now_iso,
                quarantined,
                spam_score,
                trust_level,
                classification_json,
                classified_at,
                embedding_state,
                encrypted_header_blob,
                spam_verdict,
                category,
                priority,
            ))

            # Update FTS5 index
            conn.execute("DELETE FROM emails_fts WHERE id = ?", (email_msg.id,))
            conn.execute("""
                INSERT INTO emails_fts (id, subject, sender, recipients, body, tags, account, folder)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                email_msg.id,
                email_msg.subject,
                sender_str,
                recipients_str,
                email_msg.body_text[:100000],  # index up to 100k chars of body text
                tags_str,
                email_msg.account,
                email_msg.folder,
            ))

            # Record attachments
            conn.execute("DELETE FROM attachments WHERE email_id = ?", (email_msg.id,))
            for meta in email_msg.attachments:
                attach_id = f"{email_msg.id}_{meta.sha256[:8]}"
                conn.execute("""
                    INSERT INTO attachments (
                        id, email_id, filename, content_type, size, sha256, encrypted_file_path, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    attach_id,
                    email_msg.id,
                    meta.filename,
                    meta.content_type,
                    meta.size,
                    meta.sha256,
                    meta.storage_path or "",
                    now_iso,
                ))

            conn.commit()

        return email_msg.id

    def get_email(self, email_id: str) -> Optional[EmailMessage]:
        """Fetch and decrypt an email message by ID."""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM emails WHERE id = ? LIMIT 1", (email_id,))
            row = cur.fetchone()
            if not row:
                return None

            try:
                payload = self.crypto.decrypt_json(row["encrypted_payload"])
            except Exception as e:
                raise RuntimeError(f"Failed to decrypt email {email_id}: {e}")

            sender = EmailAddress(
                name=row["sender_name"] or "",
                email=row["sender_email"],
            )

            to_addrs = [EmailAddress(**a) for a in payload.get("to", [])]
            cc_addrs = [EmailAddress(**a) for a in payload.get("cc", [])]
            bcc_addrs = [EmailAddress(**a) for a in payload.get("bcc", [])]
            attachments = [AttachmentMeta(**a) for a in payload.get("attachments", [])]

            tags = [t for t in (row["tags"] or "").split(" ") if t]

            row_keys = row.keys()
            raw_header_blob = None
            if "raw_header_blob" in row_keys and row["raw_header_blob"]:
                try:
                    raw_header_blob = self.crypto.decrypt_str(row["raw_header_blob"])
                except Exception:
                    raw_header_blob = None

            return EmailMessage(
                id=row["id"],
                account=row["account"],
                folder=row["folder"],
                uid=row["uid"],
                message_id=row["message_id"],
                subject=row["subject"],
                sender=sender,
                to=to_addrs,
                cc=cc_addrs,
                bcc=bcc_addrs,
                date=datetime.fromisoformat(row["date_iso"]),
                in_reply_to=payload.get("in_reply_to"),
                references=payload.get("references", []),
                body_text=payload.get("body_text", ""),
                body_markdown=payload.get("body_markdown", ""),
                body_html=payload.get("body_html"),
                attachments=attachments,
                flags=payload.get("flags", []),
                size=row["size"],
                raw_headers=payload.get("raw_headers", {}),
                raw_header_blob=raw_header_blob,
                hidden_text=payload.get("hidden_text"),
                tracking_domains=payload.get("tracking_domains", []),
                tags=tags,
                quarantined=bool(row["quarantined"]) if "quarantined" in row_keys and row["quarantined"] is not None else False,
                spam_score=row["spam_score"] if "spam_score" in row_keys and row["spam_score"] is not None else 0.0,
                trust_level=row["trust_level"] if "trust_level" in row_keys else None,
                classification_json=row["classification_json"] if "classification_json" in row_keys else None,
                classified_at=row["classified_at"] if "classified_at" in row_keys else None,
                embedding_state=row["embedding_state"] if "embedding_state" in row_keys and row["embedding_state"] else "pending",
                spam_verdict=row["spam_verdict"] if "spam_verdict" in row_keys else None,
                category=row["category"] if "category" in row_keys else None,
                priority=row["priority"] if "priority" in row_keys else None,
            )

    def search_emails(self, query: SearchQuery) -> SearchResults:
        """
        Execute full-text and structured search across emails using FTS5.

        Quarantined emails (`quarantined = 1`) are hidden by default -- pass
        `SearchQuery(include_quarantined=True)` to include them (e.g. for a review UI).
        """
        conditions = []
        params: List[Any] = []

        # 1. Full-text search expression. NOTE: the actual `emails_fts MATCH ?` predicate is
        # applied directly against the joined FTS table further down (not wrapped in a
        # subquery) so that `snippet()` can see the match and highlight the real hit instead
        # of always returning the first ~25 tokens of the row.
        use_fts = bool(query.query and query.query.strip())
        fts_match_param: Optional[str] = None
        if use_fts:
            clean_q = query.query.strip()
            # If query contains special FTS characters or spaces without quotes, sanitize
            # Format query for FTS5
            fts_query = clean_q
            # If not already quoted or containing boolean operators, escape quotes
            if not any(op in clean_q for op in ["AND", "OR", "NOT", "*", '"']):
                # Word prefix matching for better search UX
                words = [w for w in clean_q.split() if w]
                fts_query = " ".join(f'"{w}"*' for w in words)

            fts_match_param = fts_query

        # 2. Structured filters
        if query.account:
            conditions.append("emails.account = ?")
            params.append(query.account)

        if query.folder:
            conditions.append("emails.folder = ?")
            params.append(query.folder)

        if query.start_date:
            conditions.append("emails.date_timestamp >= ?")
            params.append(int(query.start_date.timestamp()))

        if query.end_date:
            conditions.append("emails.date_timestamp <= ?")
            params.append(int(query.end_date.timestamp()))

        if query.has_attachments is not None:
            conditions.append("emails.has_attachments = ?")
            params.append(1 if query.has_attachments else 0)

        # Sprint 04: additive structured filters over the Ollama classifier's output columns.
        if query.category:
            conditions.append("emails.category = ?")
            params.append(query.category)

        if query.spam_verdict:
            conditions.append("emails.spam_verdict = ?")
            params.append(query.spam_verdict)

        # 2b. Quarantine gate -- hidden from search/MCP by default (Plan v0.2.0: "store +
        # hide"). Applied unconditionally unless the caller explicitly opts in.
        if not query.include_quarantined:
            conditions.append("COALESCE(emails.quarantined, 0) = 0")

        if use_fts:
            base_from = "FROM emails JOIN emails_fts ON emails.id = emails_fts.id"
            all_conditions = ["emails_fts MATCH ?"] + conditions
            all_params: List[Any] = [fts_match_param] + params
        else:
            base_from = "FROM emails"
            all_conditions = conditions
            all_params = list(params)

        where_clause = " WHERE " + " AND ".join(all_conditions) if all_conditions else ""

        with self._get_conn() as conn:
            # Count total matching
            count_sql = f"SELECT COUNT(*) AS total {base_from}{where_clause}"
            total = conn.execute(count_sql, all_params).fetchone()["total"]

            # Query results with snippet if FTS used. Highlight markers are chosen to avoid
            # colliding with Obsidian syntax (e.g. '==' is Obsidian's own highlight markup).
            if use_fts:
                select_sql = f"""
                    SELECT
                        emails.id, emails.account, emails.folder, emails.message_id,
                        emails.subject, emails.sender_email, emails.sender_name,
                        emails.date_iso, emails.has_attachments, emails.attachment_count,
                        emails.tags,
                        snippet(emails_fts, 4, '»', '«', '...', 25) AS snippet_text
                    {base_from}
                    {where_clause}
                    ORDER BY emails.date_timestamp DESC
                    LIMIT ? OFFSET ?
                """
            else:
                select_sql = f"""
                    SELECT
                        emails.id, emails.account, emails.folder, emails.message_id,
                        emails.subject, emails.sender_email, emails.sender_name,
                        emails.date_iso, emails.has_attachments, emails.attachment_count,
                        emails.tags,
                        '' AS snippet_text
                    {base_from}
                    {where_clause}
                    ORDER BY emails.date_timestamp DESC
                    LIMIT ? OFFSET ?
                """

            cur = conn.execute(select_sql, all_params + [query.limit, query.offset])
            rows = cur.fetchall()

            items = []
            for row in rows:
                sender_str = f"{row['sender_name']} <{row['sender_email']}>" if row["sender_name"] else row["sender_email"]
                snippet = row["snippet_text"] or f"From: {sender_str} | Subject: {row['subject']}"
                tags = [t for t in (row["tags"] or "").split(" ") if t]

                items.append(SearchResultItem(
                    id=row["id"],
                    account=row["account"],
                    folder=row["folder"],
                    message_id=row["message_id"],
                    subject=row["subject"],
                    sender=sender_str,
                    date=datetime.fromisoformat(row["date_iso"]),
                    snippet=snippet,
                    has_attachments=bool(row["has_attachments"]),
                    attachment_count=row["attachment_count"],
                    tags=tags,
                ))

            return SearchResults(
                total=total,
                limit=query.limit,
                offset=query.offset,
                results=items,
            )

    def get_sync_state(self, account_id: str, folder: str) -> Tuple[int, int]:
        """Return (last_uid, uid_validity) for given account and folder."""
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT last_uid, uid_validity FROM sync_state WHERE account_id = ? AND folder = ?",
                (account_id, folder)
            )
            row = cur.fetchone()
            if row:
                return row["last_uid"], row["uid_validity"]
            return 0, 0

    def update_sync_state(self, account_id: str, folder: str, last_uid: int, uid_validity: int) -> None:
        """Update last UID and UID validity after sync."""
        with self._get_conn() as conn:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            conn.execute("""
                INSERT OR REPLACE INTO sync_state (account_id, folder, last_uid, uid_validity, last_sync_timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (account_id, folder, last_uid, uid_validity, now_ts))
            conn.commit()

    def log_ingest_decision(
        self,
        account_id: str,
        folder: str,
        uid: int,
        decision: str,
        reason: str = "",
    ) -> None:
        """
        Record an ingest decision (store|quarantine|block|error) for a given UID.

        This is the audit trail the watermark fix relies on: a skipped/filtered/failed UID no
        longer just vanishes -- it is always logged here, whether or not it advanced the sync
        watermark.
        """
        with self._get_conn() as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO ingest_log (account_id, folder, uid, decision, reason, ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (account_id, folder, uid, decision, reason, now_iso),
            )
            conn.commit()

    def get_unclassified_email_ids(
        self,
        limit: int,
        account: Optional[str] = None,
        backfill: bool = False,
    ) -> List[str]:
        """
        Return up to `limit` email ids eligible for the async Ollama classifier worker
        (Sprint 04): never quarantined mail (it is already hidden from search/MCP and does not
        need vault enrichment), and -- unless `backfill` is True -- only mail that has never
        been classified yet (`classified_at IS NULL`), so a plain `mailbunker classify` run
        stays idempotent and never reprocesses already-classified mail. `--backfill` drops that
        guard to (re)process the existing backlog on demand.
        """
        conditions = ["COALESCE(quarantined, 0) = 0"]
        params: List[Any] = []

        if not backfill:
            conditions.append("classified_at IS NULL")

        if account:
            conditions.append("account = ?")
            params.append(account)

        where_clause = " WHERE " + " AND ".join(conditions)
        sql = f"SELECT id FROM emails{where_clause} ORDER BY date_timestamp ASC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            cur = conn.execute(sql, params)
            return [row["id"] for row in cur.fetchall()]

    def save_classification(
        self,
        email_id: str,
        spam_verdict: str,
        category: str,
        priority: str,
        classification_json: str,
        classified_at: str,
    ) -> None:
        """
        Persist the Ollama classifier's verdict for one email (Sprint 04). Only touches the
        classifier's own columns -- `quarantined`/`spam_score`/`trust_level` remain the Sprint 03
        filter layer's exclusive responsibility, so a classify run can never silently override an
        existing filter decision.
        """
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE emails
                   SET spam_verdict = ?, category = ?, priority = ?, classification_json = ?,
                       classified_at = ?
                   WHERE id = ?""",
                (spam_verdict, category, priority, classification_json, classified_at, email_id),
            )
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Return global statistics about the bunker."""
        with self._get_conn() as conn:
            total_emails = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
            total_attachments = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            total_size_bytes = conn.execute("SELECT COALESCE(SUM(size), 0) FROM emails").fetchone()[0]
            
            acc_cur = conn.execute("""
                SELECT account, COUNT(*) as count 
                FROM emails 
                GROUP BY account
            """)
            accounts_breakdown = {row["account"]: row["count"] for row in acc_cur.fetchall()}

            return {
                "total_emails": total_emails,
                "total_attachments": total_attachments,
                "total_raw_size_bytes": total_size_bytes,
                "accounts": accounts_breakdown,
                "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            }
