---
sprint: 01
title: MCP-Härtung & Injection-Grundschutz
agent: scribe
status: done
---

# Sprint 01 — Documentation Integration Report

## Summary

Completed documentation of all Breaking Changes identified by @api-guardian following the MCP security hardening implemented in this sprint. Created new `CHANGELOG.md` file with `[Unreleased]` section containing Security, Breaking Changes, Changed, and Removed categories. Updated both `README.md` and `README.de.md` to clarify MCP tool behavior changes, particularly:

- `export_obsidian_vault` password requirement and path allowlist enforcement
- `get_email(format="json")` removal of `body_html` and `raw_headers` fields
- `search_emails` and `get_email` response sanitization and `_notice` field

All documentation follows Keep a Changelog convention and provides clear migration paths for affected consumers.

## Files Changed

| File | Action | Summary |
|------|--------|---------|
| `CHANGELOG.md` | Created | New file with `[Unreleased]` section (Security, Breaking Changes, Changed, Removed). Comprehensive details for all 3 breaking changes plus 7 security enhancements and 2 changed behaviors. |
| `README.md` | Updated | MCP Tools table: clarified `export_obsidian_vault` password requirement + path allowlist; `get_email` sanitization and `body_html`/`raw_headers` removal; `search_emails` `_notice` field. |
| `README.de.md` | Updated | German parallel of README.md updates. Tool descriptions mirrored with consistent terminology. |

## Changelog Integration

### `[Unreleased]` Section Structure

| Category | Entries | Highlights |
|----------|---------|-----------|
| **Security** | 7 items | Password enforcement, path allowlist, untrusted content neutralization, length limits, Obsidian hardening, email address escaping, tool annotations, adversarial tests. |
| **Breaking Changes** | 3 items | (1) `get_email(json)` removes `body_html`/`raw_headers`, (2) `export_obsidian_vault` password now mandatory, (3) `export_obsidian_vault` enforces path allowlist. Each with "Affected Consumers", "Migration", and "Rationale". |
| **Changed** | 2 items | `_notice` field in responses; field sanitization and error handling improvements. |
| **Removed** | 2 items | `body_html` and `raw_headers` from JSON format (duplicates Breaking Changes section for RFC clarity). |

### Migration Path Clarity

All three Breaking Changes explicitly document:
- **Affected Consumers:** Which clients/automations will break
- **Migration:** Concrete code/config changes needed
- **Rationale:** Security justification (prevents injection, exfiltration, path traversal)

This aligns with @api-guardian's recommendation (line 144–147 of the Guardian report) that consumers have clear upgrade guidance before the release sprint.

## Documentation Strategy

### README Tool Descriptions

**Original state:** Generic parameter lists with no security context.

**Updated state:**
- `search_emails`: Added `_notice` field mention
- `get_email`: Sanitization note + explicit `format="json"` deprecation for HTML/raw headers
- `export_obsidian_vault`: Marked both parameters as required + path allowlist enforcement

### Consistency (EN ↔ DE)

Both READMEs updated in parallel, maintaining terminology:
- "mandatory" / "erforderlich"
- "`_notice` field" / "`_notice`-Feld"
- "sanitized" / "bereinigt"

No new inconsistencies introduced; existing emoji/style preserved in both files.

## Compliance with Scribe Contract (v8.5)

✅ **Scope adherence:** Only edited CHANGELOG.md, README.md, README.de.md (all within assigned write scope).
✅ **No foreign commits:** All changes are solo work, no conflicts with other sprint files.
✅ **No VERSION edits:** Left `VERSION` untouched; version bump will occur in Release Sprint (04) via `scripts/version-bump.js`.
✅ **[Unreleased] only:** No dated version headings created; all entries go under `## [Unreleased]`.
✅ **Consumer impact documented:** Every Breaking Change includes affected parties and migration guidance.

## API Consumer Registry

No separate `docs/API_CONSUMERS.md` file exists yet in this repo. The MCP tools are consumed directly by AI assistants (Claude, Cursor, etc.) via FastMCP stdio, not by internal code. The README tool descriptions now serve as the primary consumer-facing API documentation.

## Quality Gates

| Gate | Status | Notes |
|------|--------|-------|
| Changelog format | ✅ PASS | Follows Keep a Changelog v1.0.0; no syntax errors. |
| README updates | ✅ PASS | Both EN and DE consistent; all tool descriptions updated; emoji/style preserved. |
| Breaking Changes clarity | ✅ PASS | Each of 3 breaking changes has affected consumers, migration path, and rationale per @api-guardian's requirement (report line 144–147). |
| Scope adherence | ✅ PASS | Only CHANGELOG.md, README.md, README.de.md touched; no code changes. |
| Version isolation | ✅ PASS | No VERSION edits; no dated CHANGELOG headings (Release Sprint responsibility). |

## Next Steps (Release Sprint — Sprint 04)

1. Orchestrator runs `node scripts/version-bump.js minor` (0.1.0 → 0.2.0).
2. Tooling promotes `[Unreleased]` to `## [0.2.0] - YYYY-MM-DD`.
3. All version touchpoints synced via `scripts/sync-version.js`.
4. Release PR created by @github-manager with CHANGELOG + version updates.
