from __future__ import annotations

"""Consent-gated, metadata-only cross-industry confirmation learning.

A confirmed defect in one customer project is not automatically transferable to
another.  This module aggregates only approved flywheel metadata from projects
that explicitly opt in, and it promotes a reusable priority hint only after the
same risk/oracle family is independently confirmed in at least two industries.
No project identifier, raw payload, endpoint value or review note is exposed in
the aggregate artifact.
"""

import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _load_json, _safe_project_id, _write_json


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 18) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _project_opt_in(project_dir: Path) -> tuple[bool, list[str]]:
    cfg = _load_json(project_dir / "real_project_config.json", {})
    if not isinstance(cfg, dict):
        return False, []
    section = cfg.get("cross_industry_learning") or {}
    if not isinstance(section, dict) or not bool(section.get("share_confirmed_metadata")):
        return False, []
    industries = section.get("industries") or section.get("industry") or cfg.get("industry") or cfg.get("industry_hint") or []
    if isinstance(industries, str):
        industries = [industries]
    clean = sorted({str(item).strip().lower() for item in industries if str(item).strip()})
    return bool(clean), clean


def _approved_registry(profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows = profile.get("registry") or []
    return [row for row in rows if isinstance(row, dict) and int(row.get("approved_confirmation_count") or 0) > 0 and str(row.get("learning_status") or "") == "approved"]


def _output_paths(root: Path) -> dict[str, Path]:
    base = root / "platform_outputs" / "cross_industry_confirmed_learning"
    return {"out": base, "profile": base / "cross_industry_confirmed_learning_profile.json"}


def build_cross_industry_confirmed_learning(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    input_root = root / "platform_inputs"
    workspace_root = root / "platform_workspace"
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    opted_in_project_count = 0
    contributing_observation_count = 0
    for project_dir in sorted(input_root.iterdir()) if input_root.exists() else []:
        if not project_dir.is_dir():
            continue
        opted_in, industries = _project_opt_in(project_dir)
        if not opted_in:
            continue
        project = _safe_project_id(project_dir.name)
        profile_path = workspace_root / project / "defect_discovery" / "confirmed_bug_flywheel_profile.json"
        profile = _load_json(profile_path, {})
        if not isinstance(profile, dict):
            continue
        opted_in_project_count += 1
        for row in _approved_registry(profile):
            risk = str(row.get("risk_type") or "business_rule")[:120]
            family = str(row.get("oracle_family") or "confirmed_bug")[:160]
            key = (risk, family)
            item = grouped.setdefault(key, {"risk_type": risk, "oracle_family": family, "industries": set(), "contributing_project_count": 0, "confirmed_observation_count": 0, "severity_distribution": defaultdict(int), "root_cause_categories": set()})
            item["industries"].update(industries)
            item["contributing_project_count"] += 1
            item["confirmed_observation_count"] += int(row.get("approved_confirmation_count") or 0)
            for severity, count in (row.get("severity_distribution") or {}).items():
                item["severity_distribution"][str(severity)] += int(count or 0)
            item["root_cause_categories"].update(str(key) for key in (row.get("root_cause_distribution") or {}).keys() if str(key))
            contributing_observation_count += int(row.get("approved_confirmation_count") or 0)
    transfer_patterns: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for item in grouped.values():
        industries = sorted(item["industries"])
        row = {
            "pattern_id": f"XIL_{_hash([item['risk_type'], item['oracle_family'], industries])}",
            "risk_type": item["risk_type"],
            "oracle_family": item["oracle_family"],
            "industries": industries,
            "independent_industry_count": len(industries),
            "contributing_project_count": int(item["contributing_project_count"]),
            "confirmed_observation_count": int(item["confirmed_observation_count"]),
            "severity_distribution": dict(item["severity_distribution"]),
            "root_cause_categories": sorted(item["root_cause_categories"])[:12],
            "evidence_policy": "approved_human_confirmation_metadata_only",
            "raw_project_identifiers_persisted": False,
            "raw_payloads_persisted": False,
        }
        if len(industries) >= 2:
            row.update({"status": "cross_industry_transfer_hint", "priority_bonus": round(min(0.06, 0.02 + 0.01 * min(len(industries), 3) + 0.005 * min(item["confirmed_observation_count"], 4)), 3), "requires_local_evidence": True})
            transfer_patterns.append(row)
        else:
            row.update({"status": "needs_independent_second_industry_confirmation", "priority_bonus": 0.0, "requires_local_evidence": True})
            candidates.append(row)
    profile = {
        "phase": "phase72_cross_industry_confirmed_learning",
        "generated_at_utc": _now(),
        "summary": {
            "opted_in_project_count": opted_in_project_count,
            "approved_confirmed_observation_count": contributing_observation_count,
            "cross_industry_transfer_pattern_count": len(transfer_patterns),
            "needs_second_industry_confirmation_count": len(candidates),
        },
        "transfer_patterns": sorted(transfer_patterns, key=lambda row: (-float(row.get("priority_bonus") or 0), row["risk_type"], row["oracle_family"])),
        "candidate_patterns": sorted(candidates, key=lambda row: (-int(row.get("confirmed_observation_count") or 0), row["risk_type"], row["oracle_family"])),
        "governance": {
            "explicit_project_opt_in_required": True,
            "independent_two_industry_confirmation_required_for_transfer_bonus": True,
            "transfer_affects_priority_only": True,
            "local_deterministic_evidence_required_for_formal_finding": True,
            "raw_payloads_notes_and_project_identifiers_not_persisted": True,
        },
    }
    paths = _output_paths(root)
    paths["out"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["profile"], profile)
    return profile


def load_cross_industry_confirmed_learning(root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    data = _load_json(_output_paths(root)["profile"], {})
    return data if isinstance(data, dict) and data else None


def annotate_probes_with_cross_industry_learning(probes: list[dict[str, Any]], root: Path | None = None) -> list[dict[str, Any]]:
    """Attach a small priority-only hint; never alter severity or execution policy."""
    root = root or ROOT
    profile = load_cross_industry_confirmed_learning(root) or build_cross_industry_confirmed_learning(root)
    by_key = {(str(row.get("risk_type") or ""), str(row.get("oracle_family") or "")): row for row in profile.get("transfer_patterns") or [] if isinstance(row, dict)}
    output: list[dict[str, Any]] = []
    for source in probes:
        probe = dict(source)
        family = str(probe.get("oracle_family") or probe.get("source") or "")
        key = (str(probe.get("risk_type") or ""), family)
        match = by_key.get(key)
        if match:
            bonus = float(match.get("priority_bonus") or 0.0)
            probe["cross_industry_confirmed_learning_bonus"] = bonus
            probe["cross_industry_confirmed_learning_pattern_id"] = match.get("pattern_id")
            probe["learning_bonus"] = max(float(probe.get("learning_bonus") or 0.0), bonus)
            reasons = list(probe.get("priority_reasons") or [])
            reasons.append(f"跨行业双确认模式优先级提示 {bonus:.2f}")
            probe["priority_reasons"] = reasons[:10]
        output.append(probe)
    return output
