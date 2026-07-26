"""A relation endpoint must name a node, or be recorded as unresolved.

Behavior IR v2's contract is that relations reference node IDS. The knowledge-asset
passthrough at ``build_behavior_ir_from_knowledge_asset`` wrote raw names straight into
``from_ref``/``to_ref``:

    from_ref=from_e,   # "balance", "order:CANCELLED", "markdown_api:GET:/api/products"
    to_ref=to_e,

so those relations pointed at nothing. Measured on the live target: **254 relations**,
about 40% of the IR's total, had endpoints matching no node. They looked present and
were inert -- a consumer resolving a relation to a node got nothing, and a declared state
transition therefore contributed no obligations while sitting visibly in the IR.

``validate_behavior_ir`` never checked endpoint integrity: it verifies unique ids and the
derivation vocabulary and stops there, so nothing anywhere flagged it.

The fix resolves names to ids and, when a name resolves to nothing, records a coverage
gap instead of emitting the relation. 254 dangling endpoints became 0, and the gap count
came out at 13 rather than 217 because two classes are not gaps at all:

* **field-of** (175 rows): ``balance owns users`` says the column belongs to the table,
  which ``entities[].fields`` already records. Counted, not recorded as a gap -- burying
  13 genuine unresolved endpoints under 175 redundant ones helps nobody, and the release
  gate counts gaps.
* **declared aliases** (66 rows): a permission matrix names ``stock`` or ``item`` while
  the schema names ``inventory`` or ``products``. The lexicon already declares those
  equivalences in ``entity_alias_groups``, so this is a declaration lookup, not a
  similarity guess.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.behavior_ir import (
    _node_reference_index,
    _resolve_node_reference,
    build_behavior_ir_from_knowledge_asset,
)


def _model():
    return {
        "entities": [
            {"id": "bir_users", "name": "users", "fields": ["id", "email", "balance"]},
            {"id": "bir_orders", "name": "orders", "fields": ["id", "status"]},
            {"id": "bir_inventory", "name": "inventory", "fields": ["sku", "available_qty"]},
        ],
        "states": [
            {"id": "bir_cancelled", "name": "CANCELLED"},
            {"id": "bir_paid", "name": "PAID"},
        ],
        "operations": [
            {"id": "bir_get_products", "operation_id": "get_api_products",
             "method": "GET", "path": "/api/products"},
        ],
        "actors": [{"id": "bir_buyer", "role": "buyer"}],
        "invariants": [],
    }


# ── the index ───────────────────────────────────────────────────────────────

def test_a_node_id_resolves_to_itself() -> None:
    index = _node_reference_index(_model())
    assert _resolve_node_reference("bir_orders", index) == "bir_orders"


def test_a_plain_entity_name_resolves() -> None:
    index = _node_reference_index(_model())
    assert _resolve_node_reference("orders", index) == "bir_orders"


def test_singular_and_plural_both_resolve() -> None:
    """Entities are named for their tables; permission rows name the business object.

    34 declared role permissions on a real target resolved to nothing purely over an "s".
    """
    index = _node_reference_index(_model())
    assert _resolve_node_reference("order", index) == "bir_orders"
    assert _resolve_node_reference("orders", index) == "bir_orders"


def test_the_entity_state_composite_resolves_to_the_state() -> None:
    """``order:CANCELLED`` must reach the CANCELLED state node, not be dropped."""
    index = _node_reference_index(_model())
    assert _resolve_node_reference("order:CANCELLED", index) == "bir_cancelled"


def test_an_interface_id_resolves_to_its_operation() -> None:
    index = _node_reference_index(_model())
    assert _resolve_node_reference("markdown_api:GET:/api/products", index) == "bir_get_products"


def test_an_unknown_name_resolves_to_nothing() -> None:
    """Resolving to nothing is the signal that produces a gap. It must not guess."""
    index = _node_reference_index(_model())
    assert _resolve_node_reference("ledger", index) == ""
    assert _resolve_node_reference("", index) == ""


def test_declared_aliases_resolve_through_the_lexicon() -> None:
    """entity_alias_groups declares stock == inventory; the resolver honours it."""
    index = _node_reference_index(_model())
    assert _resolve_node_reference("stock", index) == "bir_inventory"


def test_an_undeclared_alias_is_not_invented() -> None:
    """Only declared equivalences count, or this becomes a similarity guess."""
    index = _node_reference_index(_model())
    assert _resolve_node_reference("warehouse_bin", index) == ""


# ── the builder emits no dangling endpoint ──────────────────────────────────

def _asset(entity_relations):
    return {
        "sources": [],
        "business_objects": [
            {"name": "orders", "fields": ["id", "status"]},
            {"name": "users", "fields": ["id", "email", "balance"]},
        ],
        # A role node is needed for the from-side of a permission relation to resolve,
        # so a test about the TO side is actually testing the to side.
        "roles": [{"role": "buyer", "name": "buyer"}],
        "entity_relations": list(entity_relations),
    }


def _built(entity_relations):
    return build_behavior_ir_from_knowledge_asset(_asset(entity_relations), project_id="p")


def _node_ids(model):
    ids = set()
    for collection in ("entities", "states", "operations", "actors", "invariants",
                       "sources", "observation_surfaces", "capabilities"):
        for node in model.get(collection) or []:
            if isinstance(node, dict) and node.get("id"):
                ids.add(str(node["id"]))
    return ids


def _dangling(model):
    ids = _node_ids(model)
    return [
        r for r in model.get("relations") or []
        if isinstance(r, dict)
        and (str(r.get("from_ref")) not in ids or str(r.get("to_ref")) not in ids)
    ]


def test_a_resolvable_relation_is_emitted_with_node_ids() -> None:
    model = _built([
        {"from_entity": "orders", "to_entity": "users", "relation_type": "foreign_key"},
    ])
    assert _dangling(model) == []
    emitted = [r for r in model["relations"] if str(r.get("relation_type")) == "owns"]
    assert emitted, "a resolvable relation must survive"
    assert all(str(r["from_ref"]).startswith("bir_") for r in emitted)


def test_an_unresolvable_relation_becomes_a_gap_not_a_dangling_edge() -> None:
    model = _built([
        {"from_entity": "buyer", "to_entity": "ledger", "relation_type": "permission:read"},
    ])
    assert _dangling(model) == []
    gaps = [
        g for g in model["coverage_gaps"]
        if isinstance(g, dict) and str(g.get("reason_code")) == "RELATION_ENDPOINT_NOT_A_NODE"
    ]
    assert len(gaps) == 1
    assert gaps[0]["to_entity"] == "ledger"
    assert gaps[0]["unresolved_side"] == "to"


def test_a_field_of_relation_is_counted_not_gapped() -> None:
    """balance -> users is already in entities[].fields.

    Recording 175 of these as gaps would bury the genuine unresolved endpoints.
    """
    model = _built([
        {"from_entity": "balance", "to_entity": "users", "relation_type": "field_of"},
    ])
    gaps = [
        g for g in model["coverage_gaps"]
        if isinstance(g, dict) and str(g.get("reason_code")) == "RELATION_ENDPOINT_NOT_A_NODE"
    ]
    assert gaps == [], "a field the entity declares is not an unresolved endpoint"

    suppressed = [
        c for c in model["capabilities"]
        if isinstance(c, dict)
        and str(c.get("capability")) == "entity_relation_field_of_suppressed"
    ]
    assert len(suppressed) == 1, "the suppression must be counted, never silent"
    assert suppressed[0]["suppressed_count"] == 1


def test_a_field_that_the_entity_does_not_declare_is_still_a_gap() -> None:
    """The suppression is narrow: it applies only to fields the entity really has."""
    model = _built([
        {"from_entity": "not_a_column", "to_entity": "users", "relation_type": "field_of"},
    ])
    gaps = [
        g for g in model["coverage_gaps"]
        if isinstance(g, dict) and str(g.get("reason_code")) == "RELATION_ENDPOINT_NOT_A_NODE"
    ]
    assert len(gaps) == 1


def test_no_relation_source_emits_no_gap() -> None:
    model = _built([])
    assert _dangling(model) == []
    assert not [
        g for g in model["coverage_gaps"]
        if isinstance(g, dict) and str(g.get("reason_code")) == "RELATION_ENDPOINT_NOT_A_NODE"
    ]
