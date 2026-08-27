---
sprint: 02
title: Ingest-Fundament (Migration, Choke-Point, Header, Watermark, Schema)
agent: builder
status: done
---

# Sprint 02 — Builder Report

## Summary

Implemented the structural ingest foundation on top of Sprint 01's hardened MCP/security base
(left untouched): a versioned SQLite migration mechanism, a single shared `ingest_raw()`
choke-point used by both the one-shot sync path and the IDLE push path, extended trust-header
capture plus a full raw-header-block persisted encrypted, IMAP FLAGS fetch, a watermark/audit-log
fix that no longer silently drops filtered/failed UIDs, quarantine-aware search, and a fix for
the FTS5 `snippet()` bug. The Sprint 03 filter layers plug into `ingest/pipeline.py::filter_hook`
without needing to touch either ingest call site again; in Sprint 02 that hook is a deliberate
No-Op (`decision=store`).

## Files Changed

**New:**
- `src/mailbunker/ingest/__init__.py`
- `src/mailbunker/ingest/pipeline.py`
- `tests/test_ingest.py`

**Modified:**
- `src/mailbunker/storage/database.py` — migration mechanism, schema extension, re-ingest merge
  logic, quarantine-aware search, FTS snippet fix, `log_ingest_decision`
- `src/mailbunker/storage/models.py` — `EmailMessage` classification/quarantine fields +
  `raw_header_blob`; `SearchQuery.include_quarantined`
- `src/mailbunker/imap/parser.py` — extended trust-header whitelist, `X-Spam-*` capture, raw
  header block extraction, optional `flags` param
- `src/mailbunker/imap/client.py` — `fetch_raw_message` now fetches `(FLAGS BODY.PEEK[])` and
  returns `(raw_bytes, flags)`
- `src/mailbunker/imap/sync_manager.py` — routes through `ingest_raw`, watermark/audit-log fix
- `src/mailbunker/imap/idle_listener.py` — routes through `ingest_raw`, watermark/audit-log fix
- `tests/test_database.py` — quarantine search, snippet fix, re-ingest merge, header-blob
  encryption tests
- `tests/test_parser.py` — extended header whitelist, X-Spam-*, raw_header_blob, flags tests

## Implementation Notes (per Scope Item)

**1. Migration mechanism (`database.py`)** — `SCHEMA_VERSION = 1`. `_init_db()` keeps the
original `CREATE TABLE IF NOT EXISTS` base schema (so both a brand-new DB and a Sprint-01-era DB
end up in the same starting state) and always calls `_migrate()` afterwards, which reads
`PRAGMA user_version` and — if `< 1` — runs `_migrate_v1_add_ingest_columns()`, then sets
`PRAGMA user_version = 1`. The migration checks `PRAGMA table_info(emails)` and only issues
`ALTER TABLE emails ADD COLUMN ...` for columns that are actually missing, so it is safe to run
against both a fresh DB and a legacy one, and idempotent on every subsequent open. No FTS5
schema changes were needed for this sprint's columns (they're all on `emails`, not `emails_fts`),
so no drop+reindex path was required — noted in code for future sprints that do touch FTS
columns.

**2. Schema extension** — `emails` gained `quarantined INTEGER DEFAULT 0`, `spam_score REAL
DEFAULT 0`, `trust_level TEXT`, `classification_json TEXT`, `classified_at TEXT`,
`embedding_state TEXT DEFAULT 'pending'`, `raw_header_blob TEXT` (ciphertext, AES-256-GCM via
the existing `CryptoEngine.encrypt_str`/`decrypt_str`, same mechanism as other sensitive
fields). Indices `idx_emails_quarantined`, `idx_emails_classified` added in the migration step.
New table `ingest_log(id, account_id, folder, uid, decision, reason, ts)` plus
`idx_ingest_log_acc_folder_uid`, created via `CREATE TABLE IF NOT EXISTS` in `_init_db` (new
table, no migration needed for idempotency).

