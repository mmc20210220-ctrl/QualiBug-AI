from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ai_test_asset_center import pipeline_runtime
from ai_test_asset_center import private_pilot_scan_context_contract as scan_context
from ai_test_asset_center import ui_upload_fixture_runtime_binding as runtime_binding
from ai_test_asset_center.ui_upload_fixture_registry import (
    approve_upload_fixture,
    approved_upload_fixture_binding,
    list_upload_fixtures,
    materialize_upload_fixture_bindings,
    register_upload_fixture,
    revoke_upload_fixture,
)


_ACTOR = {"name": "qa-owner", "role": "qa_lead"}
_PROJECT = "fixture-project"


def _source(root: Path, name: str = "sample.csv", data: bytes = b"id,name\n1,Alice\n") -> Path:
    path = root / "platform_inputs" / _PROJECT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _approved(root: Path, name: str = "sample.csv", data: bytes = b"id,name\n1,Alice\n") -> dict[str, Any]:
    source = _source(root, name, data)
    registered = register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="customer-import",
        root=root,
        actor=_ACTOR,
    )
    return approve_upload_fixture(
        _PROJECT,
        fixture_id=registered["fixture"]["fixture_id"],
        root=root,
        actor=_ACTOR,
    )["fixture"]


def test_register_approve_and_resolve_binding_without_raw_source_path(tmp_path: Path) -> None:
    source = _source(tmp_path)
    registered = register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="customer-import",
        root=tmp_path,
        actor=_ACTOR,
    )

    assert registered["status"] == "REGISTERED"
    record = registered["fixture"]
    assert record["authority"] == "source_registered"
    assert record["raw_file_bytes_embedded_in_registry"] is False
    assert record["raw_source_path_embedded_in_registry"] is False
    assert "file_path" not in record
    assert "source_filename" not in record

    approved = approve_upload_fixture(
        _PROJECT,
        fixture_id=record["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )
    approved_record = approved["fixture"]
    binding = approved_upload_fixture_binding(
        _PROJECT,
        approved_record["fixture_id"],
        root=tmp_path,
    )

    assert approved["status"] == "APPROVED"
    assert binding["binding_ref"] == approved_record["binding_ref"]
    assert binding["file_path"].startswith(
        f"platform_workspace/{_PROJECT}/ui_upload_fixtures/"
    )
    assert not Path(binding["file_path"]).is_absolute()
    assert binding["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert binding["raw_file_content_included"] is False

    registry_text = (
        tmp_path
        / "platform_workspace"
        / _PROJECT
        / "ui_upload_fixture_registry.json"
    ).read_text(encoding="utf-8")
    assert str(source) not in registry_text
    assert source.name not in registry_text


def test_duplicate_registration_and_approval_are_idempotent(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="customer-import",
        root=tmp_path,
        actor=_ACTOR,
    )
    duplicate = register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="customer-import",
        root=tmp_path,
        actor=_ACTOR,
    )
    assert duplicate["status"] == "DUPLICATE_ACTIVE"
    assert duplicate["fixture"]["fixture_id"] == first["fixture"]["fixture_id"]

    approved = approve_upload_fixture(
        _PROJECT,
        fixture_id=first["fixture"]["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )
    approved_duplicate = approve_upload_fixture(
        _PROJECT,
        fixture_id=first["fixture"]["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )
    assert approved_duplicate["status"] == "DUPLICATE_ACTIVE"
    assert approved_duplicate["fixture"]["fixture_id"] == approved["fixture"]["fixture_id"]


def test_source_revocation_cascades_to_approved_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    registered = register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="customer-import",
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]
    approved = approve_upload_fixture(
        _PROJECT,
        fixture_id=registered["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]

    revoked = revoke_upload_fixture(
        _PROJECT,
        fixture_id=registered["fixture_id"],
        reason="source contract replaced",
        root=tmp_path,
        actor=_ACTOR,
    )

    assert revoked["status"] == "REVOKED"
    assert len(revoked["revoked_records"]) == 2
    with pytest.raises(KeyError, match="active_approved_upload_fixture_not_found"):
        approved_upload_fixture_binding(
            _PROJECT,
            approved["binding_ref"],
            root=tmp_path,
        )
    historical = list_upload_fixtures(
        _PROJECT,
        root=tmp_path,
        include_revoked=True,
    )
    assert historical["summary"]["revoked_count"] == 2
    assert all(row["status"] == "revoked" for row in historical["fixtures"])


def test_binding_fails_closed_on_approved_byte_drift(tmp_path: Path) -> None:
    approved = _approved(tmp_path)
    binding = approved_upload_fixture_binding(
        _PROJECT,
        approved["binding_ref"],
        root=tmp_path,
    )
    path = tmp_path / binding["file_path"]
    path.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="approved_upload_fixture_size_drift|approved_upload_fixture_hash_drift"):
        approved_upload_fixture_binding(
            _PROJECT,
            approved["binding_ref"],
            root=tmp_path,
        )


def test_registration_rejects_outside_project_and_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="outside_project_inputs"):
        register_upload_fixture(
            _PROJECT,
            file_path=outside,
            fixture_name="outside",
            root=tmp_path,
            actor=_ACTOR,
        )

    source = _source(tmp_path)
    link = source.parent / "linked.csv"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(PermissionError, match="symlink_forbidden"):
        register_upload_fixture(
            _PROJECT,
            file_path=link,
            fixture_name="linked",
            root=tmp_path,
            actor=_ACTOR,
        )


def test_materialized_bindings_use_registry_binding_refs(tmp_path: Path) -> None:
    first = _approved(tmp_path, "one.csv", b"id\n1\n")
    second = _approved(tmp_path, "two.json", b'{"id":2}')

    bindings = materialize_upload_fixture_bindings(
        _PROJECT,
        [first["fixture_id"], second["binding_ref"]],
        root=tmp_path,
    )

    assert set(bindings) == {first["binding_ref"], second["binding_ref"]}
    assert all(row["approved"] is True for row in bindings.values())
    assert all(not Path(row["file_path"]).is_absolute() for row in bindings.values())


def test_runtime_hydration_rejects_arbitrary_file_binding(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="registry_identity_missing"):
        runtime_binding._hydrate_bindings(
            _PROJECT,
            tmp_path,
            {
                "ui_file_bindings": {
                    "manual": {
                        "approved": True,
                        "file_path": "platform_inputs/fixture-project/sample.csv",
                        "sha256": "a" * 64,
                    }
                }
            },
        )


def test_runtime_hydration_threads_approved_binding_to_campaign_and_runtime(tmp_path: Path) -> None:
    approved = _approved(tmp_path)
    runtime_binding.install_ui_upload_fixture_runtime_binding()
    prepared = runtime_binding._hydrate_bindings(
        _PROJECT,
        tmp_path,
        {"ui_upload_fixture_ids": [approved["fixture_id"]]},
    )
    context = scan_context.build_campaign_context_from_scan_body(prepared)

    assert set(context["ui_file_bindings"]) == {approved["binding_ref"]}
    assert context["ui_upload_fixture_binding_summary"]["registry_derived"] is True

    source_text = "source contract"
    context.update({
        "source_manifest": {
            "source_id": "source-1",
            "source_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        },
        "scope_id": "scope-1",
        "environment_ref": "sandbox-1",
        "environment_type": "sandbox",
        "execution_mode": "approved_sandbox_write",
    })
    contract = pipeline_runtime._runtime_contract(
        context,
        "https://example.test",
        source_text,
    )

    assert contract["status"] == "approved"
    assert set(contract["ui_file_bindings"]) == {approved["binding_ref"]}
    assert contract["ui_file_bindings"][approved["binding_ref"]]["sha256"] == approved["sha256"]
    assert contract["ui_upload_fixture_binding_summary"]["absolute_file_paths_included"] is False


def test_registry_file_is_fail_closed_when_corrupt(tmp_path: Path) -> None:
    path = (
        tmp_path
        / "platform_workspace"
        / _PROJECT
        / "ui_upload_fixture_registry.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="registry_corrupt"):
        list_upload_fixtures(_PROJECT, root=tmp_path)
