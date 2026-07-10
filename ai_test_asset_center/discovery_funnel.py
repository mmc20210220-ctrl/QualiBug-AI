"""Discovery funnel observability — aggregate five-stage conversion + blockers.

Pure aggregation over a completed ``run_v12_pipeline`` result. No mock findings,
no industry hardcoding. Customer-visible validated bugs stay separated from
pending (needs_more_evidence) internal clues.
"""
from __future__ import annotations

from typing import Any

from .customer_delivery_gate import is_customer_deliverable_defect


_STAGE_NAMES = (
    "candidate_generation",
    "probe_selection",
    "execution",
    "verification",
    "formal_accounting",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _conversion(output: int, input_n: int) -> float:
    if input_n <= 0:
        return 0.0
    return round(float(output) / float(input_n), 4)


def _stage(name: str, input_n: int, output_n: int) -> dict[str, Any]:
    input_n = max(0, int(input_n))
    output_n = max(0, min(int(output_n), input_n) if input_n else max(0, int(output_n)))
    return {
        "name": name,
        "input": input_n,
        "output": output_n,
        "conversion": _conversion(output_n, input_n),
        "dropped": max(0, input_n - output_n),
    }


def _is_validated_bug(finding: dict[str, Any]) -> bool:
    """Use the formal customer-delivery gate as the single source of truth."""
    return is_customer_deliverable_defect(finding)


def _is_pending_finding(finding: dict[str, Any]) -> bool:
    if _is_validated_bug(finding):
        return False
    status = str(finding.get("final_review_status") or finding.get("business_evidence_status") or "").upper()
    if "NEEDS_MORE_EVIDENCE" in status or status.startswith("PENDING"):
        return True
    eq = _as_dict(finding.get("evidence_quality"))
    if str(eq.get("level") or "").lower() in {"needs_evidence", "needs_more_evidence"}:
        return True
    if str(finding.get("confirmation_status") or "").lower() in {"candidate", "needs_more_evidence"}:
        return True
    if finding.get("gate_passed") is False:
        return True
    quality = _as_dict(finding.get("evidence_quality"))
    if finding.get("gate_passed") and _as_int(quality.get("score")) < 90:
        return True
    return False


def _collect_blocking_reasons(
    v12_result: dict[str, Any],
    gate_results: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}

    def _bump(reason: str, n: int = 1) -> None:
        key = str(reason or "").strip()
        if not key:
            return
        counts[key] = counts.get(key, 0) + max(1, int(n))

    for item in gate_results or []:
        if not isinstance(item, dict):
            continue
        missing = item.get("business_gate_missing") or item.get("missing") or item.get("missing_requirements")
        if isinstance(missing, list):
            for reason in missing:
                _bump(str(reason))
        elif isinstance(missing, dict):
            for reason, n in missing.items():
                _bump(str(reason), _as_int(n, 1))
        reason = item.get("reason") or item.get("blocking_reason")
        if reason:
            _bump(str(reason))

    for finding in _as_list(v12_result.get("findings")):
        if not isinstance(finding, dict):
            continue
        missing = finding.get("business_gate_missing") or _as_dict(finding.get("evidence")).get("business_gate_missing")
        if isinstance(missing, list):
            for reason in missing:
                _bump(str(reason))
        status = str(finding.get("business_evidence_status") or finding.get("final_review_status") or "")
        if status.upper().startswith("PENDING_") or "NEEDS_MORE_EVIDENCE" in status.upper():
            # Map pending status tokens to gate-style reason codes when explicit missing list absent
            if not missing:
                token = status.upper().replace("PENDING_", "")
                if token and token not in {"EVIDENCE", "MORE_EVIDENCE"}:
                    _bump(token if "_" in token or token.endswith("MISSING") else f"{token}_PENDING")

    unify = _as_dict(v12_result.get("mainline_unification"))
    for origin, funnel in unify.items():
        if origin == "error" or not isinstance(funnel, dict):
            continue
        dropped = _as_int(funnel.get("dropped_no_endpoint"))
        if dropped:
            _bump("dropped_no_endpoint", dropped)
        if str(funnel.get("status") or "") == "provider_unavailable":
            _bump("llm_provider_unavailable")

    execution = _as_dict(_as_dict(v12_result.get("phases")).get("execution"))
    blocked = _as_int(execution.get("production_data_blocked"))
    if blocked:
        _bump("production_data_blocked", blocked)
    skip_telemetry = _as_dict(execution.get("skip_telemetry"))
    cleanup_counts = _as_dict(skip_telemetry.get("cleanup_status_counts"))
    if _as_int(cleanup_counts.get("failed")):
        _bump("sandbox_cleanup_failed", _as_int(cleanup_counts.get("failed")))
    if _as_int(cleanup_counts.get("not_reversible")):
        _bump("sandbox_cleanup_not_reversible", _as_int(cleanup_counts.get("not_reversible")))
    observer_counts = _as_dict(skip_telemetry.get("observer_status_counts"))
    observer_missing = sum(
        _as_int(count)
        for key, count in observer_counts.items()
        if "documented_observer_missing" in str(key)
    )
    if observer_missing:
        _bump("documented_observer_missing", observer_missing)
    reason_counts = _as_dict(skip_telemetry.get("reason_counts"))
    for reason, n in reason_counts.items():
        key = str(reason or "").split(":", 1)[0].strip()
        if key:
            _bump(key, _as_int(n, 1))
    path_binding_misses = _as_dict(skip_telemetry.get("path_binding_misses"))
    if path_binding_misses:
        _bump("missing_runtime_path_binding", sum(_as_int(n, 1) for n in path_binding_misses.values()) or len(path_binding_misses))
    blocked_scenarios = _as_int(skip_telemetry.get("scenarios_blocked"))
    if blocked_scenarios and _as_int(execution.get("executed")) == 0:
        _bump("scenarios_blocked_no_http", blocked_scenarios)
    if str(execution.get("status") or "") in {"blocked", "skipped", "plan_only"}:
        reason = str(execution.get("reason") or execution.get("status") or "execution_blocked")
        _bump(reason)
    if str(execution.get("observability_status") or "") == "FAILED_SAFE":
        _bump("execution_observability_gap")
        for item in _as_list(execution.get("observability")):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            if status in {"failed", "missing"}:
                _bump(f"observability_{item.get('kind') or 'unknown'}_{status}")
    if _as_int(execution.get("executed")) == 0:
        selected = _as_list(
            _as_dict(_as_dict(v12_result.get("phases")).get("incremental_discovery")).get("selected_slice_ids")
            or _as_dict(v12_result.get("behavior_slice_ledger")).get("selected_slice_ids")
        )
        if selected:
            _bump(str(execution.get("reason") or "no_runtime_execution_receipts"))

    # Unattempted / budget-gated candidates — empty findings must not look like "no bugs".
    incremental = _as_dict(_as_dict(v12_result.get("phases")).get("incremental_discovery"))
    stop_reason = str(
        incremental.get("stop_reason")
        or _as_dict(v12_result.get("behavior_slice_ledger")).get("stop_reason")
        or ""
    )
    if stop_reason == "slice_budget_reached":
        _bump("slice_budget_reached")
    pending_slices = _as_list(
        incremental.get("pending_slice_ids")
        or _as_dict(v12_result.get("behavior_slice_ledger")).get("pending_slice_ids")
        or incremental.get("pending")
    )
    if pending_slices:
        _bump("unattempted_behavior_slices", len(pending_slices))
    total_slices = _as_int(
        incremental.get("total_slices")
        or _as_dict(v12_result.get("behavior_slice_ledger")).get("total_slices")
    )
    selected_slices = _as_list(
        incremental.get("selected_slice_ids")
        or _as_dict(v12_result.get("behavior_slice_ledger")).get("selected_slice_ids")
    )
    if total_slices and selected_slices and len(selected_slices) < total_slices:
        _bump("unselected_behavior_slices", total_slices - len(selected_slices))

    # Standalone discovery-engine health (when embedded on the result).
    discovery_health = _as_dict(v12_result.get("discovery_engine_health") or v12_result.get("stage_status"))
    for stage_name, status in discovery_health.items():
        if str(status or "") == "FAILED_SAFE":
            _bump(f"discovery_{stage_name}_FAILED_SAFE")
    for failure in _as_list(v12_result.get("stage_failures")):
        _bump(f"stage_failure:{str(failure)[:120]}")

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"reason": reason, "count": count} for reason, count in ranked]


