---
sprint: 01
title: MCP-Härtung & Injection-Grundschutz
agent: api-guardian
status: done
---

# Sprint 01 — API Impact Analysis Report

## Scope der Analyse
Diff-Basis: `git diff` (working tree, ungetrackt gegen `HEAD`) für
`src/mailbunker/mcp/tools.py`, `src/mailbunker/mcp/server.py`, `README.md`, `README.de.md`.
Konsumenten der "API" hier sind ausschließlich **MCP-Clients** (Claude Desktop, Cursor,
Antigravity, o.ä.), die die sieben Tools über den FastMCP-stdio-Server aufrufen. Es gibt
keine internen Code-Consumer (keine Importe von `tools.py`-Funktionen außerhalb von
`server.py` und `tests/`).

## Change Summary

| Fläche | Änderung | Breaking für MCP-Clients? |
|---|---|---|
| `search_emails` Response | neues `_notice`-Feld; `subject`/`sender`/`snippet`/`tags` durch `neutralize_untrusted()` gefiltert | 🟡 Additiv/Nicht-breaking im Schema, aber **verhaltensändernd** |
| `get_email(format="markdown"\|"text")` Response | neues `_notice`-Feld; `subject`/`sender`/`content` neutralisiert | 🟡 Additiv/Nicht-breaking im Schema, verhaltensändernd |
| `get_email(format="json")` Response | `email`-Objekt verliert `body_html` und `raw_headers` (`model_dump(exclude=…)`); `subject`/`body_text`/`body_markdown` neutralisiert; neues `_notice`-Feld | 🔴 **Breaking** (Feldentfernung) |
| `export_obsidian_vault` Signatur | unverändert (`target_path: str, password: Optional[str] = None`); **Semantik** geändert: `password` de facto verpflichtend, sonst `{"error": …}` | 🔴 **Breaking** für Aufrufe ohne Passwort |
| `export_obsidian_vault` Zielpfad | neue Allowlist-Prüfung (Default: `obsidian_vault_path`) | 🔴 **Breaking** für bisher gültige Exporte außerhalb des Vault-Pfads |
| `server.py` Tool-Annotationen | `ToolAnnotations` (`destructiveHint`, `readOnlyHint`, `idempotentHint`, `openWorldHint`, `title`) auf `sync_now`/`export_obsidian_vault` ergänzt | ✅ Additiv, rückwärtskompatibel (MCP-Hosts, die Annotations ignorieren, sind unbetroffen) |
| Docstrings | Warnhinweise (SIDE EFFECTS/DANGEROUS) ergänzt | ✅ Additiv, keine Konsumenten-Aktion nötig |
| `search_emails`/`get_email` Fehlerpfade | `datetime.fromisoformat`/`db.search_emails`/`db.get_email` jetzt in try/except, liefern `{"error": …}` statt unbehandeltem Exception/Crash | ✅ Verbesserung, nicht-breaking (Exception→strukturierter Fehler ist per Definition additiv für einen Client, der vorher nur einen Server-Crash sah) |

## Breaking Changes im Detail

### 1. `get_email(format="json")` — Feldentfernung `body_html` / `raw_headers`
- **Typ:** Response-Feld-Entfernung
- **Schweregrad:** 🔴 Hoch für jeden Consumer, der HTML-Rendering oder Raw-Header-Auswertung
  auf Basis des `json`-Formats implementiert (z. B. eigene UI, die `body_html` direkt anzeigt,
  oder ein Tool, das `raw_headers` für DKIM/SPF-Anzeige parst).
- **Consumer:** Es gibt bisher keine bekannten internen/externen Code-Consumer im Repo (kein
  Client-Code vorhanden); der reale Consumer ist der **MCP-Host selbst** (LLM-Agent), der aus
  der Tool-Response Felder erwartet. Da MCP-Tools i. d. R. lose typisiert konsumiert werden
  (LLM liest JSON, kein statisches Interface), ist der praktische Schaden geringer als bei
  einem typisierten REST-Client — aber es ist dennoch eine Vertragsänderung: jeder externe
  Agent/Workflow, der explizit nach `email.body_html` sucht (z. B. um HTML lokal zu rendern),
  bekommt künftig `None`/`KeyError` statt Daten.
- **Bewertung:** Sicherheitsbegründet und laut Sprint-Auftrag (Punkt 4, Akzeptanzkriterium)
  explizit gefordert — trotzdem ein Breaking Change im engeren Sinn, kein rein additiver.

### 2. `export_obsidian_vault` — Passwort verpflichtend
- **Typ:** Aufrufsemantik-Änderung ohne Signaturänderung (`password: Optional[str] = None`
  bleibt syntaktisch optional, ist aber funktional jetzt Pflicht)
