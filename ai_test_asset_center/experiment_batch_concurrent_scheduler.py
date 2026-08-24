"""Resource-domain isolated concurrent batch scheduler.

SPEC 任务 9：批次执行从完全串行（~1 experiment/s）提升为并发度 8（默认，
环境变量 ``QUALIBUG_EXECUTOR_CONCURRENCY`` 可覆盖，2-16 校验）。

安全模型（隔离分组，接口/资源域）：
- 同一实验内的 control/treatment 步骤天然在同一 worker 内串行执行，实验
  原子性不变（每组整体交给原串行批次执行器，组内完全保持原语义）。
- 不同实验之间按资源域分组：
  * 读实验（全部步骤为 GET/HEAD）不参与写分组，每个读实验独立一组，可与
    任何实验并行（只读无状态污染）。
  * 写实验按 (interface_key, resource_key[, actor_ref]) 分组：同一键的实验
    分到同一串行组，不同键并行。同一接口 + 同一资源实例的写实验（含不同
    actor）必须串行 —— 状态竞争与 fixture 生命周期（写后清理 vs 下一实验）
    不允许并行；同一接口 + 未知资源 + 同一 actor 的写实验串行（同账户状态
    竞争防护）；同一接口不同资源实例的写实验并行（用户决策：接口独立）。
- 线程安全：线程池 worker 只返回各自独立的分批结果（原串行执行器无跨线程
  共享可变收据），聚合/计数/去重在主线程单线程完成；共享入参（behavior_ir、
  experiments_by_obligation、runtime_contract 等）只读传递。
- 失败隔离：任一组的执行异常被捕获，为该组内每个实验生成 HARNESS_FAILED
  收据（保持"每个实验都有收据"不变量），不中断其他 worker。

语义保持（与串行版本一致）：
- 全局优先化 + 预算裁剪在调度器层按原公式执行一次（复用
  ``safe_experiment_prioritizer`` 与 ``_core`` 的预算 floor），预算语义与串行
  完全一致；分组只发生在预算裁剪后的执行集上。
- 聚合时 results/findings 按原 selected 顺序重排（selected_obligation_id
  索引），并发完成顺序不影响收据顺序。
- deliverable 去重（``_deliverable_dedupe_key``）在聚合层对跨组重复再做一次
  全局折叠，与串行版本的全局去重语义一致。

接入：``experiment_batch_executor.execute_selected_experiments``（fanout 层）
内部把对串行核心的调用替换为本模块的 ``execute_selected_experiments_concurrent``，
fanout 层在聚合结果上继续展开 —— 主链零断点，不动任务 5 正在修改的
``_experiment_batch_executor_single_finding_mechanics.py``。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 8
MIN_CONCURRENCY = 2
MAX_CONCURRENCY = 16
CONCURRENCY_ENV = "QUALIBUG_EXECUTOR_CONCURRENCY"

_READ_METHODS = frozenset({"GET", "HEAD"})
_BINDING_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}|:(\w+)")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def get_concurrency() -> int:
    """Concurrency from QUALIBUG_EXECUTOR_CONCURRENCY, clamped to [2, 16].

    Invalid / unset values fall back to the default 8. Never silently degrades
    below 2 (a serial fallback would look like a dead executor on a slow batch).
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
    return max(MIN_CONCURRENCY, min(value, MAX_CONCURRENCY))


# ── 资源域分组 ──────────────────────────────────────────────────────────────

