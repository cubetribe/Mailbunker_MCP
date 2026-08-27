---
sprint: 01
title: MCP-Härtung & Injection-Grundschutz
agent: security
status: APPROVED
verdict: APPROVED (with notes)
---

# Sprint 01 — Security Review

## Summary

Read-only Security-Gate über den Sprint-01-Änderungssatz (`security/sanitize.py`,
`mcp/tools.py`, `mcp/server.py`, `storage/models.py`, `storage/obsidian.py` sowie die
drei Testdateien). Der **kritische** Exploit — unauthentisierter Klartext-Export via
`export_obsidian_vault` bei weggelassenem Passwort — ist **geschlossen** und am realen
Code verifiziert. Ebenso verifiziert: Passwort-Pflicht + `hmac.compare_digest`,
`target_path`-Allowlist mit `resolve()` (kein `..`-/Symlink-Ausbruch), `get_email(json)`
ohne `body_html`/`raw_headers`, Zero-Width/Bidi-Stripping, `EmailAddress`-Quote-Escaping.

Es verbleiben **keine offenen High/Critical**. Gefunden wurden drei Medium/Low-Punkte
(Defense-in-Depth-Lücken) — insbesondere eine bypassbare Backtick-Fencing-Neutralisierung
im Obsidian-Rendering und ein unvollständiger Invisible-Character-Filter (Unicode-Tag-Block).
Per Severity-Regel: **APPROVED with notes**.

## Verifikation der beauftragten Punkte

| # | Punkt | Ergebnis |
|---|-------|----------|
| 1 | Export: Passwort-Pflicht + `compare_digest` + Allowlist | GESCHLOSSEN. `if not password: return {"error"}` VOR jedem `export_all`. Konstantzeit-Vergleich. `dest = Path(target_path).resolve()`, Check `dest == root or root in dest.parents` gegen resolvte Roots — `..` wird kollabiert, Symlinks werden von `resolve()` aufgelöst (dest zeigt auf reales Ziel → außerhalb Root → blockiert). Kein Path-Traversal-Ausbruch gefunden. |
| 2 | `neutralize_untrusted` Zero-Width/Bidi/Control | Funktioniert für die deklarierten Ranges (U+200B–200F, U+202A–202E, U+2066–2069, U+FEFF, C0/C1 außer `\n\t`). Empirisch bestätigt. Umlaute/echte `\n`/`\t` bleiben erhalten (kein Verstümmeln). Harte Längenbegrenzung (20000) vorhanden. **ABER**: weitere Invisible-Kategorien nicht abgedeckt → Finding SEC-2. |
| 3 | `wrap_untrusted` Boundary | Boundary-Präfix `mailbunker-untrusted-` wird case-insensitiv aus dem Content gestrippt → Closing-Tag nicht fälschbar. Token = `secrets.token_hex(8)` (64 bit) — stark genug. Hinweis: Funktion ist **nicht** in `tools.py` verdrahtet (nur `neutralize_untrusted` + `_notice` aktiv) — laut Scope korrekt, aber ohne Laufzeitwirkung. |
| 4 | `get_email(format="json")` | GESCHLOSSEN. `model_dump(exclude={"body_html","raw_headers"})`; markdown/text-Zweige nutzen nur `body_text`/`body_markdown`. Test `test_get_email_json_excludes_raw_body_html_and_headers` deckt es ab. Verifiziert. |
| 5 | `EmailAddress.__str__` + Obsidian-Injektionen | `"` wird escaped (verhindert Displayname-Spoofing). `[[`/`](` werden über Backtick-Fencing bzw. Escaping neutralisiert — **aber Backtick-Fencing ist per Backtick im Displaynamen umgehbar** → Finding SEC-1. |

## Findings

