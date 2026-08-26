"""Concurrent experiment compile wrapper (SPEC task 11).

Run3 timeline: compile ≈ 90 min (1300+ obligations compiled serially at
1-2 s/obligation plus the V1.8-rescue loop) dominates the end-to-end budget
(execution is already concurrent via task 9). This module parallelizes the
compile phase with the same worker-pool pattern as
``experiment_batch_concurrent_scheduler``:

- ``compile_experiments_concurrent`` — per-obligation parallel compile,
  original-order aggregation, per-obligation exception isolation.
- ``materialize_and_recompile_abstract_pack_concurrent`` — parallel rescue
  (abstract → runtime materialization → concrete recompile) with original-order
  merge and per-row isolation.

Concurrency safety model (audited against the whole compile chain):
- **Inputs are read-only**: ``behavior_ir`` is only read by the compile chain
  (the per-obligation ``_scoped_behavior_ir`` shallow-copies it; the only
  product write site, obligation_compiler.py:559, runs during IR building).
  ``available_adapters`` / ``policy_version`` / ``planning_context`` are passed
  read-only. Each obligation dict is touched by exactly one worker (serial also
  mutates per-obligation status counters, so worker-local mutation is the same
  semantics).
- **No module-level counters / caches / RNG / clocks in the compile chain**:
  scanned all compiler modules — the only module-level mutable state is the
  memoization pair ``behavior_ir_core._SEMANTIC_MARKER_CACHE`` /
  ``_SEMANTIC_PATH_SUFFIX_CACHE`` (dict get/set are GIL-atomic; values are
  deterministic functions of the key + a static lexicon file, so a race at most
  duplicates a computation, never corrupts a result). ``process_graph_write_contract``
  copies names into ``globals()`` at import time only.
- **Outputs are independent**: every per-obligation compile returns an
  independent experiment dict; receipts use only deterministic sha256 ids
  (``stable_experiment_id`` / ``_stable_id``), no timestamps/randomness.
- **Ordering is preserved by aggregation**: results are collected as
  ``(index, pack)`` and sorted back to the input order before merging, so
  downstream consumers (``materialize_and_recompile_abstract_pack``,
  ``bind_experiment_pack_to_captured_materializations``) see exactly the serial
  sequence.
- **Failure isolation**: an exception inside one obligation's compile produces
  a HARNESS_FAILED experiment receipt for that obligation (visible in
  ``blocked_experiments`` / ``block_reason_counts`` / ``compile_failures``) and
  never aborts other workers. Serial would have aborted the whole compile;
  concurrent mode degrades per obligation and keeps the run going, matching the
  task-9 isolation pattern.

Integration: ``experiment_compiler.compile_experiments`` (the only product
entry — both mainline call sites in ``discovery_runtime_planning`` and
``adaptive_behavior_ir_expansion`` go through it) swaps its two serial calls
for the concurrent variants below. ``QUALIBUG_COMPILE_CONCURRENCY`` (default 8,
clamp [2, 16]) controls the pool; a value <= 1 forces the exact serial path
(test hook / operator kill-switch).
"""
from __future__ import annotations

import logging
import os
import time
import contextvars
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from . import experiment_compiler_base as _base
from .abstract_experiment import (
    MATERIALIZATION_SCHEMA,
    attach_passthrough_materialization,
    is_capability_gap_reason,
    promote_blocked_to_abstract,
)
from .experiment_compiler_obligation import make_experiment, stable_experiment_id
from .experiment_runtime_materialization import _resolve_planning_materialization

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 8
MIN_CONCURRENCY = 2
MAX_CONCURRENCY = 16
CONCURRENCY_ENV = "QUALIBUG_COMPILE_CONCURRENCY"
HARNESS_FAILURE_REASON = "COMPILE_HARNESS_FAILED"
COMPILE_SCHEMA = "qualibug.experiment-compile.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _submit_with_batch_indexes(
    pool: ThreadPoolExecutor,
    fn,
    /,
    *args,
    fn_kwargs: dict | None = None,
    behavior_ir=None,
):
    """Submit fn under a PRIVATE context snapshot carrying the per-batch
    Behavior-IR index bundle (SPEC-11 4.2).

    Serial batch establishes the bundle once via set_batch_indexes(
    build_batch_indexes(behavior_ir)); without it the concurrent path silently
    lost rescue semantics (measured rescued 42->0, why this wrapper had been
    unwired). Each task snapshots the caller context FRESH, primes it with the
    shared immutable indexes, then runs fn inside that private context -
    tasks stay mutually isolated (module docstring).
    """
    from .compile_batch_context import build_batch_indexes, set_batch_indexes

    indexes = build_batch_indexes(behavior_ir or {})
    kwargs = dict(fn_kwargs or {})

    def _task():
        ctx = contextvars.copy_context()
        ctx.run(set_batch_indexes, indexes)
        return ctx.run(fn, *args, **kwargs)

    return pool.submit(_task)


