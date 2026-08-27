---
report: security-review
sprint: 04
verdict: APPROVED
date: 2026-08-24
---

# Security Review — Sprint 04 (Ollama Classifier)

> Persisted by the orchestrator from the security agent's inline verdict (the agent could not
> write to disk in its environment). Core Rule 8.

## Summary
Reviewed the untrusted-email → LLM → Vault/MCP prompt-injection surface across
`classify/{prompts,classifier,client,schema,worker}.py`, `storage/obsidian.py`,
`storage/database.py`, `mcp/tools.py`. Defense-in-depth holds up under adversarial testing.
**No Critical/High. Verdict: APPROVED.**

## Confirmed (pass)
1. **`hidden_text` never fed to the model.** `build_user_prompt()` takes `body_markdown` +
   `hidden_text_present: bool` only; runtime test confirmed the payload never appears in the prompt,
   only `contains hidden text: yes`.
2. **Output neutralization of `summary`/`tags`.** Both run through `neutralize_untrusted` before
   persistence; zero-width/bidi/control chars stripped. Vault `_neutralize_obsidian_meta`
   additionally breaks `[[…]]` and backticks. A `summary` containing `\n---\nrogue: true` is emitted
   by `yaml.dump` as an indented single-quoted scalar → cannot break out of frontmatter.
3. **`get_email(json)` `summary`** is the neutralized value; `hidden_text`/`raw_header_blob`/
   `raw_headers`/`body_html`/`embedding_state` remain excluded — no regression, no new leak.
4. **Prompt structure / parsing.** System prompt forbids obeying embedded instructions; untrusted
   subject/sender/body fenced via `wrap_untrusted` (random boundary, prefix stripped). Parsing:
   `format=json`, code-fence tolerance, ≤2 retries, pydantic validators coerce to safe defaults,
   `fallback()` verdict on persistent failure — no crash, no silent loss. Classifier is
   additive-only: `save_classification()` cannot override the Sprint 03 `quarantined`/`spam_score`/
   `trust_level` gate, so a mail cannot un-quarantine itself.
5. **Quarantine respect (Sprint 04 path).** `get_unclassified_email_ids` filters
   `COALESCE(quarantined,0)=0`; worker re-checks defensively; re-export only for the classified
   (non-quarantined) message.
6. **Network/privacy.** `OllamaClient` posts only to `OLLAMA_HOST` (default localhost); no email body
   logged in the classify package.

## Findings
- **[Low, in-scope]** `classifier.py` parse-failure logging (`logger.warning(..., e)`) can embed a
  fragment of raw model output (untrusted, email-derived) into local logs. Fix: log
  `type(e).__name__` / truncated repr. → addressed in Sprint 04 finalize round.
- **[Info → elevated by orchestrator]** `ObsidianVaultExporter.export_all` and `get_email_impl` do
  not filter quarantine. `export_all` selects all ids with no `quarantined=0` guard → `export-vault`
  would write quarantined spam/phishing into the vault, contradicting the committed quarantine
  semantics ("store + hide, incl. from the vault"). `search_emails` correctly hides them. →
  `export_all`/auto-export gated in Sprint 04 finalize round; direct `get_email(id)` access left as
  explicit-access-by-design.

## Dependency audit
Only new direct dep `httpx>=0.27.0` (already transitive via `mcp`/`fastmcp`); installed 0.28.1, no
known advisories. `pip-audit` not installed; no lockfile.