def _normalize_path(path: str) -> str:
    """Strip query/fragment/trailing slash and lowercase; placeholders kept."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    return raw.rstrip("/").lower()


def _step_method(step: dict[str, Any], ir_ops: dict[str, dict[str, Any]]) -> str:
    method = _text(step.get("method") or step.get("http_method"))
    if method:
        return method.upper()
    op_ref = _text(step.get("operation_ref"))
    if op_ref:
        op = _dict(ir_ops.get(op_ref))
        method = _text(op.get("method") or op.get("http_method"))
        if method:
            return method.upper()
    return ""


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ops = _dict(behavior_ir).get("operations")
    if isinstance(ops, dict):
        return {
            _text(key): dict(row)
            for key, row in ops.items()
            if isinstance(row, dict) and _text(key)
        }
    return {
        _text(row.get("id") or row.get("operation_id")): dict(row)
        for row in _list(ops)
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _experiment_steps(exp: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan_key in ("treatment_plan", "control_plan"):
        for step in _list(exp.get(plan_key)):
            if isinstance(step, dict):
                rows.append(step)
    return rows


def _barrier_group_count(
    group_selected: list[list[Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> int:
    """Number of serial groups holding at least one barrier-pair experiment.

    Receipt-visible concurrency metadata: a barrier experiment releases its
    control/treatment pair at the same moment, so its group count says how many
    concurrent double-write windows this batch actually executed (each in its own
    isolated group).
    """
    count = 0
    for rows in group_selected:
        if any(
            _text(_dict(step).get("barrier_group"))
            for item in rows
            for exp in [
                experiments_by_obligation.get(
                    _text(_dict(item).get("obligation_id")), {}
                )
            ]
            for step in _experiment_steps(exp)
        ):
            count += 1
    return count


def _actor_ref_of(exp: dict[str, Any]) -> str:
    contract = _dict(exp.get("actor_selection_contract"))
    ref = _text(contract.get("treatment_actor_ref"))
    if not ref:
        ref = _text(exp.get("treatment_actor_ref"))
    if not ref:
        ref = _text(contract.get("control_actor_ref"))
    if not ref:
        ref = _text(exp.get("control_actor_ref"))
    if not ref:
        plan = _dict(exp.get("actor_execution_plan"))
        candidates = _list(plan.get("candidate_ids"))
        if candidates:
            ref = _text(candidates[0])
    if not ref:
        ref = _text(exp.get("actor_id"))
    return ref


def _step_actors(exp: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    """Actor refs attached to the experiment's own steps (per-step override)."""
    refs: list[str] = []
    for step in steps:
        ref = _text(step.get("actor_ref"))
        if ref:
            refs.append(ref)
    return "|".join(sorted(set(refs)))


def _resource_key_of(
    exp: dict[str, Any],
    steps: list[dict[str, Any]],
    placeholder_names: set[str],
) -> str:
    """Concrete resource-instance values bound to path placeholders.

    An empty result means "unknown resource instance" — the group falls back to
    the conservative same-interface + same-actor serialization.
    """
    if not placeholder_names:
        return ""
    resolved: dict[str, str] = {}
    for source in (
        exp.get("runtime_bindings"),
        exp.get("_pre_resolved_bindings"),
        exp.get("binding_values"),
    ):
        if isinstance(source, dict):
            for key, value in source.items():
                if value not in (None, ""):
                    resolved[str(key).strip()] = str(value).strip()
    for step in steps:
        bindings = step.get("bindings")
        if isinstance(bindings, dict):
            for key, value in bindings.items():
                if value not in (None, ""):
                    resolved[str(key).strip()] = str(value).strip()
    parts = sorted(resolved.get(name, "") for name in placeholder_names)
    return "|".join(part for part in parts if part)


