"""Shared utility functions used across the discovery / verification pipeline.

Import from here instead of copying ``_now()``, ``_hash()``, and ``_redact()``
into every module.  Since existing files use slightly different signatures
(e.g. ``_hash(value, length=24)`` vs ``_hash(value, length=12)`` positional,
``_redact(value, limit=6000)`` vs ``_redact(value, max_len=200)`` keyword-only),
migration must be done file-by-file, verifying each call site.

**Migration checklist per file:**
1. Check that ``_now()`` is identical to this module's version
2. For ``_hash()``, update callers to use ``_hash(value, length=N)`` keyword
3. For ``_redact()``, update callers to use ``_redact(value, max_len=N)`` keyword
4. Remove the local definitions after confirming all callers updated
5. Run ``python -m pytest tests/`` before committing
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _now() -> str:
    """UTC timestamp string for evidence records and audit entries."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(text: str, *, length: int = 12) -> str:
    """Short, stable hash for deduplication and fingerprinting."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:length]


def _redact(value: Any, *, max_len: int = 200) -> str:
    """Redact a value to a safe display string, truncating if needed."""
    text = str(value or "")
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text