def build_pipeline_health(v12_result: dict[str, Any]) -> dict[str, Any]:
    """Summarize whether empty findings mean 'no bugs' or 'pipeline failed safe'."""
    result = _as_dict(v12_result)
    phases = _as_dict(result.get("phases"))
    execution = _as_dict(phases.get("execution"))
    observability = [item for item in _as_list(execution.get("observability")) if isinstance(item, dict)]
    failed_obs = [
        item for item in observability
        if str(item.get("status") or "") in {"failed", "missing"}
    ]
    stage_status = _as_dict(result.get("stage_status") or result.get("discovery_engine_health"))
    failed_stages = {
        key: value for key, value in stage_status.items()
        if str(value or "") == "FAILED_SAFE"
    }
    stage_failures = [str(item) for item in _as_list(result.get("stage_failures")) if str(item).strip()]
    execution_failed_safe = str(execution.get("observability_status") or "") == "FAILED_SAFE"
    runtime_failed = str(result.get("runtime_status") or "").upper() in {"FAILED", "FAILED_SAFE"}
    funnel_error = str(result.get("discovery_funnel_error") or _as_dict(result.get("discovery_funnel")).get("error") or "")
    skip_telemetry = _as_dict(execution.get("skip_telemetry"))
    reason_counts = _as_dict(skip_telemetry.get("reason_counts"))
    binding_blocked = (
        _as_int(reason_counts.get("missing_runtime_path_binding"))
        + _as_int(reason_counts.get("precondition_not_met"))
        + len(_as_dict(skip_telemetry.get("path_binding_misses")))
    )
    incremental = _as_dict(phases.get("incremental_discovery"))
    stop_reason = str(
        incremental.get("stop_reason")
        or _as_dict(result.get("behavior_slice_ledger")).get("stop_reason")
        or ""
    )
    pending_slices = _as_list(
        incremental.get("pending_slice_ids")
        or _as_dict(result.get("behavior_slice_ledger")).get("pending_slice_ids")
    )
    unexecuted_candidates = bool(pending_slices) or stop_reason == "slice_budget_reached"
    status = "OK"
    if execution_failed_safe or failed_stages or stage_failures or runtime_failed:
        status = "FAILED_SAFE"
    elif str(execution.get("status") or "") in {"blocked", "skipped"} and _as_int(execution.get("executed")) == 0:
        status = "BLOCKED"
    elif binding_blocked and _as_int(execution.get("executed")) == 0:
        status = "FAILED_SAFE"
    elif unexecuted_candidates or binding_blocked or funnel_error:
        status = "DEGRADED"

    operator_note = str(result.get("operator_note") or "").strip()
    if status == "FAILED_SAFE" and not operator_note:
        if binding_blocked and _as_int(execution.get("executed")) == 0:
            operator_note = (
                "候选探针因 missing_runtime_path_binding / precondition_not_met 未形成运行时收据；"
                "空 findings 不能解读为「系统无缺陷」，请先补齐真实 ID / 路径绑定。"
            )
        else:
            operator_note = (
                "发现链路关键阶段失败或可观测性缺口：空 findings / 零缺陷不代表目标系统无缺陷，"
                "请先修复执行/账号/探针可观测性问题后再解读结果。"
            )
    elif status == "BLOCKED" and not operator_note:
        operator_note = (
            f"执行阶段未产出运行时收据（status={execution.get('status')}, "
            f"reason={execution.get('reason') or 'unknown'}）；不能把本轮解读为「系统无缺陷」。"
        )
    elif status == "DEGRADED" and not operator_note:
        notes = []
        if stop_reason == "slice_budget_reached":
            notes.append("本轮触及 slice_budget，仍有未执行行为切片")
        if pending_slices:
            notes.append(f"{len(pending_slices)} 个 pending 切片未尝试")
        if binding_blocked:
            notes.append("部分探针因路径绑定/前置条件未满足被跳过")
        if funnel_error:
            notes.append(f"漏斗聚合异常：{funnel_error[:120]}")
        operator_note = (
            "；".join(notes) + "。空 findings 仅表示本轮已执行子集未确认缺陷，不能外推为全量无缺陷。"
            if notes
            else "发现链路部分候选未执行；空 findings 不能外推为全量无缺陷。"
        )

    return {
        "status": status,
        "empty_findings_means_no_bugs": status == "OK",
        "execution_status": str(execution.get("status") or ""),
        "execution_reason": str(execution.get("reason") or ""),
        "observability_status": str(execution.get("observability_status") or ("ok" if observability and not failed_obs else "")),
        "observability_gaps": failed_obs[:12],
        "failed_stages": failed_stages,
        "stage_failures": stage_failures[:20],
        "unexecuted_candidate_signal": {
            "stop_reason": stop_reason,
            "pending_slice_count": len(pending_slices),
            "binding_or_precondition_blocks": binding_blocked,
            "skip_reason_counts": {k: reason_counts[k] for k in list(reason_counts)[:12]},
        },
        "operator_note": operator_note,
    }


