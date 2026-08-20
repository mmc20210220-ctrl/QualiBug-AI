from __future__ import annotations

from ai_test_asset_center.service_ref_canonicalization import (
    canonical_service_ref,
    canonicalize_behavior_ir_service_refs,
    unique_service_by_url,
)


def _topology() -> dict:
    return {
        "orders": {"approved_base_url": "http://127.0.0.1:39117/api"},
        "inventory": {"approved_base_url": "http://127.0.0.1:48763"},
    }


def test_exact_url_service_ref_maps_to_unique_topology_owner() -> None:
    topology = _topology()
    assert (
        canonical_service_ref("http://127.0.0.1:39117/api/", topology)
        == "orders"
    )


def test_declared_service_key_is_preserved() -> None:
    topology = _topology()
    assert canonical_service_ref("inventory", topology) == "inventory"


def test_unknown_url_is_not_guessed_by_host_or_port_similarity() -> None:
    topology = _topology()
    raw = "http://127.0.0.1:39117/other"
    assert canonical_service_ref(raw, topology) == raw


def test_ambiguous_exact_url_has_no_canonical_owner() -> None:
    topology = {
        "one": {"approved_base_url": "http://127.0.0.1:49001"},
        "two": {"approved_base_url": "http://127.0.0.1:49001/"},
    }
    assert unique_service_by_url(topology) == {}
    raw = "http://127.0.0.1:49001"
    assert canonical_service_ref(raw, topology) == raw


def test_behavior_ir_projection_is_routing_only_and_does_not_mutate_source() -> None:
    topology = _topology()
    original = {
        "operations": [
            {
                "id": "op-orders",
                "service": "http://127.0.0.1:39117/api/",
                "_service_name": "http://127.0.0.1:39117/api/",
            },
            {"id": "op-inventory", "service": "inventory"},
        ]
    }
    projected = canonicalize_behavior_ir_service_refs(original, topology)
    assert original["operations"][0]["service"] == "http://127.0.0.1:39117/api/"
    routed = projected["operations"][0]
    assert routed["service"] == "orders"
    assert routed["_service_name"] == "orders"
    assert routed["routing_original_service_ref"] == "http://127.0.0.1:39117/api/"
    assert routed["routing_service_ref_authority"] == "exact_topology_url_match"
    assert projected["operations"][1]["service"] == "inventory"
