from __future__ import annotations

from ._explicit_fact_semantic_normalization_core import *

def _modality(statement: str) -> tuple[str, str]:
    """Explicit modal markers determine modality; operation polarity is separate."""
    if _NEGATIVE_RE.search(statement):
        return "MUST_NOT", "NEGATIVE"
    if _MUST_RE.search(statement):
        return "MUST", "POSITIVE"
    if _MAY_RE.search(statement):
        return "MAY", "POSITIVE"
    if _ONLY_IF_RE.search(statement):
        return "ONLY_IF", "POSITIVE"
    return "ASSERTS", "POSITIVE"


def _clean_condition(value: Any) -> str:
    item = _text(value).strip(" ，,；;。")
    item = re.sub(r"^(?:在|当|如果|若|一旦)", "", item)
    item = re.sub(r"(?:的情况下|条件下|情况下|时)$", "", item)
    return item.strip(" ，,；;。")


def _qualified_object_conditions(
    statement: str,
    action: dict[str, Any],
    entities: list[str],
) -> tuple[list[str], str]:
    """Project explicit qualifiers between an action and its actor-exclusive object."""
    if not _ONLY_ACTOR_PERMISSION_RE.search(statement) or not action or not entities:
        return [], ""
    action_end = int(action.get("end", -1))
    if action_end < 0:
        return [], ""
    candidates: list[tuple[int, str]] = []
    for entity in entities:
        for match in re.finditer(re.escape(entity), statement[action_end:]):
            candidates.append((action_end + match.start(), entity))
    if not candidates:
        return [], ""
    object_start, _entity = max(candidates, key=lambda row: row[0])
    qualifier = statement[action_end:object_start].strip(" ，,；;。")
    qualifier = re.sub(r"^(?:把|将|对|向|给)", "", qualifier)
    qualifier = re.sub(r"的$", "", qualifier).strip(" ，,；;。")
    if not qualifier:
        return [], ""
    has_and = bool(_AND_RE.search(qualifier))
    has_or = bool(_OR_RE.search(qualifier))
    if has_and and has_or:
        return [qualifier], "UNRESOLVED"
    if has_and:
        rows = [re.sub(r"的$", "", _clean_condition(row)) for row in _AND_RE.split(qualifier)]
        values = _ordered_unique(row for row in rows if row)
        return values, "AND" if len(values) > 1 else "SINGLE_CONDITION"
    if has_or:
        rows = [re.sub(r"的$", "", _clean_condition(row)) for row in _OR_RE.split(qualifier)]
        values = _ordered_unique(row for row in rows if row)
        return values, "OR" if len(values) > 1 else "SINGLE_CONDITION"
    value = re.sub(r"的$", "", _clean_condition(qualifier))
    return ([value] if value else []), ("SINGLE_CONDITION" if value else "")


def _condition_coordinates(
    statement: str,
    fact: dict[str, Any],
    *,
    action: dict[str, Any],
    entities: list[str],
) -> tuple[list[str], str]:
    match = _ONLY_IF_FRAME_RE.search(statement)
    if match:
        body = _clean_condition(match.group("body"))
        has_and = bool(_AND_RE.search(body))
        has_or = bool(_OR_RE.search(body))
        if has_and and has_or:
            return [body], "UNRESOLVED"
        if has_and:
            rows = [_clean_condition(row) for row in _AND_RE.split(body)]
            return _ordered_unique(row for row in rows if row), "AND"
        if has_or:
            rows = [_clean_condition(row) for row in _OR_RE.split(body)]
            return _ordered_unique(row for row in rows if row), "OR"
        return ([body] if body else []), ("SINGLE_CONDITION" if body else "")

    qualified, qualified_combinator = _qualified_object_conditions(
        statement,
        action,
        entities,
    )
    existing = [_clean_condition(row) for row in _list(fact.get("conditions"))]
    rows = _ordered_unique([*(row for row in existing if row), *qualified])
    # Conditions are evidence coordinates, so canonical output order follows the
    # first literal occurrence in the source statement rather than producer order.
    # Values not found literally remain stable at the end.
    source_positions = {row: statement.find(row) for row in rows}
    original_order = {row: index for index, row in enumerate(rows)}
    rows.sort(
        key=lambda row: (
            source_positions[row] if source_positions[row] >= 0 else len(statement) + 1,
            original_order[row],
        )
    )
    if qualified:
        if len(rows) <= 1:
            return rows, "SINGLE_CONDITION"
        return rows, qualified_combinator or "UNRESOLVED"
    return rows, _text(fact.get("condition_combinator"))


def _normalize_condition_frame(
    fact: dict[str, Any], conditions: list[str], combinator: str
) -> None:
    fact["conditions"] = conditions
    fact["condition_combinator"] = combinator
    frame = dict(_dict(fact.get("condition_frame")))
    if not conditions and not frame:
        return
    frame["conditions"] = list(conditions)
    frame["combinator"] = combinator
    if combinator == "AND":
        frame["kind"] = "ALL"
    elif combinator == "OR":
        frame["kind"] = "ANY"
    elif combinator == "UNRESOLVED":
        frame["kind"] = "UNRESOLVED"
    elif conditions:
        frame["kind"] = "LEAF"
    fact["condition_frame"] = frame
    fact["trigger"] = {"raw": conditions[0]} if conditions else {}

__all__ = sorted(name for name in globals() if not name.startswith('__'))
