"""Field-key and cross-technical lineage evidence for enterprise identity.

This extends the existing identity graph; it is not a second resolver. Source-declared
field/key contracts enrich existing technical bindings. An unbound technical artifact is
bound only when an exact declared identity-key reference already resolves to exactly one
enterprise entity. Exact field-name matches remain review-only candidates.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from .identity_types import IDENTITY_BINDING_SCHEMA, asset_evidence
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

IDENTITY_FIELD_BINDING_SCHEMA = "qualibug.enterprise-identity-field-binding.v1"
IDENTITY_FIELD_EVIDENCE_SCHEMA = "qualibug.enterprise-identity-field-evidence.v1"

_COLLECTIONS: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    ("data_tables", ("table_id", "id"), "DATABASE_TABLE", ("columns", "fields")),
    (
        "interfaces",
        ("interface_id", "operation_id", "id"),
        "API_OPERATION",
        ("parameter_contracts", "request_body_fields", "response_fields", "fields"),
    ),
    (
        "ui_design_specs",
        ("ui_spec_id", "page_id", "id"),
        "UI_VIEW",
        ("form_fields", "fields"),
    ),
    ("events", ("event_id", "id"), "DOMAIN_EVENT", ("payload_fields", "fields")),
    (
        "event_contracts",
        ("event_id", "contract_id", "id"),
        "DOMAIN_EVENT",
        ("payload_fields", "fields"),
    ),
    (
        "message_contracts",
        ("message_id", "contract_id", "id"),
        "MESSAGE_CONTRACT",
        ("payload_fields", "fields"),
    ),
    (
        "async_contracts",
        ("contract_id", "id"),
        "ASYNC_CONTRACT",
        ("payload_fields", "fields"),
    ),
)
_FIELD_REF_KEYS = (
    "field_id",
    "column_id",
    "property_id",
    "json_path",
    "field",
    "column",
    "name",
    "path",
)
_BUSINESS_FIELD_KEYS = (
    "business_field_ref",
    "business_field",
    "entity_field_ref",
    "entity_field",
    "object_field_ref",
    "object_field",
)
_BUSINESS_FIELD_LIST_KEYS = (
    "business_field_refs",
    "business_fields",
    "entity_field_refs",
    "entity_fields",
    "object_field_refs",
    "object_fields",
)
_IDENTITY_KEY_KEYS = (
    "identity_key_ref",
    "identity_key",
    "business_key_ref",
    "business_key",
    "entity_key_ref",
    "entity_key",
)
_IDENTITY_KEY_LIST_KEYS = (
    "identity_key_refs",
    "identity_keys",
    "business_key_refs",
    "business_keys",
    "entity_key_refs",
    "entity_keys",
)
_TRUE_TEXT = {"1", "true", "yes", "y", "primary", "unique", "identity"}
_NORMALIZE_RE = re.compile(r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff]+")


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _record_ref(raw: dict[str, Any], keys: Iterable[str], fallback: str) -> str:
    return next((text(raw.get(key)) for key in keys if text(raw.get(key))), fallback)


def _normalized(value: Any) -> str:
    return _NORMALIZE_RE.sub("", text(value).casefold())


def _truthy(value: Any) -> bool:
    return value if isinstance(value, bool) else text(value).casefold() in _TRUE_TEXT


def _declared(
    raw: dict[str, Any], scalar: Iterable[str], plural: Iterable[str]
) -> list[str]:
    values: list[Any] = [raw.get(key) for key in scalar]
    for key in plural:
        values.extend(as_list(raw.get(key)))
    return unique_text(values)


def _identity_field(raw: dict[str, Any]) -> bool:
    role = text(raw.get("key_type") or raw.get("role")).upper()
    return (
        any(
            _truthy(raw.get(key))
            for key in (
                "primary_key",
                "is_primary_key",
                "unique",
                "is_unique",
                "identity",
                "is_identity",
                "identifier",
                "is_identifier",
            )
        )
        or role
        in {
            "PRIMARY",
            "PRIMARY_KEY",
            "UNIQUE",
            "UNIQUE_KEY",
            "IDENTITY",
            "IDENTIFIER",
            "BUSINESS_KEY",
        }
        or bool(_declared(raw, _IDENTITY_KEY_KEYS, _IDENTITY_KEY_LIST_KEYS))
    )


def _field_rows(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(_field_rows(item, prefix))
        return rows
    if not isinstance(value, dict):
        return rows
    row = dict(value)
    own = _record_ref(row, _FIELD_REF_KEYS, "")
    current = (
        f"{prefix}.{own}"
        if prefix and own and not own.startswith(prefix)
        else own or prefix
    )
    if own:
        row["_identity_field_path"] = current
        rows.append(row)
    for key in ("fields", "columns", "properties", "items"):
        rows.extend(_field_rows(row.get(key), current))
    return rows


def _dictionary(asset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in _dicts(asset.get("field_dictionary")):
        ref = text(
            raw.get("artifact_ref")
            or raw.get("table_id")
            or raw.get("interface_id")
            or raw.get("ui_spec_id")
            or raw.get("event_id")
            or raw.get("contract_id")
        )
        if ref:
            parsed = _field_rows(raw)
            rows[ref].extend(parsed or [raw])
    return rows


def _response_fields(interface: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for contract in _dicts(interface.get("response_contracts")):
        status = text(contract.get("status") or contract.get("status_code"))
        for key in (
            "fields",
            "response_fields",
            "body_fields",
            "properties",
            "schema",
        ):
            for field in _field_rows(contract.get(key)):
                field.setdefault("location", "RESPONSE")
                if status:
                    field.setdefault("response_status", status)
                rows.append(field)
    return rows


def _extract_fields(asset: dict[str, Any]) -> list[dict[str, Any]]:
    dictionary = _dictionary(asset)
    result: dict[str, dict[str, Any]] = {}
    for collection, id_keys, artifact_type, field_keys in _COLLECTIONS:
        for position, raw in enumerate(_dicts(asset.get(collection))):
            artifact_ref = _record_ref(raw, id_keys, f"{collection}[{position}]")
            fields = [
                field
                for key in field_keys
                for field in _field_rows(raw.get(key))
            ]
            if collection == "interfaces":
                fields.extend(_response_fields(raw))
            fields.extend(dictionary.get(artifact_ref, []))
            artifact_evidence = asset_evidence(
                raw,
                artifact_ref,
                f"source_backed_{artifact_type.lower()}_field_identity",
            )
            for field_position, field in enumerate(fields):
                field_ref = text(
                    field.get("_identity_field_path")
                    or _record_ref(field, _FIELD_REF_KEYS, "")
                ) or f"field[{field_position}]"
                business_fields = _declared(
                    field, _BUSINESS_FIELD_KEYS, _BUSINESS_FIELD_LIST_KEYS
                )
                identity_keys = _declared(
                    field, _IDENTITY_KEY_KEYS, _IDENTITY_KEY_LIST_KEYS
                )
                is_identity = _identity_field(field)
                if not business_fields and not identity_keys and not is_identity:
                    continue
                descriptor = {
                    "artifact_ref": artifact_ref,
                    "artifact_type": artifact_type,
                    "technical_field_ref": field_ref,
                    "technical_field_name": text(
                        field.get("name")
                        or field.get("field")
                        or field.get("column")
                        or field_ref.split(".")[-1]
                    ),
                    "field_location": text(field.get("location")).upper(),
                    "schema_type": text(
                        field.get("schema_type")
                        or field.get("type")
                        or field.get("data_type")
                    ),
                    "format": text(field.get("format")),
                    "business_field_refs": business_fields,
                    "identity_key_refs": identity_keys,
                    "is_identity_field": is_identity,
                    "is_primary_key": _truthy(field.get("primary_key"))
                    or _truthy(field.get("is_primary_key")),
                    "is_unique": _truthy(field.get("unique"))
                    or _truthy(field.get("is_unique")),
                    "evidence": dedupe_evidence(
                        [
                            *artifact_evidence,
                            *asset_evidence(
                                field,
                                f"{artifact_ref}:{field_ref}",
                                "source_declared_identity_field",
                            ),
                        ]
                    ),
                }
                descriptor_id = stable_id(
                    "enterprise_identity_field_descriptor",
                    artifact_ref,
                    field_ref,
                    business_fields,
                    identity_keys,
                )
                result[descriptor_id] = descriptor
    return list(result.values())


def _field_binding(
    field: dict[str, Any], entity_id: str, authority: str
) -> dict[str, Any]:
    business_fields = as_list(field.get("business_field_refs"))
    identity_keys = as_list(field.get("identity_key_refs"))
    return {
        "schema": IDENTITY_FIELD_BINDING_SCHEMA,
        "field_binding_id": stable_id(
            "enterprise_identity_field_binding",
            entity_id,
            field.get("artifact_ref"),
            field.get("technical_field_ref"),
            business_fields,
            identity_keys,
        ),
        "entity_id": entity_id,
        "artifact_ref": field.get("artifact_ref"),
        "artifact_type": field.get("artifact_type"),
        "technical_field_ref": field.get("technical_field_ref"),
        "technical_field_name": field.get("technical_field_name"),
        "field_location": field.get("field_location"),
        "schema_type": field.get("schema_type"),
        "format": field.get("format"),
        "business_field_refs": business_fields,
        "identity_key_refs": identity_keys,
        "is_identity_field": bool(field.get("is_identity_field")),
        "is_primary_key": bool(field.get("is_primary_key")),
        "is_unique": bool(field.get("is_unique")),
        "relation": (
            "IDENTIFIES_ENTITY"
            if field.get("is_identity_field")
            else "FIELD_OF_ENTITY"
        ),
        "authority": authority,
        "status": "RESOLVED",
        "automatic_entity_union_allowed": False,
        "evidence": dedupe_evidence(as_list(field.get("evidence"))),
    }


def _binding_index(
    bindings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in bindings:
        ref = text(binding.get("artifact_ref"))
        entity_id = text(binding.get("entity_id"))
        if ref and entity_id and text(binding.get("status")) == "RESOLVED":
            rows[ref].append(binding)
    return rows


def _key_conflict(
    key_ref: str,
    entity_ids: Iterable[str],
    evidence: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    candidates = sorted({text(value) for value in entity_ids if text(value)})
    kind = "IDENTITY_KEY_REF_MULTIPLE_ENTITIES"
    return {
        "kind": kind,
        "status": "UNRESOLVED",
        "conflict_id": stable_id(
            "enterprise_identity_field_conflict", kind, key_ref, candidates
        ),
        "reason_code": kind,
        "identity_key_ref": key_ref,
        "candidate_entity_ids": candidates,
        "automatic_resolution_allowed": False,
        "blocks_formal_understanding": True,
        "evidence": dedupe_evidence(evidence),
    }


def _artifact_key_conflict(
    artifact_ref: str,
    entity_ids: Iterable[str],
    identity_keys: Iterable[str],
    evidence: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    candidates = sorted({text(value) for value in entity_ids if text(value)})
    keys = sorted({text(value) for value in identity_keys if text(value)})
    kind = "IDENTITY_ARTIFACT_KEYS_DISAGREE"
    return {
        "kind": kind,
        "status": "UNRESOLVED",
        "conflict_id": stable_id(
            "enterprise_identity_field_conflict",
            kind,
            artifact_ref,
            keys,
            candidates,
        ),
        "reason_code": kind,
        "artifact_ref": artifact_ref,
        "identity_key_refs": keys,
        "candidate_entity_ids": candidates,
        "automatic_resolution_allowed": False,
        "blocks_formal_understanding": True,
        "evidence": dedupe_evidence(evidence),
    }


def _remove_resolved_unknowns(
    unknowns: list[dict[str, Any]], resolved: set[str]
) -> list[dict[str, Any]]:
    return [
        row
        for row in unknowns
        if not (
            text(row.get("reason_code")) == "CROSS_SOURCE_IDENTITY_UNRESOLVED"
            and text(
                as_dict(row.get("details")).get("artifact_ref")
                or row.get("artifact_ref")
            )
            in resolved
        )
    ]


def _dedupe_bindings(bindings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(
        {
            text(row.get("binding_id")): row
            for row in bindings
            if text(row.get("binding_id"))
        }.values()
    )


def _dedupe_conflicts(conflicts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(
        {
            text(row.get("conflict_id"))
            or stable_id(
                "enterprise_identity_conflict",
                row.get("kind"),
                row.get("artifact_ref"),
                row.get("candidate_entity_ids"),
            ): row
            for row in conflicts
        }.values()
    )


def augment_identity_field_evidence(
    asset: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Enrich bindings and close exact declared cross-technical key lineage."""
    fields = _extract_fields(asset)
    fields_by_artifact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in fields:
        fields_by_artifact[text(field.get("artifact_ref"))].append(field)

    bindings = [
        dict(row)
        for row in as_list(result.get("bindings"))
        if isinstance(row, dict)
    ]
    by_artifact = _binding_index(bindings)

    # Existing object-level technical bindings receive their complete declared
    # field projection. No entity identity is changed here.
    for artifact_ref, owners in sorted(by_artifact.items()):
        for owner in owners:
            entity_id = text(owner.get("entity_id"))
            existing = [
                dict(row)
                for row in as_list(owner.get("identity_field_bindings"))
                if isinstance(row, dict)
            ]
            generated = [
                _field_binding(
                    field,
                    entity_id,
                    "SOURCE_DECLARED_FIELD_CONTRACT",
                )
                for field in fields_by_artifact.get(artifact_ref, [])
            ]
            owner["identity_field_bindings"] = list(
                {
                    text(row.get("field_binding_id")): row
                    for row in [*existing, *generated]
                    if text(row.get("field_binding_id"))
                }.values()
            )

    # Exact declared identity keys from already-bound technical artifacts are the
    # only automatic cross-technical authority.
    key_entities: dict[str, set[str]] = defaultdict(set)
    key_evidence: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for artifact_ref, owners in by_artifact.items():
        for field in fields_by_artifact.get(artifact_ref, []):
            if not bool(field.get("is_identity_field")):
                continue
            for key_ref in as_list(field.get("identity_key_refs")):
                key = text(key_ref)
                for owner in owners:
                    entity_id = text(owner.get("entity_id"))
                    if key and entity_id:
                        key_entities[key].add(entity_id)
                        key_evidence[(key, entity_id)].extend(
                            as_list(field.get("evidence"))
                        )

    conflicts = [
        dict(row)
        for row in as_list(result.get("conflicts"))
        if isinstance(row, dict)
    ]
    for key, entity_ids in sorted(key_entities.items()):
        if len(entity_ids) > 1:
            conflicts.append(
                _key_conflict(
                    key,
                    entity_ids,
                    [
                        evidence
                        for entity_id in entity_ids
                        for evidence in key_evidence.get((key, entity_id), [])
                    ],
                )
            )

    resolved_artifacts: set[str] = set()
    conflicted_artifacts: set[str] = set()
    for artifact_ref, artifact_fields in sorted(fields_by_artifact.items()):
        if artifact_ref in by_artifact:
            continue
        identity_fields = [
            field
            for field in artifact_fields
            if bool(field.get("is_identity_field"))
            and as_list(field.get("identity_key_refs"))
        ]
        candidate_entities: set[str] = set()
        identity_keys: set[str] = set()
        supporting: list[dict[str, Any]] = []
        for field in identity_fields:
            supporting.extend(as_list(field.get("evidence")))
            for key_ref in as_list(field.get("identity_key_refs")):
                key = text(key_ref)
                if not key:
                    continue
                identity_keys.add(key)
                candidate_entities.update(key_entities.get(key, set()))
                for entity_id in key_entities.get(key, set()):
                    supporting.extend(key_evidence.get((key, entity_id), []))
        if len(candidate_entities) > 1:
            conflicts.append(
                _artifact_key_conflict(
                    artifact_ref,
                    candidate_entities,
                    identity_keys,
                    supporting,
                )
            )
            conflicted_artifacts.add(artifact_ref)
            continue
        if len(candidate_entities) != 1:
            continue

        entity_id = next(iter(candidate_entities))
        field_bindings = [
            _field_binding(
                field,
                entity_id,
                "CROSS_TECHNICAL_EXACT_IDENTITY_KEY_REF",
            )
            for field in artifact_fields
        ]
        binding = {
            "schema": IDENTITY_BINDING_SCHEMA,
            "binding_id": stable_id(
                "identity_binding",
                entity_id,
                artifact_fields[0].get("artifact_type"),
                artifact_ref,
                "IDENTIFIED_BY_KEY",
            ),
            "entity_id": entity_id,
            "artifact_type": artifact_fields[0].get("artifact_type"),
            "artifact_ref": artifact_ref,
            "artifact_label": artifact_ref,
            "relation": "IDENTIFIED_BY_KEY",
            "status": "RESOLVED",
            "authority": "CROSS_TECHNICAL_EXACT_IDENTITY_KEY_REF",
            "identity_key_refs": sorted(identity_keys),
            "identity_field_bindings": field_bindings,
            "automatic_entity_union_allowed": False,
            "evidence": dedupe_evidence(supporting),
        }
        bindings.append(binding)
        by_artifact[artifact_ref].append(binding)
        resolved_artifacts.add(artifact_ref)

    # Recompute name candidates only after exact-key closure. Exact field names are
    # diagnostic evidence and never become automatic binding authority.
    bound_names: dict[str, set[str]] = defaultdict(set)
    for artifact_ref, owners in by_artifact.items():
        for field in fields_by_artifact.get(artifact_ref, []):
            if (
                bool(field.get("is_identity_field"))
                and not as_list(field.get("identity_key_refs"))
            ):
                name = _normalized(field.get("technical_field_name"))
                if name:
                    bound_names[name].update(
                        text(owner.get("entity_id")) for owner in owners
                    )

    candidates: list[dict[str, Any]] = []
    for artifact_ref, artifact_fields in sorted(fields_by_artifact.items()):
        if artifact_ref in by_artifact or artifact_ref in conflicted_artifacts:
            continue
        for field in artifact_fields:
            if (
                not bool(field.get("is_identity_field"))
                or as_list(field.get("identity_key_refs"))
            ):
                continue
            name = _normalized(field.get("technical_field_name"))
            entity_ids = sorted(
                entity_id
                for entity_id in bound_names.get(name, set())
                if entity_id
            )
            if not name or not entity_ids:
                continue
            candidates.append(
                {
                    "candidate_id": stable_id(
                        "enterprise_identity_field_candidate",
                        artifact_ref,
                        field.get("technical_field_ref"),
                        entity_ids,
                    ),
                    "artifact_ref": artifact_ref,
                    "artifact_type": field.get("artifact_type"),
                    "technical_field_ref": field.get("technical_field_ref"),
                    "technical_field_name": field.get("technical_field_name"),
                    "candidate_entity_ids": entity_ids,
                    "reason_code": "EXACT_IDENTITY_FIELD_NAME_CANDIDATE",
                    "status": "CANDIDATE_ONLY",
                    "automatic_resolution_allowed": False,
                    "automatic_entity_union_allowed": False,
                    "evidence": dedupe_evidence(as_list(field.get("evidence"))),
                }
            )

    bindings = _dedupe_bindings(bindings)
    conflicts = _dedupe_conflicts(conflicts)
    unknowns = _remove_resolved_unknowns(
        [
            dict(row)
            for row in as_list(result.get("unknowns"))
            if isinstance(row, dict)
        ],
        resolved_artifacts,
    )
    receipt = {
        "schema": IDENTITY_FIELD_EVIDENCE_SCHEMA,
        "field_descriptor_count": len(fields),
        "field_binding_count": sum(
            len(as_list(row.get("identity_field_bindings")))
            for row in bindings
        ),
        "cross_technical_binding_count": sum(
            1
            for row in bindings
            if text(row.get("authority"))
            == "CROSS_TECHNICAL_EXACT_IDENTITY_KEY_REF"
        ),
        "candidate_only_count": len(candidates),
        "field_conflict_count": sum(
            1
            for row in conflicts
            if text(row.get("kind"))
            in {
                "IDENTITY_KEY_REF_MULTIPLE_ENTITIES",
                "IDENTITY_ARTIFACT_KEYS_DISAGREE",
            }
        ),
        "candidate_bindings": candidates,
        "automatic_field_name_binding_allowed": False,
        "automatic_entity_union_allowed": False,
        "exact_declared_identity_key_ref_required_for_cross_technical_binding": True,
        "technical_artifact_identity_decided_as_one_unit": True,
    }
    result.update(
        {
            "bindings": bindings,
            "unknowns": unknowns,
            "conflicts": conflicts,
            "identity_field_evidence": receipt,
        }
    )
    gate = dict(as_dict(result.get("gate")))
    gate.update(
        {
            "status": (
                "BLOCKED_ENTERPRISE_IDENTITY_CONFLICT"
                if conflicts
                else "PARTIAL_ENTERPRISE_IDENTITY_BINDING"
                if unknowns
                else "PASS"
            ),
            "entry_allowed": not conflicts,
            "business_understanding_allowed": not conflicts,
        }
    )
    metrics = dict(as_dict(gate.get("metrics")))
    metrics.update(
        {
            "technical_binding_count": len(bindings),
            "identity_field_binding_count": receipt["field_binding_count"],
            "cross_technical_key_binding_count": receipt[
                "cross_technical_binding_count"
            ],
            "identity_field_candidate_count": receipt["candidate_only_count"],
            "identity_field_conflict_count": receipt["field_conflict_count"],
        }
    )
    gate["metrics"] = metrics
    result["gate"] = gate
    asset["enterprise_identity_resolution"] = result
    asset["enterprise_identity_gate"] = gate
    asset["enterprise_identity_field_evidence"] = receipt
    return result


__all__ = [
    "IDENTITY_FIELD_BINDING_SCHEMA",
    "IDENTITY_FIELD_EVIDENCE_SCHEMA",
    "augment_identity_field_evidence",
]
