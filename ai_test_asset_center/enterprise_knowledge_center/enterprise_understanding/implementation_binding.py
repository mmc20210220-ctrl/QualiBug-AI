"""Source-backed Business Behavior IR to system-implementation binding.

This layer binds already-governed business behaviors to observable system surfaces.  It does
not alter business semantics and it does not create executable tests.  Exact source-backed
relationships and exact contract identities may become authoritative; token overlap is kept as
diagnostic evidence only.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from .._linking import _contract_fields_for_interface
from .schema import as_dict, as_list, dedupe_evidence, source_evidence, stable_id, text, unique_text

IMPLEMENTATION_BINDING_SCHEMA = "qualibug.business-behavior-implementation-binding.v1"
IMPLEMENTATION_BINDING_GATE_SCHEMA = "qualibug.business-behavior-implementation-binding-gate.v1"

_NON_AUTHORITATIVE_RELATION_STATUSES = {
    "candidate",
    "proposed",
    "unknown",
    "unsupported",
    "rejected",
}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_FIELD_TOKEN_RE = re.compile(r"`?([A-Za-z_][A-Za-z0-9_]{1,63}(?:\.[A-Za-z_][A-Za-z0-9_]{1,63})?)`?")


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [row for row in as_list(value) if isinstance(row, dict)]


def _authoritative_relationship(edge: dict[str, Any]) -> bool:
    status = text(edge.get("status") or "accepted").lower()
    derivation = text(edge.get("derivation")).lower().replace("-", "_")
    evidence_gate = text(edge.get("evidence_gate"))
    evidence = as_dict(edge.get("evidence"))
    if status in _NON_AUTHORITATIVE_RELATION_STATUSES:
        return False
    if derivation == "token_overlap" or evidence_gate == "token_overlap_only_requires_explicit_source_relation":
        return False
    if evidence and set(evidence) <= {"token_overlap"}:
        return False
    return True


def _source_declared_system_ref(interface: dict[str, Any]) -> str:
    """Return only an explicit system identity carried by the interface asset."""
    return text(
        interface.get("system_ref")
        or interface.get("target_system_ref")
        or interface.get("service_ref")
        or interface.get("approved_target_ref")
    )


def _source_declared_binding_specs(interface: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Copy source-declared runtime binding specs without inferring field identity."""
    return [dict(row) for row in as_list(interface.get(key)) if isinstance(row, dict)]


def _interface_evidence(interface: dict[str, Any], derivation: str) -> dict[str, Any]:
    locator = f"{text(interface.get('method')).upper()} {text(interface.get('path'))}".strip()
    return source_evidence(
        source_id=interface.get("source_id"),
        source_locator=locator,
        quote=interface.get("summary") or interface.get("source_excerpt"),
        asset_ref=interface.get("interface_id"),
        derivation=derivation,
    )


def _field_evidence(field: dict[str, Any], derivation: str) -> dict[str, Any]:
    table = text(field.get("table"))
    name = text(field.get("field") or field.get("name") or field.get("field_path"))
    locator = f"{table}.{name}" if table and name else name
    return source_evidence(
        source_id=field.get("source_id"),
        source_locator=locator,
        quote=field.get("description") or field.get("constraint"),
        asset_ref=field.get("field_id"),
        derivation=derivation,
    )


def _ui_evidence(spec: dict[str, Any], label: str) -> dict[str, Any]:
    return source_evidence(
        source_id=spec.get("source_id"),
        source_locator=f"ui:{text(spec.get('name'))};label={label}",
        quote=spec.get("description"),
        asset_ref=spec.get("ui_spec_id"),
        derivation="exact_ui_label_or_component",
    )


