"""Derive open-class formal contracts from runtime probe observations.

Four-link breadth closure, 档位 D (runtime-probe contract derivation).

The four-link chain (obligation risk family -> assertion -> observer ->
experiment protocol) is fully installed for ``performance_latency``,
``stability_reliability``, ``event_delivery_consistency`` and
``ui_state_consistency`` — but ``contract_auto_derivation`` (档位 C) only
populates the formal-contract asset keys (``performance_formal_contracts``,
``stability_formal_contracts``, ...) from *source text*.  For an unfamiliar
system with no PRD / API spec, that pass finds nothing, so those open bug
classes are structurally unreachable.

This module closes the gap from the *runtime* side: the governed surface
probe already issues real HTTP requests and (after the P2 instrumentation in
``runtime_interface_discovery``) records per-sample status + latency.  We turn
those observations into the SAME normalized contract rows the existing binders
(``bind_source_performance_contracts`` / ``bind_source_stability_contracts``)
already consume — reusing ``contract_auto_derivation``'s row builders, so the
downstream obligation compiler and observer chain is reached with zero new
wheels.

Discipline (identical in spirit to 档位 C, and to AGENTS.md 原则 6/7/14):
- Derivation is extraction + relative-invariant construction, never absolute
  SLA invention.  No industry latency/error threshold is ever assumed.
- Performance: a *relative* latency bound is derived from the observed sample
  series (p95 * multiplier).  A single slow response is NOT a defect (the
  formal_performance_surface observer already rejects retried attempts and
  treats one slow sample as non-defect); we require >= 3 clean samples.
- Stability: a contract is emitted ONLY when a non-2xx (>=500, harness fail,
  or 2xx-vs-4xx non-determinism) was actually OBSERVED on a read-only
  idempotent endpoint.  Auth-gated 401/403 are not reliability defects.
- Every emitted contract carries ``source_refs`` marking
  ``runtime_probe_observation`` (never a fabricated source quote) and a
  ``methodology_default`` multiplier.  Anything that cannot be bound exactly
  is skipped with a receipt entry — never guessed, never silently dropped.
- Fail-closed and observable: the pass always emits a receipt; it can be
  disabled by operator policy or ``QUALIBUG_DISABLE_RUNTIME_PROBE_CONTRACT_DERIVATION=1``.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

# Reuse the exact row builders and identity helpers already used by
# contract_auto_derivation (档位 C) so the contract shape stays byte-for-byte
# compatible with the binders.  No duplicate contract-row logic.
from .contract_auto_derivation import (
    _actor_role,
    _dict,
    _digest,
    _existing_operation_keys,
    _list,
    _operation_match,
    _performance_row,
    _stability_row,
    _text,
)

DERIVATION_SCHEMA = "qualibug.runtime-probe-contract-derivation.v1"

# ── Methodology defaults (product-owned measurement config, NOT business facts) ──
# Documented here and repeated in every receipt so runtime-derived contracts are
# never mistaken for source-declared SLAs.
_RUNTIME_PERF_MIN_SAMPLES = 3          # need a real baseline before asserting latency
_RUNTIME_PERF_MULTIPLIER = 2.0         # relative bound: observed p95 * this
# Must be >= 5 to match formal_stability_surface's sample_count minimum
# ([5, 20]); below that the surface rejects the contract and stability_reliability
# stays unreachable.  Aligned with _RUNTIME_PROBE_SAMPLE_COUNT so a full probe
# window always yields a surface-valid stability contract when a defect is seen.
_RUNTIME_STABILITY_MIN_SAMPLES = 5     # need repetition before claiming flakiness

_SAFE_METHODS = frozenset({"GET", "HEAD"})


def _pct(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = pct * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _probe_observations_key() -> str:
    return "runtime_probe_observations"


def probe_observations_from_receipts(
    observation_receipts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Convert runtime-interface observation receipts into probe_observations.

    The receipt (after P2 instrumentation) may carry a ``samples`` list of
    ``{status_code, duration_ms, attempts}``.  Receipts without samples degrade
    to a single sample built from ``status_code`` / ``primary_duration_ms`` so
    the producer still behaves on legacy receipts (no samples -> no latency
    contract, only stability if a non-2xx was observed).
    """
    out: list[dict[str, Any]] = []
    for raw in _list(observation_receipts):
        receipt = _dict(raw)
        if receipt.get("schema_version") != "qualibug.runtime-interface-observation.v1":
            continue
        path = _text(receipt.get("path"))
        if not path.startswith("/"):
            continue
        method = _text(receipt.get("method")).upper() or "GET"
        samples = _list(receipt.get("samples"))
        if not samples:
            status_code = int(receipt.get("status_code") or -1)
            duration_ms = receipt.get("primary_duration_ms")
            samples = [{"status_code": status_code, "duration_ms": duration_ms, "attempts": 1}]
        out.append({"method": method, "path": path, "samples": samples})
    return out


