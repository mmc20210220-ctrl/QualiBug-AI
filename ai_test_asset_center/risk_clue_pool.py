"""
RiskCluePool — project and platform learning signals for future investigation.

This module existed before the behavior-space work.  It is now the single home
for learning feedback instead of introducing a separate memory subsystem.

Project/private deployment scope:
- keep blocked/inconclusive findings as project risk clues;
- accumulate project-local learning weights from clues, confirmed findings and
  regression history;
- learn directly from system-promise regression contracts when present;
- refresh project learning on demand before scheduling consumes weights;
- never requires customer data to leave the deployment.

SaaS/platform scope:
- aggregate only sanitized pattern weights across projects;
- never stores customer titles, endpoint paths, payloads, evidence chains,
  screenshots, logs, table names or business data.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

PROJECT_LEARNING_VERSION = "risk_clue_pool_project_learning.v3"
PLATFORM_LEARNING_VERSION = "risk_clue_pool_platform_learning.v1"
PLATFORM_PROJECT_ID = "_platform"

_DIMENSION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tenant_isolation", ("tenant", "租户", "cross-tenant", "跨租户", "隔离")),
    ("authorization_access_control", ("auth", "permission", "role", "rbac", "acl", "越权", "权限", "角色", "未授权", "普通用户", "管理员")),
    ("visibility_disclosure", ("visible", "visibility", "hidden", "leak", "泄露", "可见", "展示", "隐藏", "搜索", "列表")),
    ("state_machine", ("status", "state", "transition", "lifecycle", "状态", "流转", "终态")),
    ("money_quantity_conservation", ("amount", "price", "balance", "payment", "refund", "stock", "inventory", "quantity", "金额", "余额", "支付", "退款", "库存", "数量", "超卖")),
    ("idempotency", ("duplicate", "idempot", "retry", "重复", "幂等", "重放")),
    ("concurrency_race_condition", ("concurrent", "race", "parallel", "并发", "竞态", "同时")),
    ("data_consistency", ("consistency", "integrity", "drift", "dirty", "一致", "完整", "漂移", "脏数据")),
    ("audit_traceability", ("audit", "trace", "log", "审计", "追踪", "日志", "trace_id")),
    ("ui_api_contract_drift", ("ui", "button", "form", "frontend", "页面", "按钮", "表单", "前端")),
    ("performance_reliability", ("timeout", "latency", "slow", "性能", "超时", "慢", "失败重试", "恢复")),
    ("async_eventual_consistency", ("event", "queue", "message", "notification", "callback", "webhook", "事件", "队列", "消息", "通知", "回调", "异步")),
)

_SURFACE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("api", ("api", "http", "endpoint", "request", "response", "接口", "请求", "响应")),
    ("db", ("db", "database", "table", "sql", "snapshot", "数据库", "表", "字段")),
    ("ui", ("ui", "browser", "page", "dom", "screenshot", "页面", "浏览器", "截图")),
    ("auth", ("auth", "token", "role", "permission", "鉴权", "权限", "角色")),
    ("log", ("log", "trace", "audit", "日志", "审计", "追踪")),
    ("async", ("queue", "message", "event", "callback", "webhook", "队列", "消息", "事件", "回调")),
)

_DIMENSION_ALIASES = {
    "tenant": "tenant_isolation",
    "tenant_isolation": "tenant_isolation",
    "authorization": "authorization_access_control",
    "auth": "authorization_access_control",
    "role": "authorization_access_control",
    "permission": "authorization_access_control",
    "visibility": "visibility_disclosure",
    "privacy": "visibility_disclosure",
    "state": "state_machine",
    "lifecycle": "state_machine",
    "transition": "state_machine",
    "money": "money_quantity_conservation",
    "amount": "money_quantity_conservation",
    "quantity": "money_quantity_conservation",
    "conservation": "money_quantity_conservation",
    "data_conservation": "money_quantity_conservation",
    "data_consistency": "data_consistency",
    "cross_surface_consistency": "data_consistency",
    "ui_api_contract": "ui_api_contract_drift",
    "ui_contract": "ui_api_contract_drift",
    "validation": "ui_api_contract_drift",
    "audit": "audit_traceability",
    "traceability": "audit_traceability",
    "async": "async_eventual_consistency",
    "side_effect": "async_eventual_consistency",
    "eventual_consistency": "async_eventual_consistency",
    "concurrency": "concurrency_race_condition",
    "retry": "idempotency",
    "idempotency": "idempotency",
    "performance": "performance_reliability",
    "reliability": "performance_reliability",
    "historical_bug": "historical_regression",
    "regression": "historical_regression",
}

_SURFACE_ALIASES = {
    "api": "api",
    "http": "api",
    "endpoint": "api",
    "db": "db",
    "database": "db",
    "table": "db",
    "sql": "db",
    "ui": "ui",
    "browser": "ui",
    "page": "ui",
    "auth": "auth",
    "role": "auth",
    "permission": "auth",
    "log": "log",
    "trace": "log",
    "audit": "log",
    "async": "async",
    "queue": "async",
    "event": "async",
}


def save_risk_clues(
    project: str,
    root: Path,
    findings: list[dict[str, Any]],
    *,
    max_clues: int = 500,
) -> dict[str, Any]:
    """Save unconfirmed findings and refresh project/platform learning signals."""
    pool_dir = _project_pool_dir(project, root)
    pool_dir.mkdir(parents=True, exist_ok=True)
    pool_file = pool_dir / "risk_clues.json"

    existing: dict[str, dict[str, Any]] = {}
    previous = _read_json(pool_file, {})
    if isinstance(previous, dict):
        existing = previous.get("clues", {}) if isinstance(previous.get("clues"), dict) else {}

    now = _now()
    new_count = 0

    for f in findings or []:
        if not isinstance(f, dict):
            continue
        verdict = str(f.get("verdict", "")).lower()
        status = str(f.get("validation_status", "")).lower()
        title = str(f.get("title", ""))
        clue_reason = _classify_clue(f, verdict, status)
        if not clue_reason:
            continue
        clue_key = _clue_key(title)
        if clue_key in existing:
            entry = existing[clue_key]
            entry["seen_count"] = int(entry.get("seen_count") or 1) + 1
            entry["last_seen_utc"] = now
            entry["clue_reason"] = clue_reason
            entry["last_dimensions"] = _detect_dimensions(f)
            entry["last_surfaces"] = _detect_surfaces(f)
        else:
            existing[clue_key] = {
                "title": title,
                "severity": f.get("severity", "P2"),
                "category": f.get("category", "unknown"),
                "source": f.get("source", "unknown"),
                "clue_reason": clue_reason,
                "first_seen_utc": now,
                "last_seen_utc": now,
                "seen_count": 1,
                "evidence_snippet": str(f.get("evidence", ""))[:500],
                "description": str(f.get("description", ""))[:500],
                "last_dimensions": _detect_dimensions(f),
                "last_surfaces": _detect_surfaces(f),
            }
            new_count += 1

    sorted_clues = sorted(
        existing.values(),
        key=lambda c: (int(c.get("seen_count") or 0), str(c.get("last_seen_utc") or "")),
        reverse=True,
    )[:max_clues]
    clues_dict = {_clue_key(str(c.get("title") or "")): c for c in sorted_clues if str(c.get("title") or "")}

    project_learning = _write_project_learning(project, root, clues_dict, now=now, new_count=new_count)
    platform_learning = refresh_platform_learning(root)
    return {
        "total_clues": len(clues_dict),
        "new_this_scan": new_count,
        "project_learning_signal_count": int(project_learning.get("signal_count") or 0),
        "platform_learning_signal_count": int(platform_learning.get("signal_count") or 0),
    }


def get_risk_clues(project: str, root: Path) -> dict[str, Any]:
    """Retrieve risk clues for a project."""
    pool_file = _project_pool_dir(project, root) / "risk_clues.json"
    data = _read_json(pool_file, {})
    return data if isinstance(data, dict) else {"total_clues": 0, "clues": {}}


def refresh_project_learning(project: str, root: Path) -> dict[str, Any]:
    """Refresh project learning from current clues, confirmed findings and regression history.

    Coverage steering consumes project weights through ``get_project_learning_weights``.
    Refreshing here closes the loop after regression runs: newly written
    regression history, including system-promise regression contracts, is visible
    to the next scheduler call without waiting for another ``save_risk_clues``.
    """
    data = get_risk_clues(project, root)
    clues = data.get("clues") if isinstance(data.get("clues"), dict) else {}
    new_count = int(data.get("new_this_scan") or 0) if isinstance(data, dict) else 0
    return _write_project_learning(project, root, clues, now=_now(), new_count=new_count)


def get_project_learning(project: str, root: Path) -> dict[str, Any]:
    return refresh_project_learning(project, root)


def get_project_learning_weights(project: str, root: Path) -> dict[str, float]:
    learning = get_project_learning(project, root)
    weights = learning.get("priority_weights") if isinstance(learning.get("priority_weights"), dict) else {}
    return {str(k): float(v) for k, v in weights.items() if _is_number(v)}


def get_platform_learning(root: Path) -> dict[str, Any]:
    data = _read_json(_platform_pool_file(root), {})
    return data if isinstance(data, dict) else {}


def get_platform_learning_weights(root: Path) -> dict[str, float]:
    learning = get_platform_learning(root)
    weights = learning.get("priority_weights") if isinstance(learning.get("priority_weights"), dict) else {}
    return {str(k): float(v) for k, v in weights.items() if _is_number(v)}


def build_project_learning(
    project: str,
    root: Path,
    *,
    clues_dict: dict[str, dict[str, Any]] | None = None,
    current_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    for key, clue in (clues_dict or {}).items():
        signals.append(_project_signal("risk_clue", key, clue, default_weight=0.25))
    for index, finding in enumerate(current_findings or []):
        if not isinstance(finding, dict):
            continue
        if _is_confirmed_finding(finding):
            signals.append(_project_signal("confirmed_finding", str(finding.get("evidence_id") or index), finding, default_weight=_severity_weight(finding)))
    confirmed = _read_json(_defect_workspace(project, root) / "confirmed_findings.json", {})
    if isinstance(confirmed, dict):
        for evidence_id, finding in confirmed.items():
            if isinstance(finding, dict):
                chain = _read_json(_defect_workspace(project, root) / "evidence_chains" / f"{evidence_id}.json", {})
                signals.append(_project_signal("confirmed_finding", str(evidence_id), {"finding": finding, "evidence_chain": chain}, default_weight=_severity_weight(finding)))
    regression_history = _read_json(root / "platform_outputs" / _safe_project(project) / "regression_run" / "regression_run_history.json", [])
    if isinstance(regression_history, list):
        for run_index, run in enumerate(regression_history[-20:]):
            for item_index, item in enumerate(run.get("items") if isinstance(run, dict) and isinstance(run.get("items"), list) else []):
                if isinstance(item, dict):
                    signals.append(_project_signal("regression_history", f"{run_index}:{item_index}", item, default_weight=_regression_weight(item)))
    return _learning_payload(PROJECT_LEARNING_VERSION, "project_private_deployment", signals)


def refresh_platform_learning(root: Path) -> dict[str, Any]:
    sanitized: dict[str, dict[str, Any]] = {}
    workspace_root = root / "platform_outputs"
    if workspace_root.exists():
        for project_dir in workspace_root.iterdir():
            if not project_dir.is_dir() or project_dir.name == PLATFORM_PROJECT_ID:
                continue
            pool = _read_json(project_dir / "risk_clue_pool" / "risk_clues.json", {})
            project_learning = pool.get("project_learning") if isinstance(pool, dict) and isinstance(pool.get("project_learning"), dict) else {}
            for signal in project_learning.get("signals") if isinstance(project_learning.get("signals"), list) else []:
                if not isinstance(signal, dict):
                    continue
                row = _sanitize_for_platform(project_dir.name, signal)
                sanitized[str(row["platform_signal_id"])] = row
    signals = list(sanitized.values())[-5000:]
    payload = _learning_payload(PLATFORM_LEARNING_VERSION, "platform_saas_sanitized_cross_project", signals)
    payload["privacy_rule"] = (
        "Only sanitized pattern signals are stored. Customer titles, raw endpoint paths, payloads, evidence chains, "
        "screenshots, logs, table names and business data are not retained in platform learning."
    )
    payload["contributing_project_count"] = len({str(s.get("source_project_hash") or "") for s in signals if str(s.get("source_project_hash") or "")})
    _write_json(_platform_pool_file(root), payload)
    return payload


def _write_project_learning(project: str, root: Path, clues_dict: dict[str, dict[str, Any]], *, now: str, new_count: int) -> dict[str, Any]:
    clues = clues_dict if isinstance(clues_dict, dict) else {}
    project_learning = build_project_learning(project, root, clues_dict=clues, current_findings=[])
    pool_data = {
        "phase": "risk_clue_pool_v3_project_learning",
        "project": project,
        "updated_at_utc": now,
        "total_clues": len(clues),
        "new_this_scan": max(0, int(new_count or 0)),
        "clues": clues,
        "project_learning": project_learning,
    }
    _write_json(_project_pool_dir(project, root) / "risk_clues.json", pool_data)
    return project_learning


def _learning_payload(version: str, scope: str, signals: list[dict[str, Any]]) -> dict[str, Any]:
    weights: dict[str, float] = {}
    surface_combo_weights: dict[str, float] = {}
    system_promise_signal_count = 0
    for signal in signals:
        weight = float(signal.get("learning_weight") or 0.0)
        if signal.get("system_promise_signal"):
            system_promise_signal_count += 1
        for dimension in signal.get("dimensions") or []:
            weights[str(dimension)] = weights.get(str(dimension), 0.0) + weight
        for surface in signal.get("surfaces") or []:
            key = f"surface:{surface}"
            weights[key] = weights.get(key, 0.0) + weight
        surfaces = sorted(str(s) for s in signal.get("surfaces") or [] if str(s))
        if len(surfaces) >= 2:
            combo = "+".join(surfaces)
            surface_combo_weights[combo] = surface_combo_weights.get(combo, 0.0) + weight
            weights[f"surface_combo:{combo}"] = weights.get(f"surface_combo:{combo}", 0.0) + weight
        archetype = str(signal.get("entity_archetype") or "")
        if archetype:
            weights[f"archetype:{archetype}"] = weights.get(f"archetype:{archetype}", 0.0) + weight
    return {
        "version": version,
        "scope": scope,
        "updated_at_utc": _now(),
        "signal_count": len(signals),
        "system_promise_signal_count": system_promise_signal_count,
        "signals": signals[-500:],
        "priority_weights": dict(sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:80]),
        "surface_combo_weights": dict(sorted(surface_combo_weights.items(), key=lambda kv: (-kv[1], kv[0]))[:40]),
    }


def _project_signal(source: str, signal_id: str, payload: dict[str, Any], *, default_weight: float) -> dict[str, Any]:
    dimensions = _detect_dimensions(payload)
    surfaces = _detect_surfaces(payload)
    contract = _system_contract(payload)
    system_promise_signal = bool(contract.get("promise_id") or _deep_get(payload, "system_promise_id"))
    signal = {
        "signal_id": f"{source}:{signal_id}",
        "source": source,
        "signal_kind": "system_behavior_promise" if system_promise_signal else "behavior_observation",
        "entity_hint": _entity_hint(payload),
        "entity_archetype": _entity_archetype(dimensions),
        "severity": str(_deep_get(payload, "severity") or "P2")[:10],
        "dimensions": dimensions,
        "surfaces": surfaces,
        "learning_weight": max(0.0, float(default_weight)),
        "system_promise_signal": system_promise_signal,
    }
    if contract:
        signal["contract_type"] = str(contract.get("contract_type") or "system_behavior_promise_regression")[:120]
        signal["source_family"] = str(contract.get("source_family") or "")[:120]
    regression_status = str(_deep_get(payload, "status") or _deep_get(payload, "lifecycle") or "")
    if regression_status:
        signal["regression_status"] = regression_status[:80]
    return signal


def _sanitize_for_platform(project: str, signal: dict[str, Any]) -> dict[str, Any]:
    row = {
        "source_project_hash": hashlib.sha256(_safe_project(project).encode("utf-8")).hexdigest()[:16],
        "signal_source": str(signal.get("source") or "project_signal")[:80],
        "signal_kind": str(signal.get("signal_kind") or "behavior_observation")[:80],
        "entity_archetype": str(signal.get("entity_archetype") or "generic_business_object")[:80],
        "severity": str(signal.get("severity") or "P2")[:10],
        "dimensions": sorted({str(x) for x in signal.get("dimensions") or [] if str(x)}),
        "surfaces": sorted({str(x) for x in signal.get("surfaces") or [] if str(x)}),
        "learning_weight": float(signal.get("learning_weight") or 0.0),
        "regression_status": str(signal.get("regression_status") or "")[:80],
        "system_promise_signal": bool(signal.get("system_promise_signal")),
    }
    row["platform_signal_id"] = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    return row


def _system_contract(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        contract = value.get("regression_contract") if isinstance(value.get("regression_contract"), dict) else {}
        if isinstance(contract.get("system_behavior_space"), dict) or str(contract.get("promise_id") or ""):
            return contract
        hints = value.get("system_behavior_space_evidence") if isinstance(value.get("system_behavior_space_evidence"), dict) else {}
        if hints:
            return {
                "contract_type": "system_behavior_promise_regression",
                "system_behavior_space": hints,
                "promise_id": str(hints.get("promise_id") or value.get("system_promise_id") or ""),
                "dimensions": hints.get("dimensions") or value.get("system_behavior_dimensions") or [],
                "surface_plan": hints.get("surface_plan") or value.get("system_behavior_surface_plan") or [],
                "required_assets": hints.get("required_assets") or value.get("system_behavior_required_assets") or [],
                "source_family": hints.get("source_family") or value.get("system_behavior_source_family") or "",
            }
        for child in value.values():
            found = _system_contract(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _system_contract(child)
            if found:
                return found
    return {}


def _structured_dimensions(value: Any) -> list[str]:
    found: list[str] = []
    contract = _system_contract(value)
    candidates: list[Any] = []
    if contract:
        candidates.extend(contract.get("dimensions") or [])
        hints = contract.get("system_behavior_space") if isinstance(contract.get("system_behavior_space"), dict) else {}
        candidates.extend(hints.get("dimensions") or [])
    explicit = _deep_get(value, "system_behavior_dimensions")
    if isinstance(explicit, list):
        candidates.extend(explicit)
    for item in candidates:
        canonical = _canonical_dimension(str(item or ""))
        if canonical:
            found.append(canonical)
    return sorted(set(found))


def _structured_surfaces(value: Any) -> list[str]:
    found: list[str] = []
    contract = _system_contract(value)
    candidates: list[Any] = []
    if contract:
        candidates.extend(contract.get("surface_plan") or [])
        hints = contract.get("system_behavior_space") if isinstance(contract.get("system_behavior_space"), dict) else {}
        candidates.extend(hints.get("surface_plan") or [])
    explicit = _deep_get(value, "system_behavior_surface_plan")
    if isinstance(explicit, list):
        candidates.extend(explicit)
    for item in candidates:
        canonical = _canonical_surface(str(item or ""))
        if canonical:
            found.append(canonical)
    return sorted(set(found))


def _canonical_dimension(value: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    if not key:
        return ""
    return _DIMENSION_ALIASES.get(key, key)


def _canonical_surface(value: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    if not key:
        return ""
    return _SURFACE_ALIASES.get(key, key)


def _detect_dimensions(value: Any) -> list[str]:
    text = _blob(value)
    dims = [_canonical_dimension(dimension) for dimension, keys in _DIMENSION_KEYWORDS if any(key.lower() in text for key in keys)]
    dims.extend(_structured_dimensions(value))
    return sorted({dim for dim in dims if dim})


def _detect_surfaces(value: Any) -> list[str]:
    text = _blob(value)
    surfaces = [_canonical_surface(surface) for surface, keys in _SURFACE_KEYWORDS if any(key.lower() in text for key in keys)]
    surfaces.extend(_structured_surfaces(value))
    if not surfaces and any(token in text for token in ("/api/", "http", "endpoint")):
        surfaces.append("api")
    return sorted({surface for surface in surfaces if surface})


def _blob(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str).lower()
    except Exception:
        return str(value or "").lower()


def _entity_hint(value: Any) -> str:
    text = _blob(value)
    path = re.search(r"/[a-z0-9_./{}-]+", text)
    if path:
        parts = [p for p in path.group(0).strip("/").split("/") if p and not p.startswith("{")]
        if parts:
            return re.sub(r"[^a-z0-9_\-]+", "_", parts[-1].lower()).strip("_")[:80]
    for key in ("entity", "business_object", "module", "category", "risk_type"):
        val = _deep_get(value, key)
        if val:
            return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(val).lower()).strip("_")[:80]
    return "system"


def _entity_archetype(dimensions: list[str]) -> str:
    dims = set(dimensions)
    if "money_quantity_conservation" in dims:
        return "conservation_object"
    if "tenant_isolation" in dims:
        return "tenant_scoped_object"
    if "state_machine" in dims:
        return "lifecycle_object"
    if "audit_traceability" in dims:
        return "audited_object"
    if "ui_api_contract_drift" in dims:
        return "ui_api_contract_object"
    return "generic_business_object"


def _deep_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        for child in value.values():
            found = _deep_get(child, key)
            if found not in (None, ""):
                return found
    if isinstance(value, list):
        for child in value:
            found = _deep_get(child, key)
            if found not in (None, ""):
                return found
    return None


def _is_confirmed_finding(finding: dict[str, Any]) -> bool:
    verdict = str(finding.get("verdict") or finding.get("confirmation_status") or "").lower()
    return verdict == "confirmed" or finding.get("gate_passed") is True or str(finding.get("business_evidence_status") or "").lower() == "confirmed"


def _severity_weight(finding: dict[str, Any]) -> float:
    return {"P0": 1.0, "P1": 0.85, "P2": 0.55, "P3": 0.25}.get(str(finding.get("severity") or "P2").upper(), 0.45)


def _regression_weight(item: dict[str, Any]) -> float:
    return {"failed": 0.8, "passed": 0.25, "needs_review": 0.2, "skipped": 0.05}.get(str(item.get("status") or "").lower(), 0.1)


def _classify_clue(finding: dict[str, Any], verdict: str, status: str) -> str:
    """Classify why this finding should be saved as a clue."""
    if verdict in ("inconclusive", "blocked"):
        return "inconclusive"
    if "sandbox" in status or "blocked_requires_sandbox" in status:
        return "blocked_requires_sandbox"
    if "auth" in status or "blocked_missing" in status:
        return "need_auth"
    if "data" in status or "missing_test_data" in status:
        return "need_data"
    if "state" in status:
        return "need_state"
    if verdict == "falsified":
        return ""
    if status.startswith("blocked_"):
        return "blocked"
    if verdict == "needs_more_evidence":
        return "needs_more_evidence"
    return ""


def _clue_key(title: str) -> str:
    """Generate a stable key from finding title."""
    return hashlib.md5(title.strip().lower().encode()).hexdigest()[:16]


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_project(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._") or "unscoped"


def _project_pool_dir(project: str, root: Path) -> Path:
    return root / "platform_outputs" / _safe_project(project) / "risk_clue_pool"


def _defect_workspace(project: str, root: Path) -> Path:
    return root / "platform_workspace" / _safe_project(project) / "defect_discovery"


def _platform_pool_file(root: Path) -> Path:
    return root / "platform_outputs" / PLATFORM_PROJECT_ID / "risk_clue_pool" / "platform_learning.json"


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False
