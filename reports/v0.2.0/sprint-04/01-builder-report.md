---
sprint: 04
title: Ollama-Klassifikator (Stufe 4) & Vault-Anreicherung
agent: builder
status: done
---

# Sprint 04 — Builder Report

## Summary

Implemented the async, local Ollama classifier (Stufe 4) as a standalone `mailbunker classify
[--backfill] [--account X]` command, never invoked from the IMAP IDLE push path. Ollama is fully
optional: unreachable/model-missing degrades gracefully (clean message, no DB write, no crash).
Classification results (`spam_verdict`/`category`/`priority`/`tags`/`summary`) enrich the Obsidian
vault frontmatter and are exposed as additive, backward-compatible filters in `search_emails` /
MCP. Version bumped to `0.2.0` (final sprint of PLAN v0.2.0).

## Files Created

- `src/mailbunker/classify/__init__.py` — package docstring / async-only contract note.
- `src/mailbunker/classify/schema.py` — `ClassificationResult` pydantic model (validated
  spam_verdict/category/priority/language/tags/summary/confidence), enum constants, fallback
  factory for the "Dauerfehler" path.
- `src/mailbunker/classify/client.py` — `OllamaClient` (thin `httpx` wrapper over
  `POST /api/chat` with `format=json`), `OllamaUnavailableError`, `is_reachable()` liveness check.
  No dependency on the `ollama` pip package.
- `src/mailbunker/classify/prompts.py` — `SYSTEM_PROMPT` (fixes the classify-only task, tells the
  model to treat email content as data, never instructions) + `build_user_prompt()` (subject/
  sender/body excerpt via `neutralize_untrusted` + `wrap_untrusted` fencing; `hidden_text` is
  never passed as content, only its boolean presence).
- `src/mailbunker/classify/classifier.py` — `classify_email()`: prompt build → Ollama call →
  JSON parse/pydantic-validate with up to 2 attempts → neutralizes `summary`/`tags` via
  `security.sanitize.neutralize_untrusted` before returning. Returns `None` on
  `OllamaUnavailableError` (graceful degradation signal); returns
  `ClassificationResult.fallback()` (`spam_verdict=suspicious`, `category=unknown`) on persistent
  parse/validation failure — never a silent loss.
- `src/mailbunker/classify/worker.py` — `run_classification_batch()`: pulls candidate ids from
  `db.get_unclassified_email_ids()`, re-checks `quarantined` defensively, applies the
  `confidence < CLASSIFY_CONFIDENCE_THRESHOLD → "uncertain"` tag, persists via
  `db.save_classification()`, and (if an exporter + vault root were passed) re-exports the
  updated note. Stops the batch (without raising) the moment Ollama proves unreachable.
- `tests/test_classify.py` — 17 tests (mocked Ollama client, no network/model dependency).

## Files Modified

