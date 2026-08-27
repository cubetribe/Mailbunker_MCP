---
sprint: 02
title: Ingest-Fundament (Migration, Choke-Point, Header, Watermark, Schema)
agent: api-guardian
status: blocked
---

# Sprint 02 — API Impact Analysis Report

## Scope Reviewed

Read-only review of the diff described in `plans/v0.2.0/sprint-02-ingest-foundation.md` and
`reports/v0.2.0/sprint-02/01-builder-report.md`:
`storage/database.py`, `storage/models.py`, `imap/client.py`, `imap/parser.py`,
`imap/sync_manager.py`, `imap/idle_listener.py`, `ingest/pipeline.py` (new), plus consumer files
`mcp/tools.py` (out of write-scope, checked as a consumer) and `tests/test_ingest.py`,
`tests/test_database.py`.

## Change Summary

| File / Symbol | Change Type | Breaking? |
|---|---|---|
| `storage/database.py` — schema migration (`PRAGMA user_version`) | Additive columns via `ALTER TABLE`, no FTS DDL | ✅ No |
| `storage/database.py::insert_email` | New optional kwarg `preserve_classification=True` | ✅ No |
| `storage/database.py::search_emails` | Behavior change: quarantined rows hidden by default | ⚠️ Behavioral, non-signature |
| `storage/database.py::log_ingest_decision` | New method | ✅ No |
| `storage/models.py::EmailMessage` | 7 new fields, all with safe defaults | ✅ No |
| `storage/models.py::SearchQuery.include_quarantined` | New field, default `False` | ✅ No |
| `imap/client.py::fetch_raw_message` | Return type `Optional[bytes]` → `Tuple[Optional[bytes], List[str]]` | 🔴 Yes (internal API) |
| `imap/parser.py::parse_email_message` | New optional kwarg `flags=None` | ✅ No |
| `storage/database.py::snippet()` markers | `==`/`==` → `»`/`«` | ⚠️ Consumer-visible, non-breaking |
| **`mcp/tools.py::get_email_impl` (json format)** | **Not modified, but now leaks a new field unfiltered** | 🔴 **Yes — regression against Sprint 01 hardening** |

## Breaking Changes Detected

### 1. `AsyncImapClient.fetch_raw_message` return-type change
- **Severity:** 🟡 Medium (internal API only, not exposed to MCP clients)
- **Consumers found (grep):**
  - `src/mailbunker/imap/sync_manager.py:61` → `raw_bytes, flags = await client.fetch_raw_message(uid)` ✅ updated
  - `src/mailbunker/imap/idle_listener.py:152` → `raw_bytes, flags = await self._client.fetch_raw_message(uid)` ✅ updated
  - No other call sites found in `src/` or `tests/` (`grep -rn "fetch_raw_message"` returns only these three lines plus the definition).
- **Verdict:** Both internal consumers correctly unpacked. No leftover old-style single-value calls. This is an internal contract (not part of the MCP tool surface), so acceptable as a same-sprint synchronized change — consistent with the sprint's own risk assessment ("Interne API — vertretbar").

### 2. 🔴 `raw_header_blob` unintentionally exposed via `get_email(format="json")` — regression against Sprint 01 hardening
- **Severity:** 🔴 High
- **Location:** `src/mailbunker/mcp/tools.py:144` (`get_email_impl`, `json` branch, **not modified this sprint, not in Sprint 02 write-scope**):
  ```python
  email_dict = msg.model_dump(exclude={"body_html", "raw_headers"})
  ```
- **Issue:** `EmailMessage` gained a new field `raw_header_blob: Optional[str]` this sprint — the full decrypted raw RFC822 header block (`imap/parser.py::extract_raw_header_block`), explicitly attacker-influenced content (From/Subject/Received/X-Spam-*/DKIM headers etc. as sent by an untrusted sender). Because the `exclude` set was never updated to also exclude `raw_header_blob`, this field is now serialized verbatim into every `get_email(..., format="json")` MCP response.
  - It bypasses `neutralize_untrusted()` entirely — no control-character/zero-width/bidi stripping, no 20,000-char truncation (unlike `subject`/`body_text`/`body_markdown` in the same response).
  - This is the *exact* class of exposure Sprint 01 deliberately closed and documented as a **Breaking Change** in `CHANGELOG.md` `[Unreleased]`: *"`get_email(format="json")` no longer returns `body_html` and `raw_headers`" — "Removes potential HTML-injection vectors and simplifies response surface for security hardening."* Sprint 02 reintroduces materially the same raw-header attack surface under a new field name, unfiltered, without a corresponding decision or changelog entry.
  - `markdown` and `text` format branches of `get_email_impl` build their response dict manually (no `model_dump()`) and do **not** include `raw_header_blob` — only the `json` branch is affected.
