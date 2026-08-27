---
sprint: 04
title: Ollama-Klassifikator (Stufe 4) & Vault-Anreicherung
agent: api-guardian
status: done
---

# Sprint 04 — API Guardian Report

## Scope of Analysis

Read-only review of Sprint 04's contract-relevant surface against the sprint file
(`plans/v0.2.0/sprint-04-ollama-classifier.md`) and the builder report
(`reports/v0.2.0/sprint-04/01-builder-report.md`):

- `src/mailbunker/storage/database.py` (`SCHEMA_VERSION`, `_migrate*`, `insert_email`, `get_email`,
  `search_emails`, `get_unclassified_email_ids`, `save_classification`)
- `src/mailbunker/storage/models.py` (`EmailMessage`, `SearchQuery`)
- `src/mailbunker/mcp/tools.py` (`search_emails_impl`, `get_email_impl`)
- `pyproject.toml`
- `tests/test_classify.py`, `tests/test_ingest.py` (migration coverage)

No code was changed.

---

## 1. Migration v2 (user_version 1 → 2)

**Verdict: Safe, idempotent, correctly ordered.**

- `_migrate()` (`database.py:132-152`) uses sequential `if current_version < N` steps, each
  advancing `PRAGMA user_version` only after its own `_migrate_vN_*` call returns without
  exception. `_migrate_v2_add_classifier_columns()` (`database.py:176-196`) guards every
  `ALTER TABLE ADD COLUMN` with a `PRAGMA table_info(emails)` existence check — same pattern as
  the Sprint 02 v1 step — so it is safe on: a fresh DB (columns already exist via `CREATE TABLE`),
  a Sprint 02/03 v1 DB (missing only the three new columns), and a re-open of an already-v2 DB
  (no-op).
- **A genuine v0.1.0 (pre-Sprint-02) DB opened today runs both steps in the same `_migrate()`
  call**: `current_version` starts at 0 → v1 step runs, `user_version` set to 1 → the same
  function immediately re-checks `current_version < 2` (now 1) → v2 step runs, `user_version` set
  to 2. This is exercised correctly by code path, though not by a single combined test (see gap
  below).
- `user_version` is written strictly *after* the corresponding `ALTER TABLE` block succeeds
  (no exception raised) — a mid-migration crash leaves `user_version` at the last successfully
  completed step, so a retry re-enters the correct step rather than skipping it or double-applying.
- Both migration steps commit inside the same `with self._get_conn() as conn:` context
  (`database.py:139-152`), so the column adds and the `user_version` bump for a given step are
  part of one transaction.

**Test gap (non-blocking):** `tests/test_ingest.py::test_migration_adds_columns_to_legacy_schema`
covers a genuine v0.1.0 → v1 legacy DB; `tests/test_classify.py::
test_migration_v2_adds_classifier_columns_on_v1_db` covers a *simulated* v1 → v2 DB (built by
downgrading a fresh v2 DB and re-adding a synthetic v1 shape). There is no single test that opens
a **real, fully pre-Sprint-02 (v0.1.0) database file** and asserts it lands on `user_version == 2`
with all seven v1 columns *and* all three v2 columns present in one `MailbunkerDatabase()` call.
The chained-`if` code path makes this low-risk (v1's own test already proves the v0→v1 leg;
v2's own test proves the v1→v2 leg), but a single end-to-end v0→v2 regression test would close the
last inference gap cheaply. Recommend adding it in a follow-up sprint, not blocking this one.

---

## 2. MCP Contract (`mcp/tools.py`)

**Verdict: Additive, backward-compatible. No regression of prior exclusions.**

- `search_emails_impl()` gained `category: Optional[str] = None` and
  `spam_verdict: Optional[str] = None` as trailing keyword parameters with safe defaults
  (`tools.py:43-44`). Existing callers that omit them are unaffected; `SearchQuery` treats both as
  optional equality filters (`database.py:511-518`). Non-breaking.
- `get_email_impl(format="json")` gained a derived top-level `summary` field
  (`tools.py:171-181`), parsed defensively from `classification_json` (`try/except` around
  `json.loads`, falls back to `None`) — purely additive, no existing field renamed or removed.
- **Exclusion set verified intact**: `tools.py:159-161` still excludes exactly
  `{"body_html", "raw_headers", "raw_header_blob", "embedding_state", "hidden_text"}` — the same
  five fields established across Sprint 01–03. Sprint 04 did **not** add `spam_verdict`,
  `category`, or `priority` to any exclude list, so those three new columns are surfaced via
  `model_dump()` by default (confirmed by builder report and by reading the exclude set directly)
  — consistent with the plan's intent (`category`/`spam_verdict` are meant to be filterable/
  visible), and no previously-hidden field was re-exposed.
- `markdown`/`text` format branches (`tools.py:114-151`) are unchanged by this sprint.

---

## 3. Model Fields (`EmailMessage` / `SearchQuery`)

**Verdict: Fully optional, default-safe, additive.**

- `EmailMessage.spam_verdict/category/priority: Optional[str] = None` (`models.py:82-84`) —
  no required field added, no existing field type changed.
- `SearchQuery.category/spam_verdict: Optional[str] = None` (`models.py:106-108`) — same.
- `insert_email()`'s re-ingest preservation logic (`database.py:288-312`) was extended
  symmetrically for the three new columns, matching the existing
  `quarantined/spam_score/trust_level/classification_json/classified_at/embedding_state` pattern
  — a re-ingest (e.g. IDLE redelivery) cannot clobber an existing classifier verdict.
