"""READ-side consumption of the SQLite knowledge base in the discovery mainline.

Learned risk patterns (written by closed_loop_feedback after each scan) are
consumed at planning time as a *bounded ranking boost* for compiled
obligations. This module never injects probes, never mutates compile status,
and never changes the execution budget: an obligation can only move up in the
existing selection order when its source-declared operation path or risk
family matches an entity/family that historically produced confirmed defects.

All matching is data-driven and industry-neutral: patterns carry the entity
segment, method, path, and type observed in prior confirmed findings; the
obligation side contributes only source-declared Behavior IR paths and risk
families. No benchmark or customer-specific vocabulary is encoded here.
"""

from __future__ import annotations

import math
from typing import Any

_MAX_BOOST_FACTOR = 1.5
_FAMILY_BOOST_FACTOR = 1.15
_PATH_BOOST_FACTOR = 1.35
_MIN_CONFIDENCE = 0.1
_MAX_CONSUMED_PATTERNS = 20

# Comprehension-layer (hypothesis generation) consumption bounds. The learned
# memory is attention guidance only — it never asserts business rules, bodies,
# or impact, and every hypothesis still needs source-grounded evidence.
_MAX_MEMORY_PATTERNS = 8
_MAX_MEMORY_CHARS = 1200


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _path_segments(path: str) -> set[str]:
    return {
        part.lower()
        for part in path.split("/")
        if part and not part.startswith("{")
    }