def reconcile_product_pipeline_health(
    v12_health: dict[str, Any] | None,
    *,
    execution_status: str,
    preflight_diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Reconcile V12 health with product-level preflight/execution truth.

    V12 can have no execution phase when product preflight blocks the run. In
    that case its local funnel has insufficient context and must not report OK
    or interpret empty findings as evidence of no defects.
    """
    health = dict(v12_health or {})
    normalized_execution = str(execution_status or "not_executed").strip().lower()
    diagnostics = _as_dict(preflight_diagnostics)
    preflight_present = any(
        key in diagnostics for key in ("ready", "all_checks_passed", "errors", "checks")
    )
    preflight_failed = preflight_present and (
        diagnostics.get("ready") is False
        or diagnostics.get("all_checks_passed") is False
        or _as_int(diagnostics.get("errors")) > 0
    )
    execution_completed = normalized_execution in {"completed", "executed"}
    if not execution_completed:
        health["status"] = (
            "FAILED_SAFE" if str(health.get("status") or "").upper() == "FAILED_SAFE" else "BLOCKED"
        )
        health["empty_findings_means_no_bugs"] = False
        health["execution_status"] = normalized_execution
        health["execution_reason"] = (
            "preflight_not_ready" if preflight_failed else "execution_not_completed"
        )
        health["operator_note"] = (
            "产品级执行未产生完整运行时收据；空 findings 不能解释为目标无缺陷。"
            f" execution_status={normalized_execution}, "
            f"preflight_errors={_as_int(diagnostics.get('errors'))}."
        )
    elif preflight_failed:
        health["status"] = "DEGRADED"
        health["empty_findings_means_no_bugs"] = False
        health["execution_status"] = normalized_execution
        health["execution_reason"] = "preflight_health_failed"
        health["operator_note"] = (
            "执行虽已返回，但产品级 preflight 未通过；结果覆盖不完整，"
            "空 findings 不能解释为目标无缺陷。"
        )
    health["preflight"] = {
        "present": preflight_present,
        "ready": diagnostics.get("ready"),
        "all_checks_passed": diagnostics.get("all_checks_passed"),
        "errors": _as_int(diagnostics.get("errors")),
        "warnings": _as_int(diagnostics.get("warnings")),
    }
    return health


def _build_explanation(
    *,
    validated_bug_count: int,
    pending_finding_count: int,
    candidate_count: int,
    stages: list[dict[str, Any]],
    top_blocking_reasons: list[dict[str, Any]],
    unify: dict[str, Any],
    pipeline_health: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    health = _as_dict(pipeline_health)
    health_status = str(health.get("status") or "OK")
    if health_status == "FAILED_SAFE":
        parts.append(
            str(health.get("operator_note") or "")
            or "发现链路 FAILED_SAFE：空结果不能解读为「系统无缺陷」。"
        )
    elif health_status == "BLOCKED":
        parts.append(
            str(health.get("operator_note") or "")
            or "执行阶段被阻断，本轮未形成可验证运行时证据。"
        )

    gen = next((s for s in stages if s["name"] == "candidate_generation"), None)
    sel = next((s for s in stages if s["name"] == "probe_selection"), None)
    exe = next((s for s in stages if s["name"] == "execution"), None)
    ver = next((s for s in stages if s["name"] == "verification"), None)
    acc = next((s for s in stages if s["name"] == "formal_accounting"), None)

    if validated_bug_count == 0:
        if health_status in {"FAILED_SAFE", "BLOCKED"}:
            parts.append(
                f"本轮正式记账的已验证 Bug 为 0（候选 {candidate_count}，待确认发现 {pending_finding_count}），"
                f"但链路状态为 {health_status}，不能据此宣称目标系统无缺陷。"
            )
        else:
            parts.append(f"本轮正式记账的已验证 Bug 为 0（候选 {candidate_count}，待确认发现 {pending_finding_count}）。")
    else:
        parts.append(
            f"本轮已验证 Bug {validated_bug_count}；待确认发现 {pending_finding_count}（不计入客户可见已验证 Bug）；候选信号 {candidate_count}。"
        )

    if gen and sel and gen["input"] > 0 and sel["output"] < gen["output"]:
        parts.append(
            f"候选生成 {gen['output']} 条切片，入选执行 {sel['output']} 条（预算择优，未入选 {sel['dropped']}）。"
        )
    if exe and exe["input"] > 0 and exe["dropped"] > 0:
        parts.append(f"执行阶段输入 {exe['input']}，成功产出可验证轨迹 {exe['output']}，损耗 {exe['dropped']}。")
    if ver and ver["input"] > 0:
        parts.append(f"验证阶段评估 {ver['input']}，检出违规/发现 {ver['output']}。")
    if acc and pending_finding_count > 0 and validated_bug_count < pending_finding_count:
        parts.append(
            f"{pending_finding_count} 条语义/候选发现因证据链不全未计入正式 Bug。"
        )

    top = top_blocking_reasons[:3]
    if top:
        reason_text = "、".join(f"{item['reason']}×{item['count']}" for item in top)
        parts.append(f"Top 阻断原因：{reason_text}。")

    suggestions: list[str] = []
    reason_keys = {str(item.get("reason") or "") for item in top_blocking_reasons}
    if any(key in reason_keys for key in ("BEFORE_SNAPSHOT_MISSING", "AFTER_SNAPSHOT_MISSING", "CLEANUP_PENDING")):
        suggestions.append("建议开启沙箱写探针（QUALIBUG_ENABLE_SANDBOX_WRITE=1）并确认测试环境标记，以补齐写操作 before/after/cleanup 证据链")
    if "dropped_no_endpoint" in reason_keys:
        suggestions.append("部分假设无法绑定 API 文档中的真实 endpoint，请检查 OpenAPI/API 文档是否完整")
    if "llm_provider_unavailable" in reason_keys or str(_as_dict(unify.get("llm_reasoner")).get("status") or "") == "provider_unavailable":
        suggestions.append("LLM Reasoner 当前不可用（provider offline）；配置并健康检查通过后再开 QUALIBUG_UNIFY_LLM_REASONER=1")
    if "production_data_blocked" in reason_keys:
        suggestions.append("部分探针命中生产数据排除规则被拦截，请核对 production_data_exclusion 与测试环境范围")
    if "execution_observability_gap" in reason_keys or any(key.startswith("observability_") for key in reason_keys):
        suggestions.append("多角色账号/禁用账号探针可观测性失败，请检查 platform_inputs/<project>/test_accounts.json 与登录探针配置")
    if "missing_runtime_path_binding" in reason_keys or "precondition_not_met" in reason_keys:
        suggestions.append("部分探针缺少真实路径/ID 绑定或前置条件未满足，请补齐 OpenAPI 可列表端点与多角色测试账号后再解读零缺陷")
    if "slice_budget_reached" in reason_keys or "unattempted_behavior_slices" in reason_keys or "unselected_behavior_slices" in reason_keys:
        suggestions.append("本轮切片预算未覆盖全部候选，请提高 QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND / 轮次或继续增量发现后再宣称全量无缺陷")
    if any(key.startswith("discovery_") and key.endswith("FAILED_SAFE") for key in reason_keys) or any(key.startswith("stage_failure:") for key in reason_keys):
        suggestions.append("发现引擎关键阶段 FAILED_SAFE，请查看 stage_failures / pipeline_health 后再解读零缺陷结果")
    if any("runtime" in key.lower() or "approval" in key.lower() or key in {"no_base_url", "execution_blocked"} for key in reason_keys):
        suggestions.append("检查 runtime_contract / base_url / 执行批准是否已批准，否则执行阶段会 plan_only 或 blocked")
    if suggestions:
        parts.append("下一步：" + "；".join(suggestions) + "。")
    elif validated_bug_count == 0 and not top and health_status == "OK":
        parts.append("漏斗各阶段未见明确阻断码；请核对是否有可执行切片入选，以及目标系统是否可达。")
    elif validated_bug_count == 0 and health_status == "DEGRADED":
        parts.append(str(health.get("operator_note") or "发现链路存在未执行候选，不能把本轮零缺陷外推为全量无缺陷。"))

    return "".join(parts)


def build_funnel(v12_result: dict[str, Any], gate_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate five-stage discovery funnel from a v12 pipeline result."""
    result = _as_dict(v12_result)
    phases = _as_dict(result.get("phases"))
    ledger = _as_dict(result.get("behavior_slice_ledger"))
    selection = _as_dict(phases.get("incremental_discovery"))
    execution = _as_dict(phases.get("execution"))
    oracle = _as_dict(phases.get("oracle"))
    unify = _as_dict(result.get("mainline_unification"))
    pipeline_health = build_pipeline_health(result)

    total_slices = _as_int(ledger.get("total_slices"))
    if total_slices <= 0:
        behavior_slices = _as_list(result.get("behavior_slices"))
        if behavior_slices:
            total_slices = len(behavior_slices)
    if total_slices <= 0:
        slices = _as_list(_as_dict(result.get("behavior_contract")).get("slices"))
        if not slices:
            summary = _as_dict(_as_dict(result.get("behavior_contract")).get("summary"))
            if not summary:
                summary = _as_dict(_as_dict(phases.get("state_graph")).get("behavior_slices"))
            total_slices = _as_int(summary.get("total_slices"))
        else:
            total_slices = len(slices)
    if total_slices <= 0:
        total_slices = _as_int(selection.get("total_slices")) or len(_as_list(selection.get("selected_slice_ids")))

    selected_ids = _as_list(selection.get("selected_slice_ids") or ledger.get("selected_slice_ids"))
    selected_count = len([sid for sid in selected_ids if str(sid).strip()])
    if selected_count <= 0 and isinstance(selection.get("selected"), list):
        selected_count = len(selection["selected"])
    if selected_count <= 0 and isinstance(selection.get("selected_slices"), list):
        selected_count = len(selection["selected_slices"])

    executed = _as_int(execution.get("executed"))
    failed = _as_int(execution.get("failed"))
    planned_only = _as_int(execution.get("planned_only"))
    # Successful execution traces available for verification
    execution_output = max(0, executed) if str(execution.get("status") or "") in {"completed", "partial", ""} else 0
    if str(execution.get("status") or "") == "completed" and executed == 0 and planned_only > 0:
        execution_output = 0

    total_evaluated = _as_int(oracle.get("total_evaluated"), execution_output)
    violations_found = _as_int(oracle.get("violations_found"))

    findings = [item for item in _as_list(result.get("findings")) if isinstance(item, dict)]
    validated_bug_count = sum(1 for item in findings if _is_validated_bug(item))
    pending_finding_count = sum(1 for item in findings if _is_pending_finding(item))
    candidate_count = max(len(findings), violations_found, total_slices)

    # Five stages: generation → selection → execution → verification → accounting
    gen_input = max(total_slices, selected_count)
    gen_output = total_slices if total_slices > 0 else selected_count
    # Include unified bound slices in generation observability when present
    unified_bound = sum(_as_int(_as_dict(v).get("bound")) for k, v in unify.items() if k != "error" and isinstance(v, dict))
    if unified_bound and total_slices > 0:
        gen_input = max(gen_input, total_slices)

    stages = [
        _stage("candidate_generation", max(gen_input, gen_output), gen_output),
        _stage("probe_selection", gen_output if gen_output > 0 else selected_count, selected_count),
        _stage("execution", max(selected_count, executed + failed + planned_only, execution_output), execution_output),
        _stage("verification", max(total_evaluated, execution_output), max(violations_found, len(findings))),
        _stage(
            "formal_accounting",
            max(len(findings), violations_found, validated_bug_count + pending_finding_count),
            validated_bug_count,
        ),
    ]

    # Ensure stage name order and presence
    by_name = {s["name"]: s for s in stages}
    stages = [by_name[name] for name in _STAGE_NAMES]

    top_blocking_reasons = _collect_blocking_reasons(result, gate_results)
    explanation = _build_explanation(
        validated_bug_count=validated_bug_count,
        pending_finding_count=pending_finding_count,
        candidate_count=candidate_count,
        stages=stages,
        top_blocking_reasons=top_blocking_reasons,
        unify=unify,
        pipeline_health=pipeline_health,
    )

    return {
        "stages": stages,
        "top_blocking_reasons": top_blocking_reasons,
        "validated_bug_count": validated_bug_count,
        "pending_finding_count": pending_finding_count,
        "candidate_count": candidate_count,
        "explanation": explanation,
        "pipeline_health": pipeline_health,
        "mainline_unification": {
            key: value for key, value in unify.items() if key != "error" or value
        } if unify else {},
    }
