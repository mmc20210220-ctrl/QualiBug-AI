from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from ai_test_asset_center.authority_manifest import (
    AUTHORITY_SLOTS,
    AuthorityManifestError,
    AuthorityStartupError,
    load_authority_manifest,
    load_target,
    validate_manifest_contract,
    validate_resolved_authorities_for_startup,
)


def test_authority_manifest_has_exact_ten_slots_and_valid_resolved_targets() -> None:
    manifest = load_authority_manifest()

    assert set(manifest["slots"]) == set(AUTHORITY_SLOTS)
    assert len(manifest["slots"]) == 10

    summary = validate_manifest_contract(manifest)
    assert summary["resolved"] == ["behavior_ir"]
    assert set(summary["unresolved"]) == set(AUTHORITY_SLOTS) - {"behavior_ir"}

    for slot, row in manifest["slots"].items():
        if row["status"] == "RESOLVED":
            assert load_target(row["target"]) is not None, slot


def test_manifest_rejects_resolved_authority_not_used_by_declared_production_caller() -> None:
    manifest = deepcopy(load_authority_manifest())
    manifest["slots"]["syntactic_normalize"] = {
        "status": "RESOLVED",
        "target": "builtins:len",
        "production_callers": [
            "ai_test_asset_center.discovery_runtime_planning:build_discovery_plan"
        ],
    }

    with pytest.raises(
        AuthorityManifestError,
        match="authority_not_used_by_production_caller:syntactic_normalize",
    ):
        validate_manifest_contract(manifest)


def test_unresolved_slot_never_blocks_startup_even_if_non_contract_payload_has_target() -> None:
    manifest = deepcopy(load_authority_manifest())
    manifest["slots"]["semantic_normalize"]["target"] = "does.not.exist:authority"

    summary = validate_resolved_authorities_for_startup(manifest)

    assert "semantic_normalize" in summary["unresolved"]


def test_resolved_unloadable_target_fails_startup() -> None:
    manifest = deepcopy(load_authority_manifest())
    manifest["slots"]["semantic_normalize"] = {
        "status": "RESOLVED",
        "target": "does.not.exist:authority",
        "production_callers": ["builtins:len"],
    }

    with pytest.raises(
        AuthorityStartupError,
        match="resolved_authority_unloadable:semantic_normalize",
    ):
        validate_resolved_authorities_for_startup(manifest)


def test_private_pilot_startup_chain_invokes_authority_validation_before_server_bind() -> None:
    package_root = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
    entrypoint_source = (package_root / "private_pilot_entrypoint.py").read_text(
        encoding="utf-8"
    )
    policy_source = (package_root / "policy_wiring.py").read_text(encoding="utf-8")

    bind_call = "mainline_binding = bind_product_installed_mainline_authority()"
    server_bind = "server = _service.run_private_pilot_service(root=private_root)"
    validation_call = "validate_resolved_authorities_for_startup()"

    assert bind_call in entrypoint_source
    assert server_bind in entrypoint_source
    assert entrypoint_source.index(bind_call) < entrypoint_source.index(server_bind)
    assert validation_call in policy_source