def build_learned_boost_index(learned_knowledge: Any) -> dict[str, Any]:
    """Normalize the scan-start learned_knowledge payload into boost entries.

    Returns an explicit, inspectable index. Invalid or empty payloads produce
    a NOT_CONSUMED-style receipt payload instead of raising, so planning stays
    fail-visible rather than fail-silent.
    """

    knowledge = _dict(learned_knowledge)
    load_failure = _text(knowledge.get("load_failure"))
    raw_patterns = [
        item for item in _list(knowledge.get("learned_patterns")) if isinstance(item, dict)
    ]
    entries: list[dict[str, Any]] = []
    skipped = 0
    for item in raw_patterns[:_MAX_CONSUMED_PATTERNS]:
        entity = _text(item.get("entity")).lower()
        pattern_type = _text(item.get("type")).lower()
        path = _text(item.get("path"))
        if not entity and not path and not pattern_type:
            skipped += 1
            continue
        try:
            confidence = float(item.get("_confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            usage_count = int(item.get("_usage_count") or item.get("count") or 0)
        except (TypeError, ValueError):
            usage_count = 0
        if confidence < _MIN_CONFIDENCE:
            skipped += 1
            continue
        strength = min(1.0, confidence * 0.7 + 0.3 * min(1.0, math.log1p(max(usage_count, 0)) / 2.0))
        entries.append(
            {
                "signature": _text(item.get("_key")) or _text(item.get("signature")),
                "type": pattern_type,
                "entity": entity,
                "method": _text(item.get("method")).upper(),
                "path": path,
                "strength": round(strength, 4),
            }
        )
    status = (
        "LOAD_FAILED"
        if load_failure
        else ("CONSUMED" if entries else "NO_PATTERNS")
    )
    return {
        "status": status,
        "load_failure": load_failure,
        "entries": entries,
        "pattern_count": len(entries),
        "skipped_count": skipped,
    }


def apply_learned_boost(
    *,
    score: float,
    risk_family: str,
    path_prefix: str,
    resolved_path: str,
    boost_index: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    """Return (boosted_score, matches) for one obligation.

    Matching rules (all data-driven):
      - path/entity match: a learned pattern entity equals a segment of the
        obligation's source-declared path, or the pattern path shares the
        obligation's path prefix -> stronger boost;
      - family match: the obligation's risk family equals a learned pattern
        type -> weaker boost.
    The boost factor is bounded by _MAX_BOOST_FACTOR and scaled by the
    pattern strength, so accumulated history can never dominate source risk.
    """

    base = float(score or 0.0)
    if base <= 0.0:
        return base, []
    if _text(boost_index.get("status")) != "CONSUMED":
        return base, []
    family = _text(risk_family).lower()
    prefix = _text(path_prefix).lower()
    segments = _path_segments(_text(resolved_path)) or _path_segments(prefix)
    matches: list[dict[str, Any]] = []
    factor = 1.0
    for entry in _list(boost_index.get("entries")):
        entry = _dict(entry)
        strength = min(1.0, max(0.0, float(entry.get("strength") or 0.0)))
        if strength <= 0.0:
            continue
        entity = _text(entry.get("entity")).lower()
        pattern_path = _text(entry.get("path")).lower()
        pattern_type = _text(entry.get("type")).lower()
        match_kind = ""
        kind_factor = 1.0
        if entity and (entity in segments or (prefix and f"/{entity}" in prefix)):
            match_kind = "path_entity"
            kind_factor = _PATH_BOOST_FACTOR
        elif pattern_path and prefix and pattern_path.startswith(prefix):
            match_kind = "path_prefix"
            kind_factor = _PATH_BOOST_FACTOR
        elif pattern_type and family and pattern_type == family:
            match_kind = "risk_family"
            kind_factor = _FAMILY_BOOST_FACTOR
        elif pattern_type and family and (pattern_type in family or family in pattern_type):
            # Open-taxonomy matching: pattern types are observed labels
            # (e.g. a finding category) rather than a closed enumeration, so
            # containment is checked in both directions.
            match_kind = "risk_family"
            kind_factor = _FAMILY_BOOST_FACTOR
        if not match_kind:
            continue
        entry_factor = 1.0 + (kind_factor - 1.0) * strength
        factor = max(factor, entry_factor)
        matches.append(
            {
                "signature": _text(entry.get("signature")),
                "match_kind": match_kind,
                "strength": strength,
                "boost_factor": round(entry_factor, 4),
            }
        )
    factor = min(factor, _MAX_BOOST_FACTOR)
    boosted = base if not matches else base * factor
    return round(boosted, 6), matches


def build_learning_consumption_receipt(
    boost_index: dict[str, Any],
    *,
    boosted_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Emit a visible receipt describing what the planning stage consumed."""

    return {
        "schema_version": "qualibug.learning-consumption-receipt.v1",
        "status": _text(boost_index.get("status")) or "NO_PATTERNS",
        "load_failure": _text(boost_index.get("load_failure")),
        "pattern_count": int(boost_index.get("pattern_count") or 0),
        "skipped_count": int(boost_index.get("skipped_count") or 0),
        "obligations_boosted": len(boosted_rows),
        "top_boosts": sorted(
            boosted_rows,
            key=lambda row: -float(_dict(row).get("boost_factor") or 0.0),
        )[:10],
        "authority": "ranking_boost_only_no_budget_no_compile_change",
    }


def build_learned_memory_prompt_block(learned_knowledge: Any) -> tuple[str, dict[str, Any]]:
    """Render the comprehension-layer prompt block from learned knowledge.

    Returns ``(block_text, receipt)``. The block carries this project's own
    prior confirmed-defect observation history into hypothesis generation as
    bounded attention guidance. It never states business rules, request
    bodies, credentials, or impact claims, and hypotheses must still be
    source-grounded. Empty or failed payloads yield an empty block with an
    explicit receipt so consumption stays fail-visible.
    """

    knowledge = _dict(learned_knowledge)
    load_failure = _text(knowledge.get("load_failure"))
    raw_patterns = [
        item for item in _list(knowledge.get("learned_patterns")) if isinstance(item, dict)
    ]
    if load_failure:
        return "", {"status": "LOAD_FAILED", "load_failure": load_failure, "pattern_count": 0}
    if not raw_patterns:
        return "", {"status": "NO_PATTERNS", "load_failure": "", "pattern_count": 0}

    ranked = sorted(
        raw_patterns,
        key=lambda item: (
            int(item.get("_usage_count") or item.get("count") or 0),
            float(item.get("_confidence") or 0.0),
        ),
        reverse=True,
    )[:_MAX_MEMORY_PATTERNS]

    lines: list[str] = []
    for item in ranked:
        pattern_type = _text(item.get("type")) or "uncategorized"
        entity = _text(item.get("entity")) or "unknown"
        method = _text(item.get("method"))
        count = int(item.get("count") or item.get("_usage_count") or 0)
        parts = [f"type={pattern_type}", f"entity={entity}"]
        if method:
            parts.append(f"method={method}")
        parts.append(f"confirmed_rounds={count}")
        lines.append("- " + ", ".join(parts))

    block = (
        "\n\nLEARNED RISK MEMORY (this project's own prior confirmed-defect observation history; "
        "attention guidance only, not ground truth):\n"
        + "\n".join(lines)
        + "\nPrioritize hypotheses about these historically confirmed risk areas and their "
        "related entities/states. Every hypothesis must still be grounded in the provided "
        "source materials and system observations; do not assume these defects still exist "
        "and do not infer request bodies, credentials, or business rules from this memory."
    )[:_MAX_MEMORY_CHARS]

    receipt = {
        "status": "CONSUMED",
        "load_failure": "",
        "pattern_count": len(lines),
        "authority": "comprehension_attention_guidance_only",
    }
    return block, receipt