- `save_classification()` (`database.py:678-701`) writes only
  `spam_verdict/category/priority/classification_json/classified_at` — verified it never touches
  `quarantined/spam_score/trust_level`, preserving Sprint 03's exclusive ownership of those
  columns as documented.

---

## 4. Version Bump

**Verdict: Correct, scoped exactly as planned.**

- `pyproject.toml`: `version` `0.1.0` → `0.2.0` (confirmed by `git diff HEAD -- pyproject.toml`).
- Only other `pyproject.toml` change: three dependency additions (`tldextract`, `idna` — carried
  over as direct deps from Sprint 03's tree, now declared; `httpx>=0.27.0` — new direct dep for
  `classify/client.py`). No premature edits to `VERSION`, `CHANGELOG.md`, or `plans/**` (`git diff`
  on those paths shows no Sprint 04 changes, consistent with the sprint's write-scope table and
  Core Rule 8/Sprint Contract).

**Process note (non-blocking, orchestrator-facing):** `plans/v0.2.0/sprint-04-ollama-classifier.md`
frontmatter still reads `status: planned` even though the builder report marks the sprint
`status: done` with all acceptance criteria checked. This is an integration bookkeeping gap for
the orchestrator to close at sprint integration, not a contract/API issue.

---

## 5. CHANGELOG `[Unreleased]` — items still to be added (naming only, not written here)

The current `CHANGELOG.md [Unreleased]` section only reflects Sprints 01–03. For Sprint 04,
@scribe still needs to add:

1. **Added:** async local Ollama classifier (`classify/` package: `client.py`, `classifier.py`,
   `prompts.py`, `schema.py`, `worker.py`) producing `spam_verdict`/`category`/`priority`/
   `language`/`tags`/`summary`/`confidence`; never invoked from the IMAP IDLE push path; graceful
   degradation when Ollama is unreachable (no crash, no partial write).
2. **Added:** `mailbunker classify [--backfill] [--account]` CLI command.
3. **Added:** schema migration v2 (`spam_verdict`/`category`/`priority` columns + two new
   indices), chained onto the existing `user_version` mechanism.
4. **Added:** `search_emails`/MCP `category`/`spam_verdict` filter parameters (additive).
5. **Added:** `get_email(format="json")` derived `summary` field.
6. **Added:** Obsidian vault frontmatter enrichment (`category`/`priority`/`summary`, merged
   `tags`), all passed through the existing `_neutralize_obsidian_meta()` guard.
7. **Added:** new config options `OLLAMA_HOST`, `OLLAMA_CLASSIFIER_MODEL`,
   `CLASSIFY_BATCH_SIZE`, `CLASSIFY_CONFIDENCE_THRESHOLD`, `CLASSIFY_ENABLED`.
8. **Added:** new dependency `httpx>=0.27.0` (direct).
9. **Security:** prompt-injection posture for classifier input (body only ever passed as fenced,
   neutralized data; model output re-neutralized before storage) — worth a short note given the
   Breaking/Security section precedent set by Sprints 01–03.
10. **Release:** version bump to `0.2.0` (release sprint — @scribe/tooling only, do not
    pre-empt in this changelog entry beyond noting it is the release commit).

No breaking changes to record for Sprint 04.

---

## Consumer Impact Matrix

No consumers were broken. Every change in this sprint is additive:

| Surface | Change | Breaking? | Consumer Action Required |
|---|---|---|---|
| `search_emails` MCP tool | new optional `category`, `spam_verdict` params | No | None — omit to keep prior behavior |
| `get_email(json)` MCP tool | new `summary` field | No | None — ignore if unused |
| `EmailMessage` model | new `spam_verdict/category/priority` fields (Optional, default `None`) | No | None |
| `SearchQuery` model | new `category/spam_verdict` fields (Optional, default `None`) | No | None |
| DB schema | v2 migration adds 3 columns + 2 indices | No (idempotent, existence-guarded) | None; existing rows get `NULL` for new columns |
| `pyproject.toml` | version bump + 1 new direct dependency (`httpx`) | No | Consumers doing `pip install` pick up `httpx` transitively already present |

---

## Migration Checklist

- [x] `SCHEMA_VERSION` bumped 1 → 2.
- [x] `_migrate_v2_add_classifier_columns()` existence-guarded (idempotent).
- [x] `user_version` advanced only after successful `ALTER TABLE`.
- [x] Chained v0→v1→v2 path is correct by inspection of the sequential `if` structure.
- [ ] (Recommended, non-blocking) Add one test that opens a genuine pre-Sprint-02 (v0.1.0) DB
      fixture and asserts it lands on `user_version == 2` with all v1 + v2 columns present in a
      single `MailbunkerDatabase()` call.
- [x] `search_emails`/MCP `category`/`spam_verdict` filters additive, verified.
- [x] `get_email(json)` exclude set unchanged (`body_html`, `raw_headers`, `raw_header_blob`,
      `embedding_state`, `hidden_text` all still excluded).
- [x] `pyproject.toml` = 0.2.0, no `VERSION`/`CHANGELOG.md`/`plans/**` touched.

---

## Versioning Recommendation

No breaking changes detected — a **minor** version bump (`0.1.0` → `0.2.0`, already applied) is
correct per the sprint's own `version_relevance: minor` declaration. No deprecation period or
major bump needed.