**3. Shared choke-point** — new `src/mailbunker/ingest/pipeline.py`:
`ingest_raw(db, raw_bytes, account, folder, uid, flags=None) -> Tuple[IngestDecision,
Optional[EmailMessage]]`. `IngestDecision` is a `@dataclass(decision, reason, score)`;
`decision` is one of module-level constants `DECISION_STORE`/`DECISION_QUARANTINE`/
`DECISION_BLOCK`. `filter_hook(email_msg) -> IngestDecision` is the No-Op Sprint 02 hook
(`decision=store`, `reason="no-op-sprint02"`) — Sprint 03 replaces its body only, no call-site
changes required. `parse_email_message` is imported lazily inside `ingest_raw()` to avoid a
circular import (`mailbunker.imap.__init__` eagerly imports `sync_manager`/`idle_listener`,
which import this module at load time). Both `sync_manager.py:sync_folder` and
`idle_listener.py:_sync_pending_emails` now call `ingest_raw` exclusively instead of
`parse_email_message()` + `db.insert_email()` directly.

**4. Header-erhalt (`parser.py`)** — `TRUST_HEADER_WHITELIST` extended with
`Authentication-Results`, `Received-SPF`, `DKIM-Signature`, `ARC-Authentication-Results`,
`Return-Path`, `List-Id`, `Precedence`, `Auto-Submitted`, `X-Mailer`. A second pass over
`msg.items()` captures every header whose name starts with `x-spam-` (case-insensitive),
regardless of provider-specific suffix. New `extract_raw_header_block(raw_bytes) -> str`
extracts everything before the first blank line (`\r\n\r\n` or `\n\n`) from the raw bytes and
decodes it (utf-8 with `errors="replace"`, latin-1 fallback); the result is stored on
`EmailMessage.raw_header_blob` and persisted encrypted in its own DB column (not the JSON
payload), independent of the parser's own header-whitelisting logic.

**5. IMAP FLAGS (`client.py`)** — `fetch_raw_message` now issues `(FLAGS BODY.PEEK[])` and
returns `Tuple[Optional[bytes], List[str]]` (**signature change**, was `Optional[bytes]`). Flags
are extracted from either the tuple's header line or short standalone response lines via a
`FLAGS\s*\(([^)]*)\)` regex; if the server doesn't return them, `flags` is simply `[]` and the
raw body extraction is unaffected (verified no existing test mocked the old signature).

**6. Watermark fix** — both `sync_manager.sync_folder` and
`idle_listener._sync_pending_emails` now advance `highest_uid` for *every* UID that produced an
`ingest_raw` decision (store/quarantine/block), not only for successful stores. A genuine
exception (fetch/parse failure) is caught separately, logged to `ingest_log` with
`decision="error"`, and does **not** advance the watermark, so it is retried on the next sync
pass. A message that vanished between SEARCH and FETCH (`raw_bytes is None`) is logged as
`decision="empty"` and does advance the watermark (nothing to retry). Every one of these four
outcomes (store/quarantine/block/error/empty) is now visible in `ingest_log` via
`db.log_ingest_decision`.

**7. Quarantine-aware search (`database.py::search_emails`)** — `SearchQuery.include_quarantined:
bool = False` added. Unless set, `search_emails` appends `COALESCE(emails.quarantined, 0) = 0` to
every query (FTS and structured). `mcp/tools.py` was not modified (out of write-scope) — its
`search_emails_impl` constructs `SearchQuery` via keyword args without `include_quarantined`, so
it picks up the new default (hide quarantined) automatically without any change on its side.