def _source_rule_ids(asset: dict[str, Any], behavior: dict[str, Any]) -> set[str]:
    refs = {text(value) for value in as_list(behavior.get("source_refs")) if text(value)}
    result = {value for value in refs if value.startswith("rule:")}
    for rule in _dicts(asset.get("rule_library")):
        rule_id = text(rule.get("rule_id"))
        fact_refs = unique_text(
            [
                rule.get("fact_id"),
                rule.get("source_fact_id"),
                as_dict(rule.get("semantic_contract")).get("fact_id"),
                *as_list(rule.get("fact_refs")),
            ]
        )
        if rule_id and (rule_id in refs or refs.intersection(fact_refs)):
            result.add(rule_id)
    return result


def _authoritative_interface_ids(asset: dict[str, Any], behavior: dict[str, Any]) -> set[str]:
    source_refs = {text(value) for value in as_list(behavior.get("source_refs")) if text(value)}
    source_refs |= _source_rule_ids(asset, behavior)
    result: set[str] = set()
    for edge in _dicts(asset.get("relationships")):
        if text(edge.get("relation")) not in {
            "rule_to_interface",
            "behavior_to_interface",
            "operation_to_interface",
        }:
            continue
        if text(edge.get("from")) not in source_refs:
            continue
        if _authoritative_relationship(edge) and text(edge.get("to")):
            result.add(text(edge.get("to")))
    return result


def _interface_labels(interface: dict[str, Any]) -> set[str]:
    path = text(interface.get("path")).rstrip("/")
    terminal = path.rsplit("/", 1)[-1].strip("{}") if path else ""
    labels = unique_text(
        [
            interface.get("operation_id"),
            interface.get("summary"),
            terminal,
            *as_list(interface.get("tags")),
        ]
    )
    expanded: set[str] = set()
    for label in labels:
        normalized = _norm(label)
        if normalized:
            expanded.add(normalized)
        for suffix in ("接口", "操作", "api"):
            if normalized.endswith(_norm(suffix)) and len(normalized) > len(_norm(suffix)):
                expanded.add(normalized[: -len(_norm(suffix))])
    return expanded


def _interface_source_semantics(interface: dict[str, Any]) -> set[str]:
    """Normalized source-declared phrases that identify one API operation.

    This is deliberately not a fuzzy score.  Every phrase comes from the API
    contract itself (operation id, summary, excerpt, tags or parser tokens).
    """
    values = unique_text(
        [
            interface.get("operation_id"),
            interface.get("summary"),
            interface.get("source_excerpt"),
            *as_list(interface.get("tags")),
            *as_list(interface.get("tokens")),
        ]
    )
    return {_norm(value) for value in values if _norm(value)}


def _operation_object_identity_match(
    behavior: dict[str, Any], interface: dict[str, Any]
) -> bool:
    """Match an exact action+object phrase across two source-backed contracts.

    A generic action such as ``创建`` is not an endpoint identity by itself.  It
    becomes an exact semantic identity only when at least one governed business
    object is also present in the same source-declared API phrase.  Ambiguity is
    handled by the caller and never resolved by ranking or token scores.
    """
    operation = _norm(behavior.get("operation_ref"))
    object_refs = {
        _norm(value)
        for value in as_list(behavior.get("object_refs"))
        if _norm(value)
    }
    if not operation or not object_refs:
        return False
    phrases = _interface_source_semantics(interface)
    return any(
        (operation == phrase or operation in phrase)
        and any(object_ref in phrase for object_ref in object_refs)
        for phrase in phrases
    )


