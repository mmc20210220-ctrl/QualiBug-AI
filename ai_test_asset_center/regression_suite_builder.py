from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .real_project_onboarding import ROOT, _html_escape, _safe_project_id, load_real_project_config

# First-class structured regression-oracle enricher — no symbol replacement.
ProbeOracleEnricher = Callable[[dict[str, Any]], dict[str, Any]]
_PROBE_ORACLE_ENRICHER: ProbeOracleEnricher | None = None


def register_probe_oracle_enricher(hook: ProbeOracleEnricher | None) -> None:
    """Enrich probes with concrete HTTP-status oracles before suite normalize."""
    global _PROBE_ORACLE_ENRICHER
    _PROBE_ORACLE_ENRICHER = hook


def clear_probe_oracle_enricher() -> None:
    register_probe_oracle_enricher(None)


PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}
DESTRUCTIVE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HIGH_RISK_TYPES = {
    "permission_bypass", "idor", "tenant_isolation", "payment", "refund", "money",
    "stock", "order_state", "idempotency", "duplicate_submit",
    "approval_bypass", "workflow_bypass", "prescription_authorization",
    "data_isolation", "cross_tenant", "privilege_escalation", "audit_tamper",
    "settlement", "state_machine", "conservation",
}
MODE_LIMITS = {"smoke": 20, "release": 80, "full": 500}
SEVERITY_WEIGHT = {"P0": 100, "P1": 80, "P2": 45, "P3": 20}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_json_safe(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _safe_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text[:limit]


def _normalize_probe_id(raw: Any, index: int) -> str:
    text = str(raw or f"REGRESSION_{index:04d}")
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return (text or f"REGRESSION_{index:04d}")[:96]


_COMMON_PATH_PREFIXES = {
    "api", "apis", "rest", "restful", "v1", "v2", "v3", "v4",
    "public", "internal", "service", "services", "gateway", "app", "web",
}


def _infer_module(path: str, risk_type: str, title: str = "") -> str:
    # Data-driven, industry-agnostic: derive the module from the real URL path
    # (first meaningful resource segment), never from hardcoded business keywords.
    parts = [
        p for p in str(path or "").strip("/").split("/")
        if p and "{" not in p and ":" not in p
    ]
    for part in parts:
        if part.lower() in _COMMON_PATH_PREFIXES:
            continue
        slug = re.sub(r"[^A-Za-z0-9_\-]+", "_", part).strip("_")
        if slug:
            return slug[:48]
    # No usable resource segment: fall back to the risk type as a neutral label.
    slug = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(risk_type or "").strip()).strip("_")
    return slug[:48] or "general"


def _is_destructive(method: str, risk_type: str) -> bool:
    return method.upper() in DESTRUCTIVE_METHODS or risk_type.lower() in {"payment", "refund", "idempotency", "duplicate_submit", "concurrency", "delete", "cancel_order"}


def _extract_request_body_from_steps(steps: list[Any]) -> dict[str, Any] | list[Any] | str | None:
    for step in steps:
        text = str(step or "")
        match = re.search(r"-d\s+'([^']+)'", text) or re.search(r'-d\s+"([^"]+)"', text)
        if not match:
            continue
        payload = match.group(1).strip()
        if not payload or "根据业务场景填写" in payload or '"..."' in payload or "{...}" in payload:
            continue
        try:
            return json.loads(payload)
        except Exception:
            return payload
    return None


def _extract_path_from_curl(curl_command: str) -> str:
    match = re.search(r'curl\s+-X\s+\w+\s+"([^"]+)"', str(curl_command or ""))
    if not match:
        return ""
    url = match.group(1).strip()
    if "${BASE_URL}" in url:
        return re.sub(r"^\$\{BASE_URL\}", "", url)
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return parsed.path or "/"
    return url


