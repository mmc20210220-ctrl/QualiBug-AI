from __future__ import annotations

"""Privacy-preserving platform-level behavior learning memory.

Learning has two scopes:

1. Project/private deployment memory
   - Uses local project evidence and regression outcomes.
   - Never leaves the customer's deployment in private mode.

2. SaaS/platform memory
   - Aggregates only sanitized pattern signals across projects/customers.
   - Must not contain customer raw titles, paths, payloads, evidence chains,
     screenshots, logs, table names, endpoint paths, or business data.
   - Feeds cold-start and cross-project probe prioritization.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .behavior_learning_memory import (
    BEHAVIOR_LEARNING_MEMORY_VERSION,
    build_behavior_learning_memory,
    load_behavior_learning_memory,
    persist_behavior_learning_memory,
)
from .real_project_onboarding import _safe_project_id

PLATFORM_BEHAVIOR_LEARNING_MEMORY_VERSION = "platform_behavior_learning_memory.v1"
PLATFORM_PROJECT_ID = "_platform"

_ALLOWED_SIGNAL_KEYS = {
    "dimensions",
    "surfaces",
    "severity",
    "learning_weight",
    "regression_status",
    "evidence_strength",
}


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


def _project_hash(project_id: str) -> str:
    return hashlib.sha256(_safe_project_id(project_id).encode("utf-8")).hexdigest()[:16]


def _platform_paths(root: Path) -> tuple[Path, Path]:
    return (
        root / "platform_workspace" / PLATFORM_PROJECT_ID / "behavior_learning_memory.json",
        root / "platform_outputs" / PLATFORM_PROJECT_ID / "behavior_learning_memory.json",
    )


def _entity_archetype(signal: dict[str, Any]) -> str:
    dims = {str(item) for item in signal.get("dimensions") or []}
    if "money" in dims:
        return "money_bearing_object"
    if "quantity" in dims:
        return "quantity_bearing_object"
    if "tenant" in dims:
        return "tenant_scoped_object"
    if "state" in dims:
        return "lifecycle_object"
    if "audit" in dims:
        return "audited_object"
    if "ui_api_contract" in dims:
        return "ui_api_contract_object"
    return "generic_business_object"


def sanitize_project_signal_for_platform(project_id: str, signal: dict[str, Any]) -> dict[str, Any]:
    """Return a shareable pattern signal with no customer raw data.

    The output intentionally drops title, endpoint path, evidence_id, raw entity,
    request/response, logs, DB table names and any evidence-chain payload.  It
    keeps only pattern-level learning dimensions and a salted project hash used
    for dedup/diversity accounting.
    """
    cleaned = {key: signal.get(key) for key in _ALLOWED_SIGNAL_KEYS if key in signal}
    cleaned["source_project_hash"] = _project_hash(project_id)
    cleaned["entity_archetype"] = _entity_archetype(signal)
    cleaned["signal_kind"] = str(signal.get("source") or "project_learning_signal")
    cleaned["learning_weight"] = float(cleaned.get("learning_weight") or 0.0)
    cleaned["dimensions"] = sorted({str(item) for item in cleaned.get("dimensions") or [] if str(item).strip()})
    cleaned["surfaces"] = sorted({str(item) for item in cleaned.get("surfaces") or [] if str(item).strip()})
    cleaned["platform_signal_id"] = hashlib.sha256(
        json.dumps(cleaned, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    return cleaned


def _summarize(signals: list[dict[str, Any]]) -> dict[str, Any]:
    by_dimension: dict[str, float] = {}
    by_surface: dict[str, float] = {}
    by_archetype: dict[str, float] = {}
    by_surface_combo: dict[str, float] = {}
    projects: set[str] = set()
    for signal in signals:
        weight = float(signal.get("learning_weight") or 0.0)
        project_hash = str(signal.get("source_project_hash") or "")
        if project_hash:
            projects.add(project_hash)
        for dim in signal.get("dimensions") or []:
            by_dimension[str(dim)] = by_dimension.get(str(dim), 0.0) + weight
        surfaces = sorted(str(item) for item in signal.get("surfaces") or [] if str(item))
        for surface in surfaces:
            by_surface[surface] = by_surface.get(surface, 0.0) + weight
        if surfaces:
            combo = "+".join(surfaces)
            by_surface_combo[combo] = by_surface_combo.get(combo, 0.0) + weight
        archetype = str(signal.get("entity_archetype") or "generic_business_object")
        by_archetype[archetype] = by_archetype.get(archetype, 0.0) + weight
    return {
        "platform_signal_count": len(signals),
        "contributing_project_count": len(projects),
        "top_dimensions": dict(sorted(by_dimension.items(), key=lambda kv: (-kv[1], kv[0]))[:30]),
        "top_surfaces": dict(sorted(by_surface.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
        "top_surface_combinations": dict(sorted(by_surface_combo.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
        "top_entity_archetypes": dict(sorted(by_archetype.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
    }


def build_platform_behavior_learning_memory(root: Path) -> dict[str, Any]:
    signals: dict[str, dict[str, Any]] = {}
    workspace_root = root / "platform_workspace"
    if workspace_root.exists():
        for project_dir in workspace_root.iterdir():
            if not project_dir.is_dir() or project_dir.name == PLATFORM_PROJECT_ID:
                continue
            memory_path = project_dir / "defect_discovery" / "behavior_learning_memory.json"
            memory = _read_json(memory_path, {})
            if not isinstance(memory, dict) or memory.get("version") != BEHAVIOR_LEARNING_MEMORY_VERSION:
                continue
            for signal in memory.get("signals") if isinstance(memory.get("signals"), list) else []:
                if not isinstance(signal, dict):
                    continue
                sanitized = sanitize_project_signal_for_platform(project_dir.name, signal)
                signals[sanitized["platform_signal_id"]] = sanitized
    rows = list(signals.values())[-5000:]
    summary = _summarize(rows)
    return {
        "version": PLATFORM_BEHAVIOR_LEARNING_MEMORY_VERSION,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "learning_scope": "platform_privacy_preserving_cross_project",
        "privacy_rule": "Stores only sanitized pattern signals. No customer titles, raw paths, payloads, evidence chains, screenshots, logs, table names, endpoint paths, or business data are retained.",
        "learning_goal": "Use cross-project sanitized defect and regression patterns to improve cold-start and future probe prioritization without leaking customer data.",
        "signals": rows,
        "summary": summary,
        "priority_boosts": {
            "dimensions": summary["top_dimensions"],
            "surfaces": summary["top_surfaces"],
            "surface_combinations": summary["top_surface_combinations"],
            "entity_archetypes": summary["top_entity_archetypes"],
        },
    }


def persist_platform_behavior_learning_memory(root: Path) -> dict[str, Any]:
    memory = build_platform_behavior_learning_memory(root)
    for path in _platform_paths(root):
        _write_json(path, memory)
    return memory


def refresh_learning_memories(project: str, root: Path, *, include_platform: bool = True) -> dict[str, Any]:
    project_memory = persist_behavior_learning_memory(project, root)
    platform_memory = persist_platform_behavior_learning_memory(root) if include_platform else {}
    return {
        "project_learning_memory": project_memory,
        "platform_learning_memory": platform_memory,
    }


def load_platform_behavior_learning_memory(root: Path) -> dict[str, Any]:
    for path in _platform_paths(root):
        data = _read_json(path, {})
        if isinstance(data, dict) and data.get("version") == PLATFORM_BEHAVIOR_LEARNING_MEMORY_VERSION:
            return data
    return {}


def apply_platform_learning_to_probe_candidates(space: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(space, dict) or not isinstance(memory, dict):
        return space
    boosts = memory.get("priority_boosts") if isinstance(memory.get("priority_boosts"), dict) else {}
    dim_boosts = boosts.get("dimensions") if isinstance(boosts.get("dimensions"), dict) else {}
    surface_boosts = boosts.get("surfaces") if isinstance(boosts.get("surfaces"), dict) else {}
    combo_boosts = boosts.get("surface_combinations") if isinstance(boosts.get("surface_combinations"), dict) else {}
    probes = space.get("probe_candidates") if isinstance(space.get("probe_candidates"), list) else []
    boosted = 0
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        base = float(probe.get("priority") or 0.0)
        surfaces = sorted(str(item) for item in probe.get("surface_plan") or [] if str(item))
        score = 0.0
        for surface in surfaces:
            score += min(float(surface_boosts.get(surface) or 0.0), 10.0) * 0.008
        combo = "+".join(surfaces)
        if combo:
            score += min(float(combo_boosts.get(combo) or 0.0), 10.0) * 0.01
        for intent in probe.get("oracle_intent") or []:
            dim = str(intent).split(":", 1)[-1]
            score += min(float(dim_boosts.get(dim) or 0.0), 10.0) * 0.01
        if score > 0:
            boosted += 1
            probe["base_priority"] = round(float(probe.get("base_priority") or base), 3)
            probe["platform_learning_boost"] = round(score, 3)
            probe["priority"] = round(min(0.99, base + score), 3)
            probe["platform_learning_memory_version"] = str(memory.get("version") or "")
    summary = space.get("summary") if isinstance(space.get("summary"), dict) else {}
    platform_summary = memory.get("summary") if isinstance(memory.get("summary"), dict) else {}
    summary["platform_learning_memory_version"] = str(memory.get("version") or "") if memory else ""
    summary["platform_learning_signal_count"] = int(platform_summary.get("platform_signal_count") or 0)
    summary["platform_learning_contributing_project_count"] = int(platform_summary.get("contributing_project_count") or 0)
    summary["platform_learning_boosted_probe_count"] = boosted
    space["summary"] = summary
    space["probe_candidates"] = sorted(probes, key=lambda item: (-float(item.get("priority") or 0), str(item.get("entity") or ""), str(item.get("probe_id") or "")))
    return space
