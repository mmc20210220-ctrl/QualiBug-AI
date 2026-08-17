from __future__ import annotations

from ._explicit_fact_semantic_normalization_conditions import *
from .chinese_clause_parser import extract_explicit_time_constraints

def _normalized_state_effect(statement: str) -> dict[str, Any] | None:
    matches = list(_STATE_FROM_TO_RE.finditer(statement))
    if len(matches) != 1:
        return None
    match = matches[0]
    from_state = _text(match.group("from")).removesuffix("状态")
    to_state = _text(match.group("to")).removesuffix("状态")
    if not from_state or not to_state:
        return None
    return {
        "from_state": from_state,
        "to_state": to_state,
        "raw": match.group(0),
        "source_backed": True,
    }


def _normalized_time_window(statement: str) -> dict[str, Any] | None:
    constraints = extract_explicit_time_constraints(statement)
    if len(constraints) != 1:
        return None
    return {
        key: value
        for key, value in constraints[0].items()
        if key != "resolution_status"
    }


def _normalized_postconditions(statement: str, existing: Iterable[Any]) -> list[str]:
    rows = [_text(row) for row in existing if _text(row)]
    for match in _STATE_INVARIANT_RE.finditer(statement):
        value = _text(match.group("value"))
        if value and value not in rows:
            rows.append(value)
    return rows


def _normalized_fact_type(fact: dict[str, Any]) -> str:
    if _list(fact.get("state_effects")):
        return "STATE_TRANSITION"
    if _text(fact.get("modality")).upper() in {"MAY", "MUST_NOT", "ONLY_IF"}:
        return "PERMISSION_RULE"
    return "BUSINESS_RULE"


def _normalize_primary_claim(
    fact: dict[str, Any],
    *,
    action: dict[str, Any],
    actors: list[str],
    entities: list[str],
) -> bool:
    claims = [dict(row) for row in _list(fact.get("claims")) if isinstance(row, dict)]
    changed = False
    for claim in claims:
        if _text(claim.get("claim_type")).upper() != "PRIMARY_OPERATION":
            continue
        predicate = _text(action.get("canonical"))
        if predicate and _text(claim.get("predicate")) != predicate:
            claim["predicate"] = predicate
            changed = True
        if actors and _list(claim.get("subject_refs")) != actors:
            claim["subject_refs"] = list(actors)
            changed = True
        if entities and _list(claim.get("object_refs")) != entities:
            claim["object_refs"] = list(entities)
            changed = True
    if changed:
        fact["claims"] = claims
    return changed

__all__ = sorted(name for name in globals() if not name.startswith('__'))
