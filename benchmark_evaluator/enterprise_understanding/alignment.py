"""Deterministic evaluator-side alignment for the existing understanding asset."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

ALIGNMENT_SCHEMA = "qualibug.enterprise-understanding-alignment.v1"
EXACT_STATUSES = {"EXACT_MATCH", "UNKNOWN_CORRECTLY_EXPOSED"}
COVERED_STATUSES = {*EXACT_STATUSES, "PARTIAL_MATCH"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, dict):
        return [
            _text(value.get("canonical") or value.get("raw") or value.get("name"))
        ] if _text(value.get("canonical") or value.get("raw") or value.get("name")) else []
    return [_text(value)] if _text(value) else []


def _names(row: dict[str, Any], *fields: str) -> set[str]:
    result: set[str] = set()
    for field in fields:
        result.update(_norm(value) for value in _values(row.get(field)) if _norm(value))
    return result


def _model(asset: dict[str, Any]) -> dict[str, Any]:
    value = asset.get("enterprise_understanding_model")
    return value if isinstance(value, dict) else asset


def _candidate_id(row: dict[str, Any]) -> str:
    for field in (
        "object_id", "actor_id", "operation_id", "relation_id", "lifecycle_id",
        "transition_id", "rule_id", "behavior_id", "conflict_id", "unknown_id", "fact_id",
    ):
        value = _text(row.get(field))
        if value:
            return value
    return ""


def _evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(row.get("evidence"))


def _alignment(
    gt: dict[str, Any],
    collection: str,
    status: str,
    candidate: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = candidate or {}
    return {
        "ground_truth_id": gt.get("ground_truth_id"),
        "collection": collection,
        "alignment_status": status,
        "criticality": gt.get("criticality") or "P2",
        "candidate_id": _candidate_id(candidate),
        "candidate_status": _text(candidate.get("status")),
        "candidate_evidence": _evidence(candidate),
        "details": dict(details or {}),
    }


def _entity_index(rows: Iterable[dict[str, Any]], id_fields: tuple[str, ...]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in rows:
        aliases = _names(
            row, "name", "canonical_name", "aliases", "raw_names", "raw_action_names", "display_name"
        )
        for field in id_fields:
            identity = _text(row.get(field))
            if identity:
                result[identity] = set(aliases)
        for alias in aliases:
            result.setdefault(alias, set()).update(aliases)
    return result


def _resolve(value: Any, index: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    for raw in _values(value):
        result.update(index.get(raw, set()))
        result.update(index.get(_norm(raw), set()))
        if _norm(raw):
            result.add(_norm(raw))
    return result


def _best_name(gt: dict[str, Any], candidates: list[dict[str, Any]], collection: str) -> dict[str, Any]:
    expected = _names(gt, "canonical_name", "aliases", "reason_code", "kind")
    matches = [
        row for row in candidates
        if expected & _names(
            row, "name", "canonical_name", "aliases", "raw_names", "raw_action_names",
            "display_name", "reason_code", "kind", "conflict_type",
        )
    ]
    if len(matches) == 1:
        return _alignment(gt, collection, "EXACT_MATCH", matches[0])
    if len(matches) > 1:
        return _alignment(
            gt, collection, "CONFLICTED",
            details={"candidate_ids": [_candidate_id(row) for row in matches]},
        )
    return _alignment(gt, collection, "MISSING")


def _operation_names(row: dict[str, Any]) -> set[str]:
    return _names(
        row,
        "name",
        "canonical_name",
        "raw_action_names",
        "operation_ref",
        "operation",
        "action",
        "authoritative_operation_refs",
    )


def _behavior_candidates(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Project exact implementation operation identity into evaluator candidates.

    The product keeps business-language operations on the
    Behavior IR and technical OpenAPI operationIds (for example ``listTickets``)
    on governed implementation bindings. Both are existing product authorities.
    Evaluator alignment joins them by ``behavior_ref`` instead of comparing the
    Ground Truth operationId directly with the Chinese action.

    Only source-governed, authoritative, BOUND API rows are admitted. Fuzzy,
    partial, ambiguous, or merely diagnostic endpoint candidates remain excluded.
    The projection is evaluator-local and never mutates the product snapshot.
    """
    operation_refs_by_behavior: dict[str, set[str]] = {}
    for binding in _rows(model.get("behavior_implementation_bindings")):
        behavior_ref = _text(binding.get("behavior_ref"))
        if not behavior_ref:
            continue
        for api_binding in _rows(binding.get("api_operation_bindings")):
            if api_binding.get("authoritative") is not True:
                continue
            if _text(api_binding.get("status")).upper() != "BOUND":
                continue
            operation_ref = _text(api_binding.get("operation_id"))
            if operation_ref:
                operation_refs_by_behavior.setdefault(behavior_ref, set()).add(operation_ref)

    result: list[dict[str, Any]] = []
    for behavior in _rows(model.get("business_behaviors")):
        item = dict(behavior)
        behavior_ref = _text(item.get("behavior_id"))
        operation_refs = sorted(operation_refs_by_behavior.get(behavior_ref, set()))
        if operation_refs:
            item["authoritative_operation_refs"] = operation_refs
        result.append(item)
    return result


