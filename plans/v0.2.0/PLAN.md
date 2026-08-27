# PLAN v0.2.0 — Zero-Trust Filtering & Injection Hardening

> Status: **done** (alle 4 Sprints abgeschlossen, 110/110 Tests grün, v0.2.0) · Ziel-Version: **0.2.0** (minor) · Erstellt: 2026-08-24
> Vorgänger: v0.1.0 (Initial release)

## Kontext

Mailbunker ist heute ein reiner `fetch → parse → encrypt → store`-Durchlauf **ohne jede
Policy-Ebene**. Bevor produktiv 10.000+ Bestandsmails eingelesen werden, sollen (a) die bereits
heute offenen Prompt-Injection-/Exfiltrations-Wege geschlossen und (b) eine mehrschichtige
Spam-/Trust-Filterung mit präziser Kategorisierung eingezogen werden. Die Kategorisierung dient
doppelt: als Spam-Gate **und** als Anreicherung der Obsidian-Vault-Daten.

Grundlage ist ein Code-Audit (2 Agenten mit ausgeführten Exploit-Tests) + Web-Recherche
(Prompt-Injection, Spam-Schichten, Apple FM vs. Ollama, Embeddings) mit adversarialer
Faktenprüfung. Kernbelege sind in den einzelnen Sprint-Files referenziert.

## Leitentscheidungen (vom Nutzer bestätigt)

| Entscheidung | Wahl | Begründung |
|---|---|---|
| Reihenfolge | Security-Härtung zuerst, dann Fundament, Filter, Klassifikator | Der Klartext-Export ist heute akut ausnutzbar |
| Quarantäne-Semantik | **speichern + verstecken** (`quarantined=1`, aus Suche/MCP/Embedding ausgeblendet, per Flag sichtbar) | Kein Datenverlust bei False-Positives |
| Filtertiefe | Hart-codiert (Stufe 0–2) **+ Ollama-Klassifikator (Stufe 4)** | Präzise Kategorien für gute Vault-Daten; rspamd (Stufe 3) bewusst weggelassen |
| LLM-Backend | **Ollama** (lokal), nicht Apple FM | Apple FM: 4k-Kontext, gedrosselte Rate-Limits, Guardrail-Fehlschläge bei Spam-Samples; Ollama batch-fähig & privat |
| Embeddings | **verschoben** (eigener späterer Plan) | FTS5 + LLM-Agent-Reformulierung deckt ~95 %; lokal (Zero-Trust), Kosten irrelevant |
| Provider-Signal | `Authentication-Results` + `X-Spam-*` + IMAP-`$Junk`-Flag **behalten** | Billigstes, zuverlässigstes Spam-Signal — wird heute weggeworfen |

## Zielarchitektur der Ingest-Pipeline (nach v0.2.0)

```
IMAP fetch (FLAGS + BODY.PEEK[])
   → parse (Header-Erhalt erweitert, Raw-MIME-Header verschlüsselt gespeichert)
   → ingest_raw()  [NEUER gemeinsamer Choke-Point]
        ├─ Stufe 0: Provider-Evidenz auswerten ($Junk, Auth-Results, X-Spam)
        ├─ Stufe 1: hart-codierte Heuristik (SPF/DKIM/DMARC, Mismatch, Punycode, Attachment-Denylist)
        ├─ Stufe 2: HTML-Sanitization (hidden_text abspalten, Zero-Width/Bidi strippen, data:/js: neutralisieren)
        ├─ IngestDecision: store | quarantine | (hard-block → nur ingest_log)
   → insert_email() [defensiver Guard]  + ingest_log (uid/decision/reason)
   → [async] Stufe 4: Ollama-Klassifikator → spam_verdict, category, tags, summary
   → Obsidian-Export nur für quarantined=0, angereichert mit Klassifikator-Daten
```

## Sprints

| # | Titel | Ziel | Write-Scope (Kern) | Gate |
|---|---|---|---|---|
| 01 | MCP-Härtung & Injection-Grundschutz | Offene Exfiltrations-/Injection-Wege schließen | `mcp/tools.py`, `mcp/server.py`, `storage/obsidian.py`, `storage/models.py`, neues `security/sanitize.py` | @security + @api-guardian |
| 02 | Ingest-Fundament | Migration, gemeinsamer Choke-Point, Header-Erhalt, Watermark-Fix, Schema | `storage/database.py`, `imap/parser.py`, `imap/client.py`, `imap/sync_manager.py`, `imap/idle_listener.py`, neues `ingest/pipeline.py` | @api-guardian |
| 03 | Filter-Schichten 0–2 | Provider-Evidenz + Heuristik + HTML-Sanitization | `ingest/pipeline.py`, `ingest/filters.py`, `imap/parser.py`, `config.py` | @security |
| 04 | Ollama-Klassifikator (Stufe 4) | Async Kategorisierung + Vault-Anreicherung | neues `classify/`, `storage/database.py` (Worker-Status), `storage/obsidian.py`, `config.py`, `cli.py` | Deterministic + Review |

**Ausführung strikt sequentiell** — die Write-Scopes überlappen (`database.py`, `parser.py`,
`obsidian.py` in mehreren Sprints); Parallelisierung ist ausgeschlossen. `pyproject.toml` (Deps)
und `config.py` sind Hot-Files, single-writer pro Sprint.

## Nicht in v0.2.0 (bewusst)

- Vektor-Embeddings / Hybrid-Suche (`sqlite-vec`) → eigener Plan v0.3.0
- rspamd-Daemon (Stufe 3)
- Apple-FM-Integration
- Malware-/AV-Scan von Attachments, OCR/PDF-Textextraktion
- Absender-/Kontakt-Reputationstabelle über Zeit (nur einfaches `trust_level` pro Mail)

## Release

Version-Bump auf `0.2.0` in `pyproject.toml` erfolgt **einmalig im letzten Sprint** (04), nicht bei
Arbeitsbeginn. Kein separates VERSION-File/CHANGELOG im Repo — Änderungshistorie via Git +
`reports/v0.2.0/`. Kein `git push` ohne ausdrückliche Freigabe.

## Querschnitt-Risiken

- **Bestehende produktive DBs**: ohne Migrationsmechanismus crashen neue Spalten. Sprint 02 baut
  `PRAGMA user_version` + idempotente `ALTER TABLE` **vor** allen Schemaänderungen.
- **Raw-MIME nicht aufbewahrt**: Bestandsmails können Trust-Header nie nachliefern. Ab v0.2.0 wird
  der Header-Block verschlüsselt persistiert; Backfill für Altbestand ist best-effort.
- **IDLE-Push-Latenz**: Klassifikation (Stufe 4) läuft **async**, nie im Push-Pfad.
