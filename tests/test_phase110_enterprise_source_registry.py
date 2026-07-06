from __future__ import annotations

from ai_test_asset_center.enterprise_source_registry import (
    SourceRegistryError,
    list_source_assets,
    load_source_content,
    register_source_asset,
    resolve_source_manifest,
    verify_source_manifest,
)


def test_registers_immutable_source_and_resolves_exact_content(tmp_path):
    manifest = register_source_asset(
        "enterprise-project",
        "api-contract",
        '{"openapi":"3.0.0"}',
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa_lead"},
        filename="openapi.json",
    )

    resolved = resolve_source_manifest("enterprise-project", '{"openapi":"3.0.0"}', root=tmp_path)
    verified = verify_source_manifest("enterprise-project", manifest, '{"openapi":"3.0.0"}', root=tmp_path)

    assert manifest["source_id"] == "api-contract"
    assert manifest["source_origin"] == "registered_source_registry"
    assert resolved == manifest
    assert verified["valid"] is True
    assert load_source_content("enterprise-project", manifest["source_hash"], root=tmp_path) == '{"openapi":"3.0.0"}'


def test_same_content_is_idempotent_but_changed_content_creates_a_version(tmp_path):
    first = register_source_asset("enterprise-project", "api-contract", "v1", source_type="openapi", root=tmp_path)
    replay = register_source_asset("enterprise-project", "api-contract", "v1", source_type="openapi", root=tmp_path)
    changed = register_source_asset("enterprise-project", "api-contract", "v2", source_type="openapi", root=tmp_path)
    assets = list_source_assets("enterprise-project", root=tmp_path)

    assert replay["source_hash"] == first["source_hash"]
    assert replay["source_version_id"] == first["source_version_id"]
    assert changed["source_hash"] != first["source_hash"]
    assert assets == [{
        "source_id": "api-contract",
        "source_type": "openapi",
        "latest_source_hash": changed["source_hash"],
        "latest_version_id": changed["source_version_id"],
        "version_count": 2,
        "updated_at_utc": assets[0]["updated_at_utc"],
    }]


def test_manifest_rejects_unregistered_or_modified_content(tmp_path):
    manifest = register_source_asset("enterprise-project", "api-contract", "v1", source_type="openapi", root=tmp_path)

    modified = verify_source_manifest("enterprise-project", manifest, "v2", root=tmp_path)
    unregistered = verify_source_manifest(
        "enterprise-project",
        {"source_id": "other", "source_hash": manifest["source_hash"]},
        "v1",
        root=tmp_path,
    )

    assert modified == {"valid": False, "code": "SOURCE_HASH_MISMATCH"}
    assert unregistered == {"valid": False, "code": "SOURCE_NOT_REGISTERED"}


def test_registry_rejects_empty_or_unsafe_identifiers(tmp_path):
    try:
        register_source_asset("enterprise-project", "", "v1", source_type="openapi", root=tmp_path)
    except SourceRegistryError as exc:
        assert str(exc) == "source_id is required"
    else:
        raise AssertionError("expected source id validation failure")
