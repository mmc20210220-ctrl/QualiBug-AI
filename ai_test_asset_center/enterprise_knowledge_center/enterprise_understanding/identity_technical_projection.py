"""Project explicit API, UI, event and message identities into the identity graph."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .._linking import _relationship_is_authoritative

from .identity_types import (
    IDENTITY_BINDING_SCHEMA,
    IDENTITY_EDGE_SCHEMA,
    IDENTITY_MENTION_SCHEMA,
    asset_evidence,
    identity_scope,
)
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

_COLLECTIONS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], str, str], ...] = (
    ("interfaces", ("interface_id",), ("operation_id", "summary", "name"), "API_OPERATION", "EXPOSES_ENTITY"),
    ("ui_design_specs", ("ui_spec_id",), ("name", "title", "page_name"), "UI_VIEW", "VIEW_OF"),
    ("events", ("event_id",), ("name", "event_name", "event_type", "topic"), "DOMAIN_EVENT", "EVENT_FOR"),
    ("event_contracts", ("event_id", "contract_id"), ("name", "event_name", "event_type", "topic"), "DOMAIN_EVENT", "EVENT_FOR"),
    ("message_contracts", ("message_id", "contract_id"), ("name", "message_name", "message_type", "topic"), "MESSAGE_CONTRACT", "MESSAGE_FOR"),
    ("async_contracts", ("contract_id",), ("name", "operation_id", "topic"), "ASYNC_CONTRACT", "MESSAGE_FOR"),
)
_EXPLICIT_RELATIONS = {
    "business_object_to_interface": "EXPOSES_ENTITY",
    "entity_to_interface": "EXPOSES_ENTITY",
    "object_to_interface": "EXPOSES_ENTITY",
    "business_object_to_ui": "VIEW_OF",
    "entity_to_ui": "VIEW_OF",
    "object_to_ui": "VIEW_OF",
    "business_object_to_event": "EVENT_FOR",
    "entity_to_event": "EVENT_FOR",
    "object_to_event": "EVENT_FOR",
    "business_object_to_message": "MESSAGE_FOR",
    "entity_to_message": "MESSAGE_FOR",
    "object_to_message": "MESSAGE_FOR",
}
_NON_AUTHORITATIVE = {"candidate", "proposed", "unknown", "rejected", "unsupported"}


def _record_ref(raw: dict[str, Any], keys: tuple[str, ...], fallback: str) -> str:
    return next((text(raw.get(key)) for key in keys if text(raw.get(key))), fallback)


def _declared_business_refs(raw: dict[str, Any]) -> list[str]:
    return unique_text(
        [
            raw.get("business_object"),
            raw.get("business_entity"),
            raw.get("business_object_ref"),
            raw.get("business_entity_ref"),
            raw.get("object_ref"),
            raw.get("entity_ref"),
            *as_list(raw.get("business_objects")),
            *as_list(raw.get("business_entities")),
            *as_list(raw.get("object_refs")),
            *as_list(raw.get("entity_refs")),
        ]
    )


def _business_lookup(asset: dict[str, Any], result: dict[str, Any]) -> dict[str, str]:
    lookup = dict(as_dict(result.get("label_to_entity")))
    for raw in as_list(asset.get("business_objects")):
        if not isinstance(raw, dict):
            continue
        label = text(raw.get("object") or raw.get("name"))
        entity_id = lookup.get(label)
        if not entity_id:
            continue
        for ref in unique_text([raw.get("object_id"), raw.get("id"), label, *as_list(raw.get("aliases"))]):
            lookup[ref] = entity_id
    return lookup


def _rule_fact_refs(rule: dict[str, Any]) -> list[str]:
    return unique_text(
        [
            rule.get("fact_id"),
            rule.get("source_fact_id"),
            as_dict(rule.get("semantic_contract")).get("fact_id"),
            *as_list(rule.get("fact_refs")),
        ]
    )


def _rule_entity_authority(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Resolve rule IDs through the existing fact identity authority.

    The source parser can retain a prose rule row beside its typed semantic row. They
    are joined only by exact source ID and exact statement, never by token similarity.
    Entity IDs come exclusively from the already-resolved business fact ledger.
    """
    fact_entities: dict[str, list[str]] = {}
    fact_evidence: dict[str, list[dict[str, Any]]] = {}
    for fact in as_list(as_dict(asset.get("business_fact_ledger")).get("items")):
        if not isinstance(fact, dict) or not text(fact.get("fact_id")):
            continue
        fact_id = text(fact.get("fact_id"))
        fact_entities[fact_id] = unique_text(
            [
                *as_list(fact.get("identity_resolution_refs")),
                *as_list(as_dict(fact.get("subject")).get("resolved_entity_refs")),
                *as_list(as_dict(fact.get("object")).get("resolved_entity_refs")),
            ]
        )
        fact_evidence[fact_id] = asset_evidence(
            fact, fact_id, "resolved_business_fact_identity"
        )

    rules = [
        row
        for row in as_list(asset.get("rule_library"))
        if isinstance(row, dict) and text(row.get("rule_id"))
    ]
    exact_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rule in rules:
        key = (text(rule.get("source_id")), text(rule.get("statement")))
        if all(key):
            exact_groups[key].update(_rule_fact_refs(rule))

    authority: dict[str, dict[str, Any]] = {}
    for rule in rules:
        refs = set(_rule_fact_refs(rule))
        key = (text(rule.get("source_id")), text(rule.get("statement")))
        if all(key):
            refs.update(exact_groups.get(key, set()))
        entities = unique_text(
            entity_id
            for fact_id in sorted(refs)
            for entity_id in fact_entities.get(fact_id, [])
        )
        if not entities:
            continue
        authority[text(rule.get("rule_id"))] = {
            "entity_ids": entities,
            "fact_refs": sorted(refs),
            "evidence": dedupe_evidence(
                evidence
                for fact_id in sorted(refs)
                for evidence in fact_evidence.get(fact_id, [])
            ),
        }
    return authority