**8. FTS snippet fix** — restructured `search_emails` so `emails_fts MATCH ?` is a top-level
predicate on the joined `FROM emails JOIN emails_fts ON emails.id = emails_fts.id` query (both
count and select), instead of being buried inside an `id IN (SELECT id FROM emails_fts WHERE
... MATCH ?)` subquery. `snippet()` can now see the match and highlights the real hit.
Highlight markers changed from `'==' / '=='` (collides with Obsidian's own highlight syntax) to
`'»' / '«'`.

**Re-ingest merge logic** — `insert_email` gained `preserve_classification: bool = True`. Before
the `INSERT OR REPLACE`, if a row with the same `id` already exists and
`preserve_classification` is true, the existing `quarantined`, `spam_score`, `trust_level`,
`classification_json`, `classified_at`, `embedding_state` values are read back and reused for
the write, instead of being overwritten by `email_msg`'s (usually default) values. Pass
`preserve_classification=False` to force an explicit overwrite (for a future worker that
deliberately re-applies a decision).

## Assumptions Made (undocumented in sprint file, decided here)

- `ingest_log.account_id` is populated with the same human-readable `account.name` string used
  for `emails.account` (not the internal `AccountConfig.id` used by `sync_state`) — this matches
  how `ingest_raw`'s single `account` parameter is already used for parsing/storage and avoids
  introducing a second identifier through the choke-point. Documented here for @api-guardian.
- `EmailMessage.classified_at` is typed `Optional[str]` (not `datetime`) since Sprint 02 never
  writes a real value into it (Sprint 4's classifier will) and the DB column is `TEXT`.
- `ingest_raw` returns `(IngestDecision, Optional[EmailMessage])` rather than just
  `IngestDecision`, so callers can still conditionally Obsidian-export on `decision.decision ==
  DECISION_STORE` without a second parse. `email_msg` is `None` only for `block`.
- A message that vanishes between IMAP SEARCH and FETCH (`raw_bytes is None`) is treated as a
  distinct `"empty"` ingest_log decision (watermark advances) rather than as `"error"`
  (watermark does not advance) — it's not a processing failure worth retrying forever.

## API/Schema Signature Changes (for @api-guardian)

- `MailbunkerDatabase.insert_email(email_msg, attachments_data=None, preserve_classification=True)`
  — new optional kwarg, backward compatible.
- `MailbunkerDatabase.search_emails(query: SearchQuery)` — behavior change: now hides
  `quarantined=1` rows by default (was previously not filtered at all).
- `MailbunkerDatabase.log_ingest_decision(account_id, folder, uid, decision, reason="")` — new
  method.
- `EmailMessage` — new fields: `raw_header_blob: Optional[str]`, `quarantined: bool = False`,
  `spam_score: float = 0.0`, `trust_level: Optional[str] = None`, `classification_json:
  Optional[str] = None`, `classified_at: Optional[str] = None`, `embedding_state: str =
  "pending"`. All additive with safe defaults.
- `SearchQuery` — new field `include_quarantined: bool = False`. Additive.
- `AsyncImapClient.fetch_raw_message(uid) -> Tuple[Optional[bytes], List[str]]` — **breaking**
  return-type change (was `Optional[bytes]`). Both in-repo callers (`sync_manager.py`,
  `idle_listener.py`) updated; no test mocked the old signature.
- `parse_email_message(raw_bytes, account, folder, uid, flags: Optional[List[str]] = None)` —
  new optional kwarg, backward compatible.
- New module `mailbunker.ingest` exporting `ingest_raw`, `filter_hook`, `IngestDecision`,
  `DECISION_STORE`, `DECISION_QUARANTINE`, `DECISION_BLOCK`.
- DB schema: `emails` table gained 7 columns (listed above) + 2 indices; new `ingest_log` table
  + 1 index; `PRAGMA user_version` now `1`.

## Quality Gates

- [x] Full `pytest` suite passes: **63 passed** (was 50 before this sprint; +13 new tests across
  `test_ingest.py`, `test_database.py`, `test_parser.py`)
- [x] `py_compile` clean on all changed/new modules (project has no configured
  mypy/pyflakes/lint tooling — verified via `pyproject.toml`, no `[tool.mypy]`/`[tool.ruff]`
  section and no `mypy`/`pyflakes` installed in `.venv`)
- [x] No changes outside the Sprint 02 write-scope table
- [x] Sprint 01 hardening untouched (`security/sanitize.py`, `mcp/tools.py`,
  `storage/obsidian.py`, `EmailAddress.__str__` — not in this sprint's write scope, not
  modified)

### Real pytest output

```
$ .venv/bin/python -m pytest -q
...............................................................          [100%]
63 passed in 1.74s
```

## Fix-Runde nach Review

Review-Ergebnis: @security = APPROVED (with notes), @api-guardian = BLOCKED (1 Leak). Alle drei
Punkte adressiert, gleicher (erweiterter) Write-Scope: `mcp/tools.py`, `imap/sync_manager.py`,
`imap/idle_listener.py`, `tests/test_mcp.py`. `storage/database.py` war für diese Runde nicht
nötig.

**FIX A (BLOCKING, api-guardian) — MCP-Leak von `raw_header_blob`.** In
`mcp/tools.py::get_email_impl`, json-Zweig: `msg.model_dump(exclude={"body_html",
"raw_headers"})` erweitert um `"raw_header_blob"` (unsanitisierte, angreiferbeeinflusste
Roh-Header aus Sprint 02) und `"embedding_state"` (rein interner Worker-Zustand, kein
User-Value). Regressionstest `test_get_email_json_excludes_raw_fields` in `tests/test_mcp.py`
erweitert: Fixture-Mail bekommt jetzt einen echten `raw_header_blob`-Inhalt
(`"Authentication-Results: ...spf=pass..."`), der Test prüft explizit das Fehlen von
`raw_header_blob`/`embedding_state` in `res["email"]` UND zusätzlich, dass der Header-Inhalt
(`"spf=pass"`) in keinem String der gesamten Response mehr auftaucht (robust gegen künftige
Refactorings der Payload-Struktur).

**FIX B (security, Low) — Exception-Text in `ingest_log`.** `ingest_log` ist unverschlüsselte
Metadaten-Ablage; `str(err)` eines Parse-/Fetch-Fehlers kann Header-/Adressfragmente der
verarbeiteten Mail enthalten. In `sync_manager.py` und `idle_listener.py` jeweils eine lokale
Helper-Funktion `_short_error_reason(err, max_len=80)` ergänzt, die nur `type(err).__name__` plus
eine auf Leerzeichen normalisierte, auf ~80 Zeichen gekürzte Kurzmeldung zurückgibt
(`"ValueError: some short summary..."`). Beide `except Exception as err:`-Blöcke
(`sync_manager.py:sync_folder`, `idle_listener.py:_sync_pending_emails`) rufen jetzt
`self.db.log_ingest_decision(..., "error", _short_error_reason(err))` statt `str(err)`. Der
volle Fehlertext bleibt weiterhin im (Server-seitigen, nicht persistierten) `logger.error(...)`
für Debugging erhalten — nur der unverschlüsselte DB-Datensatz wird begrenzt. Bewusst als
kleiner duplizierter Helper in beiden Dateien belassen statt in `ingest/pipeline.py`
zentralisiert, da letzteres außerhalb des für diese Fix-Runde freigegebenen Write-Scopes lag.

**CHECK C (security, Low/Info) — Migrationsreihenfolge.** Bestätigt ohne Codeänderung:
`database.py::_migrate()` ruft `_migrate_v1_add_ingest_columns(conn)` auf **Zeile 143**, setzt
`PRAGMA user_version = {SCHEMA_VERSION}` erst danach auf **Zeile 144**, beide innerhalb
desselben `with self._get_conn() as conn:`-Blocks vor dem abschließenden `conn.commit()`. Die
Spalten-Existenzprüfung (`existing_cols`) in `_migrate_v1_add_ingest_columns` macht jeden
erneuten Migrationsversuch idempotent/selbstheilend, falls ein `ALTER TABLE` mitten in der
Sequenz fehlschlägt und `user_version` dadurch nicht auf `1` gesetzt wird — der nächste Start
holt die fehlenden Spalten einfach nach, ohne bereits vorhandene doppelt anzulegen. Keine
Änderung erforderlich.

### Test-Ergebnis nach Fix-Runde

```
$ .venv/bin/python -m pytest -q
...............................................................          [100%]
63 passed in 3.66s
```

(Testanzahl unverändert bei 63, da FIX A den bestehenden Regressionstest
`test_get_email_json_excludes_raw_fields` erweitert statt einen neuen anzulegen; FIX B/CHECK C
werden durch die bereits bestehende Ingest-/Migrations-Testsuite aus der ersten Runde weiter
abgedeckt.)

## Non-Goals Honored

- No filter scoring/rule logic implemented — `filter_hook` is a literal No-Op returning
  `store` always (Sprint 03).
- No classification logic — `trust_level`/`classification_json`/`classified_at` columns exist
  but nothing writes non-default values to them in this sprint (Sprint 04).
- No backfill CLI for existing mails (explicitly deferred per PLAN.md).