def _write_group_key(
    exp: dict[str, Any],
    ir_ops: dict[str, dict[str, Any]],
) -> tuple[str, ...] | None:
    """Resource-domain group key; None means the experiment is read-free.

    Returns:
      ("res", interface_key, resource_key)  — same resource instance → serial,
          regardless of actor (fixture lifecycle / state competition).
      ("iface", interface_key, actor_ref)   — unknown resource instance →
          same interface + same actor serial (account-state competition),
          different actor parallel.
    Read experiments (no write step) return None and never join a write group.
    """
    steps = _experiment_steps(exp)
    if not steps:
        # No observable plan: treat as free (execution will BLOCK internally
        # with its own receipt; concurrency cannot corrupt anything).
        return None
    write_steps: list[dict[str, Any]] = []
    unknown_method = False
    for step in steps:
        method = _step_method(step, ir_ops)
        if not method:
            unknown_method = True
            continue
        if method not in _READ_METHODS:
            write_steps.append(step)
    if unknown_method and not write_steps:
        # Fail-closed: an unclassifiable step means we cannot prove the
        # experiment is read-only, so treat it as a write.
        write_steps = [step for step in steps]
    if not write_steps:
        return None

    write_paths = sorted({
        (method, _normalize_path(step.get("path") or step.get("path_template")))
        for step in write_steps
        for method in [_step_method(step, ir_ops) or "UNKNOWN"]
    })
    interface_key = "|".join(f"{method} {path}" for method, path in write_paths)

    placeholder_names: set[str] = set()
    for _, path in write_paths:
        for match in _BINDING_PLACEHOLDER_RE.finditer(path):
            placeholder_names.add(match.group(1) or match.group(2))
    resource_key = _resource_key_of(exp, write_steps, placeholder_names)
    # ── Barrier pairs (same-experiment concurrent double-write) ──
    # A barrier experiment releases its control/treatment pair at the same moment
    # on the same resource; the pair's race window must never overlap another
    # experiment's window on the same interface. When the concrete resource
    # instance is bound, the existing ("res", …) key already serializes against
    # every experiment touching that instance. An UNKNOWN resource instance (the
    # fixture binding is resolved only at runtime) serializes ALL barrier
    # experiments on the interface regardless of actor: two oversell pairs from
    # different actors on the same unknown SKU would otherwise corrupt each
    # other's window. Generic mechanism, never benchmark data.
    _barrier_write = any(
        _text(step.get("barrier_group")) for step in write_steps
    )
    if _barrier_write and not resource_key:
        return ("barrier", interface_key)
    if resource_key:
        return ("res", interface_key, resource_key)
    # ── P0-1: Coverage Unit arms serialize within one unit ──
    # Execution arms of one Coverage Unit probe the same defect surface with
    # different actors on the same resource domain; when the concrete resource
    # instance is unknown, arms must still share ONE serial group (fixture /
    # account-state competition between role variants would otherwise race).
    # The unit group override applies only to experiments carrying
    # ``coverage_unit_id`` (unit representatives and derived arms); every other
    # experiment keeps the exact previous grouping semantics.
    _unit_group = _text(exp.get("coverage_unit_id"))
    if _unit_group:
        return ("unit", interface_key, _unit_group)
    actor_ref = _actor_ref_of(exp) or _step_actors(exp, write_steps)
    return ("iface", interface_key, actor_ref or "unknown")


def partition_serial_groups(
    selected: list[Any],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> list[list[Any]]:
    """Split selected rows into serial groups; each group keeps its relative order.

    Groups are ordered by first appearance of their key in ``selected``; every
    read experiment forms its own free group so it can run against any write
    group concurrently. Aggregation later re-orders receipts back to the
    original ``selected`` sequence, so group order here is stability only.
    """
    ir_ops = _operation_index(behavior_ir)
    groups: dict[tuple[str, ...], list[Any]] = {}
    order: list[tuple[str, ...]] = []
    for item in selected:
        oid = _text(_dict(item).get("obligation_id"))
        exp = _dict(experiments_by_obligation.get(oid))
        key = _write_group_key(exp, ir_ops) if exp else None
        if key is None:
            # Free group: one read / plan-less experiment per group.
            key = ("read", oid or _text(_dict(item).get("experiment_id")), str(len(order)))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    return [groups[key] for key in order]


# ── 预算 / 优先化（与串行核心同一公式，保证预算语义不变）────────────────

def _phase_of(runtime_contract: dict[str, Any], validation_phase: str) -> str:
    phase = str(validation_phase or _dict(runtime_contract).get("validation_phase") or "").strip().lower()
    if phase in ("formal", "full", "production"):
        return "formal"
    return "small_scale"


def _apply_global_budget(
    selected: list[Any],
    *,
    runtime_contract: dict[str, Any],
    validation_phase: str,
    behavior_ir: dict[str, Any],
    experiments_by_obligation: dict[str, dict[str, Any]],
    family_quota: int,
) -> tuple[list[Any], list[dict[str, Any]], dict[str, Any], int]:
    """Prioritize globally and enforce the per-batch budget exactly as the
    serial core does (same floor formulas, same hard cap), so splitting into
    groups never widens the executed set.

    Returns (budgeted_selected, deferred_rows, prioritization_receipt, budget).
    """
    from . import _experiment_batch_executor_single_finding_mechanics as _core
    from .safe_experiment_prioritizer import prioritize_experiments
    from .small_scale_validation_gate import HARD_BUDGET_CAP, get_validation_budget

    phase = _phase_of(runtime_contract, validation_phase)
    budget = get_validation_budget(runtime_contract, phase=phase)
    budget = max(1, min(budget, HARD_BUDGET_CAP))
    budget = _core._operation_coverage_budget(
        selected, budget, hard_cap=HARD_BUDGET_CAP
    )
    budget = _core._family_coverage_budget(
        selected, budget, hard_cap=HARD_BUDGET_CAP
    )

    receipt: dict[str, Any] = {}
    try:
        experiments = [
            experiments_by_obligation.get(_text(_dict(s).get("obligation_id")), {})
            for s in selected
        ]
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=[_dict(s) for s in selected],
            behavior_ir=behavior_ir,
            budget=budget,
            family_quota=family_quota,
        )
        # Canonical prioritizer output is the "prioritized" scored list; the
        # scheduler must consume it or the ordering never reaches the budget.
        ordered_ids = [
            _text(_dict(row).get("obligation_id"))
            for row in _list(receipt.get("prioritized"))
            if _text(_dict(row).get("obligation_id"))
        ]
        if ordered_ids:
            by_id = {_text(_dict(s).get("obligation_id")): s for s in selected}
            reordered = [by_id[oid] for oid in ordered_ids if oid in by_id]
            remaining = [
                s for s in selected
                if _text(_dict(s).get("obligation_id")) not in set(ordered_ids)
            ]
            selected = reordered + remaining
    except Exception as exc:  # noqa: BLE001 - surfaced in campaign receipt
        logger.warning("concurrent batch prioritization failed: %s", exc)
        receipt = {"prioritization_error": str(exc)}

    deferred: list[dict[str, Any]] = []
    if len(selected) > budget:
        deferred = [dict(_dict(item)) for item in selected[budget:]]
        selected = selected[:budget]
    return selected, deferred, receipt, budget


