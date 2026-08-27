"""Async local Ollama classifier (Sprint 04 -- Stufe 4).

Never called from the IMAP IDLE push path. Entry points are the standalone
`mailbunker classify [--backfill]` CLI command (`cli.py`) and the worker it drives
(`classify/worker.py`). Ollama is an optional runtime dependency: every piece of this package
degrades gracefully (no crash, no silent data loss) when it is unreachable or the configured
model is missing.
"""
