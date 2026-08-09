"""Binding-experience learning: verified resolver mappings across scans.

Why this exists
---------------
The learning loop's planning boost only reorders obligations; the executed
set is pinned by the governed-execution binding gate (``BLOCKED_MISSING_BINDING``).
This module gives learning a second, execution-changing surface: when a
placeholder was successfully resolved in a prior scan by a source-declared
resolver operation, the next scan tries that resolver first.

Compliance boundary (must hold)
-------------------------------
- Only *source-declared resolver identities* are recorded: the resolver's
  ``operation_ref``, normalized path, method, and the placeholder target.
  Resolved business values are **never** stored (fingerprint-only binding
  receipts; customer business data never enters product knowledge).
- This is replay of source declarations with historical priority, not an
  inference of new sources. No request bodies, credentials, entities, SQL,
  or impact claims are ever synthesized.
- READ side reorders an experiment's existing resolver list (stable sort).
  It never adds resolvers, never changes binding status, never changes
  budgets, gates, or fail-closed semantics.

Write side
----------
``build_binding_experience_context`` is called at scan close. It reads the
scan's execution receipts (``binding_materialization_receipts``), records
each ``BOUND`` resolver mapping into the SQLite knowledge base under the
``binding_resolver`` category, and non-reinforces (decays) resolver mappings
that were tried but failed this scan. Failures stay visible in the receipt.

Read side
---------
``apply_binding_experience_reorder`` is called at planning time. It reorders
each compiled experiment's ``binding_plan[].resolver_operations`` so that
resolvers with verified prior success come first (stable; no history -> the
original order is untouched).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# Category used in the SQLite knowledge base for verified resolver mappings.
BINDING_RESOLVER_CATEGORY = "binding_resolver"

# Reinforce ceiling for a verified (BOUND) resolver mapping.
_BOUND_CONFIDENCE = 0.95
# Decay applied to resolver mappings that were tried but not BOUND this scan.
_FAILURE_DECAY_FACTOR = 0.95
_FAILURE_DECAY_FLOOR = 0.05


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _experiment_execution_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract per-experiment execution results from a v12 scan result."""
    v12 = _dict(result.get("v12"))
    execution = _dict(v12.get("experiment_execution"))
    return [
        row for row in _list(execution.get("results")) if isinstance(row, dict)
    ]


def _resolver_key(operation_ref: str, target: str) -> str:
    """Stable knowledge-base key: source-declared resolver + placeholder."""
    return f"{operation_ref}:{target}"


def extract_binding_experience(result: dict[str, Any]) -> dict[str, Any]:
    """Extract verified / failed resolver mappings from a scan result.

    Returns ``{"verified": [...], "failed": [...], "results_seen": N}``.
    ``verified`` entries carry only source-declared identities (operation_ref,
    normalized path, method, actor, status code) plus the placeholder target —
    never the resolved business value.
    """
    verified: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    results_seen = 0
    for exec_result in _experiment_execution_results(result):
        results_seen += 1
        for receipt in _list(exec_result.get("binding_materialization_receipts")):
            if not isinstance(receipt, dict):
                continue
            operation_ref = _text(receipt.get("resolver_operation_ref"))
            target = _text(receipt.get("target"))
            if not operation_ref or not target:
                continue
            status = _text(receipt.get("status")).upper()
            entry = {
                "operation_ref": operation_ref,
                "target": target,
                "path": _text(receipt.get("resolver_path")),
                "actor_ref": _text(receipt.get("resolver_actor_ref")),
                "source_priority": _text(receipt.get("source_priority")),
                "status_code": int(receipt.get("status_code") or 0),
            }
            if status == "BOUND":
                verified.append(entry)
            elif status in {"BLOCKED", "FAILED", "INDETERMINATE"}:
                failed.append(entry)
    return {
        "verified": verified,
        "failed": failed,
        "results_seen": results_seen,
    }