def _exact_operation_interfaces(
    behavior: dict[str, Any], interfaces: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    operation = _norm(behavior.get("operation_ref"))
    if not operation:
        return []
    direct = [
        {**row, "_binding_derivation": "exact_operation_identity"}
        for row in interfaces
        if operation in _interface_labels(row)
    ]
    matches = direct or [
        {**row, "_binding_derivation": "exact_operation_object_source_identity"}
        for row in interfaces
        if _operation_object_identity_match(behavior, row)
    ]
    has_effect = bool(
        as_list(behavior.get("state_effects"))
        or as_list(behavior.get("data_effects"))
        or text(behavior.get("permission_decision")) in {"ALLOW", "DENY", "REQUIRE_APPROVAL", "REQUIRE_CONFIRMATION"}
    )
    if has_effect:
        write_matches = [row for row in matches if text(row.get("method")).upper() in _WRITE_METHODS]
        if write_matches:
            matches = write_matches
    return matches


def _token_overlap_candidates(
    behavior: dict[str, Any], interfaces: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    tokens = {
        value
        for raw in [behavior.get("operation_ref"), *as_list(behavior.get("object_refs"))]
        for value in re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", text(raw).lower())
    }
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    for interface in interfaces:
        interface_tokens = {
            value
            for value in as_list(interface.get("tokens"))
            if text(value)
        }
        if not interface_tokens:
            interface_tokens = {
                value
                for value in re.findall(
                    r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}",
                    f"{text(interface.get('path'))} {text(interface.get('operation_id'))} {text(interface.get('summary'))}".lower(),
                )
            }
        overlap = sorted(tokens & interface_tokens)
        if overlap:
            scored.append((len(overlap), interface, overlap))
    return [
        {
            "interface_id": row.get("interface_id"),
            "method": row.get("method"),
            "path": row.get("path"),
            "status": "CANDIDATE_ONLY",
            "derivation": "token_overlap_diagnostic",
            "token_overlap": overlap,
            "authoritative": False,
            "evidence": [_interface_evidence(row, "token_overlap_diagnostic")],
        }
        for _score, row, overlap in sorted(
            scored, key=lambda item: (-item[0], text(item[1].get("interface_id")))
        )[:3]
    ]


def _bind_action(
    asset: dict[str, Any], behavior: dict[str, Any], interfaces: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {text(row.get("interface_id")): row for row in interfaces if text(row.get("interface_id"))}
    authoritative_ids = _authoritative_interface_ids(asset, behavior)
    authoritative = [by_id[value] for value in sorted(authoritative_ids) if value in by_id]
    exact = _exact_operation_interfaces(behavior, interfaces)
    conflicts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []

    if authoritative and len(exact) == 1 and text(exact[0].get("interface_id")) not in authoritative_ids:
        conflicts.append(
            {
                "conflict_id": stable_id(
                    "implementation_binding_conflict",
                    behavior.get("behavior_id"),
                    sorted(authoritative_ids),
                    exact[0].get("interface_id"),
                ),
                "kind": "BEHAVIOR_API_BINDING_CONFLICT",
                "status": "UNRESOLVED",
                "severity": "P0",
                "behavior_ref": behavior.get("behavior_id"),
                "authoritative_interface_refs": sorted(authoritative_ids),
                "exact_operation_interface_ref": exact[0].get("interface_id"),
                "automatic_resolution_allowed": False,
                "evidence": dedupe_evidence(
                    [
                        *[_interface_evidence(row, "authoritative_relationship") for row in authoritative],
                        _interface_evidence(
                            exact[0],
                            text(exact[0].get("_binding_derivation"))
                            or "exact_operation_identity",
                        ),
                    ]
                ),
            }
        )

    selected = authoritative or exact if len(exact) == 1 else authoritative
    bindings = [
        {
            "binding_id": stable_id(
                "behavior_api_binding", behavior.get("behavior_id"), row.get("interface_id")
            ),
            "interface_id": row.get("interface_id"),
            "method": text(row.get("method")).upper(),
            "path": row.get("path"),
            "operation_id": row.get("operation_id"),
            "summary": row.get("summary"),
            "system_ref": _source_declared_system_ref(row),
            "input_binding_refs": _source_declared_binding_specs(row, "input_binding_refs"),
            "output_binding_specs": _source_declared_binding_specs(row, "output_binding_specs"),
            "status": "BOUND",
            "authoritative": True,
            "derivation": (
                "authoritative_relationship"
                if text(row.get("interface_id")) in authoritative_ids
                else (
                    text(row.get("_binding_derivation"))
                    or "exact_operation_identity"
                )
            ),
            "contract_fields": sorted(_contract_fields_for_interface(row)),
            "evidence": [_interface_evidence(row, "behavior_action_binding")],
        }
        for row in selected
    ]
    if not bindings:
        if len(exact) > 1:
            unknowns.append(
                {
                    "kind": "BEHAVIOR_API_BINDING_AMBIGUOUS",
                    "reason_code": "BEHAVIOR_API_BINDING_AMBIGUOUS",
                    "behavior_ref": behavior.get("behavior_id"),
                    "candidate_interface_refs": [row.get("interface_id") for row in exact],
                    "blocks_scenario_planning": True,
                }
            )
        else:
            unknowns.append(
                {
                    "kind": "BEHAVIOR_API_BINDING_UNRESOLVED",
                    "reason_code": "BEHAVIOR_API_BINDING_UNRESOLVED",
                    "behavior_ref": behavior.get("behavior_id"),
                    "operation_ref": behavior.get("operation_ref"),
                    "blocks_scenario_planning": True,
                }
            )
            bindings.extend(_token_overlap_candidates(behavior, interfaces))
    return bindings, unknowns, conflicts


def _object_matches_table(object_ref: str, field: dict[str, Any], tables: dict[str, dict[str, Any]]) -> bool:
    table_id = text(field.get("table_id"))
    table = tables.get(table_id) or {}
    names = unique_text(
        [field.get("table"), table.get("name"), *as_list(table.get("aliases"))]
    )
    return _norm(object_ref) in {_norm(value) for value in names if _norm(value)}


def _exact_field_rows(
    field_name: str,
    *,
    field_rows: list[dict[str, Any]],
    object_refs: list[str],
    tables: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    target = _norm(field_name.split(".")[-1])
    if not target:
        return []
    matches = [
        row
        for row in field_rows
        if target
        in {
            _norm(row.get("field")),
            _norm(text(row.get("field_path")).split(".")[-1]),
            _norm(row.get("name")),
        }
    ]
    object_matches = [
        row
        for row in matches
        if any(_object_matches_table(object_ref, row, tables) for object_ref in object_refs)
    ]
    return object_matches or matches


def _bind_field(
    field_name: str,
    *,
    behavior: dict[str, Any],
    field_rows: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    api_bindings: list[dict[str, Any]],
    slot_ref: str,
    purpose: str,
) -> dict[str, Any]:
    exact_rows = _exact_field_rows(
        field_name,
        field_rows=field_rows,
        object_refs=unique_text(as_list(behavior.get("object_refs"))),
        tables=tables,
    )
    db_bindings = [
        {
            "binding_kind": "DATABASE_FIELD",
            "field_id": row.get("field_id"),
            "table_id": row.get("table_id"),
            "table": row.get("table"),
            "field": row.get("field") or row.get("field_path"),
            "authoritative": True,
            "derivation": "exact_field_identity",
            "evidence": [_field_evidence(row, "behavior_field_binding")],
        }
        for row in exact_rows
    ]
    api_field_bindings: list[dict[str, Any]] = []
    target = _norm(field_name.split(".")[-1])
    for api_binding in api_bindings:
        if not api_binding.get("authoritative"):
            continue
        matches = [value for value in as_list(api_binding.get("contract_fields")) if _norm(value) == target]
        for match in matches:
            api_field_bindings.append(
                {
                    "binding_kind": "API_CONTRACT_FIELD",
                    "interface_id": api_binding.get("interface_id"),
                    "field": match,
                    "authoritative": True,
                    "derivation": "exact_bound_interface_contract_field",
                    "evidence": as_list(api_binding.get("evidence")),
                }
            )
    candidates = [*db_bindings, *api_field_bindings]
    db_tables = {text(row.get("table_id")) for row in db_bindings if text(row.get("table_id"))}
    status = "BOUND" if candidates and len(db_tables) <= 1 else "AMBIGUOUS" if len(db_tables) > 1 else "UNBOUND"
    return {
        "slot_ref": slot_ref,
        "purpose": purpose,
        "source_field_candidate": field_name,
        "status": status,
        "bindings": candidates,
        "automatic_alias_inference_allowed": False,
    }


def _explicit_effect_fields(behavior: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for index, row in enumerate(_dicts(behavior.get("state_effects"))):
        field = text(row.get("field") or row.get("field_ref"))
        if field:
            result.append((f"state_effect:{index}", field))
    for index, row in enumerate(_dicts(behavior.get("data_effects"))):
        field = text(row.get("field") or row.get("field_path") or row.get("name"))
        if not field:
            statement = text(row.get("statement") or row.get("effect") or row.get("raw"))
            match = _FIELD_TOKEN_RE.search(statement)
            field = text(match.group(1)) if match and ("." in match.group(1) or "_" in match.group(1)) else ""
        if field:
            result.append((f"data_effect:{index}", field))
    return result


def _ui_bindings(behavior: dict[str, Any], specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operation = _norm(behavior.get("operation_ref"))
    if not operation:
        return []
    matches: list[tuple[dict[str, Any], str]] = []
    for spec in specs:
        labels = unique_text(
            [
                *as_list(spec.get("components")),
                *as_list(spec.get("text_labels")),
            ]
        )
        for label in labels:
            if _norm(label) == operation:
                matches.append((spec, label))
    return [
        {
            "binding_id": stable_id(
                "behavior_ui_binding", behavior.get("behavior_id"), spec.get("ui_spec_id"), label
            ),
            "ui_spec_id": spec.get("ui_spec_id"),
            "ui_name": spec.get("name"),
            "label": label,
            "status": "CANDIDATE_DESIGN_BINDING",
            "authoritative": False,
            "executable_locator_available": False,
            "derivation": "exact_ui_label_or_component",
            "evidence": [_ui_evidence(spec, label)],
        }
        for spec, label in matches
    ]


def build_behavior_implementation_bindings(
    asset: dict[str, Any], behaviors: Iterable[dict[str, Any]]
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Bind Behavior IR to source-backed API, UI and data observation surfaces."""
    interfaces = _dicts(asset.get("interfaces"))
    specs = _dicts(asset.get("ui_design_specs"))
    field_rows = _dicts(asset.get("field_dictionary"))
    tables = {
        text(row.get("table_id")): row
        for row in _dicts(asset.get("data_tables"))
        if text(row.get("table_id"))
    }
    bindings: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for behavior in behaviors:
        if not isinstance(behavior, dict) or not text(behavior.get("behavior_id")):
            continue
        api_bindings, action_unknowns, action_conflicts = _bind_action(asset, behavior, interfaces)
        ui_bindings = _ui_bindings(behavior, specs)
        condition_bindings: list[dict[str, Any]] = []
        for index, slot in enumerate(_dicts(behavior.get("preconditions"))):
            field_name = text(slot.get("field_candidate"))
            if not field_name:
                condition_bindings.append(
                    {
                        "slot_ref": slot.get("slot_id") or f"condition:{index}",
                        "purpose": "PRECONDITION_OBSERVER",
                        "source_field_candidate": "",
                        "status": "UNBOUND",
                        "bindings": [],
                        "reason_code": "IMPLEMENTATION_CONDITION_FIELD_UNPARSED",
                    }
                )
                continue
            condition_bindings.append(
                _bind_field(
                    field_name,
                    behavior=behavior,
                    field_rows=field_rows,
                    tables=tables,
                    api_bindings=api_bindings,
                    slot_ref=text(slot.get("slot_id")) or f"condition:{index}",
                    purpose="PRECONDITION_OBSERVER",
                )
            )

        effect_bindings = [
            _bind_field(
                field_name,
                behavior=behavior,
                field_rows=field_rows,
                tables=tables,
                api_bindings=api_bindings,
                slot_ref=slot_ref,
                purpose="EFFECT_OBSERVER",
            )
            for slot_ref, field_name in _explicit_effect_fields(behavior)
        ]
        state_fields = unique_text(
            binding.get("source_field_candidate")
            for binding in condition_bindings
            if re.search(r"(?:状态|status|state)", text(binding.get("source_field_candidate")), re.I)
        )
        if as_list(behavior.get("state_effects")) and state_fields and not effect_bindings:
            effect_bindings.extend(
                _bind_field(
                    field_name,
                    behavior=behavior,
                    field_rows=field_rows,
                    tables=tables,
                    api_bindings=api_bindings,
                    slot_ref=f"state_effect_field:{index}",
                    purpose="STATE_EFFECT_OBSERVER",
                )
                for index, field_name in enumerate(state_fields)
            )

        authoritative_api = [row for row in api_bindings if row.get("authoritative")]
        response_observer = []
        if authoritative_api and text(behavior.get("permission_decision")) not in {"", "UNSPECIFIED", "CONFLICTED"}:
            response_observer = [
                {
                    "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                    "interface_id": row.get("interface_id"),
                    "status": "BOUND_CHANNEL_ONLY",
                    "authoritative": True,
                    "expected_assertion_compiled": False,
                    "derivation": "bound_api_operation_response_channel",
                    "evidence": as_list(row.get("evidence")),
                }
                for row in authoritative_api
            ]

        condition_ready = bool(condition_bindings) and all(
            text(row.get("status")) == "BOUND" for row in condition_bindings
        ) if as_list(behavior.get("preconditions")) else True
        effect_ready = any(text(row.get("status")) == "BOUND" for row in effect_bindings) or bool(response_observer)
        action_ready = bool(authoritative_api)
        ambiguous = any(text(row.get("status")) == "AMBIGUOUS" for row in [*condition_bindings, *effect_bindings])
        semantic_ready = text(behavior.get("status")) == "CONFIRMED" and text(
            behavior.get("condition_combinator")
        ) not in {"UNRESOLVED", ""} if len(as_list(behavior.get("preconditions"))) > 1 else text(behavior.get("status")) == "CONFIRMED"
        scenario_ready = bool(action_ready and condition_ready and effect_ready and semantic_ready and not ambiguous and not action_conflicts)
        status = (
            "CONFLICTED"
            if action_conflicts
            else "AMBIGUOUS"
            if ambiguous or any(row.get("kind") == "BEHAVIOR_API_BINDING_AMBIGUOUS" for row in action_unknowns)
            else "BOUND"
            if scenario_ready
            else "PARTIAL"
            if action_ready or condition_bindings or effect_bindings or ui_bindings
            else "UNBOUND"
        )
        binding = {
            "schema": IMPLEMENTATION_BINDING_SCHEMA,
            "binding_id": stable_id("behavior_implementation_binding", behavior.get("behavior_id")),
            "behavior_ref": behavior.get("behavior_id"),
            "behavior_status": behavior.get("status"),
            "operation_ref": behavior.get("operation_ref"),
            "object_refs": unique_text(as_list(behavior.get("object_refs"))),
            "api_operation_bindings": api_bindings,
            "ui_action_bindings": ui_bindings,
            "condition_observer_bindings": condition_bindings,
            "effect_observer_bindings": effect_bindings,
            "response_observer_bindings": response_observer,
            "status": status,
            "scenario_planning_ready": scenario_ready,
            "execution_ready": False,
            "request_payload_compiled": False,
            "expected_assertion_compiled": False,
            "automatic_endpoint_fallback_allowed": False,
            "token_overlap_is_authoritative": False,
            "evidence": dedupe_evidence(
                [
                    *[evidence for row in api_bindings for evidence in as_list(row.get("evidence"))],
                    *[evidence for row in ui_bindings for evidence in as_list(row.get("evidence"))],
                    *[
                        evidence
                        for slot in [*condition_bindings, *effect_bindings]
                        for candidate in as_list(slot.get("bindings"))
                        if isinstance(candidate, dict)
                        for evidence in as_list(candidate.get("evidence"))
                    ],
                ]
            ),
        }
        bindings.append(binding)
        conflicts.extend(action_conflicts)
        unknowns.extend(action_unknowns)
        for slot in condition_bindings:
            if text(slot.get("status")) != "BOUND":
                unknowns.append(
                    {
                        "kind": "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED",
                        "reason_code": "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED",
                        "behavior_ref": behavior.get("behavior_id"),
                        "slot_ref": slot.get("slot_ref"),
                        "field_candidate": slot.get("source_field_candidate"),
                        "status": slot.get("status"),
                        "blocks_scenario_planning": True,
                    }
                )
        if not effect_ready:
            unknowns.append(
                {
                    "kind": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
                    "reason_code": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
                    "behavior_ref": behavior.get("behavior_id"),
                    "blocks_scenario_planning": True,
                }
            )
        if text(behavior.get("status")) != "CONFIRMED":
            unknowns.append(
                {
                    "kind": "IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED",
                    "reason_code": "IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED",
                    "behavior_ref": behavior.get("behavior_id"),
                    "behavior_status": behavior.get("status"),
                    "blocks_scenario_planning": True,
                }
            )

    deduped_unknowns = list(
        {
            stable_id(
                "implementation_binding_unknown",
                row.get("kind"),
                row.get("behavior_ref"),
                row.get("slot_ref"),
                row.get("field_candidate"),
            ): {"unknown_id": stable_id(
                "implementation_binding_unknown",
                row.get("kind"),
                row.get("behavior_ref"),
                row.get("slot_ref"),
                row.get("field_candidate"),
            ), **row}
            for row in unknowns
            if isinstance(row, dict)
        }.values()
    )
    status_counts: dict[str, int] = defaultdict(int)
    for row in bindings:
        status_counts[text(row.get("status")) or "UNKNOWN"] += 1
    ready = sum(1 for row in bindings if row.get("scenario_planning_ready"))
    if conflicts or status_counts["CONFLICTED"]:
        gate_status = "BLOCKED_IMPLEMENTATION_BINDING_CONFLICT"
    elif bindings and ready == len(bindings):
        gate_status = "PASS"
    elif bindings:
        gate_status = "PARTIAL_IMPLEMENTATION_BINDING"
    else:
        gate_status = "NO_BEHAVIOR_IMPLEMENTATION_BINDING"
    gate = {
        "schema": IMPLEMENTATION_BINDING_GATE_SCHEMA,
        "status": gate_status,
        "entry_allowed": gate_status == "PASS",
        "scenario_planning_allowed": gate_status == "PASS",
        "execution_allowed": False,
        "metrics": {
            "behavior_binding_count": len(bindings),
            "scenario_ready_binding_count": ready,
            "bound_binding_count": status_counts["BOUND"],
            "partial_binding_count": status_counts["PARTIAL"],
            "unbound_binding_count": status_counts["UNBOUND"],
            "ambiguous_binding_count": status_counts["AMBIGUOUS"],
            "conflicted_binding_count": status_counts["CONFLICTED"],
            "implementation_binding_conflict_count": len(conflicts),
            "implementation_binding_unknown_count": len(deduped_unknowns),
            "scenario_ready_rate": round(ready / len(bindings), 4) if bindings else 0.0,
        },
        "quality_claim": "IMPLEMENTATION_BINDING_CLOSURE_NOT_RUNTIME_VERIFICATION",
        "semantic_understanding_gate_is_separate": True,
        "arbitrary_endpoint_fallback_allowed": False,
        "token_overlap_is_authoritative": False,
        "request_payload_compiled": False,
        "expected_assertion_compiled": False,
    }
    return bindings, deduped_unknowns, conflicts, gate


__all__ = [
    "IMPLEMENTATION_BINDING_SCHEMA",
    "IMPLEMENTATION_BINDING_GATE_SCHEMA",
    "build_behavior_implementation_bindings",
]
