from __future__ import annotations

"""Behavior Learning Memory.

This module closes the product-core learning loop:

    confirmed defect / evidence chain / regression outcome
        -> learning signals
        -> stronger future probe prioritization

It is intentionally not a fake "AI training" claim.  It is a deterministic,
inspectable project memory that learns which system promises were violated,
which surfaces produced hard evidence, and which regression probes actually
closed or persisted.  Future behavior-space probe candidates can then be boosted
by those signals instead of starting cold every scan.
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import _safe_project_id

BEHAVIOR_LEARNING_MEMORY_VERSION = "behavior_learning_memory.v1"

_DIMENSION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tenant", ("tenant", "租户", "cross-tenant", "跨租户", "隔离")),
    ("authorization", ("auth", "permission", "role", "rbac", "acl", "越权", "权限", "角色", "未授权")),
    ("visibility", ("visible", "visibility", "hidden", "leak", "泄露", "可见", "展示", "隐藏", "搜索", "列表")),
    ("state", ("status", "state", "transition", "lifecycle", "状态", "流转", "终态")),
    ("money", ("amount", "price", "balance", "payment", "refund", "金额", "余额", "支付", "退款", "价格")),
    ("quantity", ("stock", "inventory", "quantity", "库存", "数量", "超卖")),
    ("idempotency", ("duplicate", "idempot", "retry", "重复", "幂等", "重放")),
    ("concurrency", ("concurrent", "race", "parallel", "并发", "竞态", "同时")),
    ("data_consistency", ("consistency", "integrity", "drift", "dirty", "一致", "完整", "漂移", "脏数据")),
    ("audit", ("audit", "trace", "log", "审计", "追踪", "日志", "trace_id")),
    ("ui_api_contract", ("ui", "button", "form", "frontend", "页面", "按钮", "表单", "前端")),
    ("performance", ("timeout", "latency", "slow", "性能", "超时", "慢")),
    ("async_side_effect", ("event", "queue", "message", "notification", "callback", "事件", "队列", "消息", "通知", "回调")),
)

_SURFACE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("api", ("api", "http", "endpoint", "request", "response", "接口", "请求", "响应")),
    ("db", ("db", "database", "table", "sql", "snapshot", "数据库", "表", "字段")),
    ("ui", ("ui", "browser", "page", "dom", "screenshot", "页面", "浏览器", "截图")),
    ("auth", ("auth", "token", "role", "permission", "鉴权", "权限", "角色")),
    ("log", ("log", "trace", "audit", "日志", "审计", "追踪")),
    ("async", ("queue", "message", "event", "callback", "webhook", "队列", "消息", "事件", "回调")),
)


def _safe_text(value: Any, limit: int = 2000) -> str:
    return str(value or "")[:limit]


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _memory_paths(project: str, root: Path) -> tuple[Path, Path]:
    safe = _safe_project_id(project)
    return (
        root / "platform_workspace" / safe / "defect_discovery" / "behavior_learning_memory.json",
        root / "platform_outputs" / safe / "behavior_learning_memory.json",
    )


def _text_blob(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str).lower()
    except Exception:
        return str(value or "").lower()


def _detect_dimensions(value: Any) -> list[str]:
    text = _text_blob(value)
    return [dimension for dimension, keys in _DIMENSION_KEYWORDS if any(key.lower() in text for key in keys)]


def _detect_surfaces(value: Any) -> list[str]:
    text = _text_blob(value)
    return [surface for surface, keys in _SURFACE_KEYWORDS if any(key.lower() in text for key in keys)]


def _entity_from_finding(item: dict[str, Any]) -> str:
    for key in ("entity", "business_object", "module", "object", "resource"):
        text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(item.get(key) or "").strip().lower()).strip("_")
        if text:
            return text[:80]
    path = _safe_text(item.get("repro_path") or item.get("_api_path") or item.get("path"), 300)
    if path:
        parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
        if parts:
            return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", parts[-1].lower()).strip("_")[:80]
    return "system"


def _signal_from_confirmed_finding(evidence_id: str, finding: dict[str, Any], chain: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"finding": finding, "evidence_chain": chain or {}}
    dims = _detect_dimensions(payload)
    surfaces = _detect_surfaces(payload)
    if not surfaces:
        repro = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
        if repro.get("method") or repro.get("path") or finding.get("_api_path"):
            surfaces.append("api")
    if not dims:
        risk = str(finding.get("risk_type") or finding.get("category") or "").strip()
        if risk:
            dims.append(risk[:80])
    return {
        "signal_id": f"confirmed:{evidence_id}",
        "source": "confirmed_finding",
        "evidence_id": evidence_id,
        "entity": _entity_from_finding(finding),
        "title": _safe_text(finding.get("title") or finding.get("name"), 300),
        "severity": _safe_text(finding.get("severity") or "P2", 10),
        "dimensions": sorted(set(dims)),
        "surfaces": sorted(set(surfaces)),
        "learning_weight": 1.0 if str(finding.get("severity") or "").upper() in {"P0", "P1"} else 0.65,
        "evidence_strength": _safe_text(finding.get("evidence_strength") or finding.get("business_evidence_status") or "confirmed", 80),
    }


def _signals_from_regression_history(history: Any) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if not isinstance(history, list):
        return signals
    for run_index, run in enumerate(history[-20:]):
        if not isinstance(run, dict):
            continue
        for item in run.get("items") if isinstance(run.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().lower()
            if status not in {"passed", "failed", "needs_review", "skipped"}:
                continue
            payload = {"run": run, "item": item}
            dims = _detect_dimensions(payload) or [str(item.get("risk_type") or "unknown")]
            surfaces = _detect_surfaces(payload) or (["api"] if item.get("path") else [])
            weight = {"failed": 0.8, "passed": 0.35, "needs_review": 0.2, "skipped": 0.05}.get(status, 0.1)
            signals.append({
                "signal_id": f"regression:{run_index}:{item.get('regression_probe_id') or item.get('issue_id') or len(signals)}",
                "source": "regression_history",
                "entity": _entity_from_finding(item),
                "title": _safe_text(item.get("title"), 300),
                "severity": _safe_text(item.get("severity") or "P2", 10),
                "dimensions": sorted(set(dims)),
                "surfaces": sorted(set(surfaces)),
                "regression_status": status,
                "learning_weight": weight,
            })
    return signals


def _summarize(signals: list[dict[str, Any]]) -> dict[str, Any]:
    by_dimension: dict[str, float] = {}
    by_surface: dict[str, float] = {}
    by_entity: dict[str, float] = {}
    for signal in signals:
        weight = float(signal.get("learning_weight") or 0.0)
        entity = str(signal.get("entity") or "system")
        by_entity[entity] = by_entity.get(entity, 0.0) + weight
        for dim in signal.get("dimensions") or []:
            by_dimension[str(dim)] = by_dimension.get(str(dim), 0.0) + weight
        for surface in signal.get("surfaces") or []:
            by_surface[str(surface)] = by_surface.get(str(surface), 0.0) + weight
    return {
        "signal_count": len(signals),
        "top_dimensions": dict(sorted(by_dimension.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
        "top_surfaces": dict(sorted(by_surface.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
        "top_entities": dict(sorted(by_entity.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
    }


def build_behavior_learning_memory(project: str, root: Path) -> dict[str, Any]:
    safe = _safe_project_id(project)
    workspace = root / "platform_workspace" / safe / "defect_discovery"
    outputs = root / "platform_outputs" / safe
    ledger = _read_json(workspace / "confirmed_findings.json", {})
    signals: list[dict[str, Any]] = []
    if isinstance(ledger, dict):
        for evidence_id, finding in ledger.items():
            if not isinstance(finding, dict):
                continue
            chain = _read_json(workspace / "evidence_chains" / f"{evidence_id}.json", {})
            signals.append(_signal_from_confirmed_finding(str(evidence_id), finding, chain if isinstance(chain, dict) else {}))
    history = _read_json(outputs / "regression_run" / "regression_run_history.json", [])
    signals.extend(_signals_from_regression_history(history))
    summary = _summarize(signals)
    return {
        "version": BEHAVIOR_LEARNING_MEMORY_VERSION,
        "project_id": safe,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "learning_goal": "Use confirmed defects, evidence chains and post-fix regression outcomes to prioritize future system-promise probes. This is project learning memory, not a fixed bug-type list.",
        "signals": signals[-500:],
        "summary": summary,
        "priority_boosts": {
            "dimensions": summary["top_dimensions"],
            "surfaces": summary["top_surfaces"],
            "entities": summary["top_entities"],
        },
    }


def persist_behavior_learning_memory(project: str, root: Path) -> dict[str, Any]:
    memory = build_behavior_learning_memory(project, root)
    for path in _memory_paths(project, root):
        _write_json(path, memory)
    return memory


def load_behavior_learning_memory(project: str, root: Path) -> dict[str, Any]:
    for path in _memory_paths(project, root):
        data = _read_json(path, {})
        if isinstance(data, dict) and data.get("version") == BEHAVIOR_LEARNING_MEMORY_VERSION:
            return data
    return {}


def apply_learning_to_probe_candidates(space: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(space, dict) or not isinstance(memory, dict):
        return space
    boosts = memory.get("priority_boosts") if isinstance(memory.get("priority_boosts"), dict) else {}
    dim_boosts = boosts.get("dimensions") if isinstance(boosts.get("dimensions"), dict) else {}
    surface_boosts = boosts.get("surfaces") if isinstance(boosts.get("surfaces"), dict) else {}
    entity_boosts = boosts.get("entities") if isinstance(boosts.get("entities"), dict) else {}
    probes = space.get("probe_candidates") if isinstance(space.get("probe_candidates"), list) else []
    learned_count = 0
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        base = float(probe.get("priority") or 0.0)
        score = 0.0
        entity = str(probe.get("entity") or "")
        score += min(float(entity_boosts.get(entity) or 0.0), 5.0) * 0.025
        for surface in probe.get("surface_plan") or []:
            score += min(float(surface_boosts.get(str(surface)) or 0.0), 5.0) * 0.018
        for intent in probe.get("oracle_intent") or []:
            dim = str(intent).split(":", 1)[-1]
            score += min(float(dim_boosts.get(dim) or 0.0), 5.0) * 0.022
        if score > 0:
            learned_count += 1
            probe["base_priority"] = round(base, 3)
            probe["learning_boost"] = round(score, 3)
            probe["priority"] = round(min(0.99, base + score), 3)
            probe["learning_memory_version"] = str(memory.get("version") or "")
    summary = space.get("summary") if isinstance(space.get("summary"), dict) else {}
    summary["learning_memory_version"] = str(memory.get("version") or "") if memory else ""
    summary["learning_signal_count"] = int((memory.get("summary") or {}).get("signal_count") or 0) if isinstance(memory.get("summary"), dict) else 0
    summary["learning_boosted_probe_count"] = learned_count
    space["summary"] = summary
    space["probe_candidates"] = sorted(probes, key=lambda item: (-float(item.get("priority") or 0), str(item.get("entity") or ""), str(item.get("probe_id") or "")))
    return space