def _relationship_business_refs(
    asset: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rule_authority = _rule_entity_authority(asset)
    for edge in as_list(asset.get("relationships")):
        if not isinstance(edge, dict):
            continue
        relation = text(edge.get("relation"))
        source, target = text(edge.get("from")), text(edge.get("to"))
        if not source or not target:
            continue
        if relation in _EXPLICIT_RELATIONS:
            status = text(edge.get("status") or "accepted").lower()
            derivation = text(edge.get("derivation")).lower().replace("-", "_")
            if status in _NON_AUTHORITATIVE:
                continue
            if derivation in {"token_overlap", "token_overlap_diagnostic"}:
                continue
            result[target].append(
                {
                    "business_ref": source,
                    "relation": _EXPLICIT_RELATIONS[relation],
                    "authority": "SOURCE_DECLARED_ASSET_RELATION",
                    "evidence": [],
                }
            )
            continue
        if relation != "rule_to_interface" or not _relationship_is_authoritative(edge):
            continue
        governed = as_dict(rule_authority.get(source))
        relationship_evidence = asset_evidence(
            edge, text(edge.get("edge_id")) or f"{source}->{target}",
            "source_backed_rule_implementation",
        )
        for entity_id in as_list(governed.get("entity_ids")):
            result[target].append(
                {
                    "business_ref": entity_id,
                    "relation": "EXPOSES_ENTITY",
                    "authority": "SOURCE_BACKED_RULE_IMPLEMENTATION",
                    "rule_ref": source,
                    "fact_refs": list(as_list(governed.get("fact_refs"))),
                    "evidence": dedupe_evidence(
                        [
                            *relationship_evidence,
                            *as_list(governed.get("evidence")),
                        ]
                    ),
                }
            )
    return result


def augment_technical_identity_projection(
    asset: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    lookup = _business_lookup(asset, result)
    relationships = _relationship_business_refs(asset)
    mentions = [row for row in as_list(result.get("mentions")) if isinstance(row, dict)]
    edges = [row for row in as_list(result.get("edges")) if isinstance(row, dict)]
    bindings = [row for row in as_list(result.get("bindings")) if isinstance(row, dict)]
    unknowns = [row for row in as_list(result.get("unknowns")) if isinstance(row, dict)]
    bound_artifacts = {text(row.get("artifact_ref")) for row in bindings if text(row.get("artifact_ref"))}

    for collection, id_keys, label_keys, artifact_type, default_relation in _COLLECTIONS:
        for index, raw in enumerate(as_list(asset.get(collection))):
            if not isinstance(raw, dict):
                continue
            artifact_ref = _record_ref(raw, id_keys, f"{collection}[{index}]")
            labels = unique_text(raw.get(key) for key in label_keys)
            if collection == "interfaces" and text(raw.get("method")) and text(raw.get("path")):
                labels.append(f"{text(raw.get('method')).upper()} {text(raw.get('path'))}")
            evidence = asset_evidence(raw, artifact_ref, f"source_backed_{artifact_type.lower()}")
            artifact_mentions: list[dict[str, Any]] = []
            for label in labels:
                mention = {
                    "schema": IDENTITY_MENTION_SCHEMA,
                    "mention_id": stable_id("identity_mention", artifact_ref, artifact_type, label),
                    "raw_label": label,
                    "comparison_keys": [label.casefold().replace(" ", "")],
                    "mention_type": "TECHNICAL_ARTIFACT",
                    "source_kind": collection.upper(),
                    "source_id": text(raw.get("source_id")) or "asset",
                    "source_locator": text(raw.get("source_locator")) or artifact_ref,
                    "scope": identity_scope(raw),
                    "artifact_type": artifact_type,
                    "artifact_ref": artifact_ref,
                    "evidence": evidence,
                }
                mentions.append(mention)
                artifact_mentions.append(mention)

            declared = [
                {
                    "business_ref": value,
                    "relation": default_relation,
                    "authority": "SOURCE_DECLARED_ASSET_RELATION",
                    "evidence": [],
                }
                for value in _declared_business_refs(raw)
            ]
            declared.extend(relationships.get(artifact_ref, []))
            known_entity_ids = {
                text(row.get("entity_id"))
                for row in as_list(result.get("clusters"))
                if isinstance(row, dict) and text(row.get("entity_id"))
            }
            entity_relations: dict[str, dict[str, Any]] = {}
            for declaration in declared:
                business_ref = text(as_dict(declaration).get("business_ref"))
                entity_id = (
                    business_ref
                    if business_ref in known_entity_ids
                    else lookup.get(business_ref)
                )
                if not entity_id:
                    continue
                current = entity_relations.setdefault(
                    entity_id,
                    {
                        "relation": text(as_dict(declaration).get("relation"))
                        or default_relation,
                        "authorities": [],
                        "rule_refs": [],
                        "fact_refs": [],
                        "evidence": [],
                    },
                )
                current["authorities"] = unique_text(
                    [
                        *as_list(current.get("authorities")),
                        as_dict(declaration).get("authority"),
                    ]
                )
                current["rule_refs"] = unique_text(
                    [
                        *as_list(current.get("rule_refs")),
                        as_dict(declaration).get("rule_ref"),
                    ]
                )
                current["fact_refs"] = unique_text(
                    [
                        *as_list(current.get("fact_refs")),
                        *as_list(as_dict(declaration).get("fact_refs")),
                    ]
                )
                current["evidence"] = dedupe_evidence(
                    [
                        *as_list(current.get("evidence")),
                        *as_list(as_dict(declaration).get("evidence")),
                    ]
                )
            for entity_id, authority_row in sorted(entity_relations.items()):
                relation = text(authority_row.get("relation")) or default_relation
                binding_evidence = dedupe_evidence(
                    [*evidence, *as_list(authority_row.get("evidence"))]
                )
                binding_id = stable_id("identity_binding", entity_id, artifact_type, artifact_ref, relation)
                bindings.append(
                    {
                        "schema": IDENTITY_BINDING_SCHEMA,
                        "binding_id": binding_id,
                        "entity_id": entity_id,
                        "artifact_type": artifact_type,
                        "artifact_ref": artifact_ref,
                        "artifact_label": labels[0] if labels else artifact_ref,
                        "relation": relation,
                        "status": "RESOLVED",
                        "identity_field_bindings": [],
                        "identity_authorities": list(
                            as_list(authority_row.get("authorities"))
                        ),
                        "source_rule_refs": list(
                            as_list(authority_row.get("rule_refs"))
                        ),
                        "source_fact_refs": list(
                            as_list(authority_row.get("fact_refs"))
                        ),
                        "evidence": binding_evidence,
                    }
                )
                bound_artifacts.add(artifact_ref)
                for mention in artifact_mentions[:1]:
                    edges.append(
                        {
                            "schema": IDENTITY_EDGE_SCHEMA,
                            "edge_id": stable_id("identity_edge", entity_id, mention.get("mention_id"), relation),
                            "entity_id": entity_id,
                            "right_mention_id": mention.get("mention_id"),
                            "relation": relation,
                            "evidence_class": (
                                "SOURCE_BACKED_RULE_IMPLEMENTATION"
                                if "SOURCE_BACKED_RULE_IMPLEMENTATION"
                                in as_list(authority_row.get("authorities"))
                                else "EXPLICIT_TECHNICAL_BINDING"
                            ),
                            "authority": (
                                "SOURCE_BACKED_RULE_IMPLEMENTATION"
                                if "SOURCE_BACKED_RULE_IMPLEMENTATION"
                                in as_list(authority_row.get("authorities"))
                                else "SOURCE_DECLARED_ASSET_RELATION"
                            ),
                            "status": "ACCEPTED",
                            "scope": identity_scope(raw),
                            "source_rule_refs": list(
                                as_list(authority_row.get("rule_refs"))
                            ),
                            "source_fact_refs": list(
                                as_list(authority_row.get("fact_refs"))
                            ),
                            "evidence": binding_evidence,
                            "automatic_union_allowed": False,
                        }
                    )
            if artifact_ref not in bound_artifacts:
                unknowns.append(
                    {
                        "schema": "qualibug.enterprise-business-unknown.v1",
                        "unknown_id": stable_id("understanding_unknown", "CROSS_SOURCE_IDENTITY_UNRESOLVED", artifact_type, artifact_ref),
                        "kind": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
                        "reason_code": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
                        "question": f"Technical artifact {artifact_ref} has no explicit business identity binding.",
                        "severity": "P1",
                        "blocks_formal_understanding": False,
                        "details": {
                            "artifact_type": artifact_type,
                            "artifact_ref": artifact_ref,
                            "labels": labels,
                            "automatic_inference_allowed": False,
                        },
                        "evidence": evidence,
                        "resolution_status": "UNRESOLVED",
                        "automatic_inference_allowed": False,
                    }
                )

    result["mentions"] = list({text(row.get("mention_id")): row for row in mentions if text(row.get("mention_id"))}.values())
    result["edges"] = list({text(row.get("edge_id")): row for row in edges if text(row.get("edge_id"))}.values())
    result["bindings"] = list({text(row.get("binding_id")): row for row in bindings if text(row.get("binding_id"))}.values())
    result["unknowns"] = list({text(row.get("unknown_id")): row for row in unknowns if text(row.get("unknown_id"))}.values())
    gate = dict(as_dict(result.get("gate")))
    conflicts = [row for row in as_list(result.get("conflicts")) if isinstance(row, dict)]
    if conflicts:
        gate.update({"status": "BLOCKED_ENTERPRISE_IDENTITY_CONFLICT", "entry_allowed": False})
    elif result["unknowns"]:
        gate.update({"status": "PARTIAL_ENTERPRISE_IDENTITY_BINDING", "entry_allowed": True})
    else:
        gate.update({"status": "PASS", "entry_allowed": True})
    gate["metrics"] = {
        **as_dict(gate.get("metrics")),
        "mention_count": len(result["mentions"]),
        "identity_edge_count": len(result["edges"]),
        "business_entity_count": len(as_list(result.get("clusters"))),
        "technical_binding_count": len(result["bindings"]),
        "technical_identity_unknown_count": len(result["unknowns"]),
        "unknown_count": len(result["unknowns"]),
        "conflict_count": len(conflicts),
    }
    result["gate"] = gate
    asset["enterprise_identity_resolution"] = result
    asset["enterprise_identity_gate"] = gate
    return result
