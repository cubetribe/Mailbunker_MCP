---
sprint: 02
title: Ingest-Fundament — Security Spotcheck
agent: security
status: done
verdict: APPROVED
---

# Sprint 02 — Security Spotcheck Report

## Scope

Enger, gezielter Spotcheck (kein Voll-Audit) auf vier Punkte. Read-only.
Geprüfte Dateien: `storage/database.py`, `imap/parser.py`, `ingest/pipeline.py`,
sowie die Aufrufstellen in `imap/sync_manager.py` und `imap/idle_listener.py` und
`crypto/engine.py`. Runtime-Verifikation via `.venv/bin/python` (Python 3.14.6).

## Verdict

**APPROVED with notes** — keine Critical/High-Findings. Zwei Low/Info-Hinweise
(defense-in-depth), die Sprint 02 nicht blockieren.

## Findings

| Severity | Titel | Location | Remediation |
|----------|-------|----------|-------------|
| Info (PASS) | raw_header_blob korrekt verschlüsselt at-rest | `storage/database.py:230-234, 302` | keine |
| Info (PASS) | Quarantäne-Ausblendung wirksam (FTS + strukturiert) | `storage/database.py:458-461` | keine |
| Low | `ingest_log.reason` speichert im Fehlerpfad `str(err)` in Klartext-Tabelle | `imap/sync_manager.py:90`, `imap/idle_listener.py:181` | Exception-Text auf Typ/Kurzmeldung reduzieren oder kürzen |
| Low/Info | Migration nicht transaktional-atomar (Py 3.12+), aber idempotent selbstheilend | `storage/database.py:139-168` | Zukünftige nicht-additive Schritte (FTS drop+reindex) nicht auf `with conn:` verlassen |

## Detail

### 1. `raw_header_blob` at-rest — PASS

Der Roh-Header-Block wird **verschlüsselt** persistiert, nicht als Klartext:

- `database.py:232-234`: `encrypted_header_blob = self.crypto.encrypt_str(email_msg.raw_header_blob) if email_msg.raw_header_blob else None`
- geschrieben in eigene Spalte an `database.py:302` (`encrypted_header_blob` als letzter Wert).
- `crypto/engine.py`: `encrypt_str` läuft über denselben `AESGCM`-Weg (AES-256-GCM,
  zufälliger `os.urandom`-Nonce, Argon2id-Key, MAGIC+salt+nonce+ciphertext) wie
  `encrypt_json` für das übrige Payload. Also derselbe Krypto-Pfad wie die sensiblen
  Payload-Felder.
- Lesepfad `get_email` (`database.py:370-375`) entschlüsselt mit `decrypt_str`.
- Der Blob landet **nicht** im FTS-Index (`database.py:307-319` indexiert nur
  subject/sender/recipients/body/tags/account/folder).

Runtime-Beleg: Rohspalte startet mit Base64-Ciphertext (`TUIwMY...`), nicht mit
`Received`; der Testmarker `evil` aus dem Header taucht in **keiner** Klartextspalte
der `emails`-Zeile und in **keiner** FTS-Zeile auf.

### 2. Quarantäne-Ausblendung — PASS

`database.py:460-461` hängt `COALESCE(emails.quarantined, 0) = 0` an, sofern nicht
`include_quarantined` gesetzt ist. Diese Condition liegt in der gemeinsamen
`conditions`-Liste und wird in **beiden** Zweigen (FTS-Join und strukturiert) und in
**beiden** Statements (Count und Select) über `where_clause` angewandt — also auch
auf dem `snippet()`-Pfad (`database.py:481-493`).

Runtime-Beleg: bei `query="secretword"` (FTS) liefert die Default-Suche nur `id1`,
die quarantänisierte `id2` bleibt verborgen; mit `include_quarantined=True`
erscheinen beide; die strukturierte Suche (ohne `query`) verbirgt `id2` ebenfalls.
Kein Inhalt-Leak über normale Suche oder FTS-Snippet.

