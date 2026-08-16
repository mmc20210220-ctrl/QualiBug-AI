"""Entity-namespaced identity resolution in the flow-data execution contract.

The multi-level dependency chain establishes several entities in one
precondition plan (address, order), and each establishment step emits an
``identity_output_binding`` whose ``alias_targets`` includes the entity's own
identity field — ``id``. Two different entities therefore both produce the bare
target ``id``.

The historical contract flattened identity producers by target name alone, so a
downstream path-param transition (ship ``/api/orders/{id}/ship``) that consumes
``id`` was reported ``SEQUENTIAL_IDENTITY_PRODUCER_AMBIGUOUS`` — not because two
orders produced the id, but because the address entity and the order entity each
produced a field NAMED ``id``.

This pins the corrected semantics: identity producers are namespaced by the
entity they establish (``identity_output_binding.entity_ref``), and a consuming
step scopes its consumption with ``subject_entity_ref``. Two entities producing
the same field name are then unambiguously resolved by entity scope, while two
producers of the SAME entity's field stay ambiguous (fail closed).
"""
from __future__ import annotations

from ai_test_asset_center.flow_data_execution_contract import _ambiguity_issues


def _identity(entity_ref: str, consumer_targets: list[str], alias_targets: list[str]) -> dict:
    return {
        "schema_version": "qualibug.identity-output-binding.v1",
        "status": "FROZEN",
        "entity_ref": entity_ref,
        "source_identity_field": "id",
        "source_path": "id",
        "source_authority": "behavior_ir.entities.identity_fields",
        "consumer_targets": consumer_targets,
        "alias_targets": alias_targets,
    }


def _requirement(*step_requirements: dict) -> dict:
    return {
        "requirement_id": "flow-1",
        "requirement_fingerprint": "fp-1",
        "binding_targets": [],
        "step_requirements": list(step_requirements),
    }


def _seq_issues(producers: list[dict], consumer: dict, target: str) -> list[dict]:
    experiment = {
        "precondition_plan": producers,
        "treatment_plan": [consumer],
    }
    requirement = _requirement(
        *[
            {"step_id": step["step_id"], "required_binding_targets": []}
            for step in producers
        ],
        {"step_id": consumer["step_id"], "required_binding_targets": [target]},
    )
    return [
        row
        for row in _ambiguity_issues(experiment, requirement)
        if row["kind"] == "SEQUENTIAL_IDENTITY_PRODUCER_AMBIGUOUS"
    ]


def test_same_field_different_entities_is_not_ambiguous_when_consumer_scoped() -> None:
    """address.id and order.id are different values: a consumer scoped to the
    order entity resolves exactly one producer, never ambiguous."""
    producers = [
        {
            "step_id": "multi_level_create_addresses",
            "identity_output_binding": _identity("ent_address", ["addressId"], ["addressId", "id"]),
        },
        {
            "step_id": "money_precondition_create",
            "identity_output_binding": _identity("ent_order", ["orderId"], ["orderId", "id"]),
        },
    ]
    consumer = {
        "step_id": "treatment_1",
        "subject_entity_ref": "ent_order",
    }
    assert _seq_issues(producers, consumer, "id") == []


def test_same_field_same_entity_still_ambiguous() -> None:
    """Two producers of the SAME entity's id stay ambiguous — entity namespace
    does not erase genuine multi-producer ambiguity (fail closed)."""
    producers = [
        {
            "step_id": "create_order_a",
            "identity_output_binding": _identity("ent_order", ["orderId"], ["orderId", "id"]),
        },
        {
            "step_id": "create_order_b",
            "identity_output_binding": _identity("ent_order", ["orderId"], ["orderId", "id"]),
        },
    ]
    consumer = {
        "step_id": "treatment_1",
        "subject_entity_ref": "ent_order",
    }
    issues = _seq_issues(producers, consumer, "id")
    assert len(issues) == 1
    assert issues[0]["consumer_target"] == "id"
    assert issues[0]["producer_step_ids"] == ["create_order_a", "create_order_b"]


def test_unscoped_consumer_with_cross_entity_producers_fails_closed() -> None:
    """No subject_entity_ref on the consumer: the bare ``id`` is unresolvable to
    one entity, so it must NOT silently resolve to either entity. It stays
    ambiguous (never a wrong-entity bind)."""
    producers = [
        {
            "step_id": "multi_level_create_addresses",
            "identity_output_binding": _identity("ent_address", ["addressId"], ["addressId", "id"]),
        },
        {
            "step_id": "money_precondition_create",
            "identity_output_binding": _identity("ent_order", ["orderId"], ["orderId", "id"]),
        },
    ]
    consumer = {"step_id": "treatment_1"}
    issues = _seq_issues(producers, consumer, "id")
    assert len(issues) == 1


def test_entity_ref_is_optional_and_backward_compatible() -> None:
    """Identity outputs WITHOUT entity_ref (legacy) still collapse by bare
    target: two producers of the same bare target stay ambiguous."""
    legacy = _identity("", ["order_id"], ["order_id"])
    producers = [
        {"step_id": "a", "identity_output_binding": dict(legacy)},
        {"step_id": "b", "identity_output_binding": dict(legacy)},
    ]
    consumer = {"step_id": "use"}
    issues = _seq_issues(producers, consumer, "order_id")
    assert len(issues) == 1
