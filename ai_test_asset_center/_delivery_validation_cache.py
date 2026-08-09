"""Thread-safe bounded content-addressed caches for the delivery validation path.

The delivery phase re-validates the same sealed receipts and findings many
times within one run: ``formal_customer_deliverable_findings`` is invoked
several times per run with the same ledger and the same occurrence findings,
and each invocation re-validates every occurrence's gate receipt, which
re-runs ``finding_payload_fingerprint`` -> ``redact_artifact`` (deep copy +
regex scan) over the whole finding payload.

All validation functions here are deterministic pure functions of their
inputs, so caching their successful results keyed by content fingerprints is
semantically identical to recomputation:

* a content change changes the fingerprint and therefore the key, forcing
  recomputation ("内容变则失效" - correctness is never weakened);
* only successful results are cached; validation failures re-raise on every
  call, so no fail-closed gate is relaxed;
* caches are bounded LRUs so memory stays flat across runs.

``clear_delivery_validation_caches`` clears every cache (run boundary / tests).
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any, Hashable


class _Missing:
    """Sentinel distinguishing 'not cached' from a cached None."""

    __slots__ = ()


_MISSING = _Missing()


class BoundedContentCache:
    """Thread-safe LRU cache keyed by hashable content addresses."""

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._data: OrderedDict[Hashable, Any] = OrderedDict()

    def get(self, key: Hashable) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return _MISSING

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# ── Shared cache instances (content-addressed, bounded) ──────────────────────

#: ``finding_payload_fingerprint`` result keyed by the raw finding's content
#: fingerprint (skips the full redact_artifact deep copy + regex pass on repeat).
FINDING_FINGERPRINT_CACHE = BoundedContentCache(maxsize=4096)

#: ``validate_customer_delivery_gate_receipt_v2`` result keyed by
#: (finding_id, finding payload fingerprint, gate receipt content fingerprint).
GATE_VALIDATION_CACHE = BoundedContentCache(maxsize=4096)

#: ``validate_customer_delivery_gate_bundle`` result keyed by the content
#: fingerprints of all bundle inputs (skips the full gate rebuild on repeat).
GATE_BUNDLE_VALIDATION_CACHE = BoundedContentCache(maxsize=2048)

#: ``validate_obligation_attempt_ledger`` result keyed by the whole ledger's
#: content fingerprint (skips per-attempt and per-occurrence revalidation).
LEDGER_VALIDATION_CACHE = BoundedContentCache(maxsize=8)

#: ``validated_deliverable_gate_index`` result keyed by the ledger's content
#: fingerprint (skips the full ledger validation + gate index rebuild).
GATE_INDEX_CACHE = BoundedContentCache(maxsize=8)


def content_fingerprint(value: Any) -> str:
    """Deterministic content address for any JSON-serializable value.

    Insertion-order JSON keeps this fast for the large occurrence payloads
    redacted and hashed during delivery validation; identical content always
    yields the same fingerprint, and any content change changes it, which is
    exactly the correctness contract the delivery caches rely on.
    """
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def clear_delivery_validation_caches() -> None:
    """Clear every delivery validation cache (run boundary or tests)."""
    for cache in (
        FINDING_FINGERPRINT_CACHE,
        GATE_VALIDATION_CACHE,
        GATE_BUNDLE_VALIDATION_CACHE,
        LEDGER_VALIDATION_CACHE,
        GATE_INDEX_CACHE,
    ):
        cache.clear()