def _current_campaign_scope_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    candidates = [
        payload.get("current_campaign_scope"),
        (payload.get("scan_meta") or {}).get("current_campaign_scope") if isinstance(payload.get("scan_meta"), dict) else {},
        (payload.get("value_metrics") or {}).get("current_campaign_scope") if isinstance(payload.get("value_metrics"), dict) else {},
        (payload.get("executive_summary") or {}).get("current_campaign_scope") if isinstance(payload.get("executive_summary"), dict) else {},
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        normalized = {
            "campaign_id": _safe_text(candidate.get("campaign_id"), 120),
            "lineage_campaign_id": _safe_text(candidate.get("lineage_campaign_id"), 120),
            "scope_id": _safe_text(candidate.get("scope_id"), 160),
            "environment_ref": _safe_text(candidate.get("environment_ref"), 160),
            "source_hash": _safe_text(candidate.get("source_hash"), 128),
            "source_snapshot_hash": _safe_text(candidate.get("source_snapshot_hash"), 128),
        }
        if any(normalized.values()):
            return normalized
    return {}


def _load_customer_ready_defect_probes(project: str, root: Path) -> list[dict[str, Any]]:
    project = _safe_project_id(project)
    candidates = [
        root / "platform_outputs" / project / "real_project" / "real_project_defect_data.json",
        root / "platform_workspace" / project / "real_project" / "real_project_defect_data.json",
    ]
    data: dict[str, Any] = {}
    for path in candidates:
        loaded = _load_json_safe(path, {})
        if not isinstance(loaded, dict):
            continue
        payload_candidates: list[dict[str, Any]] = []
        for nested_key in ("customer_ready_family_shelf", "customer_ready_snapshot"):
            nested = loaded.get(nested_key)
            if not isinstance(nested, dict):
                continue
            candidate = dict(nested)
            if not isinstance(candidate.get("current_campaign_scope"), dict):
                for scope_key in ("customer_ready_current_campaign_scope", "current_campaign_scope"):
                    if isinstance(loaded.get(scope_key), dict):
                        candidate["current_campaign_scope"] = dict(loaded.get(scope_key) or {})
                        break
            if not isinstance(candidate.get("continuous_discovery_campaign"), dict):
                for campaign_key in ("customer_ready_continuous_discovery_campaign", "continuous_discovery_campaign"):
                    if isinstance(loaded.get(campaign_key), dict):
                        candidate["continuous_discovery_campaign"] = dict(loaded.get(campaign_key) or {})
                        break
            payload_candidates.append(candidate)
        payload_candidates.append(loaded)
        for candidate in payload_candidates:
            if isinstance(candidate.get("defects"), list):
                data = candidate
                break
        if data:
            break
    defects = data.get("defects") if isinstance(data, dict) else None
    if not isinstance(defects, list):
        return []
    current_campaign_scope = _current_campaign_scope_from_payload(data)

    probes: list[dict[str, Any]] = []
    for item in defects:
        if not isinstance(item, dict):
            continue
        if str(item.get("customer_delivery_status") or "").strip().lower() != "defect":
            continue
        if str(item.get("bug_status") or "").strip().lower() != "reproduced":
            continue
        if not bool(item.get("gate_passed")) or not bool(item.get("is_reproducible")):
            continue
        reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
        raw_evidence = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
        request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
        har_evidence = reproduction.get("har_evidence") if isinstance(reproduction.get("har_evidence"), dict) else {}
        expected_actual = item.get("expected_actual_comparison") if isinstance(item.get("expected_actual_comparison"), dict) else {}
        evidence_quality = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
        method = str(reproduction.get("method") or request_raw.get("method") or "GET").upper()
        path = str(reproduction.get("path") or request_raw.get("path") or "").strip()
        if not path:
            path = _extract_path_from_curl(str(reproduction.get("curl_command") or evidence_quality.get("curl_command") or ""))
        if not path:
            continue
        request_body = _extract_request_body_from_steps(reproduction.get("steps") or []) if isinstance(reproduction.get("steps"), list) else None
        probe: dict[str, Any] = {
            "regression_probe_id": f"CRD_REG_{item.get('id') or len(probes) + 1}",
            "issue_id": item.get("id") or item.get("issue_id"),
            "title": item.get("title") or item.get("business_summary") or f"已确认缺陷回归 {len(probes) + 1}",
            "risk_type": item.get("risk_type") or "business_risk",
            "severity": item.get("severity") or "P2",
            "method": method,
            "path": path,
            "actor": har_evidence.get("actor") or request_raw.get("actor") or "unspecified",
            "expected": expected_actual.get("expected") or item.get("expected") or "原缺陷信号不应复现，若缺少自动断言则应进入人工复核。",
            "source": "customer_ready_defect_data",
            "candidate_tier": "customer_ready_defect",
            "verification_status": ((item.get("evidence_status") or {}) if isinstance(item.get("evidence_status"), dict) else {}).get("final_review_status") or "validated_candidate",
            "verification_badge": "customer_ready_defect",
            "confidence_score": round(float(evidence_quality.get("score") or 0) / 100.0, 3),
            "high_confidence_candidate": False,
            "evidence_quality": dict(evidence_quality),
        }
        if request_body is not None:
            probe["request_body"] = request_body
        if current_campaign_scope:
            probe["current_campaign_scope"] = dict(current_campaign_scope)
        probes.append(probe)
    return probes


def _load_confirmed_findings_regression_probes(project: str, root: Path) -> list[dict[str, Any]]:
    """Convert persisted confirmed defects into durable regression probes.

    ``regression_runner`` already knows how to re-verify ``confirmed_findings.json``.
    The suite builder also needs to include the same confirmed defects in
    smoke/release/full suites so a customer can run one regression command after
    a fix and see whether every delivered bug is covered.

    This bridge only consumes existing evidence-backed confirmed findings. It
    skips entries without a replayable reproduction path and does not invent
    request payloads or expected statuses.
    """
    project = _safe_project_id(project)
    ws = root / "platform_workspace" / project / "defect_discovery"
    ledger = _load_json_safe(ws / "confirmed_findings.json", {})
    if not isinstance(ledger, dict) or not ledger:
        return []
    probes: list[dict[str, Any]] = []
    for evidence_id, defect in ledger.items():
        if not isinstance(defect, dict):
            continue
        reproduction = defect.get("reproduction") if isinstance(defect.get("reproduction"), dict) else {}
        raw_evidence = defect.get("raw_evidence") if isinstance(defect.get("raw_evidence"), dict) else {}
        request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
        response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
        evidence_quality = defect.get("evidence_quality") if isinstance(defect.get("evidence_quality"), dict) else {}
        method = str(reproduction.get("method") or request_raw.get("method") or "GET").upper()
        path = str(reproduction.get("path") or request_raw.get("path") or "").strip()
        if not path:
            path = _extract_path_from_curl(str(reproduction.get("curl_command") or evidence_quality.get("curl_command") or ""))
        if not path:
            continue
        request_body: Any = reproduction.get("request_body") if "request_body" in reproduction else reproduction.get("body")
        if request_body is None and isinstance(reproduction.get("steps"), list):
            request_body = _extract_request_body_from_steps(reproduction.get("steps") or [])
        confidence_raw = defect.get("confidence_score") or evidence_quality.get("score") or 0
        try:
            confidence = float(confidence_raw)
            if confidence > 1:
                confidence = confidence / 100.0
        except (TypeError, ValueError):
            confidence = 0.0
        probe: dict[str, Any] = {
            "regression_probe_id": f"CONFIRMED_REG_{evidence_id}",
            "issue_id": defect.get("issue_id") or defect.get("id") or evidence_id,
            "title": defect.get("title") or defect.get("business_summary") or f"已确认缺陷回归 {evidence_id}",
            "risk_type": reproduction.get("risk_type") or defect.get("risk_type") or defect.get("category") or defect.get("defect_family_label") or "business_risk",
            "severity": defect.get("severity") or "P2",
            "method": method,
            "path": path,
            "actor": reproduction.get("actor") or request_raw.get("actor") or defect.get("actor") or "same_reproduction_actor",
            "expected": defect.get("expected") or "原 confirmed 缺陷信号不应复现；若缺少强自动断言则进入人工复核。",
            "source": "confirmed_findings_ledger",
            "candidate_tier": "confirmed_customer_defect",
            "verification_status": "confirmed",
            "verification_badge": "confirmed_finding_regression",
            "confidence_score": round(max(0.0, min(confidence, 1.0)), 3),
            "high_confidence_candidate": True,
            "evidence_quality": dict(evidence_quality),
            "confirmed_evidence_id": str(evidence_id),
        }
        # ── System Behavior Space contract forwarding ──
        # Forward system promise metadata from the confirmed-findings ledger
        # so regression runner and regression history inherit the contract.
        _sb_promise_id = str(defect.get("system_promise_id") or "").strip()
        _sb_regression_contract = defect.get("regression_contract") if isinstance(defect.get("regression_contract"), dict) else {}
        _sb_evidence = defect.get("system_behavior_space_evidence") if isinstance(defect.get("system_behavior_space_evidence"), dict) else {}
        _sb_dimensions = defect.get("system_behavior_dimensions") if isinstance(defect.get("system_behavior_dimensions"), list) else []
        _sb_surface_plan = defect.get("system_behavior_surface_plan") if isinstance(defect.get("system_behavior_surface_plan"), list) else []
        _sb_required_assets = defect.get("system_behavior_required_assets") if isinstance(defect.get("system_behavior_required_assets"), list) else []
        _sb_source_family = str(defect.get("system_behavior_source_family") or "").strip()
        _sb_learning_signal = defect.get("learning_signal") if isinstance(defect.get("learning_signal"), dict) else {}
        if _sb_promise_id:
            probe["system_promise_id"] = _sb_promise_id
            probe["source"] = "confirmed_findings_system_promise_ledger"
            probe["verification_badge"] = "system_promise_regression"
            if _sb_source_family:
                probe["risk_type"] = _sb_source_family
        if _sb_regression_contract:
            probe["regression_contract"] = _sb_regression_contract
        if _sb_evidence:
            probe["system_behavior_space_evidence"] = _sb_evidence
        if _sb_dimensions:
            probe["system_behavior_dimensions"] = [str(item) for item in _sb_dimensions if str(item)]
        if _sb_surface_plan:
            probe["system_behavior_surface_plan"] = [str(item) for item in _sb_surface_plan if str(item)]
        if _sb_required_assets:
            probe["system_behavior_required_assets"] = [str(item) for item in _sb_required_assets if str(item)]
        if _sb_source_family:
            probe["system_behavior_source_family"] = _sb_source_family
        if _sb_learning_signal:
            probe["learning_signal"] = _sb_learning_signal
        current_campaign_scope = defect.get("current_campaign_scope") if isinstance(defect.get("current_campaign_scope"), dict) else {}
        if current_campaign_scope:
            probe["current_campaign_scope"] = dict(current_campaign_scope)
        if request_body is not None:
            probe["request_body"] = request_body
        buggy_status_code = response_raw.get("status_code") or request_raw.get("status_code")
        if buggy_status_code not in (None, ""):
            probe["buggy_status_code"] = buggy_status_code
        probes.append(probe)
    if _PROBE_ORACLE_ENRICHER is not None:
        probes = [_PROBE_ORACLE_ENRICHER(dict(item)) for item in probes]
    return probes


def _load_fix_regression_probes(project: str, root: Path) -> list[dict[str, Any]]:
    """Load traditional fix-regression probes plus confirmed defect obligations.

    Every evidence-backed confirmed finding with a replayable reproduction path is
    included as a durable regression probe, even when older fix-regression probe
    files already exist.  Existing approved candidate sources remain supported.
    """
    project = _safe_project_id(project)
    probes: list[dict[str, Any]] = []
    p = root / "platform_workspace" / project / "defect_discovery" / "fix_regression_probes.json"
    data = _load_json_safe(p, {})
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        probes.extend(i for i in data["items"] if isinstance(i, dict))
    elif isinstance(data, list):
        probes.extend(i for i in data if isinstance(i, dict))

    phase55 = root / "platform_workspace" / project / "defect_discovery" / "confirmed_bug_regression_candidates.json"
    phase55_data = _load_json_safe(phase55, {})
    phase55_items = phase55_data.get("items") if isinstance(phase55_data, dict) else phase55_data
    if isinstance(phase55_items, list):
        probes.extend(
            {**item, "source": item.get("source") or "phase55_confirmed_bug_flywheel"}
            for item in phase55_items
            if isinstance(item, dict) and item.get("approved") is True
        )

    ui_high_conf = root / "platform_workspace" / project / "defect_discovery" / "ui_high_confidence_regression_candidates.json"
    ui_high_conf_data = _load_json_safe(ui_high_conf, {})
    ui_high_conf_items = ui_high_conf_data.get("items") if isinstance(ui_high_conf_data, dict) else ui_high_conf_data
    if isinstance(ui_high_conf_items, list):
        probes.extend(
            {**item, "source": item.get("source") or "ui_high_confidence_candidate"}
            for item in ui_high_conf_items
            if isinstance(item, dict) and item.get("approved") is True
        )

    probes.extend(_load_confirmed_findings_regression_probes(project, root))
    probes.extend(_load_customer_ready_defect_probes(project, root))
    if probes:
        return probes

    # Fallback: infer candidates from fix verification result when probes have
    # not yet been materialized.
    result = _load_json_safe(root / "platform_outputs" / project / "fix_verification" / "fix_verification_result.json", {})
    if isinstance(result, dict):
        for item in result.get("items", []) or []:
            if isinstance(item, dict) and item.get("verification_status") in {"fixed", "still_failing", "needs_review"}:
                probes.append({
                    "regression_probe_id": f"REG_{item.get('verification_id') or item.get('issue_id')}",
                    "issue_id": item.get("issue_id"),
                    "title": item.get("title"),
                    "risk_type": item.get("risk_type") or "business_risk",
                    "severity": item.get("severity") or "P2",
                    "method": item.get("method") or (item.get("evidence", {}).get("request", {}) if isinstance(item.get("evidence"), dict) else {}).get("method") or "GET",
                    "path": item.get("path") or (item.get("evidence", {}).get("request", {}) if isinstance(item.get("evidence"), dict) else {}).get("url") or "/",
                    "actor": (item.get("evidence", {}).get("request", {}) if isinstance(item.get("evidence"), dict) else {}).get("actor") or "normal_user",
                    "expected": "原缺陷信号不应复现。",
                    "source": "fix_verification_result",
                })
    return probes


def _risk_score(probe: dict[str, Any]) -> float:
    severity = str(probe.get("severity") or "P2").upper()
    risk_type = str(probe.get("risk_type") or "business_risk").lower()
    method = str(probe.get("method") or "GET").upper()
    score = float(SEVERITY_WEIGHT.get(severity, 35))
    if risk_type in HIGH_RISK_TYPES:
        score += 18
    if method in DESTRUCTIVE_METHODS:
        score += 10
    if probe.get("source") == "fix_verification_loop":
        score += 10
    if probe.get("source") == "phase55_confirmed_bug_flywheel":
        score += 14
    if probe.get("source") == "confirmed_findings_ledger":
        score += 16
    if str(probe.get("issue_id") or ""):
        score += 4
    return round(score, 2)


def _normalize_probe(probe: dict[str, Any], index: int) -> dict[str, Any]:
    if _PROBE_ORACLE_ENRICHER is not None:
        probe = _PROBE_ORACLE_ENRICHER(dict(probe or {}))
    risk_type = _safe_text(probe.get("risk_type") or "business_risk", 100)
    method = _safe_text(probe.get("method") or "GET", 12).upper()
    path = _safe_text(probe.get("path") or probe.get("url") or "/", 300)
    title = _safe_text(probe.get("title") or probe.get("expected") or f"回归探针 {index}", 240)
    severity = _safe_text(probe.get("severity") or "P2", 16).upper()
    evidence_quality = probe.get("evidence_quality") if isinstance(probe.get("evidence_quality"), dict) else {}
    normalized = {
        "regression_probe_id": _normalize_probe_id(probe.get("regression_probe_id") or probe.get("probe_id") or probe.get("id"), index),
        "issue_id": _safe_text(probe.get("issue_id"), 120),
        "title": title,
        "module": _safe_text(probe.get("module") or _infer_module(path, risk_type, title), 80),
        "risk_type": risk_type,
        "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
        "method": method,
        "path": path,
        "actor": _safe_text(probe.get("actor") or "normal_user", 80),
        "expected": _safe_text(probe.get("expected") or "原缺陷信号不应复现，业务规则保持正确。", 1200),
        "source": _safe_text(probe.get("source") or "fix_regression_probes", 120),
        "destructive": _is_destructive(method, risk_type),
        "candidate_tier": _safe_text(probe.get("candidate_tier"), 80),
        "high_confidence_candidate": bool(probe.get("high_confidence_candidate") is True),
        "verification_status": _safe_text(probe.get("verification_status"), 40),
        "verification_badge": _safe_text(probe.get("verification_badge"), 40),
        "confidence_score": float(probe.get("confidence_score") or 0.0),
        "evidence_quality": dict(evidence_quality),
        "request_body": probe.get("request_body"),
    }
    if isinstance(probe.get("current_campaign_scope"), dict):
        normalized["current_campaign_scope"] = dict(probe.get("current_campaign_scope") or {})
    # Preserve structured-oracle fields that normalize would otherwise drop.
    for key in ("expected_status_code", "buggy_status_code", "regression_oracle", "confirmed_evidence_id"):
        if key in probe:
            normalized[key] = probe[key]
    normalized["priority_score"] = _risk_score(normalized)
    normalized["tags"] = [normalized["severity"], normalized["risk_type"], normalized["module"]]
    return normalized


def _dedupe_sort(probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for idx, raw in enumerate(probes, start=1):
        p = _normalize_probe(raw, idx)
        key = (p["method"], p["path"], p["risk_type"], p.get("issue_id") or p["regression_probe_id"])
        old = seen.get(key)
        if not old or p["priority_score"] > old["priority_score"]:
            seen[key] = p
    return sorted(seen.values(), key=lambda item: (-float(item.get("priority_score") or 0), str(item.get("module") or ""), str(item.get("path") or "")))


def _select_modes(probes: list[dict[str, Any]], cfg: dict[str, Any], options: dict[str, Any]) -> dict[str, dict[str, Any]]:
    max_smoke = int(options.get("max_smoke") or cfg.get("regression_smoke_max") or MODE_LIMITS["smoke"])
    max_release = int(options.get("max_release") or cfg.get("regression_release_max") or MODE_LIMITS["release"])
    max_full = int(options.get("max_full") or cfg.get("regression_full_max") or MODE_LIMITS["full"])
    non_destructive = [p for p in probes if not p.get("destructive")]
    p0p1 = [p for p in non_destructive if p.get("severity") in {"P0", "P1"}]
    high = [p for p in non_destructive if p.get("risk_type") in HIGH_RISK_TYPES]
    smoke_candidates = []
    seen_ids: set[str] = set()
    for p in [*p0p1, *high, *non_destructive]:
        pid = str(p.get("regression_probe_id"))
        if pid not in seen_ids:
            smoke_candidates.append(p)
            seen_ids.add(pid)
    # Release/full are manifests, not execution authorization. Keep every
    # evidence-backed probe—including writes—so confirmed write defects never
    # disappear from regression coverage. The runner must enforce the shared
    # non-production write gate before sending a mutating request.
    release_candidates = probes
    full_candidates = probes
    return {
        "smoke": {"mode": "smoke", "description": "发布前快速回归，只覆盖 P0/P1、高风险、非破坏性探针。", "items": smoke_candidates[:max_smoke]},
        "release": {"mode": "release", "description": "版本发布回归，保留全部证据探针；写探针执行时强制经过非生产环境门控。", "items": release_candidates[:max_release]},
        "full": {"mode": "full", "description": "完整回归套件，覆盖所有已沉淀修复回归探针。", "items": full_candidates[:max_full]},
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = [m for m in PRIVATE_MARKERS if m.lower() in text]
    return {"passed": not leaks, "checked": True, "leak_count": len(leaks)}


def _render_report(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    cards = "".join(
        f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>"
        for label, value in {
            "总探针": summary.get("total_probe_count"),
            "Smoke": summary.get("smoke_count"),
            "Release": summary.get("release_count"),
            "Full": summary.get("full_count"),
            "P0/P1": summary.get("p0_p1_count"),
            "CI 建议": summary.get("ci_gate_recommendation"),
        }.items()
    )
    rows = []
    for item in result.get("modes", {}).get("release", {}).get("items", [])[:120]:
        rows.append(
            "<tr>"
            f"<td>{_html_escape(item.get('priority_score'))}</td>"
            f"<td>{_html_escape(item.get('severity'))}</td>"
            f"<td>{_html_escape(item.get('module'))}</td>"
            f"<td>{_html_escape(item.get('risk_type'))}</td>"
            f"<td>{_html_escape(item.get('method'))} {_html_escape(item.get('path'))}</td>"
            f"<td>{_html_escape(item.get('title'))}</td>"
            "</tr>"
        )
    module_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{v}</td></tr>" for k, v in summary.get("module_distribution", {}).items())
    risk_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{v}</td></tr>" for k, v in summary.get("risk_distribution", {}).items())
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>回归套件构建器</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:24px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eff6ff;color:#1d4ed8}}</style></head><body>
<section class='hero'><span class='badge'>Phase31 Regression Suite</span><h1>{_html_escape(summary.get('project_name'))}</h1><p>将修复验证产生的回归探针组织为 smoke / release / full 三种长期运行套件，按模块、风险类型、严重等级排序，并输出 CI 可消费的套件清单。</p><p>生成时间：{_html_escape(summary.get('generated_at'))} · 私有数据隔离：{_html_escape(summary.get('private_leak_check_passed'))}</p></section>
<section class='panel'><h2>套件概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>Release 回归套件 Top 探针</h2><table><thead><tr><th>优先级</th><th>等级</th><th>模块</th><th>风险</th><th>接口</th><th>标题</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">暂无回归探针</td></tr>'}</tbody></table></section>
<section class='panel'><h2>分布</h2><div class='grid'><div><h3>模块</h3><table>{module_rows or '<tr><td>暂无</td><td>0</td></tr>'}</table></div><div><h3>风险类型</h3><table>{risk_rows or '<tr><td>暂无</td><td>0</td></tr>'}</table></div><div><h3>CI 建议</h3><p>{_html_escape(summary.get('ci_gate_recommendation'))}</p><p>如果 release 套件中 P0/P1 回归失败，建议阻断发布；P2 回归失败建议人工复核。</p></div></div></section>
</body></html>"""


def build_regression_suite(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    raw_probes = _load_fix_regression_probes(project, root)
    probes = _dedupe_sort(raw_probes)
    current_campaign_scope = next(
        (dict(probe.get("current_campaign_scope") or {}) for probe in probes if isinstance(probe.get("current_campaign_scope"), dict) and any((probe.get("current_campaign_scope") or {}).values())),
        {},
    )
    modes = _select_modes(probes, cfg, options)
    p0_p1_count = sum(1 for p in probes if p.get("severity") in {"P0", "P1"})
    destructive_count = sum(1 for p in probes if p.get("destructive"))
    confirmed_ledger_count = sum(1 for p in probes if p.get("source") == "confirmed_findings_ledger")
    ci_gate_recommendation = "run_release_suite"
    if not probes:
        ci_gate_recommendation = "no_regression_suite_yet"
    elif p0_p1_count >= 1:
        ci_gate_recommendation = "block_on_p0_p1_regression_failure"
    summary = {
        "phase": "phase31_regression_suite_builder",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_probe_count": len(probes),
        "smoke_count": len(modes["smoke"]["items"]),
        "release_count": len(modes["release"]["items"]),
        "full_count": len(modes["full"]["items"]),
        "p0_p1_count": p0_p1_count,
        "destructive_probe_count": destructive_count,
        "confirmed_ledger_probe_count": confirmed_ledger_count,
        "allow_destructive_regression": bool(options.get("allow_destructive_regression") or cfg.get("allow_destructive_tests")),
        "module_distribution": _count_by(probes, "module"),
        "risk_distribution": _count_by(probes, "risk_type"),
        "severity_distribution": _count_by(probes, "severity"),
        "source_distribution": _count_by(probes, "source"),
        "ci_gate_recommendation": ci_gate_recommendation,
    }
    if current_campaign_scope:
        summary["current_campaign_scope"] = current_campaign_scope
    ci_gate = {
        "project_id": project,
        "suite": "release",
        "gate_policy": {
            "fail_on_p0_p1_regression": True,
            "manual_review_on_p2_regression": True,
            "skip_destructive_by_default": not summary["allow_destructive_regression"],
            "destructive_execution_requires_nonproduction_gate": True,
        },
        "expected_exit_codes": {
            "passed": 0,
            "manual_review_required": 1,
            "failed": 2,
        },
        "recommendation": ci_gate_recommendation,
    }
    if current_campaign_scope:
        ci_gate["current_campaign_scope"] = current_campaign_scope
    result = {
        "phase": "phase31_regression_suite_builder",
        "project_id": project,
        "summary": summary,
        "modes": modes,
        "ci_gate": ci_gate,
    }
    if current_campaign_scope:
        result["current_campaign_scope"] = current_campaign_scope
    private_check = _private_leak_check(result)
    summary["private_leak_check_passed"] = private_check["passed"]
    result["private_leak_check"] = private_check

    out_dir = root / "platform_outputs" / project / "regression_suite"
    workspace_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "regression_suite.json", result)
    _write_json(out_dir / "regression_suite_summary.json", summary)
    _write_json(out_dir / "regression_suite_ci_gate.json", ci_gate)
    _write_text(out_dir / "regression_suite_report.html", _render_report(result))
    _write_json(workspace_dir / "regression_suite.json", result)
    _write_json(workspace_dir / "regression_suite_manifest.json", {"summary": summary, "artifacts": {"report_html": str((out_dir / 'regression_suite_report.html').relative_to(root)).replace('\\', '/')}})
    return result


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    allow_destructive = str(os.environ.get("ALLOW_DESTRUCTIVE_REGRESSION", "0")).lower() in {"1", "true", "yes", "on"}
    result = build_regression_suite(project, options={"allow_destructive_regression": allow_destructive})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
