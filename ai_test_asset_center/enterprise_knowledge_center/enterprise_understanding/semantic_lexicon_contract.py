"""Fail-closed contract for the shared semantic lexicon.

The lexicon is language policy used by the existing comprehension mainline. Missing
or structurally invalid policy must be a visible comprehension block, never a silent
empty-dictionary fallback.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .._common import SEMANTIC_LEXICON_PATH
from .._utils import _load_json

SEMANTIC_LEXICON_CONTRACT_SCHEMA = "qualibug.semantic-lexicon-contract.v1"

_REQUIRED_SHAPES: dict[str, type] = {
    "version": int,
    "business_rule_document_markers": list,
    "rule_required_markers": list,
    "rule_prohibited_markers": list,
    "rule_condition_markers": list,
    "role_words": dict,
    "risk_terms": dict,
    "state_machine_heading_markers": list,
    "allowed_transition_markers": list,
    "forbidden_transition_markers": list,
    "permission_decision_markers": dict,
    "positive_integer_markers": list,
    "entity_token_lexicon": dict,
    "entity_alias_groups": list,
    "verb_action_lexicon": dict,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_semantic_lexicon_contract(
    path: str | Path | None = None,
) -> dict[str, Any]:
    resolved = Path(path) if path is not None else SEMANTIC_LEXICON_PATH
    errors: list[dict[str, Any]] = []
    exists = False
    try:
        exists = resolved.is_file()
    except OSError as exc:
        errors.append(
            {
                "code": "SEMANTIC_LEXICON_PATH_UNREADABLE",
                "detail": f"{type(exc).__name__}: {exc}"[:400],
            }
        )
    raw = _load_json(resolved, {}) if exists else {}
    lexicon = raw if isinstance(raw, dict) else {}
    if not exists:
        errors.append(
            {
                "code": "SEMANTIC_LEXICON_FILE_MISSING",
                "detail": str(resolved),
            }
        )
    if exists and not isinstance(raw, dict):
        errors.append(
            {
                "code": "SEMANTIC_LEXICON_ROOT_NOT_OBJECT",
                "detail": type(raw).__name__,
            }
        )
    if exists and not lexicon:
        errors.append(
            {
                "code": "SEMANTIC_LEXICON_EMPTY",
                "detail": str(resolved),
            }
        )

    for key, expected_type in _REQUIRED_SHAPES.items():
        if key not in lexicon:
            errors.append(
                {
                    "code": "SEMANTIC_LEXICON_REQUIRED_KEY_MISSING",
                    "key": key,
                    "expected_type": expected_type.__name__,
                }
            )
            continue
        value = lexicon.get(key)
        if not isinstance(value, expected_type) or (
            expected_type in {list, dict} and not value
        ):
            errors.append(
                {
                    "code": "SEMANTIC_LEXICON_REQUIRED_KEY_INVALID",
                    "key": key,
                    "expected_type": expected_type.__name__,
                    "actual_type": type(value).__name__,
                    "empty": not bool(value),
                }
            )

    permission = _dict(lexicon.get("permission_decision_markers"))
    for decision in ("allow", "deny"):
        if not _list(permission.get(decision)):
            errors.append(
                {
                    "code": "SEMANTIC_LEXICON_PERMISSION_DECISION_INCOMPLETE",
                    "decision": decision,
                }
            )

    action_lexicon = _dict(lexicon.get("verb_action_lexicon"))
    invalid_action_rows = [
        key
        for key, value in action_lexicon.items()
        if key != "comment"
        and (
            not _text(key)
            or not isinstance(value, list)
            or not [_text(item) for item in value if _text(item)]
        )
    ]
    if invalid_action_rows:
        errors.append(
            {
                "code": "SEMANTIC_LEXICON_ACTION_DIRECTION_INVALID",
                "keys": sorted(invalid_action_rows)[:50],
                "expected_shape": "as_written -> non-empty canonical action list",
            }
        )

    alias_groups = _list(lexicon.get("entity_alias_groups"))
    invalid_alias_groups = [
        index
        for index, group in enumerate(alias_groups)
        if not isinstance(group, list)
        or len({_text(item) for item in group if _text(item)}) < 2
    ]
    if invalid_alias_groups:
        errors.append(
            {
                "code": "SEMANTIC_LEXICON_ALIAS_GROUP_INVALID",
                "indexes": invalid_alias_groups[:50],
            }
        )

    status = "PASS" if not errors else "BLOCKED_COMPREHENSION_POLICY_INVALID"
    return {
        "schema": SEMANTIC_LEXICON_CONTRACT_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "path": str(resolved),
        "exists": exists,
        "version": lexicon.get("version"),
        "required_key_count": len(_REQUIRED_SHAPES),
        "validated_key_count": sum(
            1
            for key, expected_type in _REQUIRED_SHAPES.items()
            if isinstance(lexicon.get(key), expected_type) and bool(lexicon.get(key))
        ),
        "lexicon_fingerprint": _fingerprint(lexicon) if lexicon else "",
        "errors": errors,
        "empty_fallback_can_pass": False,
        "packaging_must_ship_policy": True,
    }


def apply_semantic_lexicon_contract(asset: dict[str, Any]) -> dict[str, Any]:
    receipt = validate_semantic_lexicon_contract()
    asset["semantic_lexicon_contract"] = receipt

    gate = _dict(asset.get("enterprise_comprehension_gate"))
    gate["semantic_lexicon_contract"] = {
        "status": receipt["status"],
        "entry_allowed": receipt["entry_allowed"],
        "lexicon_fingerprint": receipt["lexicon_fingerprint"],
        "error_count": len(receipt["errors"]),
    }
    if not receipt["entry_allowed"]:
        gate["status"] = "BLOCKED_COMPREHENSION_POLICY_INVALID"
        gate["entry_allowed"] = False
        gate["required_operator_action"] = (
            "restore and package the shared semantic lexicon with all required "
            "language-policy shapes before enterprise comprehension runs"
        )
    asset["enterprise_comprehension_gate"] = gate

    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != "BLOCKED_COMPREHENSION_POLICY_INVALID"
    ]
    if not receipt["entry_allowed"]:
        gaps.append(
            {
                "kind": "BLOCKED_COMPREHENSION_POLICY_INVALID",
                "gap_type": "semantic_lexicon_contract_invalid",
                "source_id": "*",
                "errors": receipt["errors"],
                "operator_action": gate.get("required_operator_action"),
            }
        )
    asset["coverage_gaps"] = gaps

    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "semantic_lexicon_contract_status": receipt["status"],
            "semantic_lexicon_fingerprint": receipt["lexicon_fingerprint"],
            "semantic_lexicon_error_count": len(receipt["errors"]),
        }
    )
    asset["summary"] = summary

    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "semantic_lexicon_is_versioned_runtime_policy": True,
            "semantic_lexicon_empty_fallback_forbidden": True,
            "semantic_lexicon_packaging_is_gated": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "SEMANTIC_LEXICON_CONTRACT_SCHEMA",
    "validate_semantic_lexicon_contract",
    "apply_semantic_lexicon_contract",
]
