"""Bind source-declared state rules to exact approved database fields.

This projection runs after database Observer drafts have been attached to an
Experiment. It replaces a generic HTTP-shaped state assertion only when one
explicit rule field resolves to exactly one approved database field binding on
a contract that has both BEFORE and AFTER drafts. No fuzzy matching, field-name
heuristics, or automatic conflict winner is allowed.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable

from .database_state_transition_oracle import (
    DATABASE_STATE_TRANSITION_ASSERTION_KIND,
)

PROJECTION_SCHEMA = "qualibug.database-state-transition-experiment-projection.v1"
_BLOCK_REASON = "BLOCKED_DATABASE_STATE_ORACLE_BINDING_AMBIGUOUS"
_STATE_KINDS = frozenset({"state_transition", "forbidden_state_transition"})
_UNKNOWN_STATES = frozenset({"", "unknown", "unknown_state"})
_ID_KEYS = frozenset(
    {
        "field_id",
        "field_ref",
        "api_field_id",
        "database_field_id",
        "field_binding_id",
    }
)
_NAME_KEYS = frozenset(
    {
        "field",
        "field_name",
        "api_field_name",
        "database_field_name",
        "state_field",
        "status_field",
        "json_path",
    }
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _dedupe(rows: Iterable[Any], key: str) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _dict(raw)
        identity = _text(row.get(key))
        if identity:
            output[identity] = row
    return list(output.values())


def _state_token(value: Any) -> str:
    normalized = _text(value).replace("-", " ").replace("_", " ")
    return "_".join(normalized.split()).casefold()


def _name_token(value: Any) -> str:
    text = _text(value)
    if text.startswith("$."):
        text = text[2:]
    text = text.replace("[]", "").strip(".")
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.casefold()


def _walk_field_tokens(value: Any, ids: set[str], names: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _text(key).lower()
            if normalized in _ID_KEYS and _text(child):
                ids.add(_text(child))
            if normalized in _NAME_KEYS and _name_token(child):
                names.add(_name_token(child))
            if isinstance(child, (dict, list)):
                _walk_field_tokens(child, ids, names)
    elif isinstance(value, list):
        for child in value:
            _walk_field_tokens(child, ids, names)


def _explicit_field_tokens(
    assertion: dict[str, Any], experiment: dict[str, Any]
) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    names: set[str] = set()
    _walk_field_tokens(assertion, ids, names)
    runtime_contract = _dict(experiment.get("field_oracle_runtime_contract"))
    for value in _list(runtime_contract.get("required_field_ids")):
        if _text(value):
            ids.add(_text(value))
    return ids, names


def _draft_pairs(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(experiment.get("database_observer_execution_drafts")):
        row = _dict(raw)
        contract_ref = _text(row.get("observer_contract_ref"))
        if contract_ref:
            groups.setdefault(contract_ref, []).append(row)
    pairs: list[dict[str, Any]] = []
    for contract_ref, drafts in groups.items():
        before = [row for row in drafts if _text(row.get("observation_phase")).upper() == "BEFORE"]
        after = [row for row in drafts if _text(row.get("observation_phase")).upper() == "AFTER"]
        if len(before) != 1 or len(after) != 1:
            continue
        before_contract = _dict(before[0].get("database_observer_contract"))
        after_contract = _dict(after[0].get("database_observer_contract"))
        if not before_contract or _canonical(before_contract) != _canonical(after_contract):
            continue
        pairs.append(
            {
                "observer_contract_ref": contract_ref,
                "before_draft": before[0],
                "after_draft": after[0],
                "contract": before_contract,
            }
        )
    return pairs


def _binding_identity_values(binding: dict[str, Any]) -> set[str]:
    return {
        _text(binding.get(key))
        for key in (
            "field_binding_id",
            "api_field_id",
            "database_field_id",
        )
        if _text(binding.get(key))
    }


def _binding_name_values(binding: dict[str, Any]) -> set[str]:
    values = {
        _name_token(binding.get("api_field_name")),
        _name_token(binding.get("database_field_name")),
    }
    path = [_text(value) for value in _list(binding.get("api_property_path")) if _text(value)]
    if path:
        values.add(_name_token(path[-1]))
    return {value for value in values if value}


def _matching_bindings(
    assertion: dict[str, Any], experiment: dict[str, Any]
) -> list[dict[str, Any]]:
    explicit_ids, explicit_names = _explicit_field_tokens(assertion, experiment)
    id_matches: list[dict[str, Any]] = []
    name_matches: list[dict[str, Any]] = []
    for pair in _draft_pairs(experiment):
        contract = _dict(pair.get("contract"))
        for raw in _list(contract.get("field_bindings")):
            binding = _dict(raw)
            if not binding or not bool(binding.get("authoritative")):
                continue
            if not bool(binding.get("read_only")) or bool(binding.get("oracle_authority_allowed")):
                continue
            candidate = {**pair, "field_binding": binding}
            if explicit_ids.intersection(_binding_identity_values(binding)):
                candidate["match_basis"] = "EXACT_FIELD_ID"
                id_matches.append(candidate)
            elif explicit_names.intersection(_binding_name_values(binding)):
                candidate["match_basis"] = "EXACT_FIELD_NAME"
                name_matches.append(candidate)
    selected = id_matches if id_matches else name_matches
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in selected:
        key = (
            _text(row.get("observer_contract_ref")),
            _text(_dict(row.get("field_binding")).get("field_binding_id")),
        )
        if all(key):
            deduped[key] = row
    return list(deduped.values())


def _source_state_assertion(assertion: dict[str, Any]) -> bool:
    kind = _text(assertion.get("kind") or assertion.get("type")).lower()
    return kind in _STATE_KINDS


def _eligible(assertion: dict[str, Any]) -> bool:
    if not _source_state_assertion(assertion):
        return False
    return bool(
        _state_token(assertion.get("from_state")) not in _UNKNOWN_STATES
        and _state_token(assertion.get("to_state")) not in _UNKNOWN_STATES
    )


def _projected_assertion(
    assertion: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    binding = _dict(candidate.get("field_binding"))
    contract = _dict(candidate.get("contract"))
    before = _dict(candidate.get("before_draft"))
    after = _dict(candidate.get("after_draft"))
    source_kind = _text(assertion.get("kind") or assertion.get("type"))
    evidence = [
        dict(row)
        for row in _list(binding.get("evidence"))
        if isinstance(row, dict)
    ]
    return {
        **deepcopy(assertion),
        "kind": DATABASE_STATE_TRANSITION_ASSERTION_KIND,
        "source_assertion_kind": source_kind,
        "database_observer_contract_ref": _text(candidate.get("observer_contract_ref")),
        "before_draft_id": _text(before.get("draft_id")),
        "after_draft_id": _text(after.get("draft_id")),
        "database_table_ref": _text(contract.get("database_table_id")),
        "database_table_name": _text(contract.get("database_table_name")),
        "database_field_id": _text(binding.get("database_field_id")),
        "database_field_name": _text(binding.get("database_field_name")),
        "api_field_id": _text(binding.get("api_field_id")),
        "api_field_name": _text(binding.get("api_field_name")),
        "field_binding_id": _text(binding.get("field_binding_id")),
        "database_state_transition_binding": {
            "schema": PROJECTION_SCHEMA,
            "match_basis": _text(candidate.get("match_basis")),
            "observer_contract_ref": _text(candidate.get("observer_contract_ref")),
            "before_draft_id": _text(before.get("draft_id")),
            "after_draft_id": _text(after.get("draft_id")),
            "database_table_ref": _text(contract.get("database_table_id")),
            "database_field_id": _text(binding.get("database_field_id")),
            "database_field_name": _text(binding.get("database_field_name")),
            "field_binding_id": _text(binding.get("field_binding_id")),
            "mapping_decision_id": _text(binding.get("mapping_decision_id")),
            "source_evidence": evidence,
            "automatic_field_inference": False,
            "fuzzy_name_matching": False,
            "observer_oracle_authority": False,
        },
    }


def _blocked(experiment: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(experiment)
    receipt = _dict(row.get("compile_receipt"))
    receipt.update(
        {
            "status": "BLOCKED",
            "reason_code": _BLOCK_REASON,
            "database_state_oracle_detail": detail,
        }
    )
    row.update(
        {
            "compile_receipt": receipt,
            "compile_status": "BLOCKED",
            "database_state_transition_projection_status": "BLOCKED",
        }
    )
    return row


def project_database_state_transition_assertions(
    experiment_pack: dict[str, Any],
) -> dict[str, Any]:
    """Replace only exact state assertions and preserve all unresolved cases visibly."""
    pack = dict(experiment_pack or {})
    compiled: list[dict[str, Any]] = []
    newly_blocked: list[dict[str, Any]] = []
    projected_assertion_count = 0
    incomplete_assertion_count = 0

    for raw in _list(pack.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = deepcopy(raw)
        assertions = [
            dict(row) for row in _list(experiment.get("assertions")) if isinstance(row, dict)
        ]
        projected: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        ambiguous: dict[str, Any] | None = None
        for assertion in assertions:
            if not _eligible(assertion):
                projected.append(assertion)
                continue
            matches = _matching_bindings(assertion, experiment)
            if len(matches) > 1:
                ambiguous = {
                    "assertion_id": _text(assertion.get("assertion_id")),
                    "candidate_count": len(matches),
                    "candidate_refs": [
                        {
                            "observer_contract_ref": _text(row.get("observer_contract_ref")),
                            "field_binding_id": _text(
                                _dict(row.get("field_binding")).get("field_binding_id")
                            ),
                            "database_field_id": _text(
                                _dict(row.get("field_binding")).get("database_field_id")
                            ),
                        }
                        for row in matches
                    ],
                    "automatic_winner_allowed": False,
                }
                break
            if not matches:
                explicit_ids, explicit_names = _explicit_field_tokens(assertion, experiment)
                gaps.append(
                    {
                        "assertion_id": _text(assertion.get("assertion_id")),
                        "reason_code": "DATABASE_STATE_EXACT_FIELD_BINDING_MISSING",
                        "explicit_field_ids": sorted(explicit_ids),
                        "explicit_field_names": sorted(explicit_names),
                        "fuzzy_matching_allowed": False,
                    }
                )
                projected.append(assertion)
                incomplete_assertion_count += 1
                continue
            projected.append(_projected_assertion(assertion, matches[0]))
            projected_assertion_count += 1

        if ambiguous is not None:
            newly_blocked.append(_blocked(experiment, ambiguous))
            continue
        experiment["assertions"] = projected
        if any(
            _text(row.get("kind")) == DATABASE_STATE_TRANSITION_ASSERTION_KIND
            for row in projected
        ):
            experiment["observers"] = [
                dict(row)
                for row in _list(experiment.get("observers"))
                if isinstance(row, dict)
                and _text(row.get("observer_id")) not in {"before_state", "after_state"}
            ]
            contract = _dict(experiment.get("field_oracle_runtime_contract"))
            if contract:
                contract.update(
                    {
                        "before_observation_contract": "approved_database_phase_aggregate",
                        "after_observation_contract": "approved_database_phase_aggregate",
                        "assertion_kind": DATABASE_STATE_TRANSITION_ASSERTION_KIND,
                        "database_state_transition_bound": True,
                    }
                )
                experiment["field_oracle_runtime_contract"] = contract
            fingerprint = _fingerprint(
                [
                    row
                    for row in projected
                    if _text(row.get("kind"))
                    == DATABASE_STATE_TRANSITION_ASSERTION_KIND
                ]
            )
            receipt = _dict(experiment.get("compile_receipt"))
            receipt.update(
                {
                    "database_state_transition_projection_status": "BOUND",
                    "database_state_transition_assertion_fingerprint": fingerprint,
                    "database_state_transition_assertion_count": sum(
                        1
                        for row in projected
                        if _text(row.get("kind"))
                        == DATABASE_STATE_TRANSITION_ASSERTION_KIND
                    ),
                    "database_state_transition_fuzzy_matching_used": False,
                }
            )
            experiment["compile_receipt"] = receipt
            experiment["database_state_transition_projection_status"] = "BOUND"
            experiment["database_state_transition_assertion_fingerprint"] = fingerprint
        elif gaps:
            experiment["database_state_transition_projection_status"] = "INCOMPLETE"
        else:
            experiment["database_state_transition_projection_status"] = "NOT_APPLICABLE"
        if gaps:
            experiment["database_state_transition_projection_gaps"] = gaps
        compiled.append(experiment)

    existing_blocked = [
        dict(row)
        for row in _list(pack.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    blocked = [*existing_blocked, *newly_blocked]
    reason_counts = dict(_dict(pack.get("block_reason_counts")))
    if newly_blocked:
        reason_counts[_BLOCK_REASON] = reason_counts.get(_BLOCK_REASON, 0) + len(newly_blocked)
    pack.update(
        {
            "experiments": compiled,
            "blocked_experiments": blocked,
            "compiled_count": len(compiled),
            "blocked_count": len(blocked),
            "block_reason_counts": reason_counts,
            "database_state_transition_experiment_projection": {
                "schema": PROJECTION_SCHEMA,
                "status": "BLOCKED" if newly_blocked else "PARTIAL" if incomplete_assertion_count else "PASS",
                "projected_assertion_count": projected_assertion_count,
                "incomplete_assertion_count": incomplete_assertion_count,
                "newly_blocked_experiment_count": len(newly_blocked),
                "automatic_field_mapping_count": 0,
                "fuzzy_name_matching_count": 0,
                "automatic_conflict_winner_count": 0,
                "second_oracle_created": False,
                "contract_oracle_remains_authority": True,
            },
        }
    )
    return pack


__all__ = [
    "PROJECTION_SCHEMA",
    "project_database_state_transition_assertions",
]