`get_email(id)` gibt bewusst auch bei quarantänisierten Mails Inhalt zurück — das ist
expliziter Direktzugriff per ID und laut Auftrag akzeptabel (kein Finding).

### 3. `ingest_log.reason` — Low

Im Regelbetrieb enthält `reason` nur Metadaten/Regel-IDs: `filter_hook` liefert
`"no-op-sprint02"` (`ingest/pipeline.py:51`), der Leer-Pfad `"no raw bytes returned"`.
Kein Betreff/Body.

Aber: Der Fehlerpfad protokolliert `str(err)` als reason
(`sync_manager.py:90`, `idle_listener.py:181`) in die **unverschlüsselte**
`ingest_log`-Tabelle. Exception-Texte aus Parse-/Datums-/Adress-Validierung können den
auslösenden Header-Wert (z. B. Betreff- oder Adressfragment) mit ausgeben. Kein
Body-Auszug, aber ein möglicher Metadaten-Leak in Klartext, der der Idee „nichts
leakt" leicht widerspricht. Empfehlung: nur Exception-Typ + generische Meldung, oder
`str(err)` hart kürzen/whitelisten. Kein Blocker.

### 4. Migration-Sicherheit — Low/Info

Verschlüsselter Bestand wird nicht angetastet: `_migrate_v1_add_ingest_columns` führt
ausschließlich additives `ALTER TABLE emails ADD COLUMN` aus; bestehende
`encrypted_payload`/`raw_header_blob`-Daten werden nicht umgeschrieben (Runtime-Beleg:
`CIPHERTEXT-DO-NOT-TOUCH` nach simuliertem Migrationsfehler unverändert).

Zur Transaktionssicherheit: Auf Python 3.12+ (Umgebung = 3.14.6) committet DDL nicht
mehr innerhalb des `with conn:`-Blocks — ein empirisch erzwungener Fehler mitten in der
Migration hinterlässt bereits angelegte Spalten, während `PRAGMA user_version` noch 0
bleibt. Für **diesen** Schritt ist das harmlos und selbstheilend: die
Existenzprüfung `if col_name not in existing_cols` (`database.py:163-165`) macht die
Migration idempotent, sodass der nächste Open die fehlenden Spalten ergänzt und
`user_version` auf 1 setzt. Es entsteht kein Datenverlust und kein dauerhaft
inkonsistenter Zustand.

Forward-looking Hinweis (kein Sprint-02-Blocker): Der Code kommentiert einen künftigen
FTS „drop+reindex"-Pfad. Ein solcher nicht-additiver, nicht-idempotenter Schritt darf
sich nicht auf den `with conn:`-Wrapper als Atomaritätsgarantie verlassen — dort
explizit `BEGIN`/`COMMIT` bzw. `conn.autocommit`-Steuerung setzen.

## Dependency Audit

Nicht durchgeführt — kein Lockfile/Manifest in dieser Änderung berührt (reiner
Applikations-Code Sprint 02). Außerhalb des Spotcheck-Scopes.

## Verdict

```
STATUS: APPROVED
- PASS: raw_header_blob AES-256-GCM-verschlüsselt (eigene Spalte, nicht im FTS) — database.py:232-234,302
- PASS: Quarantäne (quarantined=1) auf FTS- und Struktur-Pfad standardmäßig ausgeblendet — database.py:460-461
- Low: ingest_log.reason loggt str(err) im Fehlerpfad in Klartexttabelle — sync_manager.py:90 / idle_listener.py:181
- Low/Info: Migration additiv+idempotent, Bestand unangetastet; DDL auf Py3.12+ nicht transaktional-atomar (self-healing) — künftige FTS-Migration absichern
report: /Volumes/2TB_CodingProjekte/Coding_Projekte/Mailbunker_MCP/reports/v0.2.0/sprint-02/03-security-report.md
```
