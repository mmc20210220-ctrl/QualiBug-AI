"""
Discovery-chain positioning (链路定位): where did this run get stuck?

One receipt per run that answers three questions in a single view:
  1. 链路走到哪一步？  (stage-level progress: 9 stages)
  2. 卡在哪、为什么？  (per-stage blocked counts + reason-code breakdown)
  3. 损失在哪一步、归谁？ (first-loss stage + attribution via the reason-code catalog)

Pure projection layer: it only aggregates receipts the mainline already emits
(plan bundle, engine report, obligation-attempt ledger, formal projection,
contract-derivation receipt).  It computes no business facts, changes no
detection/compilation/gate logic, and never touches evaluator-private data.

Diagnostic guidance (reason-code meanings / root causes / actions) comes from
``blocker_attribution.build_reason_code_catalog`` and is explicitly marked
synthetic — it never satisfies the customer-delivery gate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .blocker_attribution import build_reason_code_catalog, profile_reason_code

CHAIN_POSITIONING_SCHEMA = "qualibug.discovery-chain-positioning.v1"

STAGE_ORDER = (
    "source_ingestion",
    "comprehension",
    "hypothesis",
    "obligation_compile",
    "experiment_compile",
    "execution",
    "observation",
    "verdict",
    "delivery_gate",
)

# Classification is derived from the SSOT registries, never from closed local
# lists: terminal statuses come from the obligation-attempt ledger, blocking
# semantics come from the reason-code registry (blocker_attribution).  Any
# future status / reason code emitted by any target system or future runner
# is therefore classified by the same rules — nothing here is a hardcoded
# enumeration that silently drops unknown values.
try:
    from ._obligation_attempt_ledger_single_occurrence_mechanics import (  # noqa: E402
        TERMINAL_STATUSES as _LEDGER_TERMINAL_STATUSES,
    )
except Exception:  # pragma: no cover - degrade visibly if the ledger moves
    _LEDGER_TERMINAL_STATUSES = frozenset({
        "DELIVERABLE", "REJECTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED",
    })

# Terminal verdicts: execution completed and the oracle produced a decision.
_COMPLETED_TERMINAL_STATUSES = frozenset({
    status for status in _LEDGER_TERMINAL_STATUSES
    if status not in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}
})
# Terminal blockers: execution did not complete toward a verdict.
_BLOCKED_TERMINAL_STATUSES = _LEDGER_TERMINAL_STATUSES - _COMPLETED_TERMINAL_STATUSES


def _is_blocking_reason(code: str) -> bool:
    """Blocking semantics from the registry SSOT; unknown codes stay visible."""
    return bool(profile_reason_code(code).get("is_blocking", True))


def _reason_family(code: str) -> str:
    return str(profile_reason_code(code).get("reason_family") or "UNREGISTERED")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value or fallback)
    except (TypeError, ValueError):
        return fallback


def _nested(result: dict[str, Any], key: str) -> dict[str, Any]:
    return _dict(result.get(key) or _dict(result.get("v12")).get(key))


def _counter(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        code = _text(row.get(field))
        if code:
            counts[code] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _stage(
    name: str,
    *,
    input_count: int,
    output_count: int,
    blocked_count: int = 0,
    reason_codes: dict[str, int] | None = None,
    key_receipts: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "stage": name,
        "input_count": max(0, _int(input_count)),
        "output_count": max(0, _int(output_count)),
        "blocked_count": max(0, _int(blocked_count)),
        "reason_code_breakdown": dict(reason_codes or {}),
        "key_receipts": list(key_receipts or []),
        "note": note,
    }


def _loss_ratio(stage: dict[str, Any]) -> float:
    """Output/input conversion loss, clamped to [0,1] (output may exceed
    input at expansion stages such as source_ingestion or hypothesis)."""
    denominator = max(1, stage["input_count"])
    return round(max(0.0, (stage["input_count"] - stage["output_count"]) / denominator), 4)


def build_chain_positioning(result: dict[str, Any] | None) -> dict[str, Any]:
    """Project one run's existing receipts into the chain-positioning view.

    Fail-soft: missing receipts degrade to zero-count stages with a visible
    note; the receipt is always emitted.  Never raises into the mainline.
    """
    run = _dict(result)
    stages: list[dict[str, Any]] = []

    # ── Stage 1: source ingestion ──
    input_receipt = _nested(run, "behavior_ir_input_receipt")
    overlay_receipt = _nested(run, "runtime_source_overlay_receipt")
    derivation_receipt = _nested(run, "experiment_compile").get("contract_derivation_receipt") \
        or _dict(run.get("contract_derivation_receipt"))
    api_operation_count = _int(input_receipt.get("api_operation_count"))
    runtime_actor_count = _int(input_receipt.get("runtime_actor_count"))
    sources_present = bool(overlay_receipt or input_receipt)
    stages.append(_stage(
        "source_ingestion",
        input_count=1 if sources_present else 0,
        output_count=api_operation_count + runtime_actor_count,
        blocked_count=1 if (sources_present and api_operation_count == 0) else 0,
        reason_codes={"api_operations_zero": 1} if (sources_present and api_operation_count == 0) else None,
        key_receipts=[
            "behavior_ir_input_receipt",
            "runtime_source_overlay_receipt",
            "contract_derivation_receipt",
        ],
        note=(
            f"derivation={_text(derivation_receipt.get('status'))}"
            if derivation_receipt
            else "no_contract_derivation_receipt"
        ),
    ))

    # ── Stage 2: comprehension (reader -> Behavior IR) ──
    behavior_ir = _nested(run, "behavior_ir")
    entity_count = len(_list(behavior_ir.get("entities")))
    invariant_count = len(_list(behavior_ir.get("invariants")))
    stages.append(_stage(
        "comprehension",
        input_count=api_operation_count,
        output_count=entity_count + invariant_count,
        key_receipts=["behavior_ir", "knowledge_source_flow_receipt"],
        note=(
            "no_entities_or_invariants_derived"
            if (api_operation_count > 0 and entity_count + invariant_count == 0)
            else ""
        ),
    ))

    # ── Stage 3: hypothesis (reasoner engines) ──
    engine_report = _dict(
        _nested(run, "test_obligations").get("mainline_reasoner_report")
    ) or _nested(run, "mainline_reasoner_report")
    llm_engines = _list(engine_report.get("llm_engines"))
    failed_engines = _list(engine_report.get("failed_engines"))
    degraded_engines = _list(engine_report.get("degraded_engines"))
    total_hypotheses = _int(engine_report.get("total_hypotheses"))
    engine_error_codes = _dict(engine_report.get("engine_error_codes"))
    error_counter: Counter[str] = Counter()
    for code in engine_error_codes.values():
        if _text(code):
            error_counter[_text(code)] += 1
    for engine in [*failed_engines, *degraded_engines]:
        engine_code = _text(engine_error_codes.get(engine))
        if not engine_code:
            error_counter["unknown_failure"] += 1
    stages.append(_stage(
        "hypothesis",
        input_count=len(llm_engines) or _int(engine_report.get("total_engines")),
        output_count=total_hypotheses,
        blocked_count=len(failed_engines) + len(degraded_engines),
        reason_codes=dict(sorted(error_counter.items(), key=lambda item: (-item[1], item[0]))),
        key_receipts=[
            "mainline_reasoner_report",
            "learned_memory_receipt",
            "fact_retrieval_receipt",
            "engine_attention_receipt",
            "semantic_dedup_receipt",
        ],
        note=f"model_attempts={_int(engine_report.get('model_attempt_count'))} responses={_int(engine_report.get('model_response_count'))}" if engine_report else "no_engine_report",
    ))

    # ── Stage 4: obligation compile ──
    obligations = [
        row for row in _list(_nested(run, "test_obligations").get("obligations"))
        if isinstance(row, dict)
    ]
    blocked_obligations = [
        row for row in obligations
        if _text(row.get("compile_status")).upper() == "BLOCKED"
    ]
    stages.append(_stage(
        "obligation_compile",
        input_count=total_hypotheses,
        output_count=len(obligations),
        blocked_count=len(blocked_obligations),
        reason_codes=_counter(blocked_obligations, "compile_reason_code") or None,
        key_receipts=[
            "test_obligations.obligation_identity_receipt",
            "binding_closure_receipt",
            "adapter_surface_install_receipt",
        ],
    ))

    # ── Stage 5: experiment compile ──
    experiment_compile = _nested(run, "experiment_compile")
    all_experiments = [
        row for row in _list(experiment_compile.get("all_experiments"))
        if isinstance(row, dict)
    ]
    blocked_experiments = [
        row for row in _list(experiment_compile.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    blocked_reason_codes = _counter(
        [dict(row.get("compile_receipt") or {}) for row in blocked_experiments],
        "reason_code",
    )
    stages.append(_stage(
        "experiment_compile",
        input_count=len(obligations),
        output_count=len(all_experiments),
        blocked_count=len(blocked_experiments),
        reason_codes=blocked_reason_codes or None,
        key_receipts=["experiment_compile.compile_receipt", "contract_derivation_receipt"],
    ))

    # ── Stage 6-8: execution / observation / verdict (attempt ledger) ──
    attempts: list[dict[str, Any]] = []
    ledger_note = ""
    try:
        from .discovery_funnel import _attempt_ledger

        ledger = _attempt_ledger(run)
        attempts = [
            dict(row) for row in _list(ledger.get("attempts"))
            if isinstance(row, dict)
        ]
    except Exception as exc:
        ledger_note = f"attempt_ledger_unavailable:{type(exc).__name__}"

    # Runtime stages count only attempts that reached execution or gate;
    # compile-stage terminal attempts belong to the compile stage (the plan
    # bundle's blocked_experiments already account for them) — counting them
    # here too would double-report the same loss.
    runtime_attempts = [
        row for row in attempts
        if _text(row.get("terminal_stage")) in {"execution", "gate"}
    ]
    compile_stage_attempts = [
        row for row in attempts
        if _text(row.get("terminal_stage")) == "compile"
    ]

    selected_count = _int(_dict(ledger).get("selected_count")) if ledger_note == "" else 0
    terminal_statuses = Counter(_text(row.get("terminal_status")).upper() for row in runtime_attempts)
    completed_terminal = sum(
        count for status, count in terminal_statuses.items()
        if status in _COMPLETED_TERMINAL_STATUSES
    )
    blocked_terminal = sum(
        count for status, count in terminal_statuses.items()
        if status in _BLOCKED_TERMINAL_STATUSES
    )
    attempt_reason_codes = _counter(runtime_attempts, "reason_code")
    blocking_reason_codes = {
        code: count for code, count in attempt_reason_codes.items()
        if _is_blocking_reason(code)
    }
    stages.append(_stage(
        "execution",
        input_count=selected_count,
        output_count=completed_terminal + blocked_terminal,
        blocked_count=blocked_terminal,
        reason_codes=blocking_reason_codes or None,
        key_receipts=["obligation_attempt_ledger", "execution_operational_receipts"],
        note=(
            ledger_note
            or (
                f"compile_stage_terminal_attempts={len(compile_stage_attempts)}"
                if compile_stage_attempts
                else ""
            )
        ),
    ))

    observed_attempts = [
        row for row in runtime_attempts
        if _list(row.get("observation_receipt_ids"))
    ]
    observer_blocked = [
        row for row in runtime_attempts
        if _reason_family(_text(row.get("reason_code"))) == "OBSERVER_CAPABILITY_GAP"
    ]
    stages.append(_stage(
        "observation",
        input_count=completed_terminal,
        output_count=len(observed_attempts),
        blocked_count=len(observer_blocked),
        reason_codes=_counter(observer_blocked, "reason_code") or None,
        key_receipts=["observation_receipts", "observer_contracts_receipt"],
    ))

    verdict_attempts = [
        row for row in runtime_attempts
        if _text(row.get("oracle_reason_code"))
        or _text(row.get("terminal_status")).upper() in _COMPLETED_TERMINAL_STATUSES
    ]
    indeterminate_attempts = [
        row for row in verdict_attempts
        if _reason_family(_text(row.get("oracle_reason_code"))) == "ORACLE_INPUT_GAP"
        or _text(row.get("terminal_status")).upper() not in _COMPLETED_TERMINAL_STATUSES
    ]
    stages.append(_stage(
        "verdict",
        input_count=len(observed_attempts) or completed_terminal,
        output_count=len(verdict_attempts) - len(indeterminate_attempts),
        blocked_count=len(indeterminate_attempts),
        reason_codes=_counter(indeterminate_attempts, "oracle_reason_code") or None,
        key_receipts=["gate_results", "contract_oracle_receipts"],
        note="indeterminate_verdicts_are_evidence_gaps" if indeterminate_attempts else "",
    ))

    # ── Stage 9: delivery gate ──
    formal_note = ""
    deliverable_count = 0
    occurrence_count = 0
    try:
        from .discovery_funnel import _formal_projection

        formal = _formal_projection(run)
        deliverable_count = _int(formal.get("formal_customer_deliverable_count"))
        occurrence_count = _int(formal.get("delivery_occurrence_count"))
    except Exception as exc:
        formal_note = f"formal_projection_unavailable:{type(exc).__name__}"
    stages.append(_stage(
        "delivery_gate",
        input_count=len(verdict_attempts),
        output_count=deliverable_count,
        blocked_count=max(0, len(verdict_attempts) - deliverable_count - len(indeterminate_attempts)),
        key_receipts=[
            "formal_count_projection",
            "formal_delivery_authority",
            "customer_delivery_gate_receipt",
        ],
        note=formal_note or f"delivery_occurrences={occurrence_count}",
    ))

    # ── First loss stage ──
    first_loss = _first_loss_stage(stages)

    # ── Chain summary ──
    catalog = build_reason_code_catalog()
    all_reason_counts: Counter[str] = Counter()
    for stage in stages:
        for code, count in stage["reason_code_breakdown"].items():
            all_reason_counts[code] += count
    top_blockers: list[dict[str, Any]] = []
    for code, count in all_reason_counts.most_common(5):
        profile = profile_reason_code(code)
        top_blockers.append({
            "reason_code": code,
            "count": count,
            "meaning": profile.get("meaning", ""),
            "likely_root_cause": profile.get("likely_root_cause", ""),
            "suggested_action": profile.get("suggested_action", ""),
        })
    summary = {
        "first_loss_stage": first_loss["stage"],
        "first_loss_basis": first_loss["basis"],
        "total_blocked": sum(stage["blocked_count"] for stage in stages),
        "total_hypotheses": total_hypotheses,
        "total_obligations": len(obligations),
        "total_experiments": len(all_experiments),
        "total_deliverables": deliverable_count,
        "top_blocker_codes": top_blockers,
        "catalog_code_count": catalog["code_count"],
    }

    return {
        "schema_version": CHAIN_POSITIONING_SCHEMA,
        "stages": stages,
        "first_loss": first_loss,
        "chain_summary": summary,
        "guidance_kind": catalog["guidance_kind"],
    }


def _first_loss_stage(stages: list[dict[str, Any]]) -> dict[str, Any]:
    """First stage with a significant loss, in pipeline order.

    Significant means: at least one blocked item, or a non-empty input that
    produced zero output.  Returns NO_SIGNIFICANT_LOSS when nothing qualifies,
    so a healthy run is not mislabeled.
    """
    for stage in stages:
        if stage["blocked_count"] >= 1:
            return {
                "stage": stage["stage"],
                "basis": f"blocked_count={stage['blocked_count']}",
            }
        if stage["input_count"] > 0 and stage["output_count"] == 0:
            return {
                "stage": stage["stage"],
                "basis": f"zero_output_from_input={stage['input_count']}",
            }
    return {"stage": "NO_SIGNIFICANT_LOSS", "basis": "no_stage_blocked_or_zero_output"}


def _stage_map(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("stage")): row
        for row in receipt.get("stages", [])
        if isinstance(row, dict)
    }


def _diff_ratio(stage: dict[str, Any]) -> float:
    denominator = max(1, int(stage.get("input_count", 0)))
    return (int(stage.get("input_count", 0)) - int(stage.get("output_count", 0))) / denominator


def render_chain_diff_markdown(run_a: dict[str, Any], run_b: dict[str, Any]) -> str:
    """Stage-level diff between two runs: which stage's conversion changed.

    Answers "回归发生在哪一阶段" for champion/challenger or before/after
    comparisons.  Diagnostic only; synthetic guidance, never delivery evidence.
    """
    receipt_a = build_chain_positioning(run_a)
    receipt_b = build_chain_positioning(run_b)
    map_a = _stage_map(receipt_a)
    map_b = _stage_map(receipt_b)
    first_a = _dict(receipt_a.get("first_loss"))
    first_b = _dict(receipt_b.get("first_loss"))
    lines: list[str] = [
        "# 链路定位对比（阶段级）",
        "",
        f"- A 第一损失点: {_text(first_a.get('stage'))} ({_text(first_a.get('basis'))})",
        f"- B 第一损失点: {_text(first_b.get('stage'))} ({_text(first_b.get('basis'))})",
        "",
        "| 阶段 | A 输入→输出 | B 输入→输出 | 阻塞 A→B | 转化率变化 | 原因码变化 |",
        "|---|---|---|---|---|---|",
    ]
    for stage in receipt_a.get("stages", []):
        name = str(stage.get("stage"))
        other = map_b.get(name, {})
        label = _STAGE_LABELS.get(name, name)
        a_blocked = int(stage.get("blocked_count", 0))
        b_blocked = int(other.get("blocked_count", 0))
        a_ratio = _diff_ratio(stage)
        b_ratio = _diff_ratio(other)
        delta = "+" if b_ratio > a_ratio else ("-" if b_ratio < a_ratio else "=")
        codes_a = set((stage.get("reason_code_breakdown") or {}).keys())
        codes_b = set((other.get("reason_code_breakdown") or {}).keys())
        new_codes = sorted(codes_b - codes_a)
        gone_codes = sorted(codes_a - codes_b)
        code_text = ""
        if new_codes:
            code_text += f"新增:{','.join(new_codes)}"
        if gone_codes:
            code_text += (f"; " if code_text else "") + f"消失:{','.join(gone_codes)}"
        lines.append(
            f"| {label} | {stage.get('input_count', 0)}→{stage.get('output_count', 0)} "
            f"| {other.get('input_count', 0)}→{other.get('output_count', 0)} "
            f"| {a_blocked}→{b_blocked} | {delta} ({b_ratio - a_ratio:+.0%}) | {code_text or '-'} |"
        )
    if _text(first_a.get("stage")) != _text(first_b.get("stage")):
        lines += [
            "",
            f"> 第一损失点从 `{_text(first_a.get('stage'))}` "
            f"迁移到 `{_text(first_b.get('stage'))}` —— "
            "卡点发生了阶段迁移，优先检查 B 新增的阻塞原因码。",
        ]
    else:
        lines += [
            "",
            f"> 第一损失点保持不变（`{_text(first_a.get('stage'))}`）；"
            "若 A→B 转化下降，对比该阶段的原因码分布变化。",
        ]
    lines += [
        "",
        "> 本报告为诊断定位信息；指导内容为合成诊断文本，不构成交付证据。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Readable report (Markdown)
# ---------------------------------------------------------------------------

_STAGE_LABELS = {
    "source_ingestion": "来源材料摄入",
    "comprehension": "业务理解 (Reader → IR)",
    "hypothesis": "假设生成 (Reasoner)",
    "obligation_compile": "义务编译",
    "experiment_compile": "实验编译",
    "execution": "执行",
    "observation": "观察",
    "verdict": "判定 (Oracle)",
    "delivery_gate": "交付门禁",
}


def render_chain_positioning_markdown(receipt: dict[str, Any]) -> str:
    """Human-readable chain-positioning report (diagnostic, synthetic guidance)."""
    receipt = _dict(receipt)
    lines: list[str] = [
        "# 发现链路定位报告",
        "",
        f"- schema: `{_text(receipt.get('schema_version'))}`",
        f"- 第一损失点: **{_text(_dict(receipt.get('first_loss')).get('stage'))}**"
        f"（{_text(_dict(receipt.get('first_loss')).get('basis'))}）",
        "",
        "| 阶段 | 输入 | 输出 | 阻塞 | 转化率 | 原因码分布 |",
        "|---|---|---|---|---|---|",
    ]
    stages = _list(receipt.get("stages"))
    for stage in stages:
        label = _STAGE_LABELS.get(_text(stage.get("stage")), _text(stage.get("stage")))
        codes = stage.get("reason_code_breakdown") or {}
        code_text = ", ".join(f"{code}×{count}" for code, count in list(codes.items())[:4])
        if len(codes) > 4:
            code_text += f" …(共{len(codes)}种)"
        ratio = _loss_ratio(stage)
        lines.append(
            f"| {label} | {stage.get('input_count', 0)} | {stage.get('output_count', 0)} "
            f"| {stage.get('blocked_count', 0)} | {ratio:.0%} | {code_text or '-'} |"
        )
    top = _list(_dict(receipt.get("chain_summary")).get("top_blocker_codes"))
    if top:
        lines += ["", "## 主要卡点与修复建议（诊断指导，非交付证据）", ""]
        for row in top:
            lines.append(
                f"- **{_text(row.get('reason_code'))}** ×{row.get('count', 0)}："
                f"{_text(row.get('meaning'))}\n"
                f"  - 最可能根因：{_text(row.get('likely_root_cause'))}\n"
                f"  - 建议动作：{_text(row.get('suggested_action'))}"
            )
    lines += [
        "",
        f"> 本报告为诊断定位信息；指导内容为合成诊断文本（{_text(receipt.get('guidance_kind'))}），"
        "不构成交付证据。",
    ]
    return "\n".join(lines)
