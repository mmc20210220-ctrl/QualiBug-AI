from __future__ import annotations

from ai_test_asset_center.coverage_unit_registry import (
    build_coverage_units,
    derive_canonical_obligation_key,
)


def _ir() -> dict:
    return {
        "operations": [
            {
                "id": "scm_read",
                "method": "GET",
                "path": "/api/orders/{id}",
                "_service_name": "scm_trade",
            },
            {
                "id": "wms_read",
                "method": "GET",
                "path": "/api/orders/{id}",
                "_service_name": "wms_ops",
            },
            {
                "id": "scm_update",
                "method": "PATCH",
                "path": "/api/orders/{id}",
                "_service_name": "scm_trade",
            },
            {
                "id": "scm_cancel",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "_service_name": "scm_trade",
            },
            {
                "id": "unscoped",
                "method": "GET",
                "path": "/api/public/{id}",
            },
        ]
    }


def _obligation(
    obligation_id: str,
    operations: list[str],
    *,
    actor: str = "actor_a",
) -> dict:
    return {
        "obligation_id": obligation_id,
        "risk_family": "authorization",
        "required_operations": operations,
        "required_actors": [actor],
        "required_observers": ["http_response", "actor_identity"],
        "cleanup_requirement": {"required": False},
        "property": {
            "template": "permitted_operation_invocation",
            "operation_ref": operations[0],
            "actor_ref": actor,
        },
    }


def test_same_method_path_on_different_services_never_collapses() -> None:
    left = derive_canonical_obligation_key(
        _obligation("obl_scm", ["scm_read"]), behavior_ir=_ir()
    )
    right = derive_canonical_obligation_key(
        _obligation("obl_wms", ["wms_read"]), behavior_ir=_ir()
    )

    assert left["normalized_operation"] == right["normalized_operation"]
    assert left["service_identity"] == "scm_trade"
    assert right["service_identity"] == "wms_ops"
    assert left["coverage_unit_id"] != right["coverage_unit_id"]


def test_build_units_keeps_same_path_different_services_separate() -> None:
    pack = build_coverage_units(
        [
            _obligation("obl_scm", ["scm_read"]),
            _obligation("obl_wms", ["wms_read"]),
        ],
        behavior_ir=_ir(),
    )

    assert pack["obligation_count"] == 2
    assert pack["unit_count"] == 2
    assert pack["collapsed_variant_count"] == 0


def test_actor_variants_on_same_service_surface_still_collapse() -> None:
    left = derive_canonical_obligation_key(
        _obligation("obl_a", ["scm_read"], actor="actor_a"),
        behavior_ir=_ir(),
    )
    right = derive_canonical_obligation_key(
        _obligation("obl_b", ["scm_read"], actor="actor_b"),
        behavior_ir=_ir(),
    )

    assert left["service_identity"] == right["service_identity"] == "scm_trade"
    assert left["coverage_unit_id"] == right["coverage_unit_id"]


def test_ordered_multi_operation_paths_do_not_collapse_on_first_operation() -> None:
    left = derive_canonical_obligation_key(
        _obligation("obl_update", ["scm_read", "scm_update"]),
        behavior_ir=_ir(),
    )
    right = derive_canonical_obligation_key(
        _obligation("obl_cancel", ["scm_read", "scm_cancel"]),
        behavior_ir=_ir(),
    )

    assert left["normalized_operation"] == right["normalized_operation"]
    assert left["ordered_operation_sequence_identity"]
    assert right["ordered_operation_sequence_identity"]
    assert left["ordered_operation_sequence_identity"] != right[
        "ordered_operation_sequence_identity"
    ]
    assert left["coverage_unit_id"] != right["coverage_unit_id"]


def test_single_unscoped_operation_preserves_legacy_identity_shape() -> None:
    key = derive_canonical_obligation_key(
        _obligation("obl_public", ["unscoped"]), behavior_ir=_ir()
    )

    assert key["service_identity"] == ""
    assert key["ordered_operation_sequence_identity"] == ""
    assert "service:" not in key["canonical_obligation_key"]
    assert "|path:" not in key["canonical_obligation_key"]
