from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from ai_test_asset_center.authority_manifest import (
    AUTHORITY_MODE_ENV,
    AUTHORITY_SLOTS,
    AuthorityManifestError,
    AuthorityStartupError,
    ProductMainlineAuthority,
    build_product_mainline_authority,
    load_authority_manifest,
    load_target,
    resolve_authority_mode,
    validate_manifest_contract,
    validate_product_mainline_authority,
    validate_resolved_authorities_for_startup,
)


EXPECTED_AUTHORITY_SLOTS = (
    "behavior_ir",
    "obligation_source",
    "planner",
    "experiment_compiler",
    "executor",
    "evidence_pipeline",
    "oracle",
    "finding_pipeline",
    "delivery_gate",
    "release_decision",
)


def test_authority_manifest_matches_architecture_spec_and_resolves_all_product_slots() -> None:
    manifest = load_authority_manifest()

    assert AUTHORITY_SLOTS == EXPECTED_AUTHORITY_SLOTS
    assert tuple(manifest["slots"]) == EXPECTED_AUTHORITY_SLOTS
    assert len(manifest["slots"]) == 10

    summary = validate_manifest_contract(manifest)
    assert summary["resolved"] == list(EXPECTED_AUTHORITY_SLOTS)
    assert summary["unresolved"] == []

    authority = build_product_mainline_authority(manifest)
    assert isinstance(authority, ProductMainlineAuthority)
    assert authority.unresolved() == ()
    for entry in authority.entries():
        assert entry.status == "RESOLVED"
        assert entry.target
        assert entry.production_callers
        assert entry.evidence
        assert load_target(entry.target) is not None, entry.capability


def test_manifest_proves_each_resolved_target_is_used_by_declared_production_caller() -> None:
    summary = validate_manifest_contract(
        load_authority_manifest(),
        verify_production_usage=True,
    )

    assert summary["resolved"] == list(EXPECTED_AUTHORITY_SLOTS)


def test_manifest_rejects_loadable_target_not_used_by_declared_production_caller() -> None:
    manifest = deepcopy(load_authority_manifest())
    manifest["slots"]["executor"] = {
        **manifest["slots"]["executor"],
        "target": "builtins:len",
        "usage": "CALL",
    }

    with pytest.raises(
        AuthorityManifestError,
        match="authority_not_used_by_production_caller:executor",
    ):
        validate_manifest_contract(manifest)


def test_product_mode_is_default_and_requires_all_ten_authorities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTHORITY_MODE_ENV, raising=False)
    assert resolve_authority_mode() == "PRODUCT"

    report = validate_product_mainline_authority(
        load_authority_manifest(),
        verify_production_usage=False,
    )
    assert report["authority_mode"] == "PRODUCT"
    assert report["strict_product"] is True
    assert report["required_count"] == 10
    assert report["resolved_count"] == 10
    assert report["unresolved"] == []
    assert tuple(report["slots"]) == EXPECTED_AUTHORITY_SLOTS
    assert all(
        report["slots"][slot]["status"] == "RESOLVED"
        for slot in EXPECTED_AUTHORITY_SLOTS
    )


def test_product_mode_fails_closed_but_explicit_compatibility_exposes_unresolved() -> None:
    manifest = deepcopy(load_authority_manifest())
    manifest["slots"]["release_decision"] = {
        "status": "UNRESOLVED",
        "target": None,
        "production_callers": [],
        "usage": "CALL",
        "evidence": "",
    }

    with pytest.raises(
        AuthorityStartupError,
        match="missing_required_product_authority:release_decision",
    ):
        validate_product_mainline_authority(manifest, mode="PRODUCT")

    compatibility = validate_product_mainline_authority(
        manifest,
        mode="COMPATIBILITY",
    )
    assert compatibility["authority_mode"] == "COMPATIBILITY"
    assert compatibility["strict_product"] is False
    assert compatibility["unresolved"] == ["release_decision"]
    assert compatibility["slots"]["release_decision"]["status"] == "UNRESOLVED"


