from __future__ import annotations

from ._explicit_fact_semantic_normalization_effects import *

def _source_locator(fact: dict[str, Any]) -> str:
    spans = [row for row in _list(fact.get("source_spans")) if isinstance(row, dict)]
    for span in spans:
        address_kind = _text(span.get("address_kind"))
        if _text(span.get("document_block_id")) or address_kind in _EXACT_ADDRESS_KINDS:
            locator = _text(span.get("locator") or span.get("source_locator"))
            if locator:
                return locator
    for span in spans:
        locator = _text(span.get("locator") or span.get("source_locator"))
        if locator:
            return locator
    return ""


def _pair_split_if_else_frames(facts: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Pair one unique split IF and ELSE fact sharing one exact source locator."""
    by_locator: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if _text(fact.get("fact_type")).upper() in _FORMAL_TYPED_COORDINATES:
            continue
        locator = _source_locator(fact)
        if locator:
            by_locator[locator].append(fact)

    paired_groups = 0
    changed_ids: list[str] = []
    for rows in by_locator.values():
        then_rows = [row for row in rows if _IF_BRANCH_RE.search(_text(row.get("raw_statement")))]
        else_rows = [row for row in rows if _ELSE_BRANCH_RE.search(_text(row.get("raw_statement")))]
        if len(then_rows) != 1 or len(else_rows) != 1:
            continue
        then_fact = then_rows[0]
        else_fact = else_rows[0]
        then_statement = _text(then_fact.get("raw_statement"))
        else_statement = _text(else_fact.get("raw_statement"))
        match = _IF_BRANCH_RE.search(then_statement)
        condition = _clean_condition(match.group("condition")) if match else ""
        conditions = [condition] if condition else list(_list(then_fact.get("conditions")))
        combinator = "SINGLE_CONDITION" if len(conditions) == 1 else _text(
            then_fact.get("condition_combinator")
        )
        paired_statement = f"{then_statement}；{else_statement}"
        for index, (fact, branch) in enumerate(((then_fact, "THEN"), (else_fact, "ELSE"))):
            fact["conditions"] = list(conditions)
            fact["condition_combinator"] = combinator
            fact["trigger"] = {"raw": conditions[0]} if conditions else {}
            frame = dict(_dict(fact.get("condition_frame")))
            frame.update(
                {
                    "kind": "IF_THEN_ELSE",
                    "combinator": combinator,
                    "conditions": list(conditions),
                    "branch": branch,
                    "branch_index": index,
                    "parent_conditions": [],
                    "paired_statement": paired_statement,
                    "source_backed": True,
                }
            )
            fact["condition_frame"] = frame
            normalization = dict(_dict(fact.get("explicit_semantic_normalization")))
            normalized_fields = set(_list(normalization.get("normalized_fields")))
            normalized_fields.add("if_then_else_frame")
            normalization.update(
                {
                    "status": "PASS",
                    "normalized_fields": sorted(_text(row) for row in normalized_fields if _text(row)),
                    "source_backed": True,
                    "governed_operation_binding": True,
                    "split_branch_pairing": True,
                    "new_fact_discovered": False,
                    "automatic_winner_used": False,
                }
            )
            fact["explicit_semantic_normalization"] = normalization
            fact["semantic_signature"] = _semantic_signature(fact)
            fact_id = _text(fact.get("fact_id"))
            if fact_id:
                changed_ids.append(fact_id)
        paired_groups += 1
    return paired_groups, changed_ids

__all__ = sorted(name for name in globals() if not name.startswith('__'))
