from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import private_pilot_continuous_handlers as continuous_handlers
from ai_test_asset_center import private_pilot_scan_context_contract as scan_context
from ai_test_asset_center import ui_upload_fixture_registry as registry
from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
    install_upload_fixture_registry_integrity,
)
from ai_test_asset_center.ui_upload_fixture_runtime_binding import (
    install_ui_upload_fixture_runtime_binding,
)


_PROJECT = "continuous-fixture-project"
_ACTOR = {"name": "qa-owner", "role": "qa_lead"}


def test_continuous_prepare_hydrates_approved_fixture_binding(tmp_path: Path) -> None:
    install_upload_fixture_registry_integrity()
    install_ui_upload_fixture_runtime_binding()
    source = tmp_path / "platform_inputs" / _PROJECT / "fixture.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("id\n1\n", encoding="utf-8")
    candidate = registry.register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="continuous-fixture",
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]
    approved = registry.approve_upload_fixture(
        _PROJECT,
        fixture_id=candidate["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]

    prepared = scan_context.prepare_scan_body_for_campaign(
        _PROJECT,
        tmp_path,
        {"ui_upload_fixture_ids": [approved["binding_ref"]]},
    )
    context = scan_context.build_campaign_context_from_scan_body(prepared)

    assert set(prepared["ui_file_bindings"]) == {approved["binding_ref"]}
    assert set(context["ui_file_bindings"]) == {approved["binding_ref"]}
    assert context["ui_upload_fixture_binding_summary"]["registry_derived"] is True
    assert (
        continuous_handlers.prepare_scan_body_for_campaign
        is scan_context.prepare_scan_body_for_campaign
    )