- **Consumer Impact Matrix:**

| Consumer | File:Line | Issue | Required Action |
|---|---|---|---|
| MCP host / any `get_email(format="json")` caller | `src/mailbunker/mcp/tools.py:144` | Receives unsanitized, untruncated raw header block from untrusted sender | Add `raw_header_blob` to the `exclude` set (or explicitly sanitize + truncate it like the other text fields), and decide deliberately whether it should ever leave the MCP boundary at all — its stated purpose (DKIM/SPF re-verification, Sprint 04) is internal, not MCP-consumer-facing |

- **Required action before this sprint is safe to ship:** either (a) exclude `raw_header_blob` from `get_email_impl`'s json `model_dump()` (matches its documented internal purpose — trust re-verification, not display), or (b) if it must be exposed, route it through `neutralize_untrusted()` with truncation and add a corresponding `[Unreleased]` changelog note, since this is a security-relevant surface change consistent with the existing "Security" section pattern from Sprint 01.
- **Note on ownership:** `mcp/tools.py` is outside Sprint 02's write-scope table, so the builder correctly did not touch it — but the sprint's schema change silently changed that file's *effective* behavior. This is a cross-sprint contract gap the write-scope model doesn't catch by itself; flagging here so @builder (or a follow-up micro-sprint) closes it before `mcp/tools.py` is next touched, ideally before v0.2.0 ships.

## Non-Breaking / Additive Changes (verified safe)

### 3. Schema migration (`storage/database.py`)
- **Idempotent:** ✅ `_migrate()` reads `PRAGMA user_version`; only runs `_migrate_v1_add_ingest_columns()` when `< 1`, and that step itself checks `PRAGMA table_info(emails)` before each `ALTER TABLE ADD COLUMN` — safe to call on a fresh DB (columns already exist), an already-migrated DB (no-op), and a legacy DB (adds columns once).
- **Bestands-DB (v0.1.0, no new columns) opens and works afterward:** ✅ Verified both by reading `_migrate_v1_add_ingest_columns` (column-existence check before every `ALTER TABLE`) and by the dedicated test `tests/test_ingest.py::test_migration_adds_columns_to_legacy_schema`, which hand-builds a pre-Sprint-02 schema (no `quarantined`/`raw_header_blob`/`ingest_log`), opens it via `MailbunkerDatabase(...)`, and asserts all 7 new columns + `ingest_log` table + `user_version == SCHEMA_VERSION` exist afterward, then successfully round-trips an `ingest_raw()` insert.
- **Idempotent re-open tested:** ✅ `tests/test_ingest.py::test_migration_is_idempotent_on_reopen` opens the same (already-migrated) DB twice and asserts no duplicate columns.
- **FTS5 handling:** ✅ Correctly avoided — no FTS5 schema (`emails_fts`) column changes were needed for this sprint's additions (all new columns live on `emails`, not `emails_fts`), so no `ALTER TABLE` on the virtual table was attempted (which SQLite FTS5 does not support for arbitrary column changes anyway) and no drop+reindex was triggered. This is correctly deferred to a future sprint that does touch FTS columns, per the code comment.
- **Data-loss risk:** ✅ None identified. `ALTER TABLE ADD COLUMN` with a constant default is a metadata-only operation in SQLite (no table rewrite, no data loss); existing rows get the column default. `ingest_log` is a new table created via `CREATE TABLE IF NOT EXISTS`, no interaction with existing data.

