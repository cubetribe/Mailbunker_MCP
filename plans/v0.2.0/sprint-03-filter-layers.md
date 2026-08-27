---
sprint: 03
title: Filter-Schichten 0–2 (Provider-Evidenz, Heuristik, HTML-Sanitization)
status: done
ux_gate: skip
version_relevance: minor
result: "DONE — 92/92 Tests grün. Stufe 0-2 im ingest_raw-Choke-Point: Provider-Evidenz, Heuristik (tldextract offline, idna, Homoglyph nur geflaggt), HTML-Sanitization mit hidden_text-Isolation (inline UND klassenbasiert via <style>). Konservative Policy (Quarantäne-Schwelle 3.0/Block 7.0, block nur bei Provider-Hard-Evidence). @security APPROVED nach Fix-Runde (High: hidden_text-Leak in get_email(json) geschlossen; Medium: CSS-Klassen-Bypass geschlossen). Restlücke Low: Kombinator-Selektoren bewusst übersprungen → v0.3.0-Härtung. Reports: reports/v0.2.0/sprint-03/."
---

# Sprint 03 — Filter-Schichten 0–2

## Ziel
Die hart-codierte, deterministische Filterung in den Choke-Point aus Sprint 02 einhängen. Deckt
~70–80 % ohne LLM/Docker. Setzt Score + Trust-Level + Quarantäne-Entscheidung.

## Scope
Neues Modul `src/mailbunker/ingest/filters.py`, aufgerufen aus `ingest/pipeline.py`:

**Stufe 0 — Provider-Evidenz** (aus den in Sprint 02 erhaltenen Headern/Flags):
- IMAP-`$Junk`/`Junk`-Flag → starkes Spam-Signal.
- `Authentication-Results` parsen: `dmarc=fail` / `spf=fail` / `dkim=fail`.
- `X-Spam-Flag: YES` / `X-Spam-Score` / `X-ICL-SCORE` auswerten.
- Entscheidung: Junk-Flag **und** DMARC-fail → Hard-Block-Kandidat.

**Stufe 1 — hart-codierte Heuristik** (neue Deps: `tldextract`, `idna`; `authentication-headers`
optional):
- From-Domain ≠ Reply-To-Domain → Score.
- Display-Name behauptet bekannte Marke, From-Domain passt nicht → Score.
- Punycode/`xn--`-Domains via `idna` dekodieren, Unicode-Script-Mixing (Homoglyph) → Score.
- Attachment-Denylist (`.exe`, `.scr`, `.js`, `.vbs`, doppelte Endungen) → Score/Block.
- `List-Id`/`Precedence: bulk`/`List-Unsubscribe` → Label `newsletter` (kein Spam).

**Stufe 2 — HTML-Sanitization** (neue Dep: `bleach`; in `imap/parser.py:54-83` integriert):
- Hidden-Elemente erkennen (`display:none`, `visibility:hidden`, `font-size:0/1px`,
  color==background, `width/height:0`, off-screen, `aria-hidden`) und in separates Feld
  `hidden_text` abspalten — **niemals** in `body_text`/FTS/Embedding, aber als Injection-Signal
  scoren.
- Zero-Width/Bidi im Body strippen (via `security/sanitize.py` aus Sprint 01).
- `data:`-URIs durch Platzhalter ersetzen (kein unbegrenztes Inlining); `javascript:`/`vbscript:`-
  hrefs neutralisieren.
- Externe Bild-/Tracking-Link-Domains extrahieren und als Feature ablegen statt roh im Markdown.

**Scoring & Policy** (`config.py`):
- Score-Akkumulation über Stufen; zwei Schwellen `SPAM_QUARANTINE_THRESHOLD`,
  `SPAM_BLOCK_THRESHOLD` (konfigurierbar).
- `IngestDecision`: `store` | `quarantine` (store + hidden) | `block` (nur ingest_log).
- `trust_level`-Ableitung: `verified` (DMARC-pass + bekannt) / `known` / `unknown` / `suspicious` /
  `malicious`.
- `FOLDER_DENYLIST` (Junk, Spam, Trash, Deleted Items, [Gmail]/Spam) in `sync_manager` auswerten.
- `MAX_MESSAGE_SIZE_BYTES`, `MAX_ATTACHMENT_SIZE_BYTES`, `ATTACHMENT_TYPE_DENYLIST`.

## Non-Goals
- Kein LLM (→ Sprint 04). rspamd bewusst **nicht** integriert.
- Keine lernende Bayes-Komponente (nur deterministische Regeln + Provider-Signal).

## Write-Scope (Ownership)
- `src/mailbunker/ingest/filters.py` (neu), `src/mailbunker/ingest/pipeline.py` (Hook füllen)
- `src/mailbunker/imap/parser.py` (HTML-Sanitization, hidden_text), `imap/sync_manager.py`
  (FOLDER_DENYLIST)
- `src/mailbunker/config.py` (Schwellen, Limits, Denylists)
- `src/mailbunker/storage/models.py` (`hidden_text`, Feld-Ergänzungen falls nötig)
- `pyproject.toml` (Deps: `bleach`, `tldextract`, `idna`)
- neue `tests/test_filters.py`

## Risks
- **False-Positives** blocken legitime Mails → Default-Schwellen konservativ; `block` nur bei
  eindeutigen Signalen (Auth-fail + Junk-Flag + Attachment-Denylist). Im Zweifel `quarantine`, nie
  `block`.
- Homoglyph-Heuristik nur **flaggen**, nicht hart blocken (legitime nicht-lateinische Absender).
- `bleach`-Whitelist darf legitime Formatierung nicht zerstören → nur Hidden-Detection + gefährliche
  Schemata, Markdown-Konvertierung bleibt html2text.

## Acceptance Criteria
- [ ] Provider-Junk-Flag + DMARC-fail → Quarantäne/Block laut Schwelle.
- [ ] Hidden-Text (display:none etc.) landet in `hidden_text`, nicht in `body_text`/FTS.
- [ ] `data:`-URIs werden nicht mehr inline übernommen.
- [ ] Newsletter korrekt gelabelt, nicht als Spam quarantänisiert.
- [ ] `FOLDER_DENYLIST` verhindert Junk/Trash-Sync.
- [ ] `tests/test_filters.py` deckt jede Regel + Grenzfälle ab; alle Tests grün.
- [ ] @security: Sanitization schließt S5/S6 aus dem Audit an der Quelle.

## Test Strategy
`pytest`; `tests/test_filters.py` mit realistischen Spam-/Ham-/Newsletter-/Phishing-Samples
(synthetisch, DE+EN); Assertion auf decision/score/trust_level/hidden_text.

## Changelog Note
Filtering: hard-coded layered pre-filter (provider evidence, SPF/DKIM/DMARC + heuristics, HTML
sanitization with hidden-text isolation); quarantine/block policy with configurable thresholds;
folder/size/attachment denylists.