def build_binding_experience_context(
    project: str, root: Path | None, result: dict[str, Any]
) -> dict[str, Any]:
    """WRITE side: persist verified resolver mappings into the knowledge base.

    Called at scan close (after ``build_closed_loop_context``). Verified
    (BOUND) mappings are reinforced to 0.95; mappings tried but not BOUND
    this scan are decayed (floor 0.05). Never stores resolved values.
    Failures stay visible in the returned receipt.
    """
    try:
        from .learning_pattern_bridge import LearningPatternBridge

        bridge = LearningPatternBridge(project=project)
        extracted = extract_binding_experience(result)
        verified = extracted["verified"]
        failed = extracted["failed"]

        stored_count = 0
        seen_keys: set[str] = set()
        for entry in verified:
            key = _resolver_key(entry["operation_ref"], entry["target"])
            seen_keys.add(key)
            content = {
                "operation_ref": entry["operation_ref"],
                "target": entry["target"],
                "path": entry["path"],
                "actor_ref": entry["actor_ref"],
                "source_priority": entry["source_priority"],
                "status_code": entry["status_code"],
                "success_count": 1,
                "last_success_at": _now(),
            }
            try:
                bridge.kb.store(
                    category=BINDING_RESOLVER_CATEGORY,
                    key=key,
                    content=content,
                    confidence=_BOUND_CONFIDENCE,
                    domains=[],
                    expiry_days=None,
                )
                stored_count += 1
            except Exception as exc:
                raise RuntimeError(
                    f"binding_resolver_store_failed:{key}:"
                    f"{type(exc).__name__}:{str(exc)[:100]}"
                ) from exc

        # A resolver mapping may be verified by many experiments in one scan;
        # ``stored_count`` counts store calls (reinforcement events), while
        # ``unique_keys`` counts the distinct knowledge entries they map to.
        unique_keys = len(seen_keys)

        # Non-reinforcement: a resolver mapping tried but not BOUND this scan
        # loses a bounded confidence slice. Never deleted; floor keeps it
        # testable (same semantics as closed-loop risk_pattern decay).
        failed_keys = [
            _resolver_key(entry["operation_ref"], entry["target"])
            for entry in failed
        ]
        decayed_count = 0
        if failed_keys:
            decayed_count = bridge.kb.adjust_confidence(
                BINDING_RESOLVER_CATEGORY,
                failed_keys,
                _FAILURE_DECAY_FACTOR,
                floor=_FAILURE_DECAY_FLOOR,
            )

        return {
            "schema_version": "qualibug.binding-experience-write.v1",
            "status": (
                "OK" if verified
                else "DECAY_ONLY" if failed
                else "NO_RECORDS"
            ),
            "results_seen": extracted["results_seen"],
            "verified_count": len(verified),
            "failed_count": len(failed),
            "stored_count": stored_count,
            "unique_keys": unique_keys,
            "decayed_count": decayed_count,
            "authority": "source_declared_resolver_replay_no_business_values",
            "recorded_at": _now(),
        }
    except Exception as exc:
        return {
            "schema_version": "qualibug.binding-experience-write.v1",
            "status": "FAILED",
            "failure": f"{type(exc).__name__}:{str(exc)[:200]}",
            "verified_count": 0,
            "stored_count": 0,
            "decayed_count": 0,
            "authority": "source_declared_resolver_replay_no_business_values",
            "recorded_at": _now(),
        }