- **Schweregrad:** 🔴 Hoch — jeder bestehende Aufruf `export_obsidian_vault(target_path=...)`
  ohne `password` (vorher: `if password and password != ...` → kein Passwort = Erfolg) schlägt
  jetzt garantiert mit `{"error": "Vault master password is required..."}` fehl. Das ist exakt
  die beabsichtigte Sicherheitskorrektur (die Lücke war der Sprintauslöser), aber
  Consumer-seitig ein Verhaltensbruch: 100 % der bisherigen passwortlosen Aufrufe brechen.
- **Zusätzlich:** Die neue Pfad-Allowlist (`export_allowed_roots` via `getattr`, Fallback
  `obsidian_vault_path`) bricht zusätzlich jeden bisherigen Aufruf mit `target_path` außerhalb
  des konfigurierten Vault-Pfads — auch mit korrektem Passwort.
- **Bewertung:** Die Signatur selbst (`Optional[str] = None`) ist irreführend, da sie
  suggeriert, ein Aufruf ohne Passwort sei weiterhin gültig. Das Sprintziel wollte genau das
  verhindern — funktional korrekt, aber die Typ-Annotation im Code (`Optional[str] = None`)
  spiegelt die neue Pflicht nicht wider. Empfehlung an @builder/@architect für Folgesprint:
  Signatur auf `password: str` (ohne Default) ändern, um den Vertrag im Typsystem abzubilden
  statt nur zur Laufzeit zu prüfen — aktuell kein Blocker, aber Typ-Contract-Drift.

### 3. `search_emails` / `get_email` — Inhaltsneutralisierung + `_notice`
- **Typ:** Additives Feld (`_notice`) + Wertneutralisierung bestehender String-Felder
- **Schweregrad:** 🟢 Niedrig als Schema-Änderung (kein Feld entfernt, `_notice` ist neu und
  additiv), aber 🟡 mittel als **Wertänderung**: Steuerzeichen, Zero-Width-/Bidi-Zeichen werden
  aus `subject`/`sender`/`snippet`/`tags`/`content`/`body_text`/`body_markdown` entfernt und
  bei Überlänge hart trunkiert (`max_len=20000` Default in `neutralize_untrusted`). Für
  legitime, sehr lange E-Mail-Bodies (>20000 Zeichen) bedeutet das sichtbaren Datenverlust
  ohne Kennzeichnung der Kürzung im Response-Objekt selbst (kein `truncated: true`-Flag,
  soweit aus dem Diff ersichtlich).
- **Consumer-Auswirkung:** Kein Consumer verlässt sich vermutlich auf exakte Byte-Identität
  von Steuerzeichen — daher praktisch nicht-breaking. Die harte Längenbegrenzung ohne
  Truncation-Indikator ist jedoch eine stille Datenverlust-Stelle, die @tester/@security im
  nächsten Blick prüfen sollte (kein STATUS: BLOCKED-Grund für diesen Sprint, da nicht im
  Scope gefordert, aber Follow-up-Punkt).

## Konsumenten-Impact-Matrix

| Consumer | Aufrufmuster | Betroffen? | Erforderliche Aktion |
|---|---|---|---|
| MCP-Host (Claude/Cursor/Antigravity), generischer Agent-Loop | Liest JSON-Response frei, kein starres Schema | Gering — LLM interpretiert `_notice` und fehlende Felder tolerant | Keine Code-Änderung nötig |
| Externe/eigene Skripte, die `get_email(format="json")["email"]["body_html"]` lesen | Erwartet `body_html`/`raw_headers` | 🔴 Ja | Auf `format="markdown"` umstellen oder eigenen HTML-Renderpfad anpassen |
| Bestehende Automationen, die `export_obsidian_vault(target_path=X)` ohne `password` aufrufen | Erwartet stillen Erfolg | 🔴 Ja | `password` aus Config/ENV ergänzen |
| Bestehende Automationen mit `target_path` außerhalb `obsidian_vault_path` | Erwartet Export dorthin | 🔴 Ja | `export_allowed_roots` in Config setzen oder Zielpfad auf Vault-Root verlegen |
| Interne Tests (`tests/test_mcp.py`, `tests/test_obsidian.py`) | — | Bereits vom Builder angepasst (38/38 grün) | — |

## Dokumentationslücke

- `README.md`/`README.de.md` wurden im selben Arbeitsstand umfassend neu geschrieben
  (Marketing-/Struktur-Update), die Tool-Tabelle listet `export_obsidian_vault` weiterhin mit
  Parametern `target_path`, `password` — **ohne Hinweis**, dass `password` jetzt zwingend ist,
  ohne Erwähnung der Pfad-Allowlist, und ohne Hinweis auf die Entfernung von
  `body_html`/`raw_headers` aus dem `json`-Format oder das neue `_notice`-Feld.
