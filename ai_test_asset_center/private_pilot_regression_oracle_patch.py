from __future__ import annotations

"""Structured regression oracles for confirmed-finding probes.

The regression runner can already execute HTTP probes, but without a strong
oracle it must mark many results as ``needs_review``.  Confirmed-finding probes
often contain enough semantics to derive a safe post-fix HTTP-status oracle, for
example tenant isolation or permission bypass should become 403 after the fix.

This patch is deliberately conservative:
- explicit oracle fields win;
- only well-known access/control and validation families are inferred;
- business-balance, money, stock and workflow invariants stay manual unless an
  explicit oracle is present;
- the runner never treats a response as passed without a concrete oracle.
"""

from typing import Any

PATCH_SOURCE = "ai_test_asset_center.private_pilot_regression_oracle_patch"


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        parsed = int(value)
        return parsed if 100 <= parsed <= 599 else None
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def infer_expected_status_code(probe: dict[str, Any]) -> int | None:
    """Infer a safe post-fix status oracle from a probe-like payload."""
    row = _as_dict(probe)
    reproduction = _as_dict(row.get("reproduction"))
    expected_actual = _as_dict(row.get("expected_actual_comparison"))
    evidence_status = _as_dict(row.get("evidence_status"))
    explicit_candidates = [
        row.get("expected_status_code"),
        row.get("fixed_expected_status_code"),
        row.get("post_fix_expected_status_code"),
        row.get("expected_status"),
        reproduction.get("expected_status_code"),
        reproduction.get("fixed_expected_status_code"),
        expected_actual.get("expected_status_code"),
        expected_actual.get("fixed_expected_status_code"),
        evidence_status.get("expected_status_code"),
    ]
    for value in explicit_candidates:
        parsed = _safe_int(value)
        if parsed is not None:
            return parsed

    text = " ".join(
        str(value or "")
        for value in (
            row.get("risk_type"), row.get("category"), row.get("defect_family"),
            row.get("defect_family_label"), row.get("title"), row.get("expected"),
            reproduction.get("risk_type"), reproduction.get("expected"),
        )
    ).lower()
    if any(token in text for token in ("tenant", "租户", "隔离", "idor", "越权", "cross-tenant", "cross tenant")):
        return 403
    if any(token in text for token in ("permission", "authorization", "authz", "rbac", "role", "权限", "无权", "forbidden")):
        return 403
    if any(token in text for token in ("unauthenticated", "unauthorized", "未登录", "未认证", "token missing")):
        return 401
    if any(token in text for token in ("input", "validation", "boundary", "参数", "校验", "非法", "bad request")):
        return 400
    if any(token in text for token in ("duplicate", "idempotency", "幂等", "重复提交", "conflict", "invalid transition", "非法流转")):
        return 409
    return None


def infer_buggy_status_code(probe: dict[str, Any]) -> int | None:
    row = _as_dict(probe)
    reproduction = _as_dict(row.get("reproduction"))
    raw_evidence = _as_dict(row.get("raw_evidence"))
    request_raw = _as_dict(raw_evidence.get("request_raw"))
    execution = _as_dict(row.get("execution"))
    candidates = [
        row.get("buggy_status_code"),
        row.get("original_status_code"),
        row.get("observed_status_code"),
        reproduction.get("buggy_status_code"),
        reproduction.get("observed_status_code"),
        request_raw.get("status_code"),
        execution.get("status_code"),
    ]
    for value in candidates:
        parsed = _safe_int(value)
        if parsed is not None:
            return parsed
    return None


def _oracle_explanation(expected_status: int, buggy_status: int | None) -> str:
    if buggy_status is not None and buggy_status != expected_status:
        return f"修复后预期 HTTP {expected_status}；原缺陷复现时观测到 HTTP {buggy_status}。"
    return f"修复后预期 HTTP {expected_status}；再次出现其他状态视为回归失败。"


def enrich_probe_oracle(probe: dict[str, Any]) -> dict[str, Any]:
    row = dict(probe or {})
    expected_status = infer_expected_status_code(row)
    if expected_status is None:
        return row
    buggy_status = infer_buggy_status_code(row)
    row["expected_status_code"] = expected_status
    if buggy_status is not None:
        row["buggy_status_code"] = buggy_status
    row["regression_oracle"] = {
        "kind": "http_status",
        "expected_status_code": expected_status,
        "buggy_status_code": buggy_status,
        "source": PATCH_SOURCE,
        "honesty_rule": "Only concrete HTTP-status oracles are auto-judged; otherwise the regression item remains needs_review.",
    }
    expected = str(row.get("expected") or "").strip()
    explanation = _oracle_explanation(expected_status, buggy_status)
    if str(expected_status) not in expected:
        row["expected"] = f"{explanation} {expected}".strip()
    return row