def get_concurrency() -> int:
    """Pool size from QUALIBUG_COMPILE_CONCURRENCY, clamped to [2, 16].

    Invalid / unset values fall back to the default 8. An explicit value of 1
    forces the exact serial path (used by tests and as an operator kill-switch);
    values 2..16 are honored as-is.
    """
    raw = _text(os.environ.get(CONCURRENCY_ENV))
    if not raw:
        return DEFAULT_CONCURRENCY
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s invalid (%r), using default %d",
            CONCURRENCY_ENV, raw, DEFAULT_CONCURRENCY,
        )
        return DEFAULT_CONCURRENCY
    if value <= 1:
        return 1
    return max(MIN_CONCURRENCY, min(value, MAX_CONCURRENCY))


# ── per-obligation compile ───────────────────────────────────────────────────

def _empty_pack() -> dict[str, Any]:
    return {
        "schema_version": COMPILE_SCHEMA,
        "compiled_count": 0,
        "blocked_count": 0,
        "abstract_count": 0,
        "experiments": [],
        "blocked_experiments": [],
        "abstract_experiments": [],
        "block_reason_counts": {},
    }


def _harness_failed_experiment(
    obligation: dict[str, Any],
    error: Exception,
) -> dict[str, Any]:
    """Receipt-visible HARNESS_FAILED experiment for an obligation whose compile
    raised. Keeps the "every obligation has a visible compile outcome"
    invariant; downstream treats it as blocked (never ABSTRACT)."""
    oid = _text(_dict(obligation).get("obligation_id")) or "unknown_obligation"
    detail = (
        f"compile_concurrent_obligation_failed:"
        f"{type(error).__name__}:{error}"[:400]
    )
    return make_experiment(
        obligation_id=oid,
        compile_receipt={
            "status": "HARNESS_FAILED",
            "reason_code": HARNESS_FAILURE_REASON,
            "detail": detail,
        },
        experiment_id=stable_experiment_id(oid, HARNESS_FAILURE_REASON),
    )


def _compile_one_obligation(
    index: int,
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str,
    policy_version: str,
    compile_one: Any,
    available_adapters: Any,
) -> tuple[int, dict[str, Any], Exception | None]:
    """Compile one obligation with the exact serial per-obligation code path
    (``experiment_compiler_base.compile_experiments`` on a single element), so
    per-obligation results are byte-identical to the serial loop. Worker-local
    data only; the obligation dict is owned by this worker."""
    try:
        pack = _base.compile_experiments(
            [obligation],
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=policy_version,
            compile_one=compile_one,
            available_adapters=available_adapters,
        )
        return index, pack, None
    except Exception as exc:  # noqa: BLE001 - isolation boundary
        logger.exception("compile obligation %d failed", index)
        failure = _empty_pack()
        failure["blocked_experiments"] = [
            _harness_failed_experiment(obligation, exc)
        ]
        failure["blocked_count"] = 1
        failure["block_reason_counts"] = {HARNESS_FAILURE_REASON: 1}
        return index, failure, exc


