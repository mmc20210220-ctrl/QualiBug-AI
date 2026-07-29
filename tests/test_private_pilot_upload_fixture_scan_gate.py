from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_scan_handlers as scan_handlers
from ai_test_asset_center import ui_upload_fixture_registry as registry
from ai_test_asset_center.private_pilot_upload_fixture_scan_gate import (
    install_upload_fixture_scan_gate,
)
from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
    install_upload_fixture_registry_integrity,
)
from ai_test_asset_center.ui_upload_fixture_runtime_binding import (
    install_ui_upload_fixture_runtime_binding,
)


_PROJECT = "fixture-scan-gate"
_ACTOR = {"name": "qa", "role": "qa_lead"}


class _Handler:
    def __init__(self) -> None:
        self.responses: list[tuple[int, dict[str, Any]]] = []

    def _json(
        self,
        payload: dict[str, Any],
        status: int = 200,
        **_kwargs: Any,
    ) -> None:
        self.responses.append((status, payload))


def _install() -> None:
    install_upload_fixture_registry_integrity()
    install_ui_upload_fixture_runtime_binding()
    install_upload_fixture_scan_gate()


def _approved_binding(tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "platform_inputs" / _PROJECT / "inbox" / "fixture.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("fixture-scan-gate", encoding="utf-8")
    registered = registry.register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="fixture",
        root=tmp_path,
        actor=_ACTOR,
    )
    approved = registry.approve_upload_fixture(
        _PROJECT,
        fixture_id=registered["fixture"]["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )
    return registry.approved_upload_fixture_binding(
        _PROJECT,
        approved["fixture"]["binding_ref"],
        root=tmp_path,
    )


def test_live_private_handler_resolves_scan_gate_from_mixin() -> None:
    _install()
    from ai_test_asset_center.private_pilot_service import PrivatePilotHandler

    assert (
        PrivatePilotHandler._handle_v12_scan
        is scan_handlers.ScanHandlersMixin._handle_v12_scan
    )
    assert getattr(
        scan_handlers.ScanHandlersMixin,
        "_qualibug_upload_fixture_scan_gate_installed",
        False,
    ) is True


def test_scan_gate_returns_conflict_for_missing_or_revoked_binding(
    tmp_path: Path,
) -> None:
    _install()
    handler = _Handler()

    scan_handlers.ScanHandlersMixin._handle_v12_scan(
        handler,
        _PROJECT,
        tmp_path,
        _ACTOR,
        {"ui_upload_fixture_ids": ["uifb_00000000000000000000"]},
    )

    status, payload = handler.responses[-1]
    assert status == 409
    assert payload["error"] == "UPLOAD_FIXTURE_BINDING_NOT_ACTIVE"
    assert "traceback" not in payload


def test_scan_gate_returns_bad_request_for_malformed_binding_payload(
    tmp_path: Path,
) -> None:
    _install()
    handler = _Handler()

    scan_handlers.ScanHandlersMixin._handle_v12_scan(
        handler,
        _PROJECT,
        tmp_path,
        _ACTOR,
        {"ui_upload_fixture_ids": "not-a-list"},
    )

    status, payload = handler.responses[-1]
    assert status == 400
    assert payload["error"] == "UPLOAD_FIXTURE_BINDING_BAD_REQUEST"
    assert "traceback" not in payload


def test_scan_gate_returns_integrity_conflict_for_file_drift(
    tmp_path: Path,
) -> None:
    _install()
    binding = _approved_binding(tmp_path)
    approved_path = tmp_path / str(binding["file_path"])
    approved_path.write_text("drifted-after-approval", encoding="utf-8")
    handler = _Handler()

    scan_handlers.ScanHandlersMixin._handle_v12_scan(
        handler,
        _PROJECT,
        tmp_path,
        _ACTOR,
        {"ui_upload_fixture_ids": [binding["binding_ref"]]},
    )

    status, payload = handler.responses[-1]
    assert status == 409
    assert payload["error"] == "UPLOAD_FIXTURE_BINDING_INTEGRITY_FAILED"
    assert "traceback" not in payload