def _clean_latency_samples(samples: list[dict[str, Any]]) -> list[int]:
    lat: list[int] = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        dur = s.get("duration_ms")
        status = int(s.get("status_code") or 0)
        attempts = int(s.get("attempts") or 1)
        # Reject retried attempts: a retry's duration includes client backoff
        # and is NOT a target response time (see sandbox_write_executor_base).
        if dur is None or attempts != 1:
            continue
        if 200 <= status < 300:
            try:
                lat.append(int(dur))
            except (TypeError, ValueError):
                continue
    return lat


def _stability_signal(samples: list[dict[str, Any]]) -> tuple[bool, list[int]]:
    """Return (has_defect, defect_statuses) for a read-only idempotent endpoint."""
    statuses: list[int] = []
    for s in samples:
        if isinstance(s, dict):
            try:
                statuses.append(int(s.get("status_code") or -1))
            except (TypeError, ValueError):
                continue
    if len(statuses) < _RUNTIME_STABILITY_MIN_SAMPLES:
        return False, []
    clean_2xx = [c for c in statuses if 200 <= c < 300]
    server_errors = [c for c in statuses if c >= 500]
    client_nonauth = [c for c in statuses if 400 <= c < 500 and c not in {401, 403, 404}]
    auth_gated = bool(statuses) and all(c in {401, 403} for c in statuses)
    inconsistent = bool(clean_2xx) and bool(server_errors or client_nonauth)
    has_defect = (not auth_gated) and bool(server_errors or inconsistent)
    return has_defect, [c for c in statuses if c >= 500 or (400 <= c < 500 and c not in {401, 403, 404})]


