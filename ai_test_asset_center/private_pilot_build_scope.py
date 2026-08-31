"""Build-scoped artifact parse reuse for command-center assembly.

One command-center build used to re-read and re-parse its heaviest inputs
(``scan_result.json`` hydration, knowledge-asset JSON) several times through
different loader helpers on the same request thread. This module gives one
explicit scope per build:

* Within an active scope each artifact identity is read and parsed AT MOST
  ONCE; every consumer shares the same parsed object.
* The cache key is ``(namespace, resolved_path, mtime_ns, size)`` so a file
  rewritten mid-build is a different identity — there is no staleness window
  even when scans write concurrently.
* Namespaces separate loader semantics: the hydrated scan-report view and the
  raw JSON-object view of the same file are different objects by contract and
  must never alias.
* The scope is thread-local and bounded by the ``with`` block; outside an
  active scope every read behaves exactly as before (no caching at all).
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

_NAMESPACE_JSON = "json"
_NAMESPACE_SCAN_REPORT = "scan_report"

_STATE = threading.local()


def _active_cache() -> dict[tuple[str, str, int, int], Any] | None:
    cache = getattr(_STATE, "cache", None)
    return cache if isinstance(cache, dict) else None


@contextmanager
def build_scope() -> Iterator[None]:
    """Scope one command-center build: identical artifact reads parse once."""
    previous = getattr(_STATE, "cache", None)
    _STATE.cache = {}
    try:
        yield
    finally:
        _STATE.cache = previous


def scoped_read_json(
    path: Path,
    loader: Callable[[], Any],
    *,
    namespace: str = _NAMESPACE_JSON,
) -> Any:
    """Return ``loader()`` for ``path``, memoized within the active build scope.

    Without an active scope this simply calls ``loader()`` every time, which
    keeps every non-command-center caller byte-for-byte on the legacy path.
    """
    cache = _active_cache()
    if cache is None:
        return loader()
    try:
        stat = path.stat()
        key = (namespace, str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return loader()
    if key in cache:
        return cache[key]
    payload = loader()
    cache[key] = payload
    return payload


def scoped_scan_report(path: Path, loader: Callable[[], Any]) -> Any:
    """Scoped read in the hydrated scan-report namespace."""
    return scoped_read_json(path, loader, namespace=_NAMESPACE_SCAN_REPORT)