# ── 失败隔离 ────────────────────────────────────────────────────────────────

def _harness_failed_batch(
    selected_group: list[Any],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    project: str,
    campaign_id: str,
    batch_nonce: str,
    error: Exception,
) -> dict[str, Any]:
    """Receipt-complete HARNESS_FAILED batch for a group whose execution threw.

    Keeps the "every experiment has a receipt" invariant: one outcome per
    selected row with status HARNESS_FAILED, mirroring the serial core's
    HARNESS_FAILURE accounting path.
    """
    from . import _experiment_batch_executor_single_finding_mechanics as _core

    results: list[dict[str, Any]] = []
    compile_results: dict[str, dict[str, Any]] = {}
    detail = (
        f"concurrent_group_execution_failed:{type(error).__name__}:{error}"[:400]
    )
    for index, item in enumerate(selected_group):
        row = _dict(item)
        oid = _text(row.get("obligation_id"))
        eid = _text(row.get("experiment_id"))
        candidate_id = _text(row.get("candidate_id")) or _core._stable_id(
            "cand", project, oid or index
        )
        slice_id = _text(row.get("slice_id") or row.get("behavior_slice_id")) or (
            _core._stable_id("slice", project, oid or candidate_id)
        )
        execution_id = _core._stable_id(
            "exec", project, campaign_id, eid, oid, batch_nonce, index
        )
        evidence_id = _core._stable_id("evidence", execution_id)
        compile_results[oid] = {
            "status": "HARNESS_FAILED",
            "reason_code": "HARNESS_FAILURE",
            "experiment_id": eid,
            "receipt_id": _core._stable_id(
                "compile", project, campaign_id, oid, batch_nonce
            ),
        }
        results.append({
            "schema_version": "qualibug.experiment-execution.v1",
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "obligation_id": oid,
            "selected_obligation_id": oid,
            "experiment_id": eid,
            "execution_id": execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
            "status": "HARNESS_FAILED",
            "reason_code": "HARNESS_FAILURE",
            "detail": detail,
            # Mirror the serial BLOCKED path (base.py:1013): the mainline
            # ledger builder reads `reason_detail` off the outcome, so without
            # this a group-level harness failure arrives with EMPTY detail and
            # the real exception (e.g. UnboundLocalError) is invisible — a
            # fail-silent violation of the no-silent-failure rule.
            "reason_detail": detail,
            "finding": None,
            "execution_receipt": {
                "status": "HARNESS_FAILED",
                "reason_code": "HARNESS_FAILURE",
                "detail": detail,
                "reason_detail": detail,
                "obligation_id": oid,
                "selected_obligation_id": oid,
                "experiment_id": eid,
                "campaign_id": campaign_id,
            },
        })
    return {
        "results": results,
        "compile_results": compile_results,
        "execution_results": {},
        "gate_results": {},
        "findings": [],
        "budget_deferred": [],
        "executed_count": 0,
        "blocked_count": 0,
        "harness_failure_count": len(results),
        "cleanup_failures": 0,
        "duplicate_delivery_count": 0,
        "group_error": detail,
    }


