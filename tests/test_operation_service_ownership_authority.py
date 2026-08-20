from __future__ import annotations

from types import SimpleNamespace

from ai_test_asset_center.operation_service_ownership_authority import (
    install_operation_service_ownership_authority,
    source_backed_operation_service_name,
)


def test_exact_source_service_metadata_beats_filename_convention() -> None:
    operation = {
        "source_refs": [{"source_id": "src-orders"}],
    }
    data = {
        "sources": [
            {
                "source_id": "src-orders",
                "filename": "orders-openapi.yaml",
                "service_name": "orders-api",
            }
        ]
    }
    assert source_backed_operation_service_name(operation, data) == "orders-api"


def test_exact_interface_system_ref_resolves_arbitrary_source_filename() -> None:
    operation = {"source_ids": ["src-inventory"]}
    data = {
        "sources": [
            {"source_id": "src-inventory", "filename": "inventory-contract-v7.json"}
        ],
        "interfaces": [
            {
                "source_ids": ["src-inventory"],
                "system_ref": "inventory-runtime",
                "source_locators": ["inventory-contract-v7.json#/paths/~1items"],
            }
        ],
    }
    assert (
        source_backed_operation_service_name(operation, data)
        == "inventory-runtime"
    )


def test_conflicting_exact_source_service_metadata_fails_closed() -> None:
    operation = {"source_ids": ["src-a", "src-b"]}
    data = {
        "sources": [
            {"source_id": "src-a", "service_name": "svc-a"},
            {"source_id": "src-b", "service_name": "svc-b"},
        ]
    }
    assert source_backed_operation_service_name(operation, data) == ""


def test_legacy_service_filename_remains_compatibility_only() -> None:
    operation = {"source_refs": [{"source_id": "src-legacy"}]}
    data = {
        "sources": [
            {"source_id": "src-legacy", "filename": "legacy_service.json"}
        ]
    }
    assert source_backed_operation_service_name(operation, data) == "legacy"


def test_arbitrary_filename_without_service_metadata_is_not_guessed() -> None:
    operation = {"source_refs": [{"source_id": "src-unknown"}]}
    data = {
        "sources": [
            {"source_id": "src-unknown", "filename": "orders-openapi.yaml"}
        ]
    }
    assert source_backed_operation_service_name(operation, data) == ""


def test_direct_operation_service_metadata_is_authoritative() -> None:
    assert (
        source_backed_operation_service_name(
            {"service": "billing", "source_ids": ["src-x"]},
            {"sources": [{"source_id": "src-x", "service_name": "other"}]},
        )
        == "billing"
    )


def test_installer_replaces_core_service_owner_resolver() -> None:
    core = SimpleNamespace(_service_name_from_source_refs=lambda operation, data: "old")
    install_operation_service_ownership_authority(core)
    assert core._service_name_from_source_refs is source_backed_operation_service_name
