from __future__ import annotations


def test_parent_path_affinity_without_relation_is_not_observer_authority() -> None:
    from ai_test_asset_center.runtime_binding_graph import _observer_authority

    source = {
        "id": "confirm-order",
        "method": "POST",
        "path": "/api/orders/{id}/confirm",
    }
    observer = {
        "id": "read-order",
        "method": "GET",
        "path": "/api/orders/{id}",
    }

    assert _observer_authority(
        source,
        observer,
        {"operations": [source, observer], "relations": [], "entities": []},
    ) == ""


def test_exact_transport_path_get_is_observer_authority() -> None:
    from ai_test_asset_center.runtime_binding_graph import _observer_authority

    source = {"id": "patch-order", "method": "PATCH", "path": "/api/orders/{id}"}
    observer = {"id": "read-order", "method": "GET", "path": "/api/orders/{id}"}

    assert _observer_authority(
        source,
        observer,
        {"operations": [source, observer], "relations": [], "entities": []},
    ) == "exact_transport_path"


def test_explicit_produces_observes_relation_chain_is_authoritative() -> None:
    from ai_test_asset_center.runtime_binding_graph import _observer_authority

    # Source and observer must sit on different transport paths so the relation
    # chain is the only authority that can bind them; an identical path would
    # legitimately resolve as ``exact_transport_path`` before the relation chain
    # is ever consulted.
    source = {"id": "create-order", "method": "POST", "path": "/api/orders"}
    observer = {"id": "list-orders", "method": "GET", "path": "/api/orders/listing"}
    behavior_ir = {
        "operations": [source, observer],
        "entities": [{"id": "entity-order"}],
        "relations": [
            {
                "relation_type": "produces",
                "from_ref": "create-order",
                "to_ref": "entity-order",
                "source_refs": [{"source_id": "api-doc"}],
                "status": "accepted",
            },
            {
                "relation_type": "observes",
                "from_ref": "list-orders",
                "to_ref": "entity-order",
                "source_refs": [{"source_id": "api-doc"}],
                "status": "accepted",
            },
        ],
    }

    assert _observer_authority(source, observer, behavior_ir) == "source_relation_chain"


def test_relation_chain_without_source_refs_is_not_authority() -> None:
    from ai_test_asset_center.runtime_binding_graph import _observer_authority

    source = {"id": "confirm-order", "method": "POST", "path": "/api/orders/{id}/confirm"}
    observer = {"id": "read-order", "method": "GET", "path": "/api/orders/{id}"}
    behavior_ir = {
        "operations": [source, observer],
        "entities": [{"id": "entity-order"}],
        "relations": [
            {
                "relation_type": "produces",
                "from_ref": "confirm-order",
                "to_ref": "entity-order",
                "status": "accepted",
            },
            {
                "relation_type": "observes",
                "from_ref": "read-order",
                "to_ref": "entity-order",
                "status": "accepted",
            },
        ],
    }

    assert _observer_authority(source, observer, behavior_ir) == ""


def test_create_item_observer_requires_frozen_identity_output_contract() -> None:
    from ai_test_asset_center.runtime_binding_graph import _observer_authority

    observer = {"id": "read-item", "method": "GET", "path": "/api/items/{itemId}"}
    source_without = {"id": "create-item", "method": "POST", "path": "/api/items"}
    source_with = {
        **source_without,
        "identity_output_binding": {
            "schema_version": "qualibug.identity-output-binding.v1",
            "status": "FROZEN",
            "source_path": "id",
            "source_identity_field": "id",
            "alias_targets": ["itemId"],
            "consumer_targets": ["itemId"],
        },
        "identity_binding_aliases": ["itemId"],
    }

    empty_ir = {"operations": [], "relations": [], "entities": []}
    assert _observer_authority(source_without, observer, empty_ir) == ""
    assert _observer_authority(source_with, observer, empty_ir) == "frozen_identity_output"


def test_requesting_one_observer_does_not_truncate_two_authoritative_candidates(monkeypatch) -> None:
    import ai_test_asset_center.runtime_binding_graph as graph

    source = {"id": "patch-order", "method": "PATCH", "path": "/api/orders/{id}"}
    read_a = {"id": "read-a", "method": "GET", "path": "/api/orders/{id}"}
    read_b = {"id": "read-b", "method": "HEAD", "path": "/api/orders/{id}"}
    behavior_ir = {
        "operations": [source, read_a, read_b],
        "relations": [],
        "entities": [],
    }
    monkeypatch.setattr(
        graph,
        "_candidate_effect_observers",
        lambda *args, **kwargs: [
            {"operation_ref": "read-a", "method": "GET", "path": "/api/orders/{id}"},
            {"operation_ref": "read-b", "method": "HEAD", "path": "/api/orders/{id}"},
        ],
    )

    assert graph.declared_effect_observers(
        source,
        behavior_ir=behavior_ir,
        max_candidates=1,
    ) == []
