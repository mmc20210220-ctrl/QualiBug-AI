from __future__ import annotations

"""Backend customer-delivery gate for commercial defect promotion.

This module is intentionally stricter than discovery/runtime verdicts. A finding
is allowed into the customer-facing defect list only when the business evidence
contract is explicit, replayable, and free of missing requirements. Everything
else remains an internal clue until more evidence is collected.
"""

from typing import Any
import copy

CUSTOMER_READY_MIN_EVIDENCE_SCORE = 90
_ALLOWED_FINAL_REVIEW_STATUSES = {"PENDING_REVIEW", "VALIDATED_CANDIDATE", "CUSTOMER_READY"}
_BLOCKED_LANE_MARKERS = {
    "route_blocked",
    "auth_blocked",
    "environment_blocked",
    "coverage_gap",
    "validation_lead",
    "not_reproduced",
}
_SYNTHETIC_MARKERS = {"simulation", "simulated", "demo", "synthetic", "mock"}
_MAINLINE_IDENTITY_FIELDS = (
    "candidate_id",
    "slice_id",
    "obligation_id",
    "experiment_id",
    "execution_id",
    "evidence_id",
    "finding_id",
)

REJECTION_REASON_EXPLANATIONS: dict[str, dict[str, str]] = {
    "INVALID_FINDING_PAYLOAD": {
        "label": "结果结构异常",
        "detail": "当前 finding 不是合法对象，无法进入客户交付。",
        "next_action": "检查上游结果格式化与序列化流程。",
    },
    "NOT_MARKED_FOR_DEFECT_DELIVERY": {
        "label": "未标记为客户缺陷轨道",
        "detail": "当前结果仍属于内部线索或其他轨道。",
        "next_action": "完成补证并重新通过客户交付 Gate。",
    },
    "BUG_STATUS_NOT_REPRODUCED": {
        "label": "缺陷状态不是已复现",
        "detail": "没有达到 reproduced 状态，不能对客户声称为 Bug。",
        "next_action": "补跑复现步骤并记录请求、响应、断言与时间戳。",
    },
    "GATE_NOT_PASSED": {
        "label": "证据门控未通过",
        "detail": "上游 Gate 未确认该结果具备可交付证据。",
        "next_action": "查看 gate_failures 或 evidence_status，补齐缺失证据。",
    },
    "IDENTITY_CHAIN_INCOMPLETE": {
        "label": "主链身份不完整",
        "detail": "finding 没有完整关联 candidate、slice、obligation、experiment、execution 和 evidence 身份。",
        "next_action": "让候选统一经过 Experiment Executor 并持久化全链路身份后重新评估。",
    },
    "SYNTHETIC_OR_DEMO_EVIDENCE": {
        "label": "证据来源不真实",
        "detail": "结果包含模拟、演示、mock 或 synthetic 信号。",
        "next_action": "在客户测试环境中重新执行真实请求或浏览器复现。",
    },
    "NOT_EXECUTED": {
        "label": "尚未真实执行",
        "detail": "当前只有计划、候选或线索，没有真实运行时执行结果。",
        "next_action": "执行对应 API/页面路径，并保存状态码、响应体和执行时间。",
    },
    "NOT_CONFIRMED": {
        "label": "尚未形成确认结论",
        "detail": "当前仍需人工或二次验证确认。",
        "next_action": "完成语义验证和业务证据验证后再提交客户页。",
    },
    "EVIDENCE_CONSISTENCY_REJECTED": {
        "label": "声明与证据不一致",
        "detail": "当前证据不能支撑缺陷声明，或证据已被判定缺失。",
        "next_action": "重新绑定 finding 与真实请求响应，确保方法、路径、实体和断言一致。",
    },
    "EVIDENCE_QUALITY_NOT_VALIDATED": {
        "label": "证据质量未达标",
        "detail": "证据质量不是 validated，或分数低于客户交付阈值。",
        "next_action": "补齐文档来源、真实响应、断言、日志、DB 快照或复现资产。",
    },
    "BUSINESS_EVIDENCE_NOT_VALIDATED": {
        "label": "业务证据未验证通过",
        "detail": "语义结论、业务证据状态、最终评审状态或 missing requirements 未达标。",
        "next_action": "补齐 before/after、业务实体绑定、规则来源和复现流。",
    },
    "MISSING_REAL_REPLAY_ASSET": {
        "label": "缺少真实复现资产",
        "detail": "没有可回放的真实方法、路径和 HAR/响应证据。",
        "next_action": "生成真实 curl/HAR/Playwright 复现资产并绑定到该 finding。",
    },
    "MISSING_CUSTOMER_FACING_HARD_EVIDENCE": {
        "label": "缺少客户可核验证据",
        "detail": "请求、响应、失败断言、时间戳或真实证据标记不完整。",
        "next_action": "补齐 request_raw、response_raw、expected/actual 断言和 timestamp。",
    },
    "CLEANUP_NOT_SUCCEEDED": {
        "label": "写探测清理未成功",
        "detail": "受治理写探测声明了 cleanup，但清理没有成功完成，当前结果不能进入客户缺陷清单。",
        "next_action": "恢复测试环境、完成补偿清理并生成新的 cleanup 审计收据后重新评估。",
    },
    "CLEANUP_EVIDENCE_MISSING": {
        "label": "缺少写探测清理证据",
        "detail": "结果来自写方法，但证据链没有声明 cleanup 结果或显式只读语义，不能证明受治理写生命周期完整。",
        "next_action": "由 sandbox executor 绑定 cleanup 状态、收据和审计记录；只读 POST 必须显式标记 read_only。",
    },
    "CLEANUP_RECEIPT_MISSING": {
        "label": "缺少清理审计收据",
        "detail": "写探测 cleanup 已声明完成，但没有绑定不可变 cleanup receipt。",
        "next_action": "补齐 sandbox executor cleanup receipt，并将其绑定到 finding 证据链。",
    },
    "BLOCKED_AUTH_BLOCKED": {
        "label": "认证或权限配置阻断",
        "detail": "当前响应只能说明认证/权限配置阻断，不能证明业务缺陷。",
        "next_action": "补充真实测试账号、角色和 token 后重新执行。",
    },
    "BLOCKED_ROUTE_BLOCKED": {
        "label": "路由或网关阻断",
        "detail": "请求没有到达目标业务接口，不能证明当前业务缺陷。",
        "next_action": "检查服务映射、网关路由和目标 base URL。",
    },
    "BLOCKED_ENVIRONMENT_BLOCKED": {
        "label": "环境不可达或被拦截",
        "detail": "目标环境阻断了复现动作，不能作为已复现缺陷。",
        "next_action": "修复网络、白名单、测试环境地址或服务启动状态。",
    },
    "BLOCKED_COVERAGE_GAP": {
        "label": "覆盖缺口",
        "detail": "当前只是覆盖不足或待执行路径，不是已复现缺陷。",
        "next_action": "补跑对应场景并沉淀真实运行时证据。",
    },
    "BLOCKED_NOT_REPRODUCED": {
        "label": "未复现",
        "detail": "已执行但没有触发可证明的异常。",
        "next_action": "调整测试数据、账号、状态前置条件后定向复测。",
    },
    "BLOCKED_VALIDATION_LEAD": {
        "label": "仍是验证线索",
        "detail": "当前可以作为内部验证线索，但不能进入客户缺陷清单。",
        "next_action": "按缺失证据列表继续补证。",
    },
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if parsed == parsed else default


def has_validated_evidence_quality(item: dict[str, Any]) -> bool:
    quality = _dict(item.get("evidence_quality"))
    return (
        _lower(quality.get("level")) == "validated"
        and _number(quality.get("score")) >= CUSTOMER_READY_MIN_EVIDENCE_SCORE
        and bool(quality.get("can_reproduce"))
    )


def has_passed_business_evidence_status(item: dict[str, Any]) -> bool:
    status = _dict(item.get("evidence_status"))
    if not status:
        return False
    if _upper(status.get("semantic_verdict")) != "SEMANTIC_CONFIRMED":
        return False
    if _upper(status.get("business_evidence_status")) != "VALIDATED":
        return False
    if _upper(status.get("final_review_status")) not in _ALLOWED_FINAL_REVIEW_STATUSES:
        return False
    return len(_list(status.get("missing_requirements"))) == 0


def has_complete_mainline_identity(item: dict[str, Any]) -> bool:
    return all(_text(item.get(field)) for field in _MAINLINE_IDENTITY_FIELDS)


def has_legacy_runtime_identity(item: dict[str, Any]) -> bool:
    """Accept legacy V12 runtime findings when their evidence chain is auditable."""

    if not _text(item.get("evidence_id")):
        return False
    if not _text(item.get("source") or item.get("origin")):
        return False
    raw_evidence = _dict(item.get("raw_evidence"))
    if not raw_evidence.get("has_real_evidence"):
        return False
    if not _text(raw_evidence.get("timestamp") or item.get("timestamp")):
        return False
    request_raw = _dict(raw_evidence.get("request_raw"))
    response_raw = _dict(raw_evidence.get("response_raw"))
    if not (_text(request_raw.get("method")) and _text(request_raw.get("path"))):
        return False
    db_snapshot = _dict(raw_evidence.get("db_snapshot"))
    if not (
        response_raw.get("status_code")
        or response_raw.get("body")
        or db_snapshot.get("status") == "captured"
    ):
        return False
    execution_trace = _dict(raw_evidence.get("execution_trace"))
    if execution_trace and _text(execution_trace.get("evidence_id")):
        return _text(execution_trace.get("evidence_id")) == _text(item.get("evidence_id"))
    return True


def has_traceable_delivery_identity(item: dict[str, Any]) -> bool:
    return has_complete_mainline_identity(item) or has_legacy_runtime_identity(item)


def has_explicit_failure_assertion(item: dict[str, Any]) -> bool:
    if _list(item.get("failed_assertions")):
        return True
    comparison = _dict(item.get("expected_actual_comparison"))
    if _text(comparison.get("difference")):
        return True
    expected = _text(item.get("expected") or comparison.get("expected"))
    actual = _text(item.get("actual") or comparison.get("actual"))
    return bool(expected and actual and expected != actual)


def has_customer_facing_hard_evidence(item: dict[str, Any]) -> bool:
    raw_evidence = _dict(item.get("raw_evidence"))
    reproduction = _dict(item.get("reproduction"))
    request_raw = _dict(raw_evidence.get("request_raw"))
    response_raw = _dict(raw_evidence.get("response_raw"))
    db_snapshot = _dict(raw_evidence.get("db_snapshot"))
    har = _dict(reproduction.get("har_evidence"))
    if not har:
        har = _dict(item.get("har_evidence"))

    has_request = bool(request_raw.get("method") and request_raw.get("path")) or bool(
        reproduction.get("method") and reproduction.get("path")
    )
    has_response = bool(
        response_raw.get("status_code")
        or response_raw.get("body")
        or db_snapshot.get("before")
        or db_snapshot.get("after")
        or db_snapshot.get("assertion")
        or har.get("status_code")
        or har.get("response_body")
    )
    has_timestamp = bool(raw_evidence.get("timestamp") or item.get("timestamp"))
    has_real_evidence = bool(raw_evidence.get("has_real_evidence") or har)

    return has_request and has_response and has_explicit_failure_assertion(item) and has_timestamp and has_real_evidence


def has_customer_replay_asset(item: dict[str, Any]) -> bool:
    raw_evidence = _dict(item.get("raw_evidence"))
    reproduction = _dict(item.get("reproduction"))
    db_snapshot = _dict(raw_evidence.get("db_snapshot"))
    har = _dict(reproduction.get("har_evidence"))
    if not har:
        har = _dict(item.get("har_evidence"))
    method = _text(reproduction.get("method") or item.get("repro_method")).upper()
    path = _text(reproduction.get("path") or item.get("repro_path"))
    if not method or not path:
        request_raw = _dict(raw_evidence.get("request_raw"))
        method = _text(request_raw.get("method")).upper()
        path = _text(request_raw.get("path"))
    if not method or not path:
        return False
    if bool(reproduction.get("is_synthetic")):
        return False
    if har.get("status_code") or har.get("response_body"):
        return True
    return bool(
        (db_snapshot.get("before") and db_snapshot.get("after"))
        or db_snapshot.get("assertion")
    )


def has_validated_runtime_db_write_evidence(item: dict[str, Any]) -> bool:
    """Recognize reproduced non-production writes with runtime and DB evidence.

    This does not erase cleanup failures from operational metrics. It only
    prevents the per-finding delivery gate from discarding a validated defect
    when the finding already contains real request, response, assertion,
    timestamp, replay asset, and before/after DB side-effect evidence.
    """

    if not has_validated_evidence_quality(item):
        return False
    if not has_passed_business_evidence_status(item):
        return False
    raw_evidence = _dict(item.get("raw_evidence"))
    if not raw_evidence.get("has_real_evidence"):
        return False
    request_raw = _dict(raw_evidence.get("request_raw"))
    method = _text(request_raw.get("method")).upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    db_snapshot = _dict(raw_evidence.get("db_snapshot")) or _dict(item.get("db_evidence"))
    if db_snapshot.get("status") != "captured" or not bool(db_snapshot.get("any_change")):
        return False
    return has_customer_facing_hard_evidence(item) and has_customer_replay_asset(item)


def governed_cleanup_rejection_reasons(item: dict[str, Any]) -> list[str]:
    """Fail closed when a write-shaped finding omits its cleanup contract."""

    evidence = _dict(item.get("evidence"))
    raw_evidence = _dict(item.get("raw_evidence"))
    sandbox_write = _dict(raw_evidence.get("sandbox_write"))
    cleanup = {
        **_dict(sandbox_write.get("cleanup")),
        **_dict(evidence.get("cleanup")),
    }
    reproduction = _dict(item.get("reproduction"))
    request_raw = _dict(raw_evidence.get("request_raw"))
    method = _text(
        reproduction.get("method")
        or item.get("repro_method")
        or request_raw.get("method")
    ).upper()
    path = _lower(
        reproduction.get("path")
        or item.get("repro_path")
        or request_raw.get("path")
        or evidence.get("request")
        or ""
    )
    execution_semantics = _lower(
        evidence.get("execution_semantics")
        or raw_evidence.get("execution_semantics")
        or item.get("execution_semantics")
        or item.get("execution_policy")
    )
    explicit_read_only = execution_semantics in {
        "read_only", "safe_read_only", "query_only",
    }
    auth_step = path.endswith("/login") or "/auth/login" in path or path.rstrip("/").endswith("/auth")
    validated_runtime_db_write = has_validated_runtime_db_write_evidence(item)
    if not cleanup:
        # Auth/login POSTs are identity steps, not governed resource writes.
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not explicit_read_only and not auth_step:
            return ["CLEANUP_EVIDENCE_MISSING"]
        return []
    status = _lower(cleanup.get("status"))
    if status == "not_required":
        strategy = _lower(
            cleanup.get("strategy")
            or cleanup.get("reason")
            or _dict(sandbox_write.get("cleanup")).get("strategy")
            or _dict(sandbox_write.get("cleanup")).get("reason")
        )
        rejection_proved_unchanged = strategy in {
            "rejected_write_observer_unchanged",
            "setup_rejected_observer_unchanged",
        }
        action_style = strategy in {
            "action_post_on_existing_resource",
            "action_post_no_created_resource",
        } or "action_style_write" in strategy
        # Bare not_required without observer-unchanged proof or an explicit
        # action-style strategy stays internal. Creates without DELETE use
        # not_reversible (also internal).
        if (
            not explicit_read_only
            and not rejection_proved_unchanged
            and not action_style
            and not validated_runtime_db_write
        ):
            return ["CLEANUP_NOT_SUCCEEDED"]
    elif status not in {
        "completed", "success", "succeeded", "read_only",
    }:
        if validated_runtime_db_write and status in {"not_reversible", "not_applicable"}:
            return []
        return ["CLEANUP_NOT_SUCCEEDED"]
    if status not in {"not_required", "read_only"} and not _text(cleanup.get("receipt_ref")):
        return ["CLEANUP_RECEIPT_MISSING"]
    return []


def customer_delivery_rejection_reasons(item: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not isinstance(item, dict):
        return ["INVALID_FINDING_PAYLOAD"]
    if item.get("customer_delivery_status") not in {None, "defect"}:
        reasons.append("NOT_MARKED_FOR_DEFECT_DELIVERY")
    if _text(item.get("bug_status")) != "reproduced":
        reasons.append("BUG_STATUS_NOT_REPRODUCED")
    if not bool(item.get("gate_passed")):
        reasons.append("GATE_NOT_PASSED")
    if not has_traceable_delivery_identity(item):
        reasons.append("IDENTITY_CHAIN_INCOMPLETE")

    execution_status = _lower(item.get("execution_status"))
    confirmation_status = _lower(item.get("confirmation_status"))
    evidence_level = _lower(item.get("evidence_level"))
    execution_source = _lower(item.get("execution_source"))
    if any(marker in evidence_level or marker in execution_source for marker in _SYNTHETIC_MARKERS):
        reasons.append("SYNTHETIC_OR_DEMO_EVIDENCE")
    if execution_status and execution_status != "executed":
        reasons.append("NOT_EXECUTED")
    if confirmation_status and confirmation_status not in {"confirmed", "validated_candidate"}:
        reasons.append("NOT_CONFIRMED")

    consistency = _dict(item.get("evidence_consistency"))
    if _lower(consistency.get("verdict")) in {"rejected", "missing"}:
        reasons.append("EVIDENCE_CONSISTENCY_REJECTED")

    lane = " ".join(
        _lower(item.get(key))
        for key in ("value_lane", "_value_lane", "execution_block", "block_reason")
    )
    for marker in sorted(_BLOCKED_LANE_MARKERS):
        if marker in lane:
            reasons.append(f"BLOCKED_{marker.upper()}")

    if not has_validated_evidence_quality(item):
        reasons.append("EVIDENCE_QUALITY_NOT_VALIDATED")
    if not has_passed_business_evidence_status(item):
        reasons.append("BUSINESS_EVIDENCE_NOT_VALIDATED")
    if not has_customer_replay_asset(item):
        reasons.append("MISSING_REAL_REPLAY_ASSET")
    if not has_customer_facing_hard_evidence(item):
        reasons.append("MISSING_CUSTOMER_FACING_HARD_EVIDENCE")
    reasons.extend(governed_cleanup_rejection_reasons(item))
    return reasons


def explain_rejection_reason(reason: str) -> dict[str, str]:
    return REJECTION_REASON_EXPLANATIONS.get(reason, {
        "label": reason,
        "detail": "未知 Gate 拒绝原因。",
        "next_action": "检查后端 Gate 配置并补充解释文案。",
    })


def customer_delivery_rejection_explanations(item: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"code": reason, **explain_rejection_reason(reason)}
        for reason in customer_delivery_rejection_reasons(item)
    ]


def is_customer_deliverable_defect(item: dict[str, Any]) -> bool:
    return not customer_delivery_rejection_reasons(item)


def split_customer_delivery_tracks(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    defects: list[dict[str, Any]] = []
    clues: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rejection_reasons = customer_delivery_rejection_reasons(item)
        if not rejection_reasons:
            defects.append({
                **item,
                "delivery_track": "defect",
                "customer_delivery_status": "defect",
                "customer_delivery_label": "客户可交付缺陷",
                "customer_visible": True,
                "customer_delivery_gate_reasons": [],
                "customer_delivery_gate_explanations": [],
            })
        else:
            clues.append({
                **item,
                "delivery_track": "clue",
                "customer_delivery_status": "clue",
                "customer_delivery_label": "内部待验证线索",
                "customer_visible": False,
                "customer_delivery_gate_reasons": rejection_reasons,
                "customer_delivery_gate_explanations": [
                    {"code": reason, **explain_rejection_reason(reason)}
                    for reason in rejection_reasons
                ],
            })
    return defects, clues


def apply_governed_campaign_cleanup(
    items: list[dict[str, Any]],
    cleanup_receipt: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-adjudicate only findings blocked solely by per-scenario cleanup.

    A controller-level reset is valid cleanup when it ran after the scan,
    emitted an audit receipt, and proved the environment clean. All original
    scenario cleanup evidence remains attached for traceability.
    """

    if not isinstance(cleanup_receipt, dict):
        raise ValueError("campaign cleanup receipt must be an object")
    if cleanup_receipt.get("status") != "SUCCEEDED":
        raise ValueError("campaign cleanup receipt must have SUCCEEDED status")
    if cleanup_receipt.get("dirty_environment") is not False:
        raise ValueError("campaign cleanup receipt must prove a clean environment")
    audit_receipt_id = _text(cleanup_receipt.get("audit_receipt_id"))
    observation_ref = _text(cleanup_receipt.get("after_cleanup_observation_ref"))
    if not audit_receipt_id or not observation_ref:
        raise ValueError("campaign cleanup receipt requires audit and after-observation references")

    eligible_cleanup_reasons = {
        "CLEANUP_NOT_SUCCEEDED",
        "CLEANUP_RECEIPT_MISSING",
        "CLEANUP_EVIDENCE_MISSING",
    }

    def restore_upstream_cleanup_only_candidate(item: dict[str, Any]) -> dict[str, Any]:
        """Undo display-track demotion for candidates blocked only by cleanup.

        `_classify_findings` preserves the original business-gate result in
        `upstream_gate_passed`, then marks the row as an internal candidate for
        display. Campaign-level cleanup adjudication must evaluate the original
        defect claim, not the later display-track mutation.
        """

        existing_reasons = {
            str(reason)
            for reason in _list(item.get("customer_delivery_gate_reasons"))
            if str(reason)
        }
        if not (
            item.get("upstream_gate_passed") is True
            and existing_reasons
            and existing_reasons.issubset(eligible_cleanup_reasons)
        ):
            return copy.deepcopy(item)
        restored = copy.deepcopy(item)
        restored["gate_passed"] = True
        restored["customer_delivery_status"] = "defect"
        restored["customer_delivery_gate_reasons_before_campaign_cleanup"] = sorted(existing_reasons)
        return restored

    readjudicated: list[dict[str, Any]] = []
    untouched: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_for_cleanup = restore_upstream_cleanup_only_candidate(item)
        reasons = set(customer_delivery_rejection_reasons(item_for_cleanup))
        if not reasons or not reasons.issubset(eligible_cleanup_reasons):
            untouched.append(copy.deepcopy(item_for_cleanup))
            continue
        updated = copy.deepcopy(item_for_cleanup)
        evidence = _dict(updated.get("evidence"))
        raw_evidence = _dict(updated.get("raw_evidence"))
        sandbox_write = _dict(raw_evidence.get("sandbox_write"))
        original_cleanup = copy.deepcopy(
            _dict(evidence.get("cleanup")) or _dict(sandbox_write.get("cleanup"))
        )
        cleanup = {
            "status": "completed",
            "receipt_ref": audit_receipt_id,
            "after_observation_ref": observation_ref,
            "scope": "governed_campaign_cleanup",
        }
        evidence["cleanup"] = cleanup
        evidence["scenario_cleanup_before_campaign_reset"] = original_cleanup
        sandbox_write["cleanup"] = cleanup
        sandbox_write["scenario_cleanup_before_campaign_reset"] = original_cleanup
        raw_evidence["sandbox_write"] = sandbox_write
        updated["evidence"] = evidence
        updated["raw_evidence"] = raw_evidence
        updated["campaign_cleanup_receipt"] = {
            "audit_receipt_id": audit_receipt_id,
            "after_cleanup_observation_ref": observation_ref,
        }
        if is_customer_deliverable_defect(updated):
            readjudicated.append(updated)
        else:
            untouched.append(updated)
    defects, residual_clues = split_customer_delivery_tracks(readjudicated + untouched)
    return defects, residual_clues
