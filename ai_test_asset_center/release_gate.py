"""Evidence-first release gate for enterprise Campaign outcomes.

The gate deliberately prefers ``not_ready`` over an optimistic pass. It never
creates findings and it does not reinterpret candidates as confirmed defects.
"""
from __future__ import annotations

from typing import Any


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _confirmed_findings(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        if _text(item.get("confirmation_status")).lower() == "confirmed":
            results.append(item)
    return results


# Customer-facing copy for every receipt-backed readiness blocker.
#
# Derived from the emitting code in
# discovery_quality_projection.build_run_delivery_readiness_projection, not from a
# guess: tests/test_release_gate_checks.py asserts every literal code appended there
# has an entry here, so the two cannot drift.
#
# Industry-neutral by contract: each line describes verification state, never a domain
# entity. None of them may imply a clean target.
_READINESS_CHECK_COPY: dict[str, tuple[str, str]] = {
    "PIPELINE_HEALTH_MISSING": (
        "执行管线健康度",
        "缺少管线健康度回执，无法判断本次运行是否可信。",
    ),
    "ATTEMPT_LEDGER_MISSING": (
        "义务尝试台账",
        "缺少义务尝试台账，本次运行的完成情况无法核对。",
    ),
    "ATTEMPT_LEDGER_INCOMPLETE": (
        "义务尝试台账",
        "义务尝试台账不完整，存在没有终态回执的义务。",
    ),
    "ATTEMPT_LEDGER_IDENTITY_MISSING": (
        "义务尝试台账身份",
        "台账缺少运行身份绑定，无法证明这些尝试属于本次运行。",
    ),
    "MAINLINE_RUN_MISSING": (
        "发现主线运行合同",
        "缺少主线运行合同，本次运行的结论无法绑定到可验证的权威。",
    ),
    "MAINLINE_AUTHORITY_IDENTITY_MISSING": (
        "发现主线权威身份",
        "主线权威身份或合同指纹缺失，运行结论不可追溯。",
    ),
    "ZERO_SELECTED_OBLIGATIONS": (
        "测试义务选取",
        "本次运行没有选中任何测试义务；空结果不代表目标无缺陷。",
    ),
    "ALL_OBLIGATIONS_BLOCKED": (
        "测试义务可执行性",
        "全部测试义务被阻断，没有任何行为被真实验证；空结果不代表目标无缺陷。",
    ),
    "NO_REAL_EXECUTION": (
        "真实执行证据",
        "没有产生任何目标请求回执，本次运行没有真实执行证据。",
    ),
    "PARTIAL_OBLIGATION_EXECUTION": (
        "测试义务执行完整性",
        "仅部分测试义务完成执行，未执行部分的行为空间没有被验证。",
    ),
    "COVERAGE_GAPS_REMAIN": (
        "行为空间覆盖缺口",
        "仍存在未闭合的覆盖缺口，缺口范围内的缺陷既未证实也未排除。",
    ),
    "CLEANUP_FAILURE": (
        "受治理写入清理",
        "存在清理失败，目标环境可能残留测试数据。",
    ),
    "FORMAL_COUNT_PROJECTION_MISSING": (
        "正式交付计数投射",
        "缺少正式交付计数投射，可交付缺陷数量无法核对。",
    ),
    "FORMAL_DELIVERY_AUTHORITY_NOT_VERIFIED": (
        "交付权威可验证性",
        "正式交付权威无法重新推导，本次运行不能证明任何可交付缺陷。",
    ),
    "CUSTOMER_OUTPUTS_NOT_PUBLISHED": (
        "客户输出发布状态",
        "本次运行的客户输出未发布（回放 / 影子运行不对外发布）。",
    ),
}

# Pipeline reason codes are generated as f"PIPELINE_{status}", so the tail is
# open-ended and cannot be enumerated. Handled by prefix rather than defaulted, so a
# new pipeline status still gets a named check instead of a raw code.
_PIPELINE_CODE_PREFIX = "PIPELINE_"


def _readiness_check_copy(code: str) -> tuple[str, str]:
    """Named copy for a reason code, or an explicit unknown -- never a silent generic."""
    if code in _READINESS_CHECK_COPY:
        return _READINESS_CHECK_COPY[code]
    if code.startswith(_PIPELINE_CODE_PREFIX):
        state = code[len(_PIPELINE_CODE_PREFIX):].replace("_", " ").lower() or "unknown"
        return (
            "执行管线健康度",
            f"发现管线状态为 {state}，本次运行的结论不可作为发布依据。",
        )
    return (
        code,
        "该项阻断由发现运行回执判定；本条尚无产品化说明文案，详见 reason_codes。",
    )


def _readiness_check_rows(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the receipt-backed readiness verdict into explicit, named checks.

    Every row is derived from run_delivery_readiness, never from finding counts. That
    distinction matters: the browser used to synthesize five checks from the finding
    list, so a run that published nothing rendered "无 P0 缺陷 ✓" and a permanently
    green "DB 验证" row driven by a key the backend never emits. An unmeasured check
    is ``pending`` here, never ``pass`` — absence of evidence is not evidence of
    absence.
    """

    rows: list[dict[str, Any]] = []
    status = _text(readiness.get("status")).upper()
    release_ready = bool(readiness.get("release_ready"))

    for code in _list(readiness.get("reason_codes")):
        normalized = _text(code)
        if not normalized:
            continue
        label, detail = _readiness_check_copy(normalized)
        rows.append({
            "name": label,
            "status": "fail",
            "detail": detail,
            "code": normalized,
            "source": "run_delivery_readiness",
        })

    executed = readiness.get("executed_obligation_count")
    selected = readiness.get("selected_obligation_count")
    if isinstance(executed, int) and isinstance(selected, int) and selected > 0:
        rows.append({
            "name": "测试义务执行率",
            "status": "pass" if executed >= selected else "pending",
            "detail": f"{executed}/{selected} 个测试义务完成终态执行。",
            "code": "OBLIGATION_EXECUTION_RATIO",
            "source": "run_delivery_readiness",
        })

    cleanup_failures = readiness.get("cleanup_failure_count")
    if isinstance(cleanup_failures, int):
        rows.append({
            "name": "受治理写入清理",
            "status": "pass" if cleanup_failures == 0 else "fail",
            "detail": (
                "所有受治理写入均已产出清理回执。"
                if cleanup_failures == 0
                else f"{cleanup_failures} 项清理失败，目标环境可能残留测试数据。"
            ),
            "code": "CLEANUP_RECEIPTS",
            "source": "run_delivery_readiness",
        })

    published = readiness.get("published_formal_deliverable_count")
    eligible = readiness.get("eligible_formal_deliverable_count")
    if release_ready:
        publication_detail = (
            f"已发布 {published} 个正式可交付缺陷。"
            if isinstance(published, int)
            else "本次运行的正式交付结论已发布。"
        )
    else:
        publication_detail = (
            f"发布被阻断：{eligible} 个缺陷通过交付门禁但未发布，"
            "空缺陷列表不代表目标无缺陷。"
            if isinstance(eligible, int) and eligible > 0
            else "发布被阻断，本次运行未产出可发布的正式交付结论。"
        )
    rows.append({
        "name": "正式交付发布决定",
        "status": "pass" if release_ready else ("fail" if status == "BLOCKED" else "pending"),
        "detail": publication_detail,
        "code": "FORMAL_PUBLICATION_DECISION",
        "source": "run_delivery_readiness",
    })
    return rows


def reconcile_release_gate_with_run_readiness(
    release_gate: dict[str, Any] | None,
    run_delivery_readiness: dict[str, Any],
) -> dict[str, Any]:
    """Qualify the legacy gate with canonical current-run publication readiness."""

    gate = dict(_record(release_gate))
    readiness = _record(run_delivery_readiness)
    if readiness.get("schema_version") != "qualibug.run-delivery-readiness.v1":
        raise ValueError("run_delivery_readiness_schema_invalid")
    release_ready = readiness.get("release_ready")
    if not isinstance(release_ready, bool):
        raise ValueError("run_delivery_readiness_release_ready_invalid")
    status = _text(readiness.get("status")).upper()
    if release_ready != (status == "READY"):
        identities = _record(readiness.get("identities"))
        raise ValueError(
            "run_delivery_readiness_contradiction:"
            f"campaign={_text(identities.get('campaign_id'))}:"
            f"run={_text(identities.get('run_id'))}:status={status}:"
            f"release_ready={release_ready}"
        )

    previous = {
        "verdict": _text(gate.get("verdict")) or "not_ready",
        "status": _text(gate.get("status")) or "inconclusive",
    }
    reasons = [dict(item) for item in _list(gate.get("reasons")) if isinstance(item, dict)]
    existing_codes = {_text(item.get("code")) for item in reasons}
    for code in _list(readiness.get("reason_codes")):
        normalized = _text(code)
        if normalized and normalized not in existing_codes:
            reasons.append({
                "code": normalized,
                "detail": "current_run_formal_finding_publication_not_ready",
                "source": "run_delivery_readiness",
            })
            existing_codes.add(normalized)

    if not release_ready:
        if previous["verdict"] == "fail":
            gate["verdict"] = "fail"
            gate["status"] = "blocked"
        else:
            gate["verdict"] = "not_ready"
            gate["status"] = "inconclusive"
    else:
        gate.setdefault("verdict", previous["verdict"])
        gate.setdefault("status", previous["status"])
    # Authoritative, named checks. Emitted so no consumer has to invent a verdict:
    # frontend/src/api/data.ts:382-392 synthesized five checks from the finding list
    # and unconditionally merged them over whatever the backend supplied, which meant
    # a run that published nothing rendered "无 P0 缺陷 ✓" plus a permanently green
    # "DB 验证" row fed by data.db_verification -- a key the backend never emits.
    existing_checks = [
        dict(item) for item in _list(gate.get("checks")) if isinstance(item, dict)
    ]
    existing_names = {_text(item.get("name")) for item in existing_checks}
    checks = list(existing_checks)
    for row in _readiness_check_rows(readiness):
        if _text(row.get("name")) not in existing_names:
            checks.append(row)
            existing_names.add(_text(row.get("name")))

    fail_count = sum(1 for item in checks if _text(item.get("status")).lower() == "fail")
    pending_count = sum(1 for item in checks if _text(item.get("status")).lower() == "pending")
    pass_count = sum(1 for item in checks if _text(item.get("status")).lower() == "pass")
    if fail_count:
        overall_status = "fail"
    elif pending_count or not checks:
        # No checks at all is "pending", never "pass". An empty check list must not
        # read as a clean release.
        overall_status = "pending"
    else:
        overall_status = "pass"

    gate.update({
        "schema_version": _text(gate.get("schema_version")) or "qualibug-release-gate-v1",
        "scope": "current_run_formal_finding_publication",
        "release_ready": release_ready and gate["verdict"] == "pass",
        "eligibility_status": status,
        "pre_reconciliation": previous,
        "run_delivery_readiness_schema_version": readiness["schema_version"],
        "reasons": reasons,
        "identities": dict(_record(readiness.get("identities"))),
        "checks": checks,
        "overall_status": overall_status,
        "has_decision": True,
        "blocking_check_count": fail_count,
        "pending_check_count": pending_count,
        "pass_check_count": pass_count,
        # The gate decides publication readiness from receipts. It never claims
        # external quality measurement, which stays NOT_MEASURED until an evaluator
        # receipt exists.
        "measurement_status": "NOT_MEASURED",
    })
    return gate


def evaluate_release_gate(
    *,
    campaign: dict[str, Any] | None,
    execution_status: str,
    runtime_contract: dict[str, Any] | None,
    evidence_bundle: dict[str, Any] | None,
    evidence_bundle_verification: dict[str, Any] | None,
    test_data_plan: dict[str, Any] | None,
    findings: list[dict[str, Any]] | None,
    coverage_gaps: list[dict[str, Any]] | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an auditable release decision from actual execution artifacts.

    A plan-only run is incomplete rather than failed. A blocked contract, a
    deferred/blocked Campaign, or a confirmed P0 finding is an explicit block.
    """
    campaign_value = _record(campaign)
    runtime = _record(runtime_contract)
    bundle = _record(evidence_bundle)
    bundle_verification = _record(evidence_bundle_verification)
    data_plan = _record(test_data_plan)
    gate_policy = _record(policy)
    reasons: list[dict[str, str]] = []
    campaign_status = _text(campaign_value.get("campaign_status")).lower()
    execution = _text(execution_status).lower()
    runtime_status = _text(runtime.get("status")).lower()
    data_status = _text(data_plan.get("status")).lower()
    bundle_status = _text(bundle.get("status")).lower()
    bundle_valid = bundle_verification.get("valid") is True
    allow_p1 = gate_policy.get("allow_confirmed_p1") is True

    if campaign_status in {"blocked", "coverage_deferred"}:
        reasons.append({"code": "CAMPAIGN_NOT_CLOSED", "detail": _text(campaign_value.get("coverage_deferred_reason")) or campaign_status})
    elif campaign_status != "completed":
        reasons.append({"code": "CAMPAIGN_NOT_COMPLETED", "detail": campaign_status or "campaign_status_missing"})
    if execution != "completed":
        reasons.append({"code": "RUNTIME_EXECUTION_NOT_COMPLETED", "detail": execution or "execution_status_missing"})
    if runtime_status != "approved":
        reasons.append({"code": "RUNTIME_CONTRACT_NOT_APPROVED", "detail": runtime_status or "runtime_contract_missing"})
    if data_status != "ready":
        reasons.append({"code": "TEST_DATA_NOT_READY", "detail": data_status or "test_data_plan_missing"})
    if bundle_status != "persisted" or not bundle_valid:
        reasons.append({"code": "EVIDENCE_BUNDLE_NOT_VERIFIED", "detail": _text(bundle_verification.get("code")) or bundle_status or "evidence_bundle_missing"})
    if _list(coverage_gaps):
        reasons.append({"code": "COVERAGE_GAPS_REMAIN", "detail": str(len(_list(coverage_gaps)))})

    confirmed = _confirmed_findings(findings)
    p0 = [item for item in confirmed if _text(item.get("severity")).upper() == "P0"]
    p1 = [item for item in confirmed if _text(item.get("severity")).upper() == "P1"]
    if p0:
        reasons.append({"code": "CONFIRMED_P0_FINDINGS", "detail": str(len(p0))})
    if p1 and not allow_p1:
        reasons.append({"code": "CONFIRMED_P1_FINDINGS", "detail": str(len(p1))})

    # ═══════════════════════════════════════════════════════════════════
    # P3-20: Coverage gap severity analysis — release blocking risk
    # ═══════════════════════════════════════════════════════════════════
    # Even without confirmed findings, if the campaign has high-severity
    # coverage gaps (P0/P1 paths not probed), the release should be blocked
    # because we simply don't know if those paths are safe.
    _HIGH_RISK_GAP_KINDS = {
        "permission", "isolation", "money", "concurrency",
        "authorization", "tenant_isolation", "payment", "idempotency",
        "privilege_escalation", "security_boundary",
    }
    high_risk_gaps: list[dict[str, Any]] = []
    total_gaps = 0
    for gap in _list(coverage_gaps):
        total_gaps += 1
        gap_kind = str(gap.get("kind") or gap.get("code") or "").lower()
        if any(risk in gap_kind for risk in _HIGH_RISK_GAP_KINDS):
            high_risk_gaps.append(dict(gap))

    # P3-20: Block release when high-risk paths have zero coverage
    if high_risk_gaps:
        gap_labels = [str(g.get("kind") or g.get("code") or "unknown") for g in high_risk_gaps[:5]]
        reasons.append({
            "code": "HIGH_RISK_COVERAGE_GAPS",
            "detail": f"{len(high_risk_gaps)} high-risk path(s) not probed: {', '.join(gap_labels[:3])}",
            "p3_20_risk": True,
        })

    campaign_not_closed_blocks = gate_policy.get("campaign_not_closed_verdict") != "not_ready"
    hard_block = any(
        reason["code"] == "CONFIRMED_P0_FINDINGS"
        or reason["code"] == "HIGH_RISK_COVERAGE_GAPS"
        or (campaign_not_closed_blocks and reason["code"] == "CAMPAIGN_NOT_CLOSED")
        for reason in reasons
    ) or runtime_status == "blocked"
    if not reasons:
        verdict, status = "pass", "release_ready"
    elif hard_block:
        verdict, status = "fail", "blocked"
    else:
        verdict, status = "not_ready", "inconclusive"
    return {
        "schema_version": "qualibug-release-gate-v1",
        "verdict": verdict,
        "status": status,
        "campaign_id": _text(campaign_value.get("campaign_id")),
        "campaign_status": campaign_status,
        "execution_status": execution,
        "runtime_contract_status": runtime_status,
        "confirmed_finding_count": len(confirmed),
        "confirmed_p0_count": len(p0),
        "confirmed_p1_count": len(p1),
        "evidence_bundle_id": _text(bundle.get("bundle_id")),
        "evidence_bundle_verified": bundle_valid,
        "reasons": reasons,
        "p3_20_coverage_risk": {
            "high_risk_gap_count": len(high_risk_gaps),
            "total_gap_count": total_gaps,
            "release_blocked_by_gaps": bool(high_risk_gaps),
        } if coverage_gaps else None,
    }
