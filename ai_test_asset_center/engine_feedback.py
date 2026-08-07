"""
Project-scoped engine attention feedback (closed loop, comprehension layer).

Confirmed defects are attributed to the reasoner engine family that produced
them (via ``_reasoner_engine`` / ``engine`` / ``_hypothesis_source`` markers on
findings).  This module records per-engine confirmation counts per project and
exposes a bounded, fail-soft attention signal consumed by
``stage_reason_all_v2``:

- write: ``record_confirmed_engine_attribution`` — called from the closed-loop
  write path (``closed_loop_feedback.build_closed_loop_context``)
- read:  ``resolve_engine_attention_weights`` — merged with policy weights
- read:  ``build_engine_attention_nudge`` — bounded per-engine prompt guidance

Boundaries (must hold):
- Product-owned confirmed findings only.  Evaluator-private signals (hidden
  ground truth, miss diagnosis) never enter this store or any prompt.
- Project-scoped (QUALIBUG_PROJECT); no cross-project leakage.
- Attention only: boosts scheduling priority and prompt guidance.  It never
  changes evidence rules, gates, budgets, compile status, or severity.
- Bounded: weight cap 2.0, no down-weighting (false-positive truth is
  evaluator-private — the legitimate negative signal is non-reinforcement),
  staleness decay, and every failure stays fail-soft with an explicit receipt.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _safe_project_id

MAX_WEIGHT = 2.0
BASE_BOOST_PER_CONFIRMED = 0.25
MAX_CONFIRMED_COUNTED = 4
STALE_DAYS = 90
WEIGHT_FILE = "engine_attention.json"

_ENGINE_MARKER_KEYS = (
    "_reasoner_engine",
    "engine_name",
    "engine",
    "_hypothesis_source",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _paths(project: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project)
    pool = root / "platform_outputs" / project / "closed_loop"
    return {"pool": pool, "file": pool / WEIGHT_FILE}


def _engine_marker(finding: dict[str, Any]) -> str:
    """Attribution marker from a finding, normalized; empty when absent.

    The marker comes from data already carried by the finding — never
    inferred from titles, paths, or vocabulary.
    """
    for key in _ENGINE_MARKER_KEYS:
        text = str(finding.get(key) or "").strip()
        if text:
            return re.sub(r"[^a-z0-9_]+", "_", text.lower())[:60]
    return ""


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_confirmed_engine_attribution(
    findings: list[dict[str, Any]],
    *,
    project: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Write side: count confirmed defects per engine family for a project.

    Idempotent and fail-soft: a write problem returns a FAILED receipt and
    never raises into the closed-loop write path.
    """
    if not project or not str(project).strip():
        return {"status": "SKIPPED", "reason": "project_not_set", "engines_updated": 0}
    root = root or ROOT
    paths = _paths(project, root)
    try:
        data = _load(paths["file"])
        records = data.setdefault("engines", {})
        now = _now()
        updated = 0
        for finding in findings or []:
            if not isinstance(finding, dict):
                continue
            marker = _engine_marker(finding)
            if not marker:
                continue
            record = records.setdefault(marker, {"confirmed": 0, "first_seen": now, "last_seen": now})
            record["confirmed"] = int(record.get("confirmed") or 0) + 1
            record["last_seen"] = now
            updated += 1
        data["updated_at_utc"] = now
        paths["pool"].mkdir(parents=True, exist_ok=True)
        paths["file"].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "status": "CONSUMED" if updated else "NO_ATTRIBUTED_ENGINE",
            "engines_updated": updated,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{str(exc)[:120]}",
            "engines_updated": 0,
        }


def _days_since(timestamp: str | None) -> float:
    try:
        if not timestamp:
            return float("inf")
        parsed = time.strptime(str(timestamp)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        return (time.time() - time.mktime(parsed)) / 86400.0
    except (ValueError, TypeError, OverflowError):
        return float("inf")


def resolve_engine_attention_weights(
    policy_weights: dict[str, Any] | None,
    *,
    project: str,
    root: Path | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Read side: merge policy weights with project-learned attention.

    Returns ``(weights, receipt)``.  Every boost is clamped to
    ``[policy_weight, MAX_WEIGHT]``, requires at least one confirmed defect in
    this project, and decays when the confirmation is stale.  Missing project
    or store keeps the policy weights unchanged (fail-soft, visible receipt).
    """
    weights = {
        str(key): float(value or 0.0)
        for key, value in (policy_weights or {}).items()
    }
    if not project or not str(project).strip():
        return weights, {"status": "SKIPPED", "reason": "project_not_set", "boosted": []}
    root = root or ROOT
    paths = _paths(project, root)
    try:
        data = _load(paths["file"])
        records = data.get("engines") if isinstance(data, dict) else None
        if not isinstance(records, dict) or not records:
            return weights, {"status": "EMPTY", "reason": "no_engine_confirmations", "boosted": []}
        boosted: list[dict[str, Any]] = []
        for engine, record in records.items():
            if not isinstance(record, dict):
                continue
            confirmed = int(record.get("confirmed") or 0)
            if confirmed < 1:
                continue
            if _days_since(record.get("last_seen")) > STALE_DAYS:
                continue
            base = float(weights.get(engine, 1.0) or 0.0)
            boost = 1.0 + BASE_BOOST_PER_CONFIRMED * min(confirmed, MAX_CONFIRMED_COUNTED)
            boosted_weight = min(max(base * boost, base), MAX_WEIGHT)
            weights[engine] = boosted_weight
            boosted.append({"engine": engine, "confirmed": confirmed, "weight": round(boosted_weight, 4)})
        return weights, {"status": "CONSUMED" if boosted else "NO_BOOSTED", "boosted": boosted}
    except Exception as exc:
        return weights, {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{str(exc)[:120]}",
            "boosted": [],
        }


def load_engine_attention_records(
    project: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Raw per-engine confirmation records for a project (empty dict when
    unavailable).  Used to build per-engine prompt nudges; fail-soft."""
    if not project or not str(project).strip():
        return {}
    root = root or ROOT
    paths = _paths(project, root)
    data = _load(paths["file"])
    records = data.get("engines") if isinstance(data, dict) else None
    if not isinstance(records, dict):
        return {}
    return {str(engine): dict(record) for engine, record in records.items() if isinstance(record, dict)}


def build_engine_attention_nudge(engine_name: str, attention: dict[str, Any]) -> str:
    """Bounded per-engine prompt guidance from project-confirmed history.

    Empty string when the engine has no confirmed history.  The text is a
    static template plus the confirmed count — product-owned data only, never
    benchmark or customer vocabulary.
    """
    entry = attention.get(engine_name) if isinstance(attention, dict) else None
    record = entry if isinstance(entry, dict) else {}
    confirmed = int(record.get("confirmed") or 0)
    if confirmed < 1:
        return ""
    return (
        "\n\n[PROJECT-LEARNED PRIORITY] This engine family has "
        f"{confirmed} confirmed defect(s) in this project's history. "
        "Investigate this family's patterns first, then look deeper for "
        "related defects."
    )