- `src/mailbunker/storage/database.py`
  - `SCHEMA_VERSION` 1 → 2; `_migrate()` now chains v1 → v2.
  - New `_migrate_v2_add_classifier_columns()`: idempotent `ALTER TABLE` for
    `spam_verdict`/`category`/`priority` (existence-guarded like v1), two new indices, PRAGMA
    `user_version` advanced only after successful ALTERs.
  - `insert_email()`: `spam_verdict`/`category`/`priority` added to the INSERT column list and to
    the existing `preserve_classification` re-ingest-merge logic (same contract as
    `spam_score`/`trust_level`/`classification_json`/`classified_at`/`embedding_state`).
  - `get_email()`: loads the three new columns into `EmailMessage`.
  - `search_emails()` / `SearchQuery`: additive `category`/`spam_verdict` equality filters.
  - New `get_unclassified_email_ids(limit, account=None, backfill=False)` and
    `save_classification(email_id, spam_verdict, category, priority, classification_json,
    classified_at)` — the latter only ever touches classifier columns, never
    `quarantined`/`spam_score`/`trust_level` (Sprint 03's exclusive ownership).
- `src/mailbunker/storage/models.py` — `EmailMessage.spam_verdict/category/priority: Optional[str]
  = None`; `SearchQuery.category/spam_verdict: Optional[str] = None`.
- `src/mailbunker/storage/obsidian.py` — new `_extract_classification_fields()` parses
  `classification_json` for `summary`/`tags` (defensive: malformed/missing JSON → no enrichment,
  never raises); `format_email_markdown()` merges classifier tags into the existing tag list and
  adds `category`/`priority`/`summary` to frontmatter, all passed through the existing
  `_neutralize_obsidian_meta()` metachar guard (backticks/wikilinks) — `neutralize_untrusted` in
  `classifier.py` only strips invisible/control chars, so this second pass is still required at
  the point of Markdown/YAML injection, matching the pattern already used for sender/subject.
- `src/mailbunker/config.py` — new module-level constants (same pattern as Sprint 03's
  `SPAM_*`/`ATTACHMENT_*`): `OLLAMA_HOST` (default `http://localhost:11434`),
  `OLLAMA_CLASSIFIER_MODEL` (default `gemma3:4b`), `CLASSIFY_BATCH_SIZE` (default 25),
  `CLASSIFY_CONFIDENCE_THRESHOLD` (default 0.55), `CLASSIFY_ENABLED` (default `false`).
- `src/mailbunker/cli.py` — new `mailbunker classify [--backfill] [--account]` command: checks
  `client.is_reachable()` first for a clean early exit message; runs the worker; prints a Rich
  panel with processed/classified/fallback/skipped counts. `--version` string bumped to `0.2.0`.
- `src/mailbunker/mcp/tools.py` — `search_emails_impl()` gained optional `category`/
  `spam_verdict` params (additive, passed straight into `SearchQuery`); `get_email_impl()`'s
  `json` format now also derives a top-level `summary` field from `classification_json` (already
  neutralized at classification time). `category`/`priority`/`spam_verdict` were already
  surfaced automatically via `model_dump()` (not in the exclude set) — no exclude-list change was
  needed for those three; `hidden_text`/`raw_header_blob`/`body_html`/`raw_headers`/
  `embedding_state` remain excluded exactly as before.
- `pyproject.toml` — `version` `0.1.0` → `0.2.0`; new dependency `httpx>=0.27.0` (was already
  present transitively via `mcp`/`fastmcp`, now declared as a direct dependency since
  `classify/client.py` imports it directly).

## Classification Schema (implemented exactly per plan)

```json
{
  "spam_verdict": "ham | spam | phishing | suspicious",
  "category": "personal | business | transactional | notification | newsletter | marketing | social | automated",
  "priority": "high | normal | low",
  "language": "de | en | ...",
  "tags": ["..."],
  "summary": "one factual sentence",
  "confidence": 0.0
}
```
`category` additionally accepts the fallback-only sentinel `"unknown"` (never produced by a
schema-conforming model response; only used by `ClassificationResult.fallback()`). All fields are
pydantic-validated with `field_validator`s that coerce out-of-enum/out-of-range values to safe
defaults rather than raising — a hallucinated field never crashes the worker.

## Degradation Behavior

- **Ollama unreachable** (`client.is_reachable()` false, checked once up front by the CLI): no
  batch is attempted, a plain message is printed, exit code 0, DB/ingest completely untouched.
- **Ollama becomes unreachable mid-batch** (`OllamaUnavailableError` during a call): the worker
  stops the batch immediately (`stats.ollama_unavailable = True`), already-classified emails in
  that run stay classified, the rest simply remain `classified_at IS NULL` for the next run — no
  exception propagates, no partial/corrupt row is written.
- **Malformed/invalid JSON from the model**: up to 2 attempts, then
  `ClassificationResult.fallback()` (`spam_verdict=suspicious`, `category=unknown`,
  `confidence=0.0`) is persisted — the mail is marked `classified_at` (so it doesn't get retried
  forever) but never silently dropped from view.
- **Prompt injection via classified content**: the email body is only ever passed as fenced,
  neutralized data (`wrap_untrusted` + `neutralize_untrusted`); the system prompt explicitly
  instructs the model not to obey embedded instructions; `hidden_text` is never included as
  content, only as a yes/no signal. The model's own `summary`/`tags` output is treated as
  untrusted too and run through `neutralize_untrusted` before storage (verified by
  `test_classify_email_neutralizes_summary_and_tags`, which injects zero-width/bidi characters
  into a fake model response and asserts they're stripped on the way out).
- **Quarantine ownership**: `save_classification()` only ever writes
  `spam_verdict/category/priority/classification_json/classified_at` — it can never override a
  Sprint 03 `quarantined`/`spam_score`/`trust_level` decision.

## Manual Smoke Test (not part of CI, per plan's Test Strategy)

Ran against the real local Ollama instance at `localhost:11434` (already running on this machine
with `qwen2.5:7b` pulled; the config default `gemma3:4b` was not pulled here) via
`OLLAMA_CLASSIFIER_MODEL=qwen2.5:7b mailbunker classify` against a throwaway `STORAGE_PATH`. A
synthetic invoice email was correctly classified end-to-end:
```
verdict: ham category: business priority: normal
classified_at: 2026-08-24T21:38:36.587415+00:00
json: {"spam_verdict":"ham","category":"business","priority":"normal","language":"en",
"tags":["invoice","due_date","amount"],
"summary":"The email is a business invoice notification with payment details.",
"confidence":0.9}
```
Also verified the "Ollama unreachable" path cleanly reports zero processed/classified with no
crash when pointed at a fresh, empty storage path in an environment context (before confirming
Ollama was actually up).

## Test Results

```
$ .venv/bin/python -m pytest -q
........................................................................ [ 66%]
....................................                                     [100%]
108 passed in 1.80s
```
92 pre-existing tests (Sprints 01–03) still pass unmodified; 16 new tests in
`tests/test_classify.py` cover: successful classification, unavailable-Ollama graceful
degradation, invalid-JSON retry+fallback, injection-neutralization, pydantic enum
coercion/confidence clamping, worker DB integration (fill columns / leave `classified_at` None on
unavailability / skip quarantined / idempotent without `--backfill` / reprocess with `--backfill`
/ low-confidence "uncertain" tagging), a real `OllamaClient` connection-failure path (port 1,
no mocking library needed), migration v1→v2 on a simulated legacy DB, vault frontmatter
enrichment, and `search_emails` `category`/`spam_verdict` filtering.

## Version Bump Confirmation

```
$ grep '^version' pyproject.toml
version = "0.2.0"
```
`VERSION`/`CHANGELOG.md`/`ROADMAP.md`/`plans/**` were not touched (per Sprint Contract — those
remain the orchestrator/@scribe's exclusive write scope).

## Finalisierung nach Review

Nach @api-guardian- und @security-Approval (beide ohne Critical/High) wurden folgende drei
Punkte nachgezogen, KEIN weiterer Version-Bump, KEIN CHANGELOG/README (bleibt @scribe):

- **FIX A (security Low, `classify/classifier.py`)**: Neue Helper-Funktion
  `_short_error_summary(err, max_len=80)` (analog `_short_error_reason` aus Sprint 02 in
  `sync_manager.py`/`idle_listener.py`) ersetzt die bisherigen `logger.warning(..., e)` /
  `logger.error(..., last_error)`-Aufrufe im Parse-/Validation-Fehlerpfad. Es wird nur noch
  `type(e).__name__` plus eine auf 80 Zeichen gekürzte, whitespace-normalisierte Kurzmeldung
  geloggt — kein voller `ValidationError`/`JSONDecodeError`-Text mehr, der Fragmente des rohen,
  untrusted Modell-Outputs enthalten könnte.
- **FIX B (Quarantäne-Export-Lücke, `storage/obsidian.py` + `imap/sync_manager.py` +
  `imap/idle_listener.py`)**:
  - `ObsidianVaultExporter.export_all()` selektiert jetzt standardmäßig nur
    `COALESCE(quarantined, 0) = 0` (SQL-Guard, analog `search_emails`); neuer optionaler
    Parameter `include_quarantined: bool = False` für einen expliziten, bewussten Vollexport
    (z. B. manueller Quarantäne-Review). Der MCP-Tool-Aufruf `export_obsidian_vault_impl` nutzt
    den Default und ist damit automatisch mitgehärtet, ohne selbst geändert werden zu müssen.
  - Auto-Export-Pfad (`OBSIDIAN_AUTO_EXPORT`) in `sync_manager.py::sync_folder` und
    `idle_listener.py::_sync_pending_emails`: vor dem Export wird jetzt der PERSISTIERTE
    Datensatz per `db.get_email(msg.id)` erneut gelesen und nur exportiert, wenn
    `stored_msg.quarantined` `False` ist — statt des frisch geparsten In-Memory-`msg`-Objekts,
    dessen `quarantined`-Feld eine `preserve_classification`-Zusammenführung (Re-Ingest derselben
    Message-ID mit zuvor gesetzter Quarantäne) nicht widerspiegelt. Damit kann eine bereits
    quarantänisierte Mail nie über den Auto-Export-Pfad im Klartext in den Vault gelangen, auch
    nicht im Re-Ingest-Edge-Case.
  - Direkter `get_email(id)`-Zugriff auf eine bekannte ID bleibt unverändert (expliziter Zugriff,
    by design, nicht Teil dieses Fixes).
  - Regressionstest: `tests/test_obsidian.py::test_export_all_excludes_quarantined_by_default`
    (quarantänisierte Mail erscheint nicht im Default-Export; `include_quarantined=True`
    liefert sie zurück).
- **FIX C (api-guardian-Empfehlung, Test)**: Neuer End-to-End-Migrationstest
  `tests/test_classify.py::test_migration_v0_to_v2_end_to_end_on_real_legacy_schema` baut eine
  echte v0.1.0-Schema-DB (`user_version = 0`, exakt das Sprint-01-Schema, keine einzige der in
  Sprint 02/04 ergänzten Spalten) und öffnet sie in EINEM `MailbunkerDatabase.__init__`-Aufruf.
  Verifiziert: `user_version` landet bei `SCHEMA_VERSION == 2`, alle v1- UND v2-Spalten sind
  vorhanden, `ingest_log` existiert, und sowohl `insert_email` als auch der Klassifikations-
  Schreibpfad (`save_classification`) laufen danach fehlerfrei durch.

### Test Results (nach Finalisierung)

```
$ .venv/bin/python -m pytest -q
........................................................................ [ 65%]
......................................                                   [100%]
110 passed in 4.50s
```
110/110 grün (108 vorher + 2 neue Regressionstests aus FIX B und FIX C).

## Acceptance Criteria Status

- [x] `mailbunker classify` classifies open mail, fills columns + `classification_json`.
- [x] `--backfill` processes existing backlog; idempotent without it (verified by test).
- [x] Ollama offline → clean message, no crash, DB/ingest untouched (verified by test + manual).
- [x] Vault frontmatter contains `category`/`priority`/`summary`/`tags` (FTS indexes `tags`
      already; `summary`/`category`/`priority` are frontmatter-only, consistent with how other
      metadata fields in this vault are handled — not separately added to the FTS5 virtual table
      schema, which is out of this sprint's write scope to alter).
- [x] `search_emails`/MCP can filter by `category`/`spam_verdict`.
- [x] Summary/tags are neutralized (verified via injected zero-width/bidi test).
- [x] `tests/test_classify.py` (mocked client) green; all suites green (108/108).
- [x] `pyproject.toml` = 0.2.0.