def _judge_with_structured_oracle(original_judge: Any, probe: dict[str, Any], execution: dict[str, Any], skipped: bool = False, skip_reason: str = "") -> dict[str, Any]:
    expected_status = infer_expected_status_code(probe)
    if skipped or expected_status is None:
        return original_judge(probe, execution, skipped=skipped, skip_reason=skip_reason)
    result = original_judge(probe, execution, skipped=skipped, skip_reason=skip_reason)
    if result.get("status") == "needs_review" and execution.get("reachable"):
        actual_status = execution.get("status_code")
        if actual_status == expected_status:
            result["status"] = "passed"
            result["passed"] = True
            result["reason"] = f"结构化回归 oracle 通过：响应状态 {actual_status} 符合预期 HTTP {expected_status}。"
        else:
            result["status"] = "failed"
            result["passed"] = False
            result["reason"] = f"结构化回归 oracle 失败：响应状态 {actual_status} 不符合预期 HTTP {expected_status}。"
    result["regression_oracle"] = {
        "kind": "http_status",
        "expected_status_code": expected_status,
        "buggy_status_code": infer_buggy_status_code(probe),
        "source": PATCH_SOURCE,
    }
    return result


def install_regression_oracle_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    from ai_test_asset_center import regression_runner, regression_suite_builder

    if getattr(regression_suite_builder, "_REGRESSION_ORACLE_PATCHED", False):
        return

    original_loader = getattr(regression_suite_builder, "_load_confirmed_findings_regression_probes")
    original_normalize = getattr(regression_suite_builder, "_normalize_probe")
    original_judge = getattr(regression_runner, "_judge_probe")

    def _load_confirmed_findings_with_oracles(project: str, root: Any) -> list[dict[str, Any]]:
        probes = original_loader(project, root)
        return [enrich_probe_oracle(item) for item in probes if isinstance(item, dict)]

    def _normalize_probe_with_oracle(probe: dict[str, Any], index: int) -> dict[str, Any]:
        enriched = enrich_probe_oracle(probe if isinstance(probe, dict) else {})
        normalized = original_normalize(enriched, index)
        for key in ("expected_status_code", "buggy_status_code", "regression_oracle", "confirmed_evidence_id"):
            if key in enriched:
                normalized[key] = enriched[key]
        return normalized

    def _judge_probe_with_oracle(probe: dict[str, Any], execution: dict[str, Any], skipped: bool = False, skip_reason: str = "") -> dict[str, Any]:
        return _judge_with_structured_oracle(original_judge, probe, execution, skipped=skipped, skip_reason=skip_reason)

    regression_suite_builder._ORIGINAL_REGRESSION_ORACLE_CONFIRMED_LOADER = original_loader  # type: ignore[attr-defined]
    regression_suite_builder._ORIGINAL_REGRESSION_ORACLE_NORMALIZE = original_normalize  # type: ignore[attr-defined]
    regression_runner._ORIGINAL_REGRESSION_ORACLE_JUDGE = original_judge  # type: ignore[attr-defined]
    regression_suite_builder._load_confirmed_findings_regression_probes = _load_confirmed_findings_with_oracles  # type: ignore[assignment]
    regression_suite_builder._normalize_probe = _normalize_probe_with_oracle  # type: ignore[assignment]
    regression_runner._judge_probe = _judge_probe_with_oracle  # type: ignore[assignment]
    regression_suite_builder._REGRESSION_ORACLE_PATCHED = True  # type: ignore[attr-defined]
    regression_runner._REGRESSION_ORACLE_PATCHED = True  # type: ignore[attr-defined]
    regression_suite_builder._REGRESSION_ORACLE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    regression_runner._REGRESSION_ORACLE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_regression_oracle_patch() -> None:
    from ai_test_asset_center import regression_runner, regression_suite_builder

    original_loader = getattr(regression_suite_builder, "_ORIGINAL_REGRESSION_ORACLE_CONFIRMED_LOADER", None)
    original_normalize = getattr(regression_suite_builder, "_ORIGINAL_REGRESSION_ORACLE_NORMALIZE", None)
    original_judge = getattr(regression_runner, "_ORIGINAL_REGRESSION_ORACLE_JUDGE", None)
    if callable(original_loader):
        regression_suite_builder._load_confirmed_findings_regression_probes = original_loader  # type: ignore[assignment]
    if callable(original_normalize):
        regression_suite_builder._normalize_probe = original_normalize  # type: ignore[assignment]
    if callable(original_judge):
        regression_runner._judge_probe = original_judge  # type: ignore[assignment]
    regression_suite_builder._REGRESSION_ORACLE_PATCHED = False  # type: ignore[attr-defined]
    regression_runner._REGRESSION_ORACLE_PATCHED = False  # type: ignore[attr-defined]