def derive_runtime_probe_contracts(
    asset: dict[str, Any] | None,
    *,
    operations: list[dict[str, Any]] | None = None,
    runtime_observations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive performance/stability contracts from runtime probe observations.

    Returns ``(asset, receipt)``.  The asset is returned unchanged when disabled
    or when no observations are supplied; the receipt always records what was
    attempted, derived, and skipped (with reason codes).
    """
    receipt: dict[str, Any] = {
        "schema_version": DERIVATION_SCHEMA,
        "enabled": True,
        "derived": {"performance": 0, "stability": 0},
        "skipped": [],
        "methodology_defaults": {
            "performance": {
                "min_samples": _RUNTIME_PERF_MIN_SAMPLES,
                "relative_multiplier": _RUNTIME_PERF_MULTIPLIER,
                "note": "max_latency_ms = observed p95 * multiplier; relative bound, not an SLA",
            },
            "stability": {
                "min_samples": _RUNTIME_STABILITY_MIN_SAMPLES,
                "note": "emitted only when a non-2xx was OBSERVED on a read-only idempotent endpoint; 401/403 are not reliability defects",
            },
        },
    }
    merged = dict(_dict(asset))
    if not runtime_observations:
        receipt.update({"status": "NOT_REQUESTED", "reason": "no_runtime_observations"})
        return merged, receipt

    if enabled is None:
        enabled = True
    if str(os.environ.get("QUALIBUG_DISABLE_RUNTIME_PROBE_CONTRACT_DERIVATION", "")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        enabled = False
    receipt["enabled"] = enabled
    if not enabled:
        receipt.update({"status": "DISABLED", "reason": "operator_policy_or_env"})
        return merged, receipt

    ops = _list(operations)
    actors = _list(runtime_actors)
    existing_perf = _existing_operation_keys(merged, "performance_formal_contracts")
    existing_stab = _existing_operation_keys(merged, "stability_formal_contracts")
    perf_rows: list[dict[str, Any]] = []
    stab_rows: list[dict[str, Any]] = []

    for obs in _list(runtime_observations):
        if not isinstance(obs, dict):
            continue
        method = _text(obs.get("method")).upper()
        path = _text(obs.get("path"))
        if method not in _SAFE_METHODS:
            receipt["skipped"].append({
                "kind": "open_class",
                "operation": f"{method}:{path}",
                "reason": "non_get_head_probe_sample",
            })
            continue
        if not path.startswith("/"):
            receipt["skipped"].append({
                "kind": "open_class",
                "operation": f"{method}:{path}",
                "reason": "invalid_probe_path",
            })
            continue
        samples = _list(obs.get("samples"))
        if not samples:
            receipt["skipped"].append({
                "kind": "open_class",
                "operation": f"{method}:{path}",
                "reason": "no_probe_samples",
            })
            continue
        key = (method, path)

        # ── Stability: only when a defect was actually observed ──
        has_defect, defect_statuses = _stability_signal(samples)
        if has_defect and key not in existing_stab:
            operation = _operation_match(ops, method, path)
            if operation is None:
                receipt["skipped"].append({
                    "kind": "stability",
                    "operation": f"{method}:{path}",
                    "reason": "operation_not_found",
                })
            else:
                actor_role = _actor_role(operation, actors)
                row = _stability_row(
                    operation,
                    {"error_rate_pct": 0.0, "quote": ""},
                    source_id="runtime_probe",
                    actor_role=actor_role,
                )
                row.update({
                    "contract_id": "rtprobe_stab_" + _digest(method, path, "runtime_probe", "stability", len(samples)),
                    "origin": "runtime_probe_contract_derivation",
                    "derivation": "observed_runtime_probe",
                    "sample_count": len(samples),
                    "max_failed_samples": 0,
                    "expected_status_class": 2,
                    "confidence": 0.7,
                    "source_refs": [{
                        "source_id": "runtime_probe",
                        "kind": "runtime_probe_observation",
                        "locator": f"{method}:{path}",
                        "quote": "",
                    }],
                    "observed_defect_statuses": defect_statuses,
                })
                stab_rows.append(row)
                existing_stab.add(key)

        # ── Performance: relative p95 bound from observed clean samples ──
        lat = _clean_latency_samples(samples)
        if len(lat) >= _RUNTIME_PERF_MIN_SAMPLES and key not in existing_perf:
            operation = _operation_match(ops, method, path)
            if operation is None:
                receipt["skipped"].append({
                    "kind": "performance",
                    "operation": f"{method}:{path}",
                    "reason": "operation_not_found",
                })
            else:
                actor_role = _actor_role(operation, actors)
                p95 = _pct(sorted(lat), 0.95)
                max_latency_ms = round(p95 * _RUNTIME_PERF_MULTIPLIER, 3)
                row = _performance_row(
                    operation,
                    {"max_latency_ms": max_latency_ms, "percentile": "p95", "quote": ""},
                    source_id="runtime_probe",
                    actor_role=actor_role,
                )
                row.update({
                    "contract_id": "rtprobe_perf_" + _digest(method, path, "runtime_probe", "performance", p95),
                    "origin": "runtime_probe_contract_derivation",
                    "derivation": "observed_runtime_probe",
                    "sample_count": len(lat),
                    "max_latency_ms": max_latency_ms,
                    "percentile": "p95",
                    "confidence": 0.7,
                    "source_refs": [{
                        "source_id": "runtime_probe",
                        "kind": "runtime_probe_observation",
                        "locator": f"{method}:{path}",
                        "quote": "",
                    }],
                    "observed_p95_ms": round(p95, 3),
                })
                perf_rows.append(row)
                existing_perf.add(key)

    if perf_rows:
        merged["performance_formal_contracts"] = [
            *merged.get("performance_formal_contracts", []),
            *perf_rows,
        ]
        receipt["derived"]["performance"] = len(perf_rows)
    if stab_rows:
        merged["stability_formal_contracts"] = [
            *merged.get("stability_formal_contracts", []),
            *stab_rows,
        ]
        receipt["derived"]["stability"] = len(stab_rows)

    receipt["status"] = (
        "CONSUMED"
        if sum(receipt["derived"].values())
        else "NO_CONTRACTS_DERIVED"
    )
    return merged, receipt


__all__ = [
    "derive_runtime_probe_contracts",
    "probe_observations_from_receipts",
    "DERIVATION_SCHEMA",
]