def build_binding_experience_index(learned_knowledge: Any) -> dict[str, Any]:
    """Normalize the scan-start learned_knowledge payload into a resolver
    success index.

    The payload is expected to carry ``binding_resolvers`` entries (loaded by
    ``LearningPatternBridge.load_binding_experience`` at scan start). Each
    entry is ``{key, operation_ref, target, confidence, success_count}``.
    Returns an explicit, inspectable index; invalid payloads yield
    ``NO_PATTERNS`` instead of raising, so planning stays fail-visible.
    """
    knowledge = _dict(learned_knowledge)
    raw = [
        item for item in _list(knowledge.get("binding_resolvers"))
        if isinstance(item, dict)
    ]
    if _text(knowledge.get("load_failure")) and not raw:
        return {
            "status": "LOAD_FAILED",
            "load_failure": _text(knowledge.get("load_failure")),
            "entries": [],
            "resolver_count": 0,
        }
    entries: list[dict[str, Any]] = []
    for item in raw:
        operation_ref = _text(item.get("operation_ref"))
        target = _text(item.get("target"))
        if not operation_ref or not target:
            continue
        try:
            confidence = float(item.get("confidence") or item.get("_confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            success_count = int(item.get("success_count") or item.get("_usage_count") or 1)
        except (TypeError, ValueError):
            success_count = 1
        entries.append({
            "key": _text(item.get("key")) or _resolver_key(operation_ref, target),
            "operation_ref": operation_ref,
            "target": target,
            "confidence": confidence,
            "success_count": max(success_count, 1),
        })
    if not entries:
        return {
            "status": "NO_PATTERNS",
            "load_failure": "",
            "entries": [],
            "resolver_count": 0,
        }
    return {
        "status": "CONSUMED",
        "load_failure": "",
        "entries": entries,
        "resolver_count": len(entries),
    }


def apply_binding_experience_reorder(
    experiments: Any,
    learned_knowledge: Any,
) -> dict[str, Any]:
    """READ side: reorder each experiment's resolver list by verified success.

    For every experiment in ``experiments`` (mapping obligation_id ->
    experiment row, or a list of rows), each ``binding_plan`` entry with
    ``status == "runtime_resolvable"`` has its ``resolver_operations``
    stable-sorted so resolvers with a verified prior BOUND hit come first.
    Resolvers without history keep their relative order; an empty index
    leaves every plan untouched.

    Additive only: never adds resolvers, never changes binding status,
    never changes budgets, gates, or compile state.
    """
    index = build_binding_experience_index(learned_knowledge)
    if index.get("status") != "CONSUMED":
        return {
            "schema_version": "qualibug.binding-experience-read.v1",
            "status": index.get("status") or "NO_PATTERNS",
            "load_failure": index.get("load_failure") or "",
            "reordered_count": 0,
            "plans_scanned": 0,
            "authority": "resolver_priority_reorder_only_no_new_sources",
        }

    success_by_ref: dict[str, tuple[float, int]] = {}
    for entry in index["entries"]:
        ref = entry["operation_ref"]
        current = success_by_ref.get(ref)
        if current is None or entry["success_count"] > current[1]:
            success_by_ref[ref] = (entry["confidence"], entry["success_count"])

    def _sort_key(resolver: dict[str, Any]) -> tuple[int, int]:
        # Verified-first stable sort: (has_experience desc, success_count desc)
        ref = _text(resolver.get("operation_ref"))
        prior = success_by_ref.get(ref)
        if prior is None:
            return (0, 0)
        return (1, prior[1])

    rows = (
        list(experiments.values())
        if isinstance(experiments, dict)
        else [row for row in _list(experiments) if isinstance(row, dict)]
    )
    reordered_count = 0
    plans_scanned = 0
    top_reorders: list[dict[str, Any]] = []
    for row in rows:
        plan = row.get("binding_plan")
        if not isinstance(plan, list):
            continue
        plans_scanned += 1
        for binding in plan:
            if not isinstance(binding, dict):
                continue
            if _text(binding.get("status")) != "runtime_resolvable":
                continue
            resolvers = [r for r in _list(binding.get("resolver_operations")) if isinstance(r, dict)]
            if len(resolvers) < 2:
                continue
            sorted_resolvers = sorted(resolvers, key=_sort_key, reverse=True)
            if sorted_resolvers != resolvers:
                binding["resolver_operations"] = sorted_resolvers
                reordered_count += 1
                if len(top_reorders) < 10:
                    top_reorders.append({
                        "obligation_id": _text(row.get("obligation_id")),
                        "target": _text(binding.get("target")),
                        "resolver_order": [
                            _text(r.get("operation_ref")) for r in sorted_resolvers
                        ][:5],
                    })

    return {
        "schema_version": "qualibug.binding-experience-read.v1",
        "status": "CONSUMED" if plans_scanned else "NO_PLANS",
        "load_failure": "",
        "resolver_count": index["resolver_count"],
        "plans_scanned": plans_scanned,
        "reordered_count": reordered_count,
        "top_reorders": top_reorders,
        "authority": "resolver_priority_reorder_only_no_new_sources",
    }