| Severity | Titel | Location | Scope | Remediation |
|----------|-------|----------|-------|-------------|
| Medium | Backtick-Fencing im Obsidian-Rendering per Backtick im Displaynamen/Attachment-Namen ausbrechbar | `storage/obsidian.py:55-56,83-87` | in-scope (Punkt 6) | Backticks im gefenceten Wert escapen/entfernen oder auf HTML-Escape statt Inline-Code umstellen. Auch Attachment-Label: `` ` `` und `[` nicht escaped. |
| Medium | Unvollständiger Invisible-Character-Filter: Unicode-Tag-Block (U+E0000–E007F) und weitere Unsichtbare (U+2028/2029, U+00AD, U+2060, U+180E, U+FFF9) werden nicht gestrippt | `security/sanitize.py:19-33` | teil-scope (deklarierte Ranges umgesetzt; AC „Steuerzeichen erscheinen nicht mehr" bleibt lückenhaft) | Tag-Block und die genannten Format-/Separator-Codepoints ergänzen; robuster: per `unicodedata.category` alle `Cf`/`Cc`/`Co`/`Cs` außer `\n\t` filtern. |
| Low | `hmac.compare_digest` wirft `TypeError` bei Nicht-ASCII-Passwort (nicht in try/except) | `mcp/tools.py:204` | in-scope | Vergleich auf Bytes umstellen (`.encode("utf-8")`) oder in try/except kapseln; sonst crasht der Export bei legitimen Umlaut-Passwörtern statt strukturiertem Fehler. Kein Auth-Bypass (fail-closed), aber Robustheits-/DoS-Aspekt. |

### Detail SEC-1 (bestätigt am Code)
Gerendert: `` > **From:** `"x` [[Injected]] `y" <e@x.com>` `` — der Backtick im Displaynamen
schließt die Inline-Code-Span; `[[Injected]]` wird von Obsidian als **live Wikilink**
gerendert (potenziell auch `![](http://…)`-Bild-Embed → Auto-Fetch/Beacon). Der S4-Test
`test_email_address_wikilink_name_is_backtick_fenced_in_obsidian_render` prüft nur einen
Namen **ohne** Backtick und gibt daher falsche Sicherheit.

## Restrisiken (bewusst / außerhalb Sprint-Scope)
- FTS-`snippet()`-Bug (`database.py:340`, kein MATCH) — dokumentierter Non-Goal → **Sprint 02**. Bestätigt als noch offen.
- `sync_now` ohne harten Guard, nur `ToolAnnotations`/Docstring — verlässt sich vollständig darauf, dass der MCP-Host die Hints respektiert. Scope-konform, aber Restrisiko.
- `wrap_untrusted` implementiert/getestet, aber nicht in Responses verdrahtet — keine Laufzeitwirkung.

## Testqualität
Überwiegend aussagekräftig-adversarial (Export ohne/falsches PW + „writes nothing", Pfad-Allowlist,
json-Exclusion, RFC-2047-Betreff, Zero-Width-Stripping mit Umlaut-Erhalt). **Schwächen**: der
Wikilink/Backtick-Test (S4) deckt den realen Backtick-Bypass nicht ab (SEC-1); kein Test für
Unicode-Tag/Format-Zeichen (SEC-2); kein Nicht-ASCII-Passwort-Fall (SEC-3).

## Dependency Audit
Keine Manifest-/Lockfile-Änderung im Sprint-Diff (nur `src`/`tests`). Kein neues Dependency-Risiko
eingeführt; Audit nicht anwendbar für diesen Änderungssatz.

## Verdict

```
STATUS: APPROVED
- SEC-1 (Medium, in-scope): Obsidian-Backtick-Fencing per Backtick im Displayname/Attachment ausbrechbar → Wikilink/Markdown-Injektion
- SEC-2 (Medium, teil-scope): Invisible-Filter deckt Unicode-Tag-Block (U+E0000–E007F) und weitere Format/Separator-Codepoints nicht ab
- SEC-3 (Low, in-scope): compare_digest wirft TypeError bei Nicht-ASCII-Passwort (kein Bypass, aber ungefangener Crash)
- Kritischer Export-Exploit geschlossen; keine offenen High/Critical
report: /Volumes/2TB_CodingProjekte/Coding_Projekte/Mailbunker_MCP/reports/v0.2.0/sprint-01/02-security-report.md
```

APPROVED (with notes) — keine offenen High/Critical; drei Medium/Low-Härtungspunkte für @builder als Folge-Fix empfohlen (SEC-1 vorzugsweise noch in v0.2.0).