def test_authority_modes_are_explicit_and_invalid_mode_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTHORITY_MODE_ENV, "compatibility")
    assert resolve_authority_mode() == "COMPATIBILITY"
    assert resolve_authority_mode("BENCHMARK") == "BENCHMARK"
    assert resolve_authority_mode("TEST") == "TEST"

    with pytest.raises(AuthorityStartupError, match="authority_mode_invalid"):
        resolve_authority_mode("silent_fallback")


def test_product_manifest_does_not_bind_mock_benchmark_or_compatibility_gate() -> None:
    manifest = load_authority_manifest()
    targets = {
        slot: str(row.get("target") or "")
        for slot, row in manifest["slots"].items()
    }

    assert "customer_delivery_gate_v2:" in targets["delivery_gate"]
    assert ".release_gate:evaluate_release_gate" in targets["release_decision"]
    for target in targets.values():
        lowered = target.lower()
        assert "benchmark_evaluator" not in lowered
        assert "benchmark_runtime" not in lowered
        assert "._private_eval" not in lowered
        assert "core.engine:" not in lowered
        assert "backend.main:" not in lowered
        assert "mockengine" not in lowered


def test_resolved_unloadable_target_fails_contract_and_startup() -> None:
    manifest = deepcopy(load_authority_manifest())
    manifest["slots"]["release_decision"] = {
        **manifest["slots"]["release_decision"],
        "target": "does.not.exist:authority",
    }

    with pytest.raises(
        AuthorityManifestError,
        match="resolved_authority_target_unloadable:release_decision",
    ):
        validate_manifest_contract(manifest, verify_production_usage=False)

    with pytest.raises(AuthorityManifestError):
        validate_resolved_authorities_for_startup(manifest, mode="PRODUCT")


def test_private_pilot_startup_validates_and_audits_authority_before_server_bind() -> None:
    package_root = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
    entrypoint_source = (package_root / "private_pilot_entrypoint.py").read_text(
        encoding="utf-8"
    )
    policy_source = (package_root / "policy_wiring.py").read_text(encoding="utf-8")

    bind_call = "mainline_binding = bind_product_installed_mainline_authority()"
    server_bind = "server = _service.run_private_pilot_service(root=private_root)"
    validation_call = "authority_report = validate_resolved_authorities_for_startup()"

    assert bind_call in entrypoint_source
    assert server_bind in entrypoint_source
    assert entrypoint_source.index(bind_call) < entrypoint_source.index(server_bind)
    assert validation_call in policy_source
    assert '"authority_mode": authority_mode' in policy_source
    assert '"authority_manifest": authority_report' in policy_source
    assert "non_product_authority_mode_for_product_entrypoint" in policy_source


def test_product_mode_rejects_legacy_runner_before_registry_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center.policy_wiring import (
        bind_product_installed_mainline_authority,
    )

    monkeypatch.setenv(AUTHORITY_MODE_ENV, "PRODUCT")
    monkeypatch.setenv("QUALIBUG_MAINLINE_AUTHORITY", "legacy_champion")

    with pytest.raises(
        RuntimeError,
        match="legacy_mainline_forbidden_in_product_mode:legacy_champion",
    ):
        bind_product_installed_mainline_authority()


def test_authority_callers_are_reachable_from_private_pilot_scan_mainline() -> None:
    package_root = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
    handler_source = (package_root / "private_pilot_scan_handlers.py").read_text(
        encoding="utf-8"
    )
    scan_source = (package_root / "__main__.py").read_text(encoding="utf-8")
    v12_source = (package_root / "v12_pipeline.py").read_text(encoding="utf-8")
    coordinator_source = (package_root / "discovery_mainline.py").read_text(
        encoding="utf-8"
    )

    assert "from .__main__ import scan" in handler_source
    assert "result = scan(" in handler_source
    assert "result = _scan_impl(" in scan_source
    assert "from .v12_pipeline import run_v12_pipeline" in scan_source
    assert "v12 = run_v12_pipeline(" in scan_source
    assert (
        "from .discovery_runtime import build_discovery_plan, run_experiment_candidate"
        in v12_source
    )
    assert "result = run_discovery_mainline(" in v12_source
    assert "build_plan=build_discovery_plan" in v12_source
    assert "experiment_runner=run_experiment_candidate" in v12_source
    assert "plan = build_plan(inputs, campaign)" in coordinator_source
    assert "result = runner(inputs, campaign, plan)" in coordinator_source