def _object_names(row: dict[str, Any], index: dict[str, set[str]]) -> set[str]:
    result = _resolve(row.get("object_refs"), index)
    result.update(_resolve(row.get("objects"), index))
    result.update(_names(row, "object", "object_name", "subject_object"))
    return result


def _actor_names(row: dict[str, Any], index: dict[str, set[str]]) -> set[str]:
    result = _resolve(row.get("actor_refs"), index)
    result.update(_resolve(row.get("actors"), index))
    result.update(_names(row, "actor", "role", "actor_name"))
    return result


def _align_operation(gt: dict[str, Any], candidates: list[dict[str, Any]], object_index: dict[str, set[str]]) -> dict[str, Any]:
    expected_action = _names(gt, "canonical_name", "aliases")
    expected_objects = {_norm(value) for value in _values(gt.get("object_refs"))}
    action_matches = [row for row in candidates if expected_action & _operation_names(row)]
    exact = [
        row for row in action_matches
        if not expected_objects or expected_objects & _object_names(row, object_index)
    ]
    if len(exact) == 1:
        return _alignment(gt, "operations", "EXACT_MATCH", exact[0])
    if len(exact) > 1:
        return _alignment(
            g²È="24¤™½ÈÙ…±Õ”¥¸}Ù…±Õ•Ì¡Ð¹•Ð ‰½‰©•Ñ}É•™Ìˆ¤¥ô(€€€•áÁ•Ñ•‘}…Ñ½ÉÌ€ôí}¹½É´¡Ù…±Õ”¤™½ÈÙ…±Õ”¥¸}Ù…±Õ•Ì¡Ð¹•Ð ‰…Ñ½É}É•™Ìˆ¤¥ô(€€€…Ñ¥½¹}µ…Ñ¡•Ì€ômÉ½Ü™½ÈÉ½Ü¥¸…¹‘¥‘…Ñ•Ì¥˜•áÁ•Ñ•‘}…Ñ¥½¸€˜}½Á•É…Ñ¥½¹}¹…µ•Ì¡É½Ü¥t(€€€½‰©•Ñ}µ…Ñ¡•Ì€ômÉ½Ü™½ÈÉ½Ü¥¸…Ñ¥½¹}µ…Ñ¡•Ì¥˜•áÁ•Ñ•‘}½‰©•ÑÌ€˜}½‰©•Ñ}¹…µ•Ì¡É½Ü°½‰©•Ñ}¥¹‘•à¥t(€€€¥˜¹½Ð…Ñ¥½¹}µ…Ñ¡•Ìè(€€€€€€€É•ÑÕÉ¸}…±¥¹µ•¹Ð¡Ð°½±±•Ñ¥½¸°€‰5%MM%9ˆ¤(€€€¥˜¹½Ð½‰©•Ñ}µ…Ñ¡•Ìè(€€€€€€€É•ÑÕÉ¸}…±¥¹µ•¹Ð (€€€€€€€€€€€Ð°½±±•Ñ¥½¸°€‰]I=9}	%9%9ˆ°(€€€€€€€€€€€…Ñ¥½¹}µ…Ñ¡•ÍlÁt¥˜±•¸¡…Ñ¥½¹}µ…Ñ¡•Ì¤€ôô€Ä•±Í”9½¹”°(€€€€€€€€€€€ì‰Í±½Ðˆè€‰½‰©•Ñ}É•™Ì‰ô°(€€€€€€€€¤(€€€•áÁ•Ñ•‘}Á•Éµ¥ÍÍ¥½¸€ô}¹½É´¡Ð¹•Ð ‰Á•Éµ¥ÍÍ¥½¹}‘•¥Í¥½¸ˆ¤¤(€€€•áÁ•Ñ•‘}½¹‘¥Ñ¥½¹Ì€ô}½¹‘¥Ñ¥½¹}Í¥¹…ÑÕÉ”¡Ð¹•Ð ‰ÁÉ•½¹‘¥Ñ¥½¹Ìˆ¤¤(€€€•áÁ•Ñ•‘}•™™•ÑÌ€ô}ÍÑ…Ñ•}•™™•Ñ}Í¥¹…ÑÕÉ”¡Ð¹•Ð ‰ÍÑ…Ñ•}•™™•ÑÌˆ¤¤(€€€•á…Ðè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€Á…ÉÑ¥…°è±¥ÍÑmÑÕÁ±•m‘¥ÑmÍÑÈ°¹åt°±¥ÍÑmÍÑÉuut€ômt(€€€™½ÈÉ½Ü¥¸½‰©•Ñ}µ…Ñ¡•Ìè(€€€€€€€µ¥ÍÍ¥¹œè±¥ÍÑmÍÑÉt€ômt(€€€€€€€¥˜•áÁ•Ñ•‘}…Ñ½ÉÌ…¹¹½Ð•áÁ•Ñ•‘}…Ñ½ÉÌ€˜}…Ñ½É}¹…µ•Ì¡É½Ü°…Ñ½É}¥¹‘•à¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰…Ñ½É}É•™Ìˆ¤(€€€€€€€¥˜•áÁ•Ñ•‘}Á•Éµ¥ÍÍ¥½¸…¹}¹½É´¡É½Ü¹•Ð ‰Á•Éµ¥ÍÍ¥½¹}‘•¥Í¥½¸ˆ¤¤€„ô•áÁ•Ñ•‘}Á•Éµ¥ÍÍ¥½¸è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰Á•Éµ¥ÍÍ¥½¹}‘•¥Í¥½¸ˆ¤(€€€€€€€¥˜•áÁ•Ñ•‘}½¹‘¥Ñ¥½¹Ì…¹¹½Ð•áÁ•Ñ•‘}½¹‘¥Ñ¥½¹Ì¹¥ÍÍÕ‰Í•Ð (€€€€€€€€€€€}½¹‘¥Ñ¥½¹}Í¥¹…ÑÕÉ”¡É½Ü¹•Ð ‰ÁÉ•½¹‘¥Ñ¥½¹Ìˆ¤½ÈÉ½Ü¹•Ð ‰½¹‘¥Ñ¥½¹Ìˆ¤¤(€€€€€€€€¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰ÁÉ•½¹‘¥Ñ¥½¹Ìˆ¤(€€€€€€€¥˜•áÁ•Ñ•‘}•™™•ÑÌ…¹¹½Ð•áÁ•Ñ•‘}•™™•ÑÌ¹¥ÍÍÕ‰Í•Ð (€€€€€€€€€€€}ÍÑ…Ñ•}•™™•Ñ}Í¥¹…ÑÕÉ”¡É½Ü¹•Ð ‰ÍÑ…Ñ•}•™™•ÑÌˆ¤¤(€€€€€€€€¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰ÍÑ…Ñ•}•™™•ÑÌˆ¤(€€€€€€€¥˜µ¥ÍÍ¥¹œè(€€€€€€€€€€€Á…ÉÑ¥…°¹…ÁÁ•¹ ¡É½Ü°µ¥ÍÍ¥¹œ¤¤(€€€€€€€•±Í”è(€€€€€€€€€€€•á…Ð¹…ÁÁ•¹¡É½Ü¤(€€€¥˜±•¸¡•á…Ð¤€ôô€Äè(€€€€€€€…¹‘¥‘…Ñ”€ô•á…ÑlÁt(€€€€€€€½¹™¥Éµ•€ô}Ñ•áÐ¡…¹‘¥‘…Ñ”¹•Ð ‰ÍÑ…ÑÕÌˆ¤¤¹ÕÁÁ•È ¤¥¸ìˆˆ°€‰=9%I5ˆ°€‰AQ‰ô(€€€€€€€É•ÑÕÉ¸}…±¥¹µ•¹Ð (€€€€€€€€€€€Ð°½±±•Ñ¥½¸°€‰aQ}5Q ˆ¥˜½¹™¥Éµ••±Í”€‰AIQ%1}5Q ˆ°…¹‘¥‘…Ñ”°(€€€€€€€€€€€ì‰…¹‘¥‘…Ñ•}¹½Ñ}½¹™¥Éµ•ˆè¹½Ð½¹™¥Éµ•‘ô°(€€€€€€€€¤(€€€¥˜±•¸¡•á…Ð¤€ø€Äè(€€€€€€€É•ÑÕÉ¸}…±¥¹µ•¹Ð (€€€€€€€€€€€Ð°½±±•Ñ¥½¸°€‰=91%Qˆ°(€€€€€€€€€€€‘•Ñ…¥±Ìõì‰…¹‘¥‘…Ñ•}¥‘Ìˆèm}…¹‘¥‘…Ñ•}¥¡É½Ü¤™½ÈÉ½Ü¥¸•á…Ñuô°(€€€€€€€€¤(€€€…¹‘¥‘…Ñ”°µ¥ÍÍ¥¹œ€ôÁ…ÉÑ¥…±lÁt(€€€É•ÑÕÉ¸}…±¥¹µ•¹Ð (€€€€€€€Ð°½±±•Ñ¥½¸°€‰AIQ%1}5Q ˆ°…¹‘¥‘…Ñ”¥˜±•¸¡Á…ÉÑ¥…°¤€ôô€Ä•±Í”9½¹”°(€€€€€€€ì‰µ¥ÍÍ¥¹}½É}ÝÉ½¹}Í±½ÑÌˆèµ¥ÍÍ¥¹ô°(€€€€¤(()‘•˜}…±¥¹}•áÁ•Ñ•‘}Õ¹­¹½Ý¸¡Ðè‘¥ÑmÍÑÈ°¹åt°…¹‘¥‘…Ñ•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€•áÁ•Ñ•€ô}¹…µ•Ì¡Ð°€‰É•…Í½¹}½‘”ˆ°€‰­¥¹ˆ°€‰…¹½¹¥…±}¹…µ”ˆ°€‰…±¥…Í•Ìˆ¤(€€€µ…Ñ¡•Ì€ôl(€€€€€€€É½Ü™½ÈÉ½Ü¥¸…¹‘¥‘…Ñ•Ì(€€€€€€€¥˜•áÁ•Ñ•€˜}¹…µ•Ì¡É½Ü°€‰É•…Í½¹}½‘”ˆ°€‰­¥¹ˆ°€‰ÅÕ•ÍÑ¥½¸ˆ°€‰µ•ÍÍ…”ˆ¤(€€€t(€€€¥˜µ…Ñ¡•Ìè(€€€€€€€É•ÑÕÉ¸}…±¥¹µ•¹Ð (€€€€€€€€€€€Ð°€‰•áÁ•Ñ•‘}Õ¹­¹½Ý¹Ìˆ°€‰U9-9=]9}=IIQ1e}aA=Mˆ°(€€€€€€€€€€€µ…Ñ¡•ÍlÁt¥˜±•¸¡µ…Ñ¡•Ì¤€ôô€Ä•±Í”9½¹”°(€€€€€€€€¤(€€€É•ÑÕÉ¸}…±¥¹µ•¹Ð¡Ð°€‰•áÁ•Ñ•‘}Õ¹­¹½Ý¹Ìˆ°€‰5%MM%9ˆ¤(()‘•˜…±¥¹}•¹Ñ•ÉÁÉ¥Í•}Õ¹‘•ÉÍÑ…¹‘¥¹œ¡É½Õ¹‘}ÑÉÕÑ è‘¥ÑmÍÑÈ°¹åt°…ÍÍ•Ðè‘¥ÑmÍÑÈ°¹åt¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€µ½‘•°€ô}µ½‘•°¡…ÍÍ•Ð¤(€€€½‰©•ÑÌ€ô}É½ÝÌ¡µ½‘•°¹•Ð ‰‰ÕÍ¥¹•ÍÍ}½‰©•ÑÌˆ¤¤(€€€…Ñ½ÉÌ€ô}É½ÝÌ¡µ½‘•°¹•Ð ‰…Ñ½ÉÌˆ¤¤(€€€½Á•É…Ñ¥½¹Ì€ô}É½ÝÌ¡µ½‘•°¹•Ð ‰½Á•É…Ñ¥½¹Ìˆ¤¤(€€€É•±…Ñ¥½¹Ì€ô}É½ÝÌ¡µ½‘•°¹•Ð ‰½‰©•Ñ}É•±…Ñ¥½¹Ìˆ¤¤(€€€ÑÉ…¹Í¥Ñ¥½¹Ì€ô}ÑÉ…¹Í¥Ñ¥½¹}É½ÝÌ¡µ½‘•°¤(€€€ÉÕ±•Ì€ô}É½ÝÌ¡µ½‘•°¹•Ð ‰ÉÕ±•Ìˆ¤¤(€€€‰•¡…Ù¥½ÉÌ€ô}‰•¡…Ù¥½É}…¹‘¥‘…Ñ•Ì¡µ½‘•°¤(€€€½¹™±¥ÑÌ€ô}É½ÝÌ¡µ½‘•°¹•Ð ‰½¹™±¥ÑÌˆ¤¤(€€€Õ¹­¹½Ý¹Ì€ô}É½ÝÌ¡µ½‘•°¹•Ð ‰Õ¹­¹½Ý¹Ìˆ¤¤(€€€½‰©•Ñ}¥¹‘•à€ô}•¹Ñ¥Ñå}¥¹‘•à¡½‰©•ÑÌ°€ ‰½‰©•Ñ}¥ˆ°€‰•¹Ñ¥Ñå}¥ˆ¤¤(€€€…Ñ½É}¥¹‘•à€ô}•¹Ñ¥Ñå}¥¹‘•à¡…Ñ½ÉÌ°€ ‰…Ñ½É}¥ˆ°€‰É½±•}¥ˆ¤¤(€€€…±¥¹µ•¹ÑÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰‰ÕÍ¥¹•ÍÍ}½‰©•ÑÌˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}‰•ÍÑ}¹…µ”¡É½Ü°½‰©•ÑÌ°€‰‰ÕÍ¥¹•ÍÍ}½‰©•ÑÌˆ¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰…Ñ½ÉÌˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}‰•ÍÑ}¹…µ”¡É½Ü°…Ñ½ÉÌ°€‰…Ñ½ÉÌˆ¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰½Á•É…Ñ¥½¹Ìˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}…±¥¹}½Á•É…Ñ¥½¸¡É½Ü°½Á•É…Ñ¥½¹Ì°½‰©•Ñ}¥¹‘•à¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰½‰©•Ñ}É•±…Ñ¥½¹Ìˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}…±¥¹}É•±…Ñ¥½¸¡É½Ü°É•±…Ñ¥½¹Ì°½‰©•Ñ}¥¹‘•à¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰ÍÑ…Ñ•}ÑÉ…¹Í¥Ñ¥½¹Ìˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}…±¥¹}ÑÉ…¹Í¥Ñ¥½¸¡É½Ü°ÑÉ…¹Í¥Ñ¥½¹Ì°½‰©•Ñ}¥¹‘•à¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰‰ÕÍ¥¹•ÍÍ}ÉÕ±•Ìˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}…±¥¹}‰•¡…Ù¥½È¡É½Ü°ÉÕ±•Ì°½‰©•Ñ}¥¹‘•à°…Ñ½É}¥¹‘•à°€‰‰ÕÍ¥¹•ÍÍ}ÉÕ±•Ìˆ¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰‰ÕÍ¥¹•ÍÍ}‰•¡…Ù¥½ÉÌˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}…±¥¹}‰•¡…Ù¥½È¡É½Ü°‰•¡…Ù¥½ÉÌ°½‰©•Ñ}¥¹‘•à°…Ñ½É}¥¹‘•à°€‰‰ÕÍ¥¹•ÍÍ}‰•¡…Ù¥½ÉÌˆ¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰½¹™±¥ÑÌˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}‰•ÍÑ}¹…µ”¡É½Ü°½¹™±¥ÑÌ°€‰½¹™±¥ÑÌˆ¤¤(€€€™½ÈÉ½Ü¥¸}É½ÝÌ¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰•áÁ•Ñ•‘}Õ¹­¹½Ý¹Ìˆ¤¤è(€€€€€€€…±¥¹µ•¹ÑÌ¹…ÁÁ•¹¡}…±¥¹}•áÁ•Ñ•‘}Õ¹­¹½Ý¸¡É½Ü°Õ¹­¹½Ý¹Ì¤¤((€€€µ…Ñ¡•‘}…¹‘¥‘…Ñ•}¥‘Ì€ôì(€€€€€€€}Ñ•áÐ¡É½Ü¹•Ð ‰…¹‘¥‘…Ñ•}¥ˆ¤¤(€€€€€€€™½ÈÉ½Ü¥¸…±¥¹µ•¹ÑÌ(€€€€€€€¥˜}Ñ•áÐ¡É½Ü¹•Ð ‰…¹‘¥‘…Ñ•}¥ˆ¤¤…¹É½Ü¹•Ð ‰…±¥¹µ•¹Ñ}ÍÑ…ÑÕÌˆ¤¥¸=YI}MQQUML(€€€ô(€€€™½Éµ…±}½±±•Ñ¥½¹Ì€ôì(€€€€€€€€‰‰ÕÍ¥¹•ÍÍ}½‰©•ÑÌˆè½‰©•ÑÌ°(€€€€€€€€‰…Ñ½ÉÌˆè…Ñ½ÉÌ°(€€€€€€€€‰½Á•É…Ñ¥½¹Ìˆè½Á•É…Ñ¥½¹Ì°(€€€€€€€€‰½‰©•Ñ}É•±…Ñ¥½¹ÌˆèÉ•±…Ñ¥½¹Ì°(€€€€€€€€‰‰ÕÍ¥¹•ÍÍ}ÉÕ±•ÌˆèÉÕ±•Ì°(€€€€€€€€‰‰ÕÍ¥¹•ÍÍ}‰•¡…Ù¥½ÉÌˆè‰•¡…Ù¥½ÉÌ°(€€€ô(€€€Õ¹µ…Ñ¡•‘}½¹™¥Éµ•è±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™½È½±±•Ñ¥½¸°…¹‘¥‘…Ñ•Ì¥¸™½Éµ…±}½±±•Ñ¥½¹Ì¹¥Ñ•µÌ ¤è(€€€€€€€™½ÈÉ½Ü¥¸…¹‘¥‘…Ñ•Ìè(€€€€€€€€€€€¥‘•¹Ñ¥Ñä€ô}…¹‘¥‘…Ñ•}¥¡É½Ü¤(€€€€€€€€€€€¥˜¥‘•¹Ñ¥Ñä…¹¥‘•¹Ñ¥Ñä¹½Ð¥¸µ…Ñ¡•‘}…¹‘¥‘…Ñ•}¥‘Ì…¹}Ñ•áÐ¡É½Ü¹•Ð ‰ÍÑ…ÑÕÌˆ¤¤¹ÕÁÁ•È ¤¥¸ì(€€€€€€€€€€€€€€€€‰=9%I5ˆ°€‰AQˆ°€‰AMLˆ(€€€€€€€€€€€ôè(€€€€€€€€€€€€€€€Õ¹µ…Ñ¡•‘}½¹™¥Éµ•¹…ÁÁ•¹¡ì(€€€€€€€€€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}¥ˆè¥‘•¹Ñ¥Ñä°(€€€€€€€€€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}½±±•Ñ¥½¸ˆè½±±•Ñ¥½¸°(€€€€€€€€€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}ÍÑ…ÑÕÌˆè}Ñ•áÐ¡É½Ü¹•Ð ‰ÍÑ…ÑÕÌˆ¤¤°(€€€€€€€€€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}•Ù¥‘•¹”ˆè}•Ù¥‘•¹”¡É½Ü¤°(€€€€€€€€€€€€€€€ô¤((€€€•áÁ•Ñ•‘}Õ¹­¹½Ý¹}…¹‘¥‘…Ñ•}¥‘Ì€ôì(€€€€€€€}Ñ•áÐ¡É½Ü¹•Ð ‰…¹‘¥‘…Ñ•}¥ˆ¤¤(€€€€€€€™½ÈÉ½Ü¥¸…±¥¹µ•¹ÑÌ(€€€€€€€¥˜É½Ü¹•Ð ‰½±±•Ñ¥½¸ˆ¤€ôô€‰•áÁ•Ñ•‘}Õ¹­¹½Ý¹Ìˆ…¹}Ñ•áÐ¡É½Ü¹•Ð ‰…¹‘¥‘…Ñ•}¥ˆ¤(€€€ô(€€€Õ¹•áÁ•Ñ•‘}Õ¹­¹½Ý¹Ì€ôl(€€€€€€€ì(€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}¥ˆè}…¹‘¥‘…Ñ•}¥¡É½Ü¤°(€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}½±±•Ñ¥½¸ˆè€‰Õ¹­¹½Ý¹Ìˆ°(€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}ÍÑ…ÑÕÌˆè}Ñ•áÐ¡É½Ü¹•Ð ‰ÍÑ…ÑÕÌˆ¤¤°(€€€€€€€€€€€€‰…¹‘¥‘…Ñ•}•Ù¥‘•¹”ˆè}•Ù¥‘•¹”¡É½Ü¤°(€€€€€€€€€€€€‰…±¥¹µ•¹Ñ}ÍÑ…ÑÕÌˆè€‰U9-9=]9}M!=U1}!Y}	9}IM=1Yˆ°(€€€€€€€€€€€€‰É•…Í½¹}½‘”ˆèÉ½Ü¹•Ð ‰É•…Í½¹}½‘”ˆ¤½ÈÉ½Ü¹•Ð ‰­¥¹ˆ¤°(€€€€€€€ô(€€€€€€€™½ÈÉ½Ü¥¸Õ¹­¹½Ý¹Ì(€€€€€€€¥˜}…¹‘¥‘…Ñ•}¥¡É½Ü¤…¹}…¹‘¥‘…Ñ•}¥¡É½Ü¤¹½Ð¥¸•áÁ•Ñ•‘}Õ¹­¹½Ý¹}…¹‘¥‘…Ñ•}¥‘Ì(€€€t(€€€É•ÑÕÉ¸ì(€€€€€€€€‰Í¡•µ„ˆè1%959Q}M!5°(€€€€€€€€‰ÁÉ½©•Ñ}¥ˆèÉ½Õ¹‘}ÑÉÕÑ ¹•Ð ‰ÁÉ½©•Ñ}¥ˆ¤°(€€€€€€€€‰É½Õ¹‘}ÑÉÕÑ¡}Ù…±¥‘…Ñ¥½¹}ÍÑ…ÑÕÌˆè€ (€€€€€€€€€€€É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰Ù…±¥‘…Ñ¥½¹}É••¥ÁÐˆ°íô¤¹•Ð ‰ÍÑ…ÑÕÌˆ¤(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É½Õ¹‘}ÑÉÕÑ ¹•Ð ‰Ù…±¥‘…Ñ¥½¹}É••¥ÁÐˆ¤°‘¥Ð¤•±Í”€‰U9-9=]8ˆ(        ),
        "alignments": alignments,
        "unmatched_confirmed_candidates": unmatched_confirmed,
        "unexpected_unknowns": unexpected_unknowns,
        "alignment_authority": "DETERMINISTIC_EXACT_ALIAS_AND_SLOT_MATCHING",
        "fuzzy_or_llm_match_can_confirm": False,
        "model_writeback_allowed": False,
    }


__all__ = [
    "ALIGNMENT_SCHEMA", "EXACT_STATUSES", "COVERED_STATUSES", "align_enterprise_understanding"
]