### 4. `EmailMessage` new fields / `search_emails` behavior change
- All 7 new fields (`quarantined`, `spam_score`, `trust_level`, `classification_json`, `classified_at`, `embedding_state`, `raw_header_blob`) are `Optional`/defaulted — additive, do not break existing `EmailMessage(...)` construction or `model_dump()` call sites for consumers that don't care about them (except the `raw_header_blob` leak documented above).
- `SearchQuery.include_quarantined: bool = False` — additive; `mcp/tools.py::search_emails_impl` builds `SearchQuery(...)` via keyword args without this parameter, so it silently inherits the new default (hide quarantined). Correctly verified: no `SearchQuery` positional-arg construction exists anywhere in the repo that this new field could shift.
- **Behavioral change worth a changelog line (even though nothing is quarantined yet in Sprint 02):** `search_emails` previously returned *all* matching rows; as of this sprint, any row with `quarantined=1` is hidden by default, and no code path sets `quarantined=1` yet (No-Op filter hook), so no observable difference exists *today*. But this is exactly the kind of "behavior changes the moment Sprint 03 lands" fact that belongs in `[Unreleased]` now, so MCP consumers/integrators are not surprised later without a paper trail. Recommend @scribe add a note under "Changed" (not "Breaking," since no consumer is affected in this sprint) along the lines of: *"`search_emails` gains an `include_quarantined` opt-in and will hide quarantined emails by default once quarantine classification is enabled (Sprint 03)."*

### 5. FTS `snippet()` marker change (`==`/`==` → `»`/`«`)
- **Consumer impact:** Low. `snippet_text` is only consumed inside `search_emails_impl` (`mcp/tools.py`), passed through `neutralize_untrusted()`, and returned as a display string (`item["snippet"]`) — no in-repo consumer parses the `==` markers programmatically. External MCP clients that scraped `==...==` markers out of prior snippets (e.g., to re-render highlighting) would need to switch to `»...«`. Worth a one-line "Changed" note since it's an observable MCP response format change, even though it fixes a real bug (Obsidian syntax collision) and is a clear net improvement.

## Version Relevance

Sprint file declares `version_relevance: minor` — **agreed**, conditional on the `raw_header_blob`
leak (finding #2) being closed before release. The schema migration, new fields, and
`fetch_raw_message` signature change are all internal/additive and consistent with a `0.1.0` →
`0.2.0` minor bump. The `raw_header_blob` exposure, if shipped as-is, would need to be classified
as an unplanned security regression, not a feature addition — it should be fixed in this sprint's
scope (or an immediate follow-up before v0.2.0 ships), not carried into the version bump silently.

## Changelog Note — items to add under `[Unreleased]` (naming only, not drafting text myself)

1. **Security (fix before release):** `raw_header_blob` field must be excluded/sanitized in
   `get_email(format="json")` before shipping — currently leaks unsanitized raw email headers,
   undermining the Sprint 01 `raw_headers`/`body_html` removal rationale.
2. **Added:** Ingest choke-point (`ingest_raw`), schema migration mechanism (`PRAGMA
   user_version`), IMAP FLAGS capture, extended trust-header whitelist + raw header block
   persistence (encrypted), `ingest_log` audit table, watermark fix for filtered/failed UIDs.
3. **Changed:** `search_emails` hides `quarantined=1` rows by default (`include_quarantined`
   opt-in) — behaviorally inert today, becomes observable once Sprint 03 lands.
4. **Changed:** FTS `snippet()` highlight markers changed from `==...==` to `»...«` to avoid
   colliding with Obsidian highlight syntax; also now correctly centers on the actual match.
5. **Internal/non-breaking:** `AsyncImapClient.fetch_raw_message` return type changed to include
   IMAP flags (internal API, both call sites updated in the same commit) — no changelog entry
   needed unless the project changelogs internal APIs.

## Migration Checklist

- [ ] Exclude (or sanitize+truncate) `raw_header_blob` in `mcp/tools.py::get_email_impl`'s
      `json` branch `model_dump()` call — **blocking**.
- [ ] @scribe: add `[Unreleased]` entries per the list above (Security item first).
- [ ] Confirm intended long-term consumer of `raw_header_blob` (Sprint 04 classifier / DKIM
      re-verification) — if it is meant to stay internal-only, consider not exposing it through
      any MCP tool response at all, now or later.

## Sign-off on Sprint 02 Scope Items 1–8 (per sprint file)

Items 1 (migration), 2 (schema extension), 3 (choke-point), 4 (header-erhalt), 5 (FLAGS), 6
(watermark-fix), 7 (quarantine-aware search signature), 8 (snippet fix) are all implemented as
additive/internal changes and verified against the code and the new tests
(`tests/test_ingest.py`, `tests/test_database.py`). The one item that fails review is the
**consumer-facing leak in `mcp/tools.py`**, which is a side effect of item 4 (the new
`raw_header_blob` field) landing without a corresponding update to the existing JSON-export
filter — an integration gap between this sprint's write-scope and Sprint 01's hardening, not a
flaw in the migration/choke-point/watermark work itself.
