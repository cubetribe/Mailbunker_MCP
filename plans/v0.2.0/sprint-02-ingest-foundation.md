---
sprint: 02
title: Ingest-Fundament (Migration, Choke-Point, Header, Watermark, Schema)
status: done
ux_gate: skip
version_relevance: minor
result: "DONE — 63/63 Tests grün. Migration (PRAGMA user_version) legacy-DB-sicher & idempotent, gemeinsamer ingest_raw-Choke-Point, Trust-Header + verschlüsselter raw_header_blob, IMAP-FLAGS, Watermark-Fix + ingest_log, quarantäne-aware Suche, FTS-snippet-Bug behoben. @api-guardian-Blocker (raw_header_blob-Leak in get_email(json)) gefixt; @security APPROVED (ingest_log-reason gekürzt). Reports: reports/v0.2.0/sprint-02/."
---

# Sprint 02 — Ingest-Fundament

## Ziel
Die strukturellen Vorarbeiten schaffen, ohne die keine Filterung sicher andockbar ist:
Schema-Migration, ein **gemeinsamer** Ingest-Choke-Point, Erhalt der Trust-Header, Reparatur der
Watermark-Buchführung und die neuen Klassifikations-Spalten.

## Scope
1. **Migrationsmechanismus** (`storage/database.py`): `PRAGMA user_version` einführen; idempotente
   Migrationsschritte (`_migrate()`), aufgerufen in `_init_db`. Schritt 1 legt die neuen Spalten per
   `ALTER TABLE ADD COLUMN` an (nur wenn fehlend). FTS5-Änderungen via Drop+Reindex-Pfad.
2. **Schema-Erweiterung** `emails`: `quarantined INTEGER DEFAULT 0`, `spam_score REAL DEFAULT 0`,
   `trust_level TEXT`, `classification_json TEXT`, `classified_at TEXT`, `embedding_state TEXT
   DEFAULT 'pending'`, `raw_header_blob TEXT` (verschlüsselt). Index `idx_emails_quarantined`,
   `idx_emails_classified`. Neue Tabelle `ingest_log(account_id, folder, uid, decision, reason,
   ts)`.
3. **Gemeinsamer Choke-Point** — neues Modul `src/mailbunker/ingest/pipeline.py`:
   `ingest_raw(ctx, raw_bytes, account, folder, uid) -> IngestDecision`. Kapselt
   `parse → (Filter-Hook, in Sprint 03 gefüllt) → insert_email|quarantine|block`. Der duplizierte
   Block aus `sync_manager.py:59-78` **und** `idle_listener.py:148-173` ruft nur noch `ingest_raw`.
   In Sprint 02 ist der Filter-Hook ein No-Op (`decision=store`), aber der Pfad steht.
4. **Header-Erhalt** (`imap/parser.py:200-203`): Whitelist erweitern um
   `Authentication-Results`, `Received-SPF`, `DKIM-Signature`, `ARC-Authentication-Results`,
   `Return-Path`, `List-Id`, `Precedence`, `Auto-Submitted`, `X-Mailer`, alle `X-Spam-*`.
   Zusätzlich **kompletten Roh-Header-Block** (bis erste Leerzeile) in `raw_header_blob`
   (verschlüsselt via bestehendem Payload-Weg) persistieren.
5. **IMAP-FLAGS fetchen** (`imap/client.py:129-146`): `(BODY.PEEK[])` → `(FLAGS BODY.PEEK[])`;
   Flags parsen und in `EmailMessage.flags` befüllen (`$Junk`, `\Seen` etc.).
6. **Watermark-Fix** (`sync_manager.py:59-80`, `idle_listener.py:148-176`): `highest_uid` auch für
   gefilterte/quarantänisierte Mails fortschreiben; jede UID-Entscheidung in `ingest_log`
   protokollieren; Fehlerfall (Exception) von bewusster Filterung unterscheidbar machen.
7. **Suche/MCP quarantäne-aware** (`storage/database.py:281-389`): `search_emails` blendet
   `quarantined=1` standardmäßig aus; optionaler Parameter `include_quarantined=False`.
8. **FTS-`snippet()`-Bug beheben** (`database.py:334-346`): `MATCH` in die Hauptabfrage ziehen,
   damit `snippet()` die Fundstelle zentriert/hervorhebt; Highlight-Marker so wählen, dass er nicht
   wie Obsidian-Syntax aussieht.

## Non-Goals
- Keine Filterregeln/Scoring-Logik (nur der No-Op-Hook) → Sprint 03.
- Keine Klassifikation → Sprint 04.
- Kein Backfill bestehender Mails (separater CLI-Task, best effort, später).

## Write-Scope (Ownership)
- `src/mailbunker/ingest/__init__.py` (neu), `src/mailbunker/ingest/pipeline.py` (neu)
- `src/mailbunker/storage/database.py`, `src/mailbunker/storage/models.py`
- `src/mailbunker/imap/parser.py`, `src/mailbunker/imap/client.py`,
  `src/mailbunker/imap/sync_manager.py`, `src/mailbunker/imap/idle_listener.py`
- `tests/test_database.py`, `tests/test_parser.py`, neue `tests/test_ingest.py`

## Risks
- **Migration auf Bestands-DB**: muss idempotent und getestet sein (alte DB ohne Spalten → nach
  Migration lauffähig, keine Datenverluste). Test mit fixture-DB im Alt-Schema.
- `INSERT OR REPLACE` (database.py:168) überschreibt Klassifikation bei Re-Ingest → Merge-Logik,
  die bestehende `classification_json`/`quarantined` bewahrt, wenn Re-Ingest.
- Roh-Header-Blob erhöht Speicher (~Faktor gering, nur Header) — akzeptiert.
- FLAGS-Fetch-Syntax providerabhängig → gegen aioimaplib absichern, Fallback ohne Flags.

## Acceptance Criteria
- [ ] Alt-Schema-DB wird beim Öffnen migriert, `insert_email` läuft danach fehlerfrei.
- [ ] Beide Ingest-Pfade (One-Shot + IDLE) rufen ausschließlich `ingest_raw`.
- [ ] Trust-Header + Roh-Header-Block landen verschlüsselt in der DB.
- [ ] `flags` ist befüllt (sofern Server liefert).
- [ ] Übersprungene UID geht nicht mehr verloren; `ingest_log` enthält decision/reason.
- [ ] `search_emails` blendet quarantänisierte Mails aus; `include_quarantined` funktioniert.
- [ ] `snippet()` liefert echte Fundstelle; Tests grün.
- [ ] @api-guardian: Schema-/Model-/Suchsignatur-Änderungen dokumentiert.

## Test Strategy
`pytest`; neue `tests/test_ingest.py` (Choke-Point, Watermark, ingest_log); Migrations-Test mit
Alt-Schema-Fixture; Parser-Test für erweiterte Header + Roh-Blob.

## Changelog Note
Ingest: unified ingest choke-point; schema migrations (PRAGMA user_version); preserved trust headers
and raw header block; IMAP FLAGS capture; watermark/ingest-log fix; quarantine-aware search; FTS
snippet fix.