- Es existiert **keine `CHANGELOG.md`** im Repo (noch nicht angelegt) — die im Sprintfile
  vorgesehene Changelog Note ("Security: mandatory password + path allowlist for vault export;
  untrusted-content neutralization…") ist bisher nirgends persistiert. Das ist der erwartete
  @scribe-Schritt (Core Rule 11), aber es fehlt aktuell jede für MCP-Client-Betreiber sichtbare
  Migrationsnotiz.
- **Fazit:** Consumer-Migrationsnotiz fehlt vollständig. Für ein Minor-Release mit tatsächlich
  breaking Verhalten (Pflichtpasswort, Feldentfernung) ist das ein Dokumentationsdefizit, kein
  reiner Prosa-Wunsch — bestehende Deployments, die `export_obsidian_vault` bisher
  passwortlos oder mit freiem Zielpfad automatisiert haben, brechen ohne Vorwarnung.

## Versionierungsempfehlung

Die drei identifizierten Breaking Changes (`body_html`/`raw_headers`-Entfernung,
Passwortpflicht, Pfad-Allowlist) sind **inhaltlich Sicherheitsfixes, die eine vorher
exploitable Schwachstelle schließen** (Klartext-Exfiltration ohne Auth, Prompt-Injection via
Mail-Inhalt). Das rechtfertigt sachlich einen "Security Fix"-Charakter statt einer normalen
API-Erweiterung. Dennoch bleibt es aus reiner Contract-Sicht eine Breaking Change:

1. **Empfehlung:** Minor-Bump (0.1.0 → 0.2.0) ist vertretbar **unter der Bedingung**, dass
   @scribe im CHANGELOG explizit unter einem eigenen Abschnitt "BREAKING / Security" (nicht
   nur "Security") dokumentiert:
   - `export_obsidian_vault`: `password` ist ab sofort **zwingend erforderlich**; passwortlose
     Aufrufe schlagen fehl.
   - `export_obsidian_vault`: `target_path` muss innerhalb des konfigurierten Vault-Pfads
     (oder `export_allowed_roots`) liegen.
   - `get_email(format="json")`: `body_html` und `raw_headers` sind **nicht mehr Teil der
     Response**; für HTML-Inhalt `format="markdown"` verwenden.
   - Alle Responses tragen jetzt ein `_notice`-Feld; Textfelder werden längenbegrenzt und von
     Steuer-/Zero-Width-Zeichen bereinigt.
2. Für strikte SemVer-Praxis (Contract-Bruch = Major) wäre 0.2.0 knapp, aber da dieses Projekt
   sich noch in der 0.x-Phase befindet (SemVer erlaubt Breaking Changes in Minor-Versionen
   solange Major=0) und laut Sprintfile `version_relevance: minor` bereits so geplant ist, ist
   der Minor-Bump **vertretbar** — vorausgesetzt die o. g. Migrationsnotiz wird ergänzt, bevor
   der Release-Sprint das CHANGELOG befüllt.
3. Die Signatur-Typannotation `password: Optional[str] = None` sollte in einem Folgesprint auf
   `password: str` geschärft werden, um den Typ-Contract mit dem Laufzeitverhalten in Einklang
   zu bringen (kein Blocker für diesen Sprint).

## Migration Checklist (für Consumer-Betreiber, nicht @builder)

- [ ] `.env`/Aufrufkonfiguration prüfen: `VAULT_PASSWORD` wird bei jedem
      `export_obsidian_vault`-Aufruf mitgegeben.
- [ ] Zielverzeichnisse für Exporte liegen innerhalb `obsidian_vault_path` oder sind über
      `export_allowed_roots` freigegeben.
- [ ] Eigene Integrationen, die `get_email(format="json")["email"]["body_html"]` oder
      `["raw_headers"]` lesen, auf `format="markdown"` oder eigenes HTML-Handling umstellen.
- [ ] CHANGELOG-Eintrag mit den drei o. g. Breaking-Punkten vor Release-Sprint ergänzen
      (@scribe).
- [ ] README-Tool-Tabelle (`export_obsidian_vault`, `get_email`) um die geänderte Semantik
      ergänzen (@scribe/@docs-dx).

## Server-Signaturen (`server.py`)

Keine Parameteränderungen an den sieben Tool-Funktionssignaturen selbst — nur
`ToolAnnotations`-Metadaten (`title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`,
`openWorldHint`) und erweiterte Docstrings. Das ist rückwärtskompatibel: MCP-Clients, die
Annotations nicht auswerten, sind unbetroffen; Clients, die sie auswerten (z. B. zur
Nutzerbestätigung vor destruktiven Aktionen), erhalten strengere/korrektere Hinweise — rein
additiv, keine Breaking Change.
