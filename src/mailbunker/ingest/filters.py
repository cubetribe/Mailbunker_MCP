"""
Hard-coded, deterministic filter layers (Sprint 03 -- Filter-Schichten 0-2).

Stufe 0 (provider evidence) and Stufe 1 (heuristics) live here; Stufe 2 (HTML sanitization,
`hidden_text` extraction) already ran inside `imap/parser.py::parse_email_message` before this
module ever sees the `EmailMessage`, so `evaluate()` only needs to *score* its output
(`email_msg.hidden_text`), not produce it.

No LLM, no rspamd (explicit Sprint 03 non-goals -- see plans/v0.2.0/PLAN.md). Everything here is
plain, auditable, hand-written rules over provider-supplied signals (IMAP flags,
`Authentication-Results`, `X-Spam-*`) and structural heuristics (domain mismatch, punycode,
homoglyph mixing, attachment denylist, list/bulk headers).

Conservative policy (plans/v0.2.0/sprint-03-filter-layers.md "Risks"): `block` requires an
explicit hard-evidence combination on top of crossing `SPAM_BLOCK_THRESHOLD` -- score alone,
however high, only ever reaches `quarantine`. False positives must never delete mail.

`evaluate(email_msg)` is the single entry point called from `ingest/pipeline.py::filter_hook`. It
mutates `email_msg.trust_level` and `email_msg.tags` (newsletter label) in place and returns an
`IngestDecision` (decision/reason/score); the caller (`ingest_raw`) applies `spam_score` and the
`quarantined` flag.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

import idna
import tldextract

from ..config import (
    ATTACHMENT_TYPE_DENYLIST,
    MAX_ATTACHMENT_SIZE_BYTES,
    MAX_MESSAGE_SIZE_BYTES,
    SPAM_BLOCK_THRESHOLD,
    SPAM_QUARANTINE_THRESHOLD,
)
from ..storage.models import EmailMessage
from .pipeline import DECISION_BLOCK, DECISION_QUARANTINE, DECISION_STORE, IngestDecision

# Offline-configured tldextract instance: `suffix_list_urls=()` disables the remote Public Suffix
# List fetch, `cache_dir=None` disables the on-disk cache lookup/refresh path entirely, so domain
# parsing relies solely on the snapshot bundled inside the installed `tldextract` package. This is
# a Zero-Trust tool -- it must never make a network request while classifying untrusted mail.
_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Trust/provider-evidence header names as captured into `EmailMessage.raw_headers` by
# `imap/parser.py::TRUST_HEADER_WHITELIST` (Sprint 02) plus the X-Spam-*/X-ICL-Score prefix
# capture. Kept as constants here (not re-declared in parser.py) since this is where they are
# consumed.
_H_AUTH_RESULTS = "Authentication-Results"
_H_REPLY_TO = "Reply-To"
_H_LIST_ID = "List-Id"
_H_LIST_UNSUBSCRIBE = "List-Unsubscribe"
_H_PRECEDENCE = "Precedence"
_H_X_SPAM_FLAG = "X-Spam-Flag"
_H_X_SPAM_SCORE = "X-Spam-Score"
_H_X_ICL_SCORE = "X-ICL-Score"

_AUTH_RESULT_RE = re.compile(r"\b(dmarc|spf|dkim)\s*=\s*([a-zA-Z]+)", re.I)
_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")

# --- Stufe 0: provider evidence -----------------------------------------------------------------

_SCORE_JUNK_FLAG = 4.0
_SCORE_DMARC_FAIL = 3.0
_SCORE_SPF_FAIL = 1.5
_SCORE_DKIM_FAIL = 1.0
_SCORE_X_SPAM_FLAG_YES = 3.0
_SCORE_X_SPAM_NUMERIC_HIGH = 2.0
_X_SPAM_NUMERIC_HIGH_THRESHOLD = 5.0

# --- Stufe 1: heuristics --------------------------------------------------------------------

_SCORE_REPLY_TO_MISMATCH = 1.5
_SCORE_BRAND_MISMATCH = 3.0
_SCORE_PUNYCODE_FLAG = 1.0
_SCORE_HOMOGLYPH_FLAG = 1.5
_SCORE_ATTACHMENT_DENYLIST = 4.0
_SCORE_ATTACHMENT_DOUBLE_EXT = 1.0
_SCORE_OVERSIZED_MESSAGE = 1.5
_SCORE_OVERSIZED_ATTACHMENT = 2.0

# Small, conservative set of brands worth checking display-name-vs-domain mismatch for. Keys are
# matched as a whole word (case-insensitive) against the decoded From display name; values are the
# set of registered domains considered legitimate for that brand. Intentionally short: a longer
# list risks false positives on legitimate senders whose company name happens to contain a
# common word, which this sprint's policy explicitly wants to avoid.
KNOWN_BRANDS: Dict[str, Set[str]] = {
    "paypal": {"paypal.com", "paypal.de"},
    "amazon": {"amazon.com", "amazon.de", "amazon.co.uk"},
    "apple": {"apple.com"},
    "microsoft": {"microsoft.com"},
    "google": {"google.com"},
    "dhl": {"dhl.com", "dhl.de"},
    "sparkasse": {"sparkasse.de"},
    "postbank": {"postbank.de"},
    "ebay": {"ebay.com", "ebay.de"},
    "netflix": {"netflix.com"},
    "telekom": {"telekom.de"},
    "volksbank": {"volksbank.de"},
}
_BRAND_RES = {brand: re.compile(rf"\b{re.escape(brand)}\b", re.I) for brand in KNOWN_BRANDS}

# Newsletter/bulk-mail evidence headers -- these are a LABEL, never a score contribution (a
# legitimate newsletter must never be pushed towards quarantine just for identifying itself).
_NEWSLETTER_TAG = "newsletter"


def _flags_normalized(flags: List[str]) -> Set[str]:
    return {f.lstrip("\\$").lower() for f in flags}


def _parse_auth_results(header_val: str) -> Dict[str, str]:
    """Parse an `Authentication-Results` header value into `{"dmarc": "fail", "spf": "pass", ...}`.

    Only the first `key=value` occurrence of each mechanism is kept (a header can list a
    mechanism more than once across relay hops; the first is the outermost/most relevant one for
    our single-header-capture model from Sprint 02).
    """
    results: Dict[str, str] = {}
    for match in _AUTH_RESULT_RE.finditer(header_val or ""):
        key = match.group(1).lower()
        if key not in results:
            results[key] = match.group(2).lower()
    return results


def _extract_numeric_score(header_val: Optional[str]) -> Optional[float]:
    """Extract the first float found in a spam-score header (e.g. `"5.2 / 10.0"` -> 5.2)."""
    if not header_val:
        return None
    match = _NUMERIC_RE.search(header_val)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _registered_domain(email_addr: str) -> Optional[str]:
    """Return the registered (eTLD+1) domain of an email address, offline, or None if unparsable."""
    if not email_addr or "@" not in email_addr:
        return None
    host = email_addr.rsplit("@", 1)[-1].strip().lower()
    if not host:
        return None
    try:
        extracted = _TLD_EXTRACTOR(host)
    except Exception:
        return None
    domain = extracted.top_domain_under_public_suffix if hasattr(extracted, "top_domain_under_public_suffix") else ""
    return domain or (host if "." in host else None)


def _is_punycode_domain(domain: Optional[str]) -> bool:
    if not domain:
        return False
    return any(label.startswith("xn--") for label in domain.split("."))


def _decode_punycode(domain: str) -> Optional[str]:
    """Decode an `xn--`-prefixed domain to its Unicode form via `idna`, or None on failure."""
    try:
        return idna.decode(domain)
    except Exception:
        # `idna.decode` is strict about IDNA2008 label rules; some real-world (older IDNA2003 /
        # malformed) punycode labels fail it. That's fine -- it just means we can't produce the
        # decoded form for further inspection here; the punycode-presence flag itself already
        # fired independently of this.
        return None


_SCRIPT_RANGES: Tuple[Tuple[str, int, int], ...] = (
    ("Latin", 0x0041, 0x024F),
    ("Cyrillic", 0x0400, 0x04FF),
    ("Greek", 0x0370, 0x03FF),
)


def _char_script(ch: str) -> Optional[str]:
    cp = ord(ch)
    for name, start, end in _SCRIPT_RANGES:
        if start <= cp <= end:
            return name
    return None


def _has_mixed_scripts(text: str) -> bool:
    """Detect Latin/Cyrillic/Greek script-mixing within a single string (classic homoglyph attack,
    e.g. Cyrillic 'а' substituted for Latin 'a' in "paypal.com"). Digits, punctuation, and any
    character outside these three scripts are ignored -- this only flags mixing *between* them.
    """
    scripts_seen = {s for s in (_char_script(ch) for ch in text) if s is not None}
    return len(scripts_seen) > 1


def _get_extensions(filename: str) -> List[str]:
    """Return every dot-separated suffix of a filename, lowercased with a leading dot, e.g.
    "invoice.pdf.exe" -> [".pdf", ".exe"]."""
    parts = filename.lower().strip().split(".")
    if len(parts) < 2:
        return []
    return [f".{p}" for p in parts[1:]]


def _score_provider_evidence(email_msg: EmailMessage) -> Tuple[float, List[str], Dict[str, bool]]:
    """Stufe 0: IMAP `$Junk`/`Junk` flag, `Authentication-Results` (SPF/DKIM/DMARC), `X-Spam-*`."""
    score = 0.0
    reasons: List[str] = []
    headers = email_msg.raw_headers or {}

    flags_norm = _flags_normalized(email_msg.flags)
    is_junk_flag = "junk" in flags_norm and "notjunk" not in flags_norm

    if is_junk_flag:
        score += _SCORE_JUNK_FLAG
        reasons.append("imap-junk-flag")

    auth_results = _parse_auth_results(headers.get(_H_AUTH_RESULTS, ""))
    dmarc_fail = auth_results.get("dmarc") == "fail"
    spf_fail = auth_results.get("spf") == "fail"
    dkim_fail = auth_results.get("dkim") == "fail"
    dmarc_pass = auth_results.get("dmarc") == "pass"
    spf_pass = auth_results.get("spf") == "pass"
    dkim_pass = auth_results.get("dkim") == "pass"

    if dmarc_fail:
        score += _SCORE_DMARC_FAIL
        reasons.append("dmarc-fail")
    if spf_fail:
        score += _SCORE_SPF_FAIL
        reasons.append("spf-fail")
    if dkim_fail:
        score += _SCORE_DKIM_FAIL
        reasons.append("dkim-fail")

    if headers.get(_H_X_SPAM_FLAG, "").strip().upper() == "YES":
        score += _SCORE_X_SPAM_FLAG_YES
        reasons.append("x-spam-flag-yes")

    numeric_score = _extract_numeric_score(headers.get(_H_X_SPAM_SCORE) or headers.get(_H_X_ICL_SCORE))
    if numeric_score is not None and numeric_score >= _X_SPAM_NUMERIC_HIGH_THRESHOLD:
        score += _SCORE_X_SPAM_NUMERIC_HIGH
        reasons.append(f"x-spam-score-high:{numeric_score}")

    signals = {
        "is_junk_flag": is_junk_flag,
        "dmarc_fail": dmarc_fail,
        "spf_fail": spf_fail,
        "dkim_fail": dkim_fail,
        "dmarc_pass": dmarc_pass,
        "spf_pass": spf_pass,
        "dkim_pass": dkim_pass,
        "any_auth_fail": dmarc_fail or spf_fail or dkim_fail,
        "full_auth_pass": dmarc_pass and spf_pass and dkim_pass,
    }
    return score, reasons, signals


def _score_heuristics(email_msg: EmailMessage) -> Tuple[float, List[str], Dict[str, bool]]:
    """Stufe 1: from/reply-to mismatch, brand-display-name mismatch, punycode/homoglyph,
    attachment denylist, newsletter labeling (no score)."""
    score = 0.0
    reasons: List[str] = []
    headers = email_msg.raw_headers or {}

    sender_email = email_msg.sender.email if email_msg.sender else ""
    sender_domain = _registered_domain(sender_email)

    # From vs Reply-To domain mismatch.
    reply_to_header = headers.get(_H_REPLY_TO, "")
    reply_to_email = None
    if reply_to_header:
        match = re.search(r"[\w.+\-]+@[\w\-.]+", reply_to_header)
        if match:
            reply_to_email = match.group(0)
    reply_to_domain = _registered_domain(reply_to_email) if reply_to_email else None
    reply_to_mismatch = bool(reply_to_domain and sender_domain and reply_to_domain != sender_domain)
    if reply_to_mismatch:
        score += _SCORE_REPLY_TO_MISMATCH
        reasons.append(f"reply-to-domain-mismatch:{sender_domain}!={reply_to_domain}")

    # Brand display-name mismatch (phishing signature: "PayPal Support" from a non-PayPal domain).
    display_name = (email_msg.sender.name or "") if email_msg.sender else ""
    brand_mismatch = False
    for brand, expected_domains in KNOWN_BRANDS.items():
        if _BRAND_RES[brand].search(display_name):
            if sender_domain not in expected_domains:
                score += _SCORE_BRAND_MISMATCH
                reasons.append(f"brand-display-name-mismatch:{brand}:{sender_domain}")
                brand_mismatch = True
            break  # only the first matching brand is scored -- avoids double counting

    # Punycode / homoglyph mixing on the From domain. FLAG ONLY per sprint policy -- legitimate
    # internationalized domains exist and must never be hard-blocked for this alone.
    is_punycode = _is_punycode_domain(sender_domain)
    is_homoglyph = False
    if is_punycode:
        score += _SCORE_PUNYCODE_FLAG
        reasons.append(f"punycode-domain:{sender_domain}")
        decoded = _decode_punycode(sender_domain) if sender_domain else None
        if decoded and _has_mixed_scripts(decoded):
            is_homoglyph = True
            score += _SCORE_HOMOGLYPH_FLAG
            reasons.append(f"homoglyph-mixed-script:{decoded}")
    elif _has_mixed_scripts(display_name):
        # Mixed-script display name (independent of domain punycode) is a softer homoglyph signal
        # (e.g. a Cyrillic lookalike brand name with an ASCII domain).
        is_homoglyph = True
        score += _SCORE_HOMOGLYPH_FLAG
        reasons.append("homoglyph-mixed-script:display-name")

    # Attachment denylist (executable/dangerous extensions, including double-extension spoofing).
    attachment_denylist_hit = False
    for meta in email_msg.attachments:
        extensions = _get_extensions(meta.filename)
        if not extensions:
            continue
        last_ext = extensions[-1]
        if last_ext in ATTACHMENT_TYPE_DENYLIST:
            attachment_denylist_hit = True
            score += _SCORE_ATTACHMENT_DENYLIST
            reasons.append(f"attachment-denylist:{meta.filename}")
            if len(extensions) >= 2:
                score += _SCORE_ATTACHMENT_DOUBLE_EXT
                reasons.append(f"attachment-double-extension:{meta.filename}")
        if meta.size and meta.size > MAX_ATTACHMENT_SIZE_BYTES:
            score += _SCORE_OVERSIZED_ATTACHMENT
            reasons.append(f"oversized-attachment:{meta.filename}")

    # Oversized message (resource/anomaly signal, not inherently malicious -- mild score only).
    if email_msg.size and email_msg.size > MAX_MESSAGE_SIZE_BYTES:
        score += _SCORE_OVERSIZED_MESSAGE
        reasons.append("oversized-message")

    # Newsletter / bulk-mail labeling -- LABEL ONLY, never scored as spam.
    is_newsletter = bool(
        headers.get(_H_LIST_ID)
        or headers.get(_H_LIST_UNSUBSCRIBE)
        or headers.get(_H_PRECEDENCE, "").strip().lower() == "bulk"
    )
    if is_newsletter and _NEWSLETTER_TAG not in email_msg.tags:
        email_msg.tags.append(_NEWSLETTER_TAG)

    signals = {
        "reply_to_mismatch": reply_to_mismatch,
        "brand_mismatch": brand_mismatch,
        "is_punycode": is_punycode,
        "is_homoglyph": is_homoglyph,
        "attachment_denylist_hit": attachment_denylist_hit,
        "is_newsletter": is_newsletter,
    }
    return score, reasons, signals


def _score_hidden_content(email_msg: EmailMessage) -> Tuple[float, List[str]]:
    """Stufe 2 output scoring: hidden HTML content extracted by `imap/parser.py` is an
    injection/spam signal, never a body-content signal (it is already excluded from body_text)."""
    if email_msg.hidden_text:
        return 2.5, ["hidden-html-content-detected"]
    return 0.0, []


def _derive_trust_level(
    total_score: float,
    hard_evidence: bool,
    provider_signals: Dict[str, bool],
) -> str:
    """
    Derive `trust_level` from message-level signals only (no sender/contact reputation history --
    that table is an explicit v0.2.0 non-goal per PLAN.md; Sprint 04's classifier operates with
    more context and may refine this further).

    - `malicious`: hard block-evidence combination present.
    - `suspicious`: score reached the quarantine threshold without hard evidence.
    - `verified`: full SPF+DKIM+DMARC alignment and no negative score at all.
    - `known`: DMARC (or SPF/DKIM) passes with no negative signals, but not full triple-alignment.
    - `unknown`: default -- no strong evidence either way (e.g. no auth headers present).
    """
    if hard_evidence:
        return "malicious"
    if total_score >= SPAM_QUARANTINE_THRESHOLD:
        return "suspicious"
    if provider_signals.get("full_auth_pass") and total_score <= 0:
        return "verified"
    if (provider_signals.get("dmarc_pass") or provider_signals.get("spf_pass") or provider_signals.get("dkim_pass")) and total_score <= 0:
        return "known"
    return "unknown"


def evaluate(email_msg: EmailMessage) -> IngestDecision:
    """
    Run Stufe 0-2 scoring against a parsed `EmailMessage` and return the ingest decision.

    Mutates `email_msg.trust_level` and `email_msg.tags` (newsletter label) in place. Does NOT set
    `email_msg.spam_score`/`email_msg.quarantined` -- that remains `ingest/pipeline.py::ingest_raw`'s
    responsibility so a single place applies the decision consistently for every outcome
    (store/quarantine/block), not just quarantine.
    """
    provider_score, provider_reasons, provider_signals = _score_provider_evidence(email_msg)
    heuristic_score, heuristic_reasons, heuristic_signals = _score_heuristics(email_msg)
    hidden_score, hidden_reasons = _score_hidden_content(email_msg)

    total_score = provider_score + heuristic_score + hidden_score
    reasons = provider_reasons + heuristic_reasons + hidden_reasons

    is_junk_flag = provider_signals["is_junk_flag"]
    dmarc_fail = provider_signals["dmarc_fail"]
    any_auth_fail = provider_signals["any_auth_fail"]
    attachment_denylist_hit = heuristic_signals["attachment_denylist_hit"]

    # Conservative hard-evidence combinations -- required IN ADDITION to crossing
    # SPAM_BLOCK_THRESHOLD before a `block` decision is ever made. Score alone never blocks.
    hard_evidence = (is_junk_flag and dmarc_fail) or (
        is_junk_flag and attachment_denylist_hit and any_auth_fail
    )

    email_msg.trust_level = _derive_trust_level(total_score, hard_evidence, provider_signals)

    if total_score >= SPAM_BLOCK_THRESHOLD and hard_evidence:
        decision = DECISION_BLOCK
    elif total_score >= SPAM_QUARANTINE_THRESHOLD:
        decision = DECISION_QUARANTINE
    else:
        decision = DECISION_STORE

    reason = ",".join(reasons) if reasons else "no-signals"
    return IngestDecision(decision=decision, reason=reason, score=total_score)