# ── 聚合（主线程单线程执行，线程安全）─────────────────────────────────────

def _merge_group_batches(
    group_batches: list[dict[str, Any]],
    group_selected: list[list[Any]],
    *,
    selected: list[Any],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str,
    deferred: list[dict[str, Any]],
    budget: int,
    family_quota: int,
    phase: str,
    prioritization_receipt: dict[str, Any],
    prioritization_failed: bool,
) -> dict[str, Any]:
    """Merge per-group batch results back into one batch, receipts ordered by
    the original ``selected`` sequence. All mutations happen on the caller
    thread only — worker threads never touch shared aggregates."""
    from . import _experiment_batch_executor_single_finding_mechanics as _core

    # 1) Flatten in original order via selected_obligation_id index.
    order_index: dict[str, int] = {
        _text(_dict(item).get("obligation_id")): index
        for index, item in enumerate(selected)
        if _text(_dict(item).get("obligation_id"))
    }
    flat_results: list[dict[str, Any]] = []
    flat_compile: dict[str, dict[str, Any]] = {}
    flat_execution: dict[str, dict[str, Any]] = {}
    flat_gate: dict[str, dict[str, Any]] = {}
    executed = blocked = harness = cleanup_failures = 0
    operator_cancelled_count = 0
    operator_cancelled_receipt: dict[str, Any] = {}
    group_errors: list[str] = []
    group_validation_notes: list[str] = []
    for batch, rows in zip(group_batches, group_selected):
        batch = _dict(batch)
        if _text(batch.get("group_error")):
            group_errors.append(_text(batch["group_error"]))
        inner_receipt = _dict(batch.get("campaign_validation_receipt"))
        inner_reasons = [
            _text(value)
            for value in _list(inner_receipt.get("reasons"))
            if _text(value)
        ]
        if _text(inner_receipt.get("campaign_validation_status")) == "FAILED" and inner_reasons:
            group_validation_notes.extend(inner_reasons)
        results = [
            dict(row) for row in _list(batch.get("results")) if isinstance(row, dict)
        ]
        for row in results:
            key = _text(row.get("selected_obligation_id")) or _text(
                row.get("obligation_id")
            )
            if key in order_index:
                flat_results.append((order_index[key], row))
            else:
                # Receipt for a row the scheduler did not budget — keep stable.
                flat_results.append((len(order_index) + len(flat_results), row))
        for key, value in _dict(batch.get("compile_results")).items():
            if isinstance(value, dict):
                flat_compile[str(key)] = dict(value)
        for key, value in _dict(batch.get("execution_results")).items():
            if isinstance(value, dict):
                flat_execution[str(key)] = dict(value)
        for key, value in _dict(batch.get("gate_results")).items():
            if isinstance(value, dict):
                flat_gate[str(key)] = dict(value)
        executed += int(batch.get("executed_count") or 0)
        blocked += int(batch.get("blocked_count") or 0)
        harness += int(batch.get("harness_failure_count") or 0)
        cleanup_failures += int(batch.get("cleanup_failures") or 0)
        operator_cancelled_count += int(
            batch.get("operator_cancelled_count") or 0
        )
        if not operator_cancelled_receipt and isinstance(
            batch.get("operator_cancelled_receipt"), dict
        ):
            operator_cancelled_receipt = dict(batch["operator_cancelled_receipt"])
    flat_results.sort(key=lambda pair: pair[0])
    results = [row for _, row in flat_results]

    # 2) Cross-group deliverable dedupe (same global semantics as serial core).
    delivered_finding_ids: dict[str, dict[str, Any]] = {}
    duplicate_delivery_count = 0
    findings: list[dict[str, Any]] = []
    for row in results:
        status = _text(row.get("status")).upper()
        if status not in ("EXECUTED", "DELIVERABLE"):
            continue
        finding = row.get("finding")
        if not isinstance(finding, dict):
            continue
        dedupe_key = _core._deliverable_dedupe_key(finding)
        if dedupe_key and dedupe_key in delivered_finding_ids:
            duplicate_delivery_count += 1
            first = delivered_finding_ids[dedupe_key]
            variants = _list(first.get("duplicate_variants"))
            variant_note = _text(finding.get("title")) or dedupe_key
            if variant_note not in variants:
                variants.append(variant_note)
            first["duplicate_variants"] = variants
            finding["duplicate_of"] = (
                _text(first.get("finding_id") or first.get("id")) or dedupe_key
            )
            row["finding"] = finding
        else:
            if dedupe_key:
                delivered_finding_ids[dedupe_key] = finding
            findings.append(finding)

    batch_result: dict[str, Any] = {
        "schema_version": "qualibug.experiment-execution-batch.v1",
        "selected_count": len(selected),
        "executed_count": executed,
        "blocked_count": blocked,
        "harness_failure_count": harness,
        "cleanup_failures": cleanup_failures,
        "budget_exceeded_count": len(deferred),
        "budget_deferred": deferred,
        "experiment_budget": budget,
        "family_execution_quota": family_quota,
        "duplicate_delivery_count": duplicate_delivery_count,
        "validation_phase": phase,
        "operator_cancelled_count": operator_cancelled_count,
        "findings": findings,
        "results": results,
        "compile_results": flat_compile,
        "execution_results": flat_execution,
        "gate_results": flat_gate,
        "every_experiment_has_receipt": all(
            isinstance(row.get("execution_receipt"), dict) for row in results
        ),
        "concurrency": {
            "mode": "concurrent",
            "max_workers": get_concurrency(),
            "group_count": len(group_batches),
            "barrier_group_count": _barrier_group_count(
                group_selected, experiments_by_obligation
            ),
            "group_errors": group_errors,
            "group_validation_notes": group_validation_notes,
        },
    }
    if operator_cancelled_receipt:
        batch_result["operator_cancelled_receipt"] = operator_cancelled_receipt
    if prioritization_receipt:
        batch_result["prioritization_receipt"] = prioritization_receipt

    # 3) Rebuild gate / funnel / attribution on the merged view.
    from .small_scale_validation_gate import check_validation_gate

    validation_gate = check_validation_gate(
        batch_result,
        campaign_id=campaign_id,
        run_id=_text(_dict(mainline_run).get("run_id")),
        phase=phase,
    )
    batch_result["validation_gate"] = validation_gate

    funnel_failed = False
    funnel_error = ""
    attribution_failed = False
    attribution_error = ""
    try:
        from .execution_coverage_funnel import build_execution_coverage_funnel

        all_exps = [
            experiments_by_obligation.get(oid, {})
            for oid in order_index
        ]
        all_obls = [_dict(s) for s in selected]
        batch_result["execution_coverage_funnel"] = build_execution_coverage_funnel(
            obligations=all_obls,
            experiments=all_exps,
            execution_results=list(flat_execution.values()),
            findings=findings,
            campaign_id=campaign_id,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced in campaign receipt
        funnel_failed = True
        funnel_error = str(exc)
        logger.warning("concurrent batch funnel generation failed: %s", exc)
    try:
        from .blocker_attribution import attribute_all_blockers

        batch_result["blocker_attribution"] = attribute_all_blockers(
            obligations=[_dict(s) for s in selected],
            experiments=[
                experiments_by_obligation.get(oid, {}) for oid in order_index
            ],
            execution_results=list(flat_execution.values()),
            behavior_ir=behavior_ir,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced in campaign receipt
        attribution_failed = True
        attribution_error = str(exc)
        logger.warning("concurrent batch attribution failed: %s", exc)

    campaign_status = "PASSED"
    campaign_reasons: list[str] = []
    if prioritization_failed:
        campaign_status = "FAILED"
        campaign_reasons.append("HARNESS_PRIORITIZATION_FAILED:concurrent")
    if funnel_failed:
        campaign_status = "FAILED"
        campaign_reasons.append(f"HARNESS_COVERAGE_FUNNEL_FAILED:{funnel_error[:100]}")
    if attribution_failed:
        campaign_status = "FAILED"
        campaign_reasons.append(f"HARNESS_BLOCKER_ATTRIBUTION_FAILED:{attribution_error[:100]}")
    if group_errors:
        campaign_status = "FAILED"
        campaign_reasons.extend(
            f"HARNESS_GROUP_EXECUTION_FAILED:{error[:100]}" for error in group_errors
        )
    if not batch_result.get("execution_coverage_funnel"):
        campaign_status = "FAILED"
        campaign_reasons.append("missing_execution_coverage_funnel")
    if not batch_result.get("blocker_attribution"):
        campaign_status = "FAILED"
        campaign_reasons.append("missing_blocker_attribution")
    batch_result["campaign_validation_receipt"] = {
        "schema_version": "qualibug.campaign-validation-receipt.v1",
        "campaign_validation_status": campaign_status,
        "reasons": campaign_reasons,
        "prioritization_present": bool(prioritization_receipt),
        "funnel_present": bool(batch_result.get("execution_coverage_funnel")),
        "attribution_present": bool(batch_result.get("blocker_attribution")),
        "degraded_mode": campaign_status == "FAILED",
        "customer_deliverable": campaign_status == "PASSED",
    }
    return batch_result


# ── 并发入口 ────────────────────────────────────────────────────────────────

def _run_group(
    group: list[Any],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    root: Any,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str,
    validation_phase: str,
    batch_nonce: str,
) -> dict[str, Any]:
    from . import _experiment_batch_executor_single_finding_mechanics as _core

    try:
        return _core.execute_selected_experiments(
            group,
            experiments_by_obligation=experiments_by_obligation,
            behavior_ir=behavior_ir,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            mainline_run=mainline_run,
            campaign_id=campaign_id,
            experiment_budget=len(group),
            validation_phase=validation_phase,
        )
    except Exception as exc:  # noqa: BLE001 - failure isolation boundary
        logger.exception("concurrent group execution failed (%d experiments)", len(group))
        return _harness_failed_batch(
            group,
            experiments_by_obligation=experiments_by_obligation,
            project=project,
            campaign_id=campaign_id,
            batch_nonce=batch_nonce,
            error=exc,
        )


def execute_selected_experiments_concurrent(
    selected: list[Any],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    root: Any,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str = "",
    experiment_budget: int = 100,
    validation_phase: str = "",
) -> dict[str, Any]:
    """Batch execution with resource-domain isolated concurrency.

    Signature-compatible with
    ``_experiment_batch_executor_single_finding_mechanics.execute_selected_experiments``:
    same inputs, same batch-result contract (plus a ``concurrency`` metadata
    block). Serial fallbacks keep byte-identical semantics when concurrency is
    unavailable (single group / worker count 1).
    """
    from . import _experiment_batch_executor_single_finding_mechanics as _core
    from .experiment_runtime_support import _stable_id, _text as _t

    selected_ids = [_t(_dict(item).get("obligation_id")) for item in selected]
    if not all(selected_ids) or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected_obligation_identity_invalid")
    run_contract = _dict(mainline_run)
    if not run_contract or _t(run_contract.get("campaign_id")) != _t(campaign_id):
        raise ValueError("experiment batch mainline campaign identity mismatch")

    family_quota = max(
        1, int(_dict(runtime_contract).get("family_execution_quota") or 0) or 1
    )
    phase = _phase_of(runtime_contract, validation_phase)

    # Global prioritization + budget (identical formulas to the serial core).
    selected, deferred, prioritization_receipt, budget = _apply_global_budget(
        selected,
        runtime_contract=runtime_contract,
        validation_phase=validation_phase,
        behavior_ir=behavior_ir,
        experiments_by_obligation=experiments_by_obligation,
        family_quota=family_quota,
    )
    prioritization_failed = bool(
        _dict(prioritization_receipt).get("prioritization_error")
    )
    if prioritization_failed:
        prioritization_receipt = {}

    groups = partition_serial_groups(
        selected, experiments_by_obligation, behavior_ir
    )
    concurrency = get_concurrency()
    # ── Scheduling-time topology trace ──────────────────────────────────────
    # The batch receipt only survives a clean scan_result write; a wrap-up
    # crash used to erase the answer to "why did execution take this long".
    # Emitting the topology here makes group collapse visible immediately
    # (CMP_77d5dfe1 round-2 post-mortem gap).
    group_sizes = sorted((len(g) for g in groups), reverse=True)
    logger.warning(
        "[exec-trace] schedule groups=%d workers=%d selected=%d deferred=%d "
        "top_group_sizes=%s",
        len(groups),
        concurrency,
        len(selected),
        len(deferred),
        group_sizes[:5],
    )
    if not selected:
        # Empty selection: return an empty batch shaped like the core's, with
        # the same validation envelope, so downstream accounting stays intact.
        batch = _merge_group_batches(
            [], [],
            selected=selected,
            experiments_by_obligation=experiments_by_obligation,
            behavior_ir=behavior_ir,
            mainline_run=mainline_run,
            campaign_id=campaign_id,
            deferred=deferred,
            budget=budget,
            family_quota=family_quota,
            phase=phase,
            prioritization_receipt=prioritization_receipt,
            prioritization_failed=prioritization_failed,
        )
        batch["concurrency"] = {
            "mode": "empty", "max_workers": concurrency, "group_count": 0,
            "group_errors": [],
        }
        return batch

    if len(groups) <= 1 or concurrency <= 1:
        # Serial fallback: exact core semantics, no pool overhead.
        batch = _run_group(
            selected,
            experiments_by_obligation=experiments_by_obligation,
            behavior_ir=behavior_ir,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            mainline_run=mainline_run,
            campaign_id=campaign_id,
            validation_phase=validation_phase,
            batch_nonce=str(time.time_ns()),
        )
        merged = _merge_group_batches(
            [batch], [selected],
            selected=selected,
            experiments_by_obligation=experiments_by_obligation,
            behavior_ir=behavior_ir,
            mainline_run=mainline_run,
            campaign_id=campaign_id,
            deferred=deferred,
            budget=budget,
            family_quota=family_quota,
            phase=phase,
            prioritization_receipt=prioritization_receipt,
            prioritization_failed=prioritization_failed,
        )
        merged["concurrency"] = {
            "mode": "serial_fallback",
            "max_workers": concurrency,
            "group_count": len(groups),
            "group_errors": [batch["group_error"]] if batch.get("group_error") else [],
        }
        logger.warning(
            "[exec-trace] batch mode=serial_fallback groups=%d selected=%d",
            len(groups),
            len(selected),
        )
        return merged

    batch_nonce = str(time.time_ns())
    started = time.time()
    group_selected: list[list[Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="qualibug-exp") as pool:
        futures = []
        for group in groups:
            group_selected.append(group)
            futures.append(
                pool.submit(
                    _run_group,
                    group,
                    experiments_by_obligation=experiments_by_obligation,
                    behavior_ir=behavior_ir,
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    mainline_run=mainline_run,
                    campaign_id=campaign_id,
                    validation_phase=validation_phase,
                    batch_nonce=batch_nonce,
                )
            )
        group_batches = [future.result() for future in futures]
    merged = _merge_group_batches(
        group_batches,
        group_selected,
        selected=selected,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=behavior_ir,
        mainline_run=mainline_run,
        campaign_id=campaign_id,
        deferred=deferred,
        budget=budget,
        family_quota=family_quota,
        phase=phase,
        prioritization_receipt=prioritization_receipt,
        prioritization_failed=prioritization_failed,
    )
    merged["concurrency"] = {
        "mode": "concurrent",
        "max_workers": concurrency,
        "group_count": len(groups),
        "elapsed_ms": int((time.time() - started) * 1000),
        "group_errors": [
            _text(_dict(batch).get("group_error"))
            for batch in group_batches
            if _text(_dict(batch).get("group_error"))
        ],
    }
    logger.warning(
        "[exec-trace] batch mode=concurrent groups=%d workers=%d elapsed_ms=%d",
        len(groups),
        concurrency,
        merged["concurrency"]["elapsed_ms"],
    )
    return merged


__all__ = [
    "DEFAULT_CONCURRENCY",
    "MAX_CONCURRENCY",
    "MIN_CONCURRENCY",
    "CONCURRENCY_ENV",
    "execute_selected_experiments_concurrent",
    "get_concurrency",
    "partition_serial_groups",
]