def _merge_obligation_packs(
    results: list[tuple[int, dict[str, Any], Exception | None]],
    obligations: list[dict[str, Any]],
    concurrency: int,
    elapsed_ms: int,
) -> dict[str, Any]:
    """Merge per-obligation packs in original order (single-threaded). Result
    shape is byte-identical to the serial ``compile_experiments`` pack when no
    obligation raised; ``concurrency`` metadata is additive and
    ``compile_failures`` appears only when isolation actually fired."""
    compiled: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    abstract: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    failures: dict[str, Any] = {}
    for index, pack, error in results:
        pack = _dict(pack)
        compiled.extend(_list(pack.get("experiments")))
        blocked.extend(_list(pack.get("blocked_experiments")))
        abstract.extend(_list(pack.get("abstract_experiments")))
        for code, count in _dict(pack.get("block_reason_counts")).items():
            if isinstance(count, int):
                reason_counts[str(code)] = reason_counts.get(str(code), 0) + count
        if error is not None and index < len(obligations):
            obl = obligations[index]
            obl["compile_status"] = "HARNESS_FAILED"
            obl["block_reason"] = HARNESS_FAILURE_REASON
            oid = _text(_dict(obl).get("obligation_id")) or f"obligation_{index}"
            failures[oid] = {
                "index": index,
                "error": f"{type(error).__name__}: {error}"[:400],
            }
    pack = {
        "schema_version": COMPILE_SCHEMA,
        "compiled_count": len(compiled),
        "blocked_count": len(blocked),
        "abstract_count": len(abstract),
        "experiments": compiled,
        "blocked_experiments": blocked,
        "abstract_experiments": abstract,
        "block_reason_counts": reason_counts,
        "concurrency": {
            "mode": "concurrent" if concurrency > 1 and len(results) > 1 else "serial",
            "max_workers": concurrency,
            "obligation_count": len(results),
            "elapsed_ms": elapsed_ms,
        },
    }
    if failures:
        pack["compile_failures"] = failures
    return pack


def compile_experiments_concurrent(
    obligations: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    compile_one: Any = None,
    available_adapters: Any = None,
) -> dict[str, Any]:
    """Compile obligations with an 8-thread pool (configurable via
    ``QUALIBUG_COMPILE_CONCURRENCY``); aggregated receipts keep the input order.

    Signature-compatible with ``experiment_compiler_base.compile_experiments``
    (same arguments, same pack contract plus additive ``concurrency`` /
    ``compile_failures`` metadata). With concurrency <= 1 (or <= 1 obligation)
    it delegates to the serial base function unchanged.
    """
    compiler = compile_one or _base.compile_experiment_for_obligation
    concurrency = get_concurrency()
    rows = [obl for obl in obligations if isinstance(obl, dict)]
    if len(rows) <= 1 or concurrency <= 1:
        started = time.time()
        pack = _base.compile_experiments(
            obligations,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=policy_version,
            compile_one=compiler,
            available_adapters=available_adapters,
        )
        pack["concurrency"] = {
            "mode": "serial",
            "max_workers": concurrency,
            "obligation_count": len(rows),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        return pack

    started = time.time()
    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="qualibug-compile"
    ) as pool:
        futures = [
            _submit_with_batch_indexes(
                pool,
                _compile_one_obligation,
                index,
                obligation,
                fn_kwargs={
                    "behavior_ir": behavior_ir,
                    "environment_type": environment_type,
                    "policy_version": policy_version,
                    "compile_one": compiler,
                    "available_adapters": available_adapters,
                },
                behavior_ir=behavior_ir,
            )
            for index, obligation in enumerate(rows)
        ]
        results = [future.result() for future in futures]
    results.sort(key=lambda pair: pair[0])
    return _merge_obligation_packs(
        results,
        rows,
        concurrency,
        int((time.time() - started) * 1000),
    )


# ── V1.8 rescue loop (abstract → materialization → concrete recompile) ───────

