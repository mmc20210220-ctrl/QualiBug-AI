from __future__ import annotations


def _evidence(source: str, locator: str, fact_id: str) -> dict:
    return {
        "source_id": source,
        "source_locator": locator,
        "quote": f"source statement for {fact_id}",
        "quote_hash": f"hash:{fact_id}",
        "fact_id": fact_id,
        "derivation": "source_span",
    }


def _model() -> dict:
    order_evidence = _evidence("prd:order", "PRD.md#订单", "fact:order")
    customer_evidence = _evidence("prd:customer", "PRD.md#客户", "fact:customer")
    operation_evidence = _evidence("api:order", "openapi.yaml#/orders", "fact:create")
    lifecycle_evidence = _evidence("prd:order", "PRD.md#订单状态", "fact:lifecycle")
    relation_evidence = _evidence("prd:order", "PRD.md#订单客户", "fact:relation")
    behavior_evidence = _evidence("prd:order", "PRD.md#创建订单", "fact:behavior")
    candidate_evidence = _evidence("prd:legacy", "LEGACY.md#客户档案", "fact:candidate")
    return {
        "model_id": "model:world",
        "gate": {"status": "PASS", "entry_allowed": True},
        "business_objects": [
            {
                "entity_id": "entity:order",
                "object_id": "entity:order",
                "canonical_label": "订单",
                "name": "订单",
                "aliases": ["Order"],
                "source_kinds": ["business_object_asset"],
                "identity_resolution_status": "RESOLVED",
                "status": "UNDERSTOOD",
                "evidence": [order_evidence],
                # Deliberately stale legacy refs. The world model must rebuild them
                # from final authority collections instead of copying these values.
                "operation_refs": ["legacy:operation"],
                "lifecycle_refs": ["legacy:lifecycle"],
                "relation_refs": ["legacy:relation"],
            },
            {
                "entity_id": "entity:customer",
                "object_id": "entity:customer",
                "canonical_label": "客户",
                "name": "客户",
                "aliases": ["Customer"],
                "source_kinds": ["business_object_asset"],
                "identity_resolution_status": "RESOLVED",
                "status": "UNDERSTOOD",
                "evidence": [customer_evidence],
            },
        ],
        "operations": [
            {
                "operation_id": "operation:create-order",
                "name": "创建订单",
                "business_entity_refs": ["entity:order"],
                "object_refs": ["订单"],
                "evidence": [operation_evidence],
            }
        ],
        "lifecycles": [
            {
                "lifecycle_id": "lifecycle:order",
                "business_entity_ref": "entity:order",
                "object_ref": "订单",
                "evidence": [lifecycle_evidence],
            }
        ],
        "object_relations": [
            {
                "relation_id": "relation:customer-order",
                "source_entity_ref": "entity:customer",
                "target_entity_ref": "entity:order",
                "relation_type": "PLACES",
                "evidence": [relation_evidence],
            }
        ],
        "business_behaviors": [
            {
                "behavior_id": "behavior:create-order",
                "status": "CONFIRMED",
                "operation_ref": "operation:create-order",
                "business_entity_refs": ["entity:order"],
                "object_refs": ["订单"],
                "actor_refs": ["actor:customer"],
                "evidence": [behavior_evidence],
            },
            {
                "behavior_id": "behavior:customer-review",
                "status": "CANDIDATE",
                "operation_ref": "review customer",
                "business_entity_refs": ["entity:customer"],
                "object_refs": ["客户"],
                "actor_refs": [],
                "evidence": [candidate_evidence],
            },
        ],
        "behavior_implementation_bindings": [
            {
                "binding_id": "binding:create-order-api",
                "behavior_ref": "behavior:create-order",
                "evidence": [operation_evidence],
            }
        ],
        "identity_structural_candidates": [
            {
                "candidate_id": "candidate:customer-profile",
                "candidate_entity_ids": ["entity:customer", "entity:order"],
                "reason_code": "INDEPENDENT_STRUCTURAL_EVIDENCE_MATCH",
                "matched_dimensions": ["EXACT_OPERATION_SET"],
                "evidence": [candidate_evidence],
                "automatic_entity_union_allowed": False,
            }
        ],
        "conflicts": [],
        "source_authority_conflicts": [],
        "evidence_index": [
            order_evidence,
            customer_evidence,
            operation_evidence,
            lifecycle_evidence,
            relation_evidence,
            behavior_evidence,
            candidate_evidence,
        ],
    }
