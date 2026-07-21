"""Evidence extraction: runtime observations, DB evidence, HAR, path matching."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403


def _infer_family_from_title(title: str, repro_path: str) -> str:
    """从标题推断缺陷族（fallback）"""
    text = (title or "").lower()
    if any(k in text for k in ("401", "403", "权限", "越权", "tenant", "idor", "鉴权")):
        return "security_boundary"
    if any(k in text for k in ("隐私", "合规", "脱敏", "敏感", "audit", "审计", "泄露")):
        return "privacy_compliance"
    if any(k in text for k in ("trace", "日志", "观测", "告警", "error code", "operationid")):
        return "observability"
    if any(k in text for k in ("配置", "环境", "开关", "部署", "config", "env")):
        return "configuration_drift"
    if any(k in text for k in ("db verified", "数据", "一致性", "幂等", "idempot", "integrity", "constraint", "约束")):
        return "data_integrity"
    if any(k in text for k in ("性能", "latency", "slow", "吞吐", "内存", "fanout")):
        return "performance"
    if any(k in text for k in ("timeout", "超时", "重试", "抖动", "间歇", "storm")):
        return "stability"
    if any(k in text for k in ("兼容", "compat", "版本")):
        return "compatibility"
    if any(k in text for k in ("本地化", "i18n", "locale", "timezone", "时区", "无障碍")):
        return "accessibility_i18n"
    if any(k in text for k in ("ui", "页面", "渲染", "route", "导航", "空白")):
        return "ui"
    if any(k in text for k in ("ux", "体验", "反馈", "cta", "交互", "可用性")):
        return "uiux"
    if repro_path or any(k in text for k in ("openapi", "schema", "contract", "spec")):
        return "api_contract"
    return "scenario_flow"


def _build_taxonomy(finding: dict) -> dict:
    """构建分类标签（从前端 resolveFindingTaxonomy 迁移）"""
    explicit_family = str(finding.get("defect_family") or "").strip()
    risk_type = str(finding.get("risk_type") or finding.get("category") or "").strip()
    title = str(finding.get("title") or "")
    repro_path = str(finding.get("_api_path") or finding.get("repro_path") or finding.get("path") or "")
    raw_bucket = str(finding.get("reporting_bucket") or "").strip()
    quality_gap = bool(finding.get("quality_assurance_gap"))

    if explicit_family in DEFECT_FAMILY_META:
        family = explicit_family
    elif risk_type in RISK_TYPE_TO_FAMILY:
        family = RISK_TYPE_TO_FAMILY[risk_type]
    else:
        family = _infer_family_from_title(title, repro_path)

    meta = DEFECT_FAMILY_META.get(family, DEFECT_FAMILY_META["scenario_flow"])
    return {
        "defect_family": family,
        "defect_family_label": meta["label"],
        "reporting_bucket": raw_bucket or meta["reporting_bucket"],
        "reporting_bucket_label": meta["bucket_label"],
        "quality_assurance_gap": quality_gap,
    }


def _ui_verification_display(finding: dict) -> dict:
    verification = finding.get("ui_verification") if isinstance(finding.get("ui_verification"), dict) else {}
    gate = finding.get("ui_candidate_gate") if isinstance(finding.get("ui_candidate_gate"), dict) else {}
    status = _clean(verification.get("status") or finding.get("verification_badge"))
    if status == "verified" or status == "ui_verified":
        return {
            "verification_badge": "ui_verified",
            "verification_label": _clean(finding.get("verification_label")) or "已二次验真",
            "customer_evidence_label": _clean(finding.get("customer_evidence_label")) or "UI 二次验真通过",
            "verification_rank": 3,
        }
    if gate.get("passed") is True or status == "ui_candidate":
        return {
            "verification_badge": "ui_candidate",
            "verification_label": _clean(finding.get("verification_label")) or "待二次验真",
            "customer_evidence_label": _clean(finding.get("customer_evidence_label")) or "UI 候选待二次验真",
            "verification_rank": 2,
        }
    if finding.get("defect_family") == "ui" or str(finding.get("risk_type") or "").strip() in {"frontend_ui", "ui_execution", "frontend_execution_runtime"} or status == "ui_signal":
        return {
            "verification_badge": "ui_signal",
            "verification_label": _clean(finding.get("verification_label")) or "仅 UI 信号",
            "customer_evidence_label": _clean(finding.get("customer_evidence_label")) or "UI 观测信号",
            "verification_rank": 1,
        }
    return {
        "verification_badge": _clean(finding.get("verification_badge")),
        "verification_label": _clean(finding.get("verification_label")),
        "customer_evidence_label": _clean(finding.get("customer_evidence_label")),
        "verification_rank": 0,
    }


def _high_confidence_candidate_display(finding: dict) -> dict:
    high_conf = bool(finding.get("high_confidence_candidate"))
    tier = _clean(finding.get("candidate_tier"))
    if high_conf or tier == "high_confidence_ui_candidate":
        return {
            "high_confidence_candidate": True,
            "candidate_tier": "high_confidence_ui_candidate",
            "candidate_tier_label": "高可信 UI 候选",
        }
    if tier:
        return {
            "high_confidence_candidate": False,
            "candidate_tier": tier,
            "candidate_tier_label": "UI 候选",
        }
    return {
        "high_confidence_candidate": False,
        "candidate_tier": "",
        "candidate_tier_label": "",
    }


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _clean(value: Any) -> str:
    return str(value or "").strip()


def _read_json_policy(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def _display_ready_policy() -> dict[str, Any]:
    return _read_json_policy(DISPLAY_READY_POLICY_PATH)


def _policy_section(name: str) -> dict[str, Any]:
    section = _display_ready_policy().get(name)
    return section if isinstance(section, dict) else {}


def _policy_list(name: str) -> list[str]:
    values = _display_ready_policy().get(name)
    return [str(item) for item in values] if isinstance(values, list) else []


def _format_policy_text(template: str, **values: str) -> str:
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return template


_PLACEHOLDER_TOKEN_RE = re.compile(
    r"(?:QUALIBUG_UNRESOLVED_ID|<\s*(?:FILL|TODO|REQUIRED|SANDBOX|REPLACE)[^>]*>|"
    r"\{[^}/]+\}|:id\b|/id\b|"
    r"(?:^|[/{:_-])(?:example|sample|mock|placeholder)(?:$|[}/_-])|"
    r"(?:^|[{:_-])(?:demo|draft|test)(?:$|[}_-]))",
    re.I,
)


def _is_placeholder_value(value: Any) -> bool:
    text = _clean(value)
    return bool(text and _PLACEHOLDER_TOKEN_RE.search(text))


def _is_declared_path_template(value: Any) -> bool:
    text = normalize_path_placeholders(_clean(value))
    return bool(text.startswith("/") and re.search(r"\{[A-Za-z_]\w*\}", text))


def _is_unresolved_path_value(value: Any) -> bool:
    text = _clean(value)
    if not text:
        return False
    if _is_declared_path_template(text):
        return False
    return _is_placeholder_value(text)


def _canonical_runtime_path(value: Any) -> str:
    return normalize_path_placeholders(_clean(value).split("?", 1)[0])


def _declared_path_matches_observed(declared_path: str, observed_path: str) -> bool:
    declared = _canonical_runtime_path(declared_path)
    observed = _canonical_runtime_path(observed_path)
    if not declared or not observed:
        return False
    if declared == observed:
        return True
    if _is_declared_path_template(declared):
        pattern = re.escape(declared)
        pattern = re.sub(r"\\\{[A-Za-z_]\w*\\\}", r"[^/]+", pattern)
        return bool(re.fullmatch(pattern, observed))
    return False


def _response_body_has_value(body: Any) -> bool:
    if body in (None, "", [], {}):
        return False
    if isinstance(body, dict):
        return any(_response_body_has_value(v) for v in body.values())
    if isinstance(body, list):
        return any(_response_body_has_value(v) for v in body)
    return bool(_clean(body))


def _status_code_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


_RUNTIME_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")
_RUNTIME_TOKEN_STOPWORDS = {
    "api", "body", "code", "data", "error", "false", "http", "message", "null",
    "path", "post", "request", "response", "status", "true", "type",
    "接口", "响应", "错误", "状态", "返回", "系统", "应该", "实际", "预期",
}


def _runtime_text_tokens(value: Any) -> set[str]:
    text = _clean(value).lower()
    tokens = {token for token in _RUNTIME_TOKEN_RE.findall(text) if token not in _RUNTIME_TOKEN_STOPWORDS}
    return {token for token in tokens if not _is_placeholder_value(token)}


def _finding_semantic_text(finding: dict) -> str:
    parts = [
        finding.get("title"),
        finding.get("bug_title"),
        finding.get("expected_behavior"),
        finding.get("expected"),
        finding.get("actual_behavior"),
        finding.get("actual"),
        finding.get("description"),
        finding.get("source_entity"),
        finding.get("source_value"),
    ]
    return " ".join(_clean(part) for part in parts if _clean(part))


def _runtime_observation_supports_finding(finding: dict, obs: dict[str, Any]) -> bool:
    """Return True only when runtime response evidence belongs to this finding.

    Same endpoint is not enough: a generic 500 from a placeholder request must not
    be used as proof for an unrelated business-state finding. Apply the same
    binding rule to HAR and evidence.calls runtime rows so every display layer
    reads a consistent evidence source.
    """
    path = _clean(obs.get("path"))
    if _is_placeholder_value(path):
        return False

    finding_text = _finding_semantic_text(finding)
    finding_text_lower = finding_text.lower()
    body_text = _clean(obs.get("body"))
    request_text = _clean(obs.get("request_body"))
    runtime_text = " ".join(part for part in (body_text, request_text) if part)
    runtime_text_lower = runtime_text.lower()
    status = _status_code_int(obs.get("status_code"))

    placeholder_tokens = [
        token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", runtime_text_lower)
        if _is_placeholder_value(token)
    ]
    if placeholder_tokens and not any(token in finding_text_lower for token in placeholder_tokens):
        return False

    has_identifier_context = (
        bool(re.search(r"\b(?:uuid|id|identifier|address|param|parameter)\b", finding_text_lower))
        or any(token in finding_text for token in ("主键", "标识", "地址", "参数"))
    )
    if "uuid" in runtime_text_lower and not has_identifier_context:
        return False

    return True


def _relevant_har_evidence(finding: dict) -> dict[str, Any]:
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    if not har:
        return {}
    obs = {
        "source": "har",
        "method": _clean(har.get("method") or finding.get("_api_method") or finding.get("method") or "GET").upper(),
        "path": _clean(har.get("path") or finding.get("_api_path") or finding.get("path")),
        "status_code": har.get("status_code"),
        "body": har.get("response_body"),
        "request_body": har.get("request_body"),
        "duration_ms": har.get("duration_ms") or 0,
        "actor": _clean(har.get("actor")),
    }
    return har if _runtime_observation_supports_finding(finding, obs) else {}


def _runtime_relevance_mismatch_reasons(finding: dict) -> list[str]:
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    if not har or _relevant_har_evidence(finding):
        return []
    if not (har.get("status_code") or har.get("response_body")):
        return []
    return ["运行时响应与当前缺陷描述不匹配，已拒绝作为复现证据"]


def _extract_runtime_call_evidence(finding: dict) -> list[dict[str, Any]]:
    from ._quality import _deep_get  # lazy
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    calls = evidence.get("calls") if isinstance(evidence.get("calls"), list) else []
    rows: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        call_text = _clean(call.get("call") or call.get("path"))
        method = ""
        path = _clean(call.get("path"))
        if call_text:
            parts = call_text.split(maxsplit=1)
            method = parts[0].upper()
            if len(parts) > 1:
                path = parts[1]
        for role, result in (call.get("results") or {}).items():
            if not isinstance(result, dict):
                continue
            if "status" not in result and "body" not in result:
                continue
            rows.append({
                "method": method,
                "path": path,
                "role": str(role),
                "status_code": result.get("status"),
                "body": result.get("body"),
                "duration_ms": result.get("duration_ms") or result.get("elapsed_ms") or 0,
                "request_url": _deep_get(result, "_request", "url") or _deep_get(result, "request", "url"),
            })
    return rows


def _runtime_observations(finding: dict) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    har = _relevant_har_evidence(finding)
    if har.get("status_code") or har.get("response_body") or har.get("path"):
        observations.append({
            "source": "har",
            "method": _clean(har.get("method") or finding.get("_api_method") or finding.get("method") or "GET").upper(),
            "path": _clean(har.get("path") or finding.get("_api_path") or finding.get("path")),
            "status_code": har.get("status_code"),
            "body": har.get("response_body"),
            "request_body": har.get("request_body"),
            "duration_ms": har.get("duration_ms") or 0,
            "actor": _clean(har.get("actor")),
        })
    for row in _extract_runtime_call_evidence(finding):
        observations.append({
            "source": "runtime_call",
            "method": row.get("method"),
            "path": row.get("path"),
            "status_code": row.get("status_code"),
            "body": row.get("body"),
            "duration_ms": row.get("duration_ms"),
            "actor": row.get("role"),
            "request_url": row.get("request_url"),
        })
    return observations


def _observation_has_response_payload(obs: dict[str, Any]) -> bool:
    return _status_code_int(obs.get("status_code")) > 0 or _response_body_has_value(obs.get("body"))


def _declared_request_identity(finding: dict) -> tuple[str, str]:
    from ._quality import _deep_get  # lazy
    method = _clean(
        finding.get("_api_method")
        or finding.get("repro_method")
        or _deep_get(finding, "evidence", "method")
        or finding.get("method")
    ).upper()
    path = _clean(
        finding.get("_api_path")
        or finding.get("repro_path")
        or _deep_get(finding, "evidence", "path")
        or finding.get("path")
    )
    return method, path


def _runtime_identity_mismatch_reasons(finding: dict, obs: dict[str, Any] | None = None) -> list[str]:
    """Check that runtime evidence is bound to the declared request identity.

    A response from the wrong method/path must never be used as proof for the
    current finding. This is intentionally generic and does not depend on any
    business table or industry vocabulary.
    """
    declared_method, declared_path = _declared_request_identity(finding)
    reasons: list[str] = []
    if declared_path and _is_unresolved_path_value(declared_path):
        reasons.append(f"API path is a placeholder or unresolved template: {declared_path}")

    observations = [obs] if obs is not None else _runtime_observations(finding)
    observed_paths: list[str] = []
    observed_methods: list[str] = []
    for row in observations:
        if not isinstance(row, dict):
            continue
        observed_path = _clean(row.get("path"))
        observed_method = _clean(row.get("method")).upper()
        if observed_path and not _is_unresolved_path_value(observed_path):
            observed_paths.append(observed_path)
        if observed_method:
            observed_methods.append(observed_method)

    if declared_path and observed_paths and not any(_declared_path_matches_observed(declared_path, observed_path) for observed_path in observed_paths):
        reasons.append(
            f"Declared API path {declared_path} does not match observed runtime path(s): "
            f"{', '.join(sorted(set(observed_paths))[:5])}"
        )
    if declared_method and observed_methods and declared_method not in observed_methods:
        reasons.append(
            f"Declared API method {declared_method} does not match observed runtime method(s): "
            f"{', '.join(sorted(set(observed_methods))[:5])}"
        )
    return reasons


def _accepted_runtime_observations(finding: dict) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for obs in _runtime_observations(finding):
        if not isinstance(obs, dict):
            continue
        path = _clean(obs.get("path"))
        if _is_unresolved_path_value(path):
            continue
        if not _observation_has_response_payload(obs):
            continue
        if not _runtime_observation_supports_finding(finding, obs):
            continue
        if _runtime_identity_mismatch_reasons(finding, obs):
            continue
        accepted.append(obs)
    return accepted


def _best_runtime_observation(finding: dict) -> dict[str, Any]:
    """Pick the single runtime row every display layer should use.

    Preference: anomaly-bearing rows first, then HAR before evidence.calls, then
    richer payloads. Returning one canonical row prevents the UI from showing a
    different response than the one used for status/gate decisions.
    """
    rows = _accepted_runtime_observations(finding)
    if not rows:
        return {}

    def _rank(obs: dict[str, Any]) -> tuple[int, int, int, int]:
        status = _status_code_int(obs.get("status_code"))
        has_error_status = 1 if status >= 400 else 0
        body_text = _clean(obs.get("body"))
        has_error_body = 1 if any(token in body_text.lower() for token in ("error", "exception", "traceback", "failed")) else 0
        source_rank = 1 if _clean(obs.get("source")) == "har" else 0
        richness = (1 if body_text else 0) + (1 if _clean(obs.get("request_body")) else 0) + (1 if _clean(obs.get("actor")) else 0)
        return has_error_status, has_error_body, source_rank, richness

    return sorted(rows, key=_rank, reverse=True)[0]


def _runtime_body_excerpt(value: Any, limit: int = 1000) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(value)
    else:
        text = _clean(value)
    return text[:limit]


def _has_runtime_response(finding: dict) -> bool:
    return bool(_accepted_runtime_observations(finding))


def _extract_verified_db_evidence(finding: dict) -> dict | None:
    """提取已验证的 DB 硬证据。

    仅接受真实 before/after 快照或明确标记的 DB verifier 输出，不把 SQL 提示、
    relevant_tables、source_entity/source_value 这类线索当作已验证 DB 证据。
    """
    if not isinstance(finding, dict):
        return None

    db_evidence = finding.get("db_evidence")
    if isinstance(db_evidence, dict):
        before = db_evidence.get("before_db_snapshot") or db_evidence.get("before")
        after = db_evidence.get("after_db_snapshot") or db_evidence.get("after")
        business_operation = _clean(db_evidence.get("business_operation") or db_evidence.get("operation"))
        assertion = _clean(db_evidence.get("db_assertion") or db_evidence.get("assertion"))
        if before and after and business_operation and assertion:
            return {
                "before": before,
                "after": after,
                "business_operation": business_operation,
                "assertion": assertion,
                "table": _clean(db_evidence.get("table")),
                "column": _clean(db_evidence.get("column")),
                "value": _clean(db_evidence.get("value")),
                "violation": assertion,
                "raw": _clean(finding.get("title")),
                "source": "db_evidence",
            }

    for snapshot in finding.get("db_snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        before = snapshot.get("before_db_snapshot") or snapshot.get("before")
        after = snapshot.get("after_db_snapshot") or snapshot.get("after")
        business_operation = _clean(snapshot.get("business_operation") or snapshot.get("operation"))
        assertion = _clean(snapshot.get("db_assertion") or snapshot.get("assertion"))
        if before and after and business_operation and assertion:
            return {
                "before": before,
                "after": after,
                "business_operation": business_operation,
                "assertion": assertion,
                "table": _clean(snapshot.get("table")),
                "column": _clean(snapshot.get("column")),
                "value": _clean(snapshot.get("value")),
                "violation": assertion,
                "raw": _clean(finding.get("title")),
                "source": "db_snapshots",
            }

    title = _clean(finding.get("title"))
    if str(finding.get("source") or "").lower() == "db_verifier" or title.startswith("[DB Verified]"):
        db_ev = _extract_verified_db_evidence(finding)
        if db_ev:
            db_ev = dict(db_ev)
            db_ev["source"] = "db_verifier"
            return db_ev
    return None


def _has_db_clue(finding: dict) -> bool:
    from ._quality import _deep_get  # lazy
    return bool(
        _clean(finding.get("source_entity") or finding.get("source_value"))
        or _has_any_value(_deep_get(finding, "investigation_guidance", "relevant_tables"))
        or _clean(_deep_get(finding, "investigation_guidance", "sql_verify"))
    )


def _has_anomaly_signal(finding: dict) -> bool:
    from ._quality import _deep_get  # lazy
    if _extract_verified_db_evidence(finding):
        return True
    for obs in _accepted_runtime_observations(finding):
        status = _status_code_int(obs.get("status_code"))
        if status >= 400:
            return True
        body = obs.get("body")
        if isinstance(body, dict) and any(k in body for k in ("error", "error_code", "error_message", "exception")):
            return True
        body_text = _clean(body)
        if body_text and any(token in body_text.lower() for token in ("error", "exception", "traceback", "failed")):
            return True
    failed_assertions = finding.get("failed_assertions")
    if isinstance(failed_assertions, list) and any(
        (isinstance(assertion, dict) and _has_any_value(assertion)) or _clean(assertion)
        for assertion in failed_assertions
    ):
        return True
    comparison = finding.get("expected_actual_comparison") if isinstance(finding.get("expected_actual_comparison"), dict) else {}
    if _clean(comparison.get("difference")):
        return True
    semantic_verdict = _clean(finding.get("semantic_verdict") or _deep_get(finding, "evidence_status", "semantic_verdict")).upper()
    business_evidence_status = _clean(finding.get("business_evidence_status") or _deep_get(finding, "evidence_status", "business_evidence_status")).upper()
    expected = _clean(finding.get("expected_behavior") or finding.get("expected"))
    actual = _clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description"))
    if semantic_verdict == "SEMANTIC_CONFIRMED" and business_evidence_status == "VALIDATED" and expected and actual and expected != actual:
        return True
    return False


def _path_mismatch_reasons(finding: dict) -> list[str]:
    """Backward-compatible wrapper; now checks both path and method binding."""
    return _runtime_identity_mismatch_reasons(finding)


def _has_any_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_any_value(v) for v in value)
    if value and isinstance(value, dict):
        return any(_has_any_value(v) for v in value.values())
    return _clean(value).strip() != ""


def _normalize_severity(value: Any) -> str:
    v = str(value or "").lower()
    if v in ("critical", "p0"):
        return "P0"
    if v in ("high", "p1"):
        return "P1"
    return "P2"


# ═══════════════════════════════════════════════════════════════════════
# 证据质量评分（从前端 buildEvidenceQuality 迁移）
# ═══════════════════════════════════════════════════════════════════════