def _rescue_one_abstract(
    index: int,
    abstract_exp: dict[str, Any],
    *,
    obligations_by_id: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    compile_one: Any,
    environment_type: str,
    policy_version: str,
    available_adapters: Any,
    planning_context: dict[str, Any] | None,
    _actor_tokens: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Serial-equivalent per-row rescue outcome.

    Returns ``(index, outcome)`` where outcome is one of:
      {"kind": "skip", "row": original_abstract, "receipt": None, "patch": False}
      {"kind": "still_abstract", "row": enriched_abstract, "receipt": r, "patch": False}
      {"kind": "compiled", "row": concrete, "receipt": r, "patch": True}
      {"kind": "blocked", "row": concrete, "receipt": r, "patch": False}
    All list appends / obligation-row mutations happen on the main thread after
    sorting back to the input order, so the merged result is byte-identical to
    the serial ``materialize_and_recompile_abstract_pack``.
    """
    oid = _text(abstract_exp.get("obligation_id"))
    obligation = deepcopy(_dict(obligations_by_id.get(oid)))
    if not obligation:
        return index, {
            "kind": "skip",
            "row": abstract_exp,
            "receipt": None,
            "patch": False,
        }
    from .rescue_dedupe import (
        materialization_unresolved_reasons,
        rescue_cache_lookup,
        rescue_cache_record_reuse,
        rescue_cache_store,
        rescue_evidence_fingerprint,
    )

    compile_reason = _text(
        _dict(abstract_exp.get("compile_receipt")).get("reason_code")
    )
    fingerprint = rescue_evidence_fingerprint(
        obligation_id=oid,
        compile_reason=compile_reason,
        obligation=obligation,
        abstract_experiment=abstract_exp,
        behavior_ir=behavior_ir,
        actor_tokens=_actor_tokens,
    )
    cached = rescue_cache_lookup(fingerprint)
    if cached is not None and not cached.get("can_recompile"):
        # Identical evidence + prior NOT_MATERIALIZED outcome: skip the
        # expensive re-resolution + recompile, reuse the failure receipt,
        # keep the obligation ABSTRACT (never fabricate success).
        rescue_cache_record_reuse()
        receipt = dict(cached.get("materialization_receipt") or {})
        receipt["rescue_cache_hit"] = True
        receipt["rescue_cache_fingerprint"] = fingerprint
        enriched = dict(abstract_exp)
        enriched["materialization_receipt"] = receipt
        enriched["compile_receipt"] = {
            **_dict(enriched.get("compile_receipt")),
            "status": "ABSTRACT",
            "awaiting_materialization": True,
            "materialization_status": _text(receipt.get("status")),
            "rescue_cache_hit": True,
        }
        return index, {
            "kind": "still_abstract",
            "row": enriched,
            "receipt": receipt,
            "patch": False,
            "fingerprint": fingerprint,
            "cache_hit": True,
        }
    resolution = _resolve_planning_materialization(
        obligation=obligation,
        abstract_experiment=abstract_exp,
        behavior_ir=behavior_ir,
        planning_context=planning_context,
        _actor_tokens=_actor_tokens,
    )
    receipt = dict(resolution["materialization_receipt"])
    enriched = dict(abstract_exp)
    enriched["materialization_receipt"] = receipt

    if not resolution.get("can_recompile"):
        rescue_cache_store(
            fingerprint,
            materialization_receipt=receipt,
            can_recompile=False,
            still_blocked_reason=materialization_unresolved_reasons(receipt),
        )
        enriched["compile_receipt"] = {
            **_dict(enriched.get("compile_receipt")),
            "status": "ABSTRACT",
            "awaiting_materialization": True,
            "materialization_status": receipt.get("status"),
        }
        return index, {
            "kind": "still_abstract",
            "row": enriched,
            "receipt": receipt,
            "patch": False,
            "fingerprint": fingerprint,
            "cache_hit": False,
        }

    obligation = dict(obligation)
    existing_bindings = [
        dict(row)
        for row in _list(obligation.get("binding_plan"))
        if isinstance(row, dict)
    ]
    obligation["_planning_materialization"] = {
        "schema_version": MATERIALIZATION_SCHEMA,
        "receipt": receipt,
        "binding_plan_extras": list(resolution.get("binding_plan_extras") or []),
        "state_establishment_steps": list(
            _list(receipt.get("state_establishment_steps"))
        ),
        "cleanup_plan": _dict(receipt.get("cleanup_plan")),
    }
    obligation["binding_plan"] = existing_bindings + list(
        resolution.get("binding_plan_extras") or []
    )
    prop = dict(_dict(obligation.get("property")))
    prop["planning_materialization_bindings"] = list(
        resolution.get("binding_plan_extras") or []
    )
    if receipt.get("state_establishment_steps"):
        prop["state_establishment_steps"] = list(
            receipt.get("state_establishment_steps") or []
        )
    obligation["property"] = prop

    concrete = compile_one(
        obligation,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )
    concrete_receipt = _dict(concrete.get("compile_receipt"))
    concrete_status = _text(concrete_receipt.get("status")).upper()
    if concrete_status == "COMPILED":
        concrete = dict(concrete)
        concrete["materialization_receipt"] = {
            **receipt,
            "status": "MATERIALIZED",
            "recompiled": True,
        }
        concrete["experiment_phase"] = "CONCRETE"
        concrete["abstract_experiment"] = _dict(
            abstract_exp.get("abstract_experiment")
        )
        return index, {
            "kind": "compiled",
            "row": concrete,
            "receipt": receipt,
            "patch": True,
            "fingerprint": fingerprint,
            "cache_hit": False,
        }
    if is_capability_gap_reason(concrete_receipt.get("reason_code")):
        retained = promote_blocked_to_abstract(concrete, obligation)
        retained["materialization_receipt"] = {
            **receipt,
            "status": "NOT_MATERIALIZED",
            "recompile_reason_code": concrete_receipt.get("reason_code"),
            "recompile_detail": concrete_receipt.get("detail"),
        }
        return index, {
            "kind": "still_abstract",
            "row": retained,
            "receipt": receipt,
            "patch": False,
            "fingerprint": fingerprint,
            "cache_hit": False,
        }
    concrete = dict(concrete)
    concrete["materialization_receipt"] = receipt
    return index, {
        "kind": "blocked",
        "row": concrete,
        "receipt": receipt,
        "patch": False,
        "fingerprint": fingerprint,
        "cache_hit": False,
    }


def materialize_and_recompile_abstract_pack_concurrent(
    pack: dict[str, Any],
    *,
    obligations: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
    compile_one: Any,
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: Any = None,
    planning_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize ABSTRACT experiments and recompile concurrently; merged in
    the original abstract-row order. Signature-compatible with
    ``experiment_runtime_materialization.materialize_and_recompile_abstract_pack``;
    with <= 1 abstract row (or concurrency <= 1) it delegates to the serial
    function unchanged. Additive ``concurrency`` / ``rescue_failures`` metadata.
    """
    from .experiment_runtime_materialization import (
        materialize_and_recompile_abstract_pack,
    )

    context = dict(_dict(planning_context))
    if available_adapters is not None:
        context.setdefault("available_adapters", available_adapters)

    result = deepcopy(_dict(pack))
    compiled = [
        attach_passthrough_materialization(row)
        for row in _list(result.get("experiments"))
        if isinstance(row, dict)
    ]
    blocked = [
        row
        for row in _list(result.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    abstract = [
        row
        for row in _list(result.get("abstract_experiments"))
        if isinstance(row, dict)
    ]

    remaining_blocked: list[dict[str, Any]] = []
    obligations_by_id = {
        _text(row.get("obligation_id")): row
        for row in obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    for row in blocked:
        reason = _text(_dict(row.get("compile_receipt")).get("reason_code"))
        if is_capability_gap_reason(reason) and _text(
            _dict(row.get("compile_receipt")).get("status")
        ).upper() != "ABSTRACT":
            oid = _text(row.get("obligation_id"))
            abstract.append(
                promote_blocked_to_abstract(row, obligations_by_id.get(oid))
            )
        elif _text(_dict(row.get("compile_receipt")).get("status")).upper() == "ABSTRACT":
            abstract.append(row)
        else:
            remaining_blocked.append(row)

    concurrency = get_concurrency()
    started = time.time()
    if len(abstract) <= 1 or concurrency <= 1:
        final = materialize_and_recompile_abstract_pack(
            result,
            obligations=obligations,
            behavior_ir=behavior_ir,
            compile_one=compile_one,
            environment_type=environment_type,
            policy_version=policy_version,
            available_adapters=available_adapters,
            planning_context=planning_context,
        )
        final["concurrency"] = {
            "mode": "serial",
            "max_workers": concurrency,
            "abstract_row_count": len(abstract),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        return final

    materialization_receipts: list[dict[str, Any]] = []
    still_abstract: list[dict[str, Any]] = []
    recompiled = 0
    rescue_failures: list[dict[str, Any]] = []
    outcomes: list[tuple[int, dict[str, Any]]] = []
    # SPEC-11 4.3: load the token catalog once per rescue batch (per-row loads
    # meant file parses and possible HTTP logins per actor per row).
    from pathlib import Path

    _actor_tokens: dict[str, str] | None = None
    _root = context.get("root")
    _project = _text(context.get("project"))
    if _root and _project:
        try:
            from .experiment_runtime_support import load_actor_tokens

            _actor_tokens = load_actor_tokens(
                Path(_root), _project, base_url=_text(context.get("base_url"))
            )
        except Exception as exc:  # noqa: BLE001 - per-row fallback
            logger.warning("concurrent rescue token catalog load failed: %s", exc)
    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="qualibug-rescue"
    ) as pool:
        futures = [
            _submit_with_batch_indexes(
                pool,
                _rescue_one_abstract,
                index,
                abstract_exp,
                fn_kwargs={
                    "obligations_by_id": obligations_by_id,
                    "behavior_ir": behavior_ir,
                    "compile_one": compile_one,
                    "environment_type": environment_type,
                    "policy_version": policy_version,
                    "available_adapters": available_adapters,
                    "planning_context": context,
                    "_actor_tokens": _actor_tokens,
                },
                behavior_ir=behavior_ir,
            )
            for index, abstract_exp in enumerate(abstract)
        ]
        for future, abstract_exp in zip(futures, abstract):
            try:
                outcomes.append(future.result())
            except Exception as exc:  # noqa: BLE001 - isolation boundary
                logger.exception("rescue row failed")
                rescue_failures.append({
                    "obligation_id": _text(abstract_exp.get("obligation_id")),
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                })
                outcomes.append((
                    len(outcomes),
                    {
                        "kind": "skip",
                        "row": abstract_exp,
                        "receipt": None,
                        "patch": False,
                    },
                ))
    outcomes.sort(key=lambda pair: pair[0])
    rescue_stats: dict[str, int] = {
        "attempt_count": len(abstract),
        "unique_count": 0,
        "cache_hit_count": 0,
        "reexecuted_count": 0,
    }
    _unique_fingerprints: set[str] = set()
    for index, outcome in outcomes:
        _fp = _text(outcome.get("fingerprint"))
        if _fp:
            _unique_fingerprints.add(_fp)
        if outcome.get("cache_hit") is True:
            rescue_stats["cache_hit_count"] += 1
        elif outcome.get("kind") != "skip":
            rescue_stats["reexecuted_count"] += 1
    rescue_stats["unique_count"] = len(_unique_fingerprints)
    for index, outcome in outcomes:
        row = _dict(outcome.get("row"))
        receipt = outcome.get("receipt")
        if receipt is not None:
            materialization_receipts.append(dict(receipt))
        kind = _text(outcome.get("kind"))
        if kind == "compiled":
            compiled.append(row)
            recompiled += 1
            oid = _text(row.get("obligation_id"))
            obl_row = obligations_by_id.get(oid)
            if isinstance(obl_row, dict):
                obl_row["compile_status"] = "COMPILED"
                obl_row["block_reason"] = ""
        elif kind == "blocked":
            remaining_blocked.append(row)
        else:  # still_abstract / skip
            still_abstract.append(row)

    result["experiments"] = compiled
    result["blocked_experiments"] = remaining_blocked
    result["abstract_experiments"] = still_abstract
    result["compiled_count"] = len(compiled)
    result["blocked_count"] = len(remaining_blocked)
    result["abstract_count"] = len(still_abstract)
    result["materialization_receipts"] = materialization_receipts
    result["materialization_summary"] = {
        "schema_version": "qualibug.experiment-materialization-summary.v1",
        "abstract_input_count": len(abstract),
        "recompiled_count": recompiled,
        "still_abstract_count": len(still_abstract),
        "materialized_receipt_count": sum(
            1
            for row in materialization_receipts
            if _text(row.get("status")) == "MATERIALIZED"
        ),
        "not_materialized_receipt_count": sum(
            1
            for row in materialization_receipts
            if _text(row.get("status")) != "MATERIALIZED"
        ),
        "fixture_actor_state_observer_cleanup_front_loaded": True,
        "rescue_dedupe": dict(rescue_stats),
    }
    counts: dict[str, int] = {}
    for item in remaining_blocked + still_abstract:
        code = _text(_dict(item.get("compile_receipt")).get("reason_code")) or "UNKNOWN"
        counts[code] = counts.get(code, 0) + 1
    result["block_reason_counts"] = counts
    # Preserve the initial-compile concurrency metadata additively: the rescue
    # pass reuses the ``concurrency`` key for its own timing, so the compile
    # pass's metadata would otherwise be overwritten. Both modes are
    # receipt-visible.
    if isinstance(result.get("concurrency"), dict):
        result["compile_concurrency"] = dict(result["concurrency"])
    result["concurrency"] = {
        "mode": "concurrent",
        "max_workers": concurrency,
        "abstract_row_count": len(abstract),
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    if rescue_failures:
        result["rescue_failures"] = rescue_failures
    return result


__all__ = [
    "DEFAULT_CONCURRENCY",
    "MAX_CONCURRENCY",
    "MIN_CONCURRENCY",
    "CONCURRENCY_ENV",
    "compile_experiments_concurrent",
    "get_concurrency",
    "materialize_and_recompile_abstract_pack_concurrent",
]
