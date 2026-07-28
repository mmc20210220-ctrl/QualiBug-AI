from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import professional_ui_complex_interactions as complex_ui
from ai_test_asset_center import professional_ui_coverage_projection as coverage
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center import professional_ui_readonly as professional
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.professional_ui_complex_interaction_hardening import (
    _execute_with_final_popup_url,
    _hash_download_hardened,
    _resolve_upload_files_hardened,
)
from ai_test_asset_center.professional_ui_interaction_privacy_guard import EVIDENCE_POLICY
from ai_test_asset_center.professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    PERSISTENT_PROBE_PROPERTY,
)


_RUNTIME_CONTRACT = {
    "status": "approved",
    "approved_base_url": "https://example.test",
}


def _contract(treatment: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_id": "ui-complex-interaction",
        "operation_ref": "upload-document",
        "actor_ref": "qa-operator",
        "ui_request": {
            "request_id": "ui-complex-interaction",
            "provider": "playwright_browser_plan",
            "start_url": "/documents",
            "execution_mode": interaction.WRITE_MODE,
            "browser_plan": {
                "execution_mode": interaction.WRITE_MODE,
                "write_approved": True,
                "interaction_contract": {
                    "cleanup_strategy": "browser_compensation",
                    "equivalence": "source_declared_state_probes",
                    "equivalence_scope": EQUIVALENCE_SCOPE,
                    "target_scope": "approved_nonproduction_target",
                    "evidence_policy": EVIDENCE_POLICY,
                },
                "state_probes": [
                    {
                        "probe_id": "document-count-ui",
                        "property": "count",
                        "selector": ".document-row",
                    },
                    {
                        "probe_id": "document-count-persistent",
                        "property": PERSISTENT_PROBE_PROPERTY,
                        "method": "GET",
                        "url": "/api/documents/summary",
                        "json_pointer": "/count",
                        "expected_status_class": 2,
                        "max_response_bytes": 100_000,
                    },
                ],
                "steps": [
                    {"phase": "setup", "action": "goto", "url": "/documents"},
                    treatment,
                    {
                        "phase": "assertion",
                        "action": "expect_text",
                        "selector": "#result",
                        "text": "Ready",
                    },
                    {
                        "phase": "cleanup",
                        "action": "click",
                        "selector": "#remove-test-document",
                    },
                ],
            },
        },
    }


def test_complex_actions_enter_source_contract_and_runtime_vocabularies() -> None:
    contract = _contract({
        "phase": "treatment",
        "action": complex_ui.SET_INPUT_FILES,
        "selector": "input[type=file]",
        "file_refs": ["approved-pdf-fixture"],
    })

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-complex-source",
    )

    assert gaps == []
    assert len(contracts) == 1
    assert complex_ui.COMPLEX_INTERACTIVE_ACTIONS.issubset(
        interaction.INTERACTIVE_ACTIONS
    )
    assert complex_ui.COMPLEX_INTERACTIVE_ACTIONS.issubset(
        coverage.CATEGORY_ACTIONS["workflow_interaction"]
    )


def test_source_admission_rejects_literal_upload_path() -> None:
    contract = _contract({
        "phase": "treatment",
        "action": complex_ui.SET_INPUT_FILES,
        "selector": "input[type=file]",
        "file_refs": ["fixture"],
        "file_path": "/tmp/secret.pdf",
    })

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-complex-literal-path",
    )

    assert contracts == []
    assert any(
        "runtime_file_refs_only" in value
        for value in gaps[0]["missing_requirements"]
    )


def test_upload_validation_allows_empty_cleanup_but_not_empty_treatment() -> None:
    cleanup = {
        "phase": "cleanup",
        "action": complex_ui.SET_INPUT_FILES,
        "selector": "input[type=file]",
        "file_refs": [],
    }
    complex_ui._validate_complex_interaction(cleanup, complex_ui.SET_INPUT_FILES)

    with pytest.raises(
        interaction._browser.BrowserExecutionError,
        match="browser_upload_treatment_file_refs_missing",
    ):
        complex_ui._validate_complex_interaction(
            {
                **cleanup,
                "phase": "treatment",
            },
            complex_ui.SET_INPUT_FILES,
        )


def test_upload_binding_is_project_scoped_hashed_and_minimized(tmp_path: Path) -> None:
    project = "demo"
    fixture = tmp_path / "platform_inputs" / project / "fixtures" / "sample.pdf"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"governed upload fixture")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    token = complex_ui._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": project,
        "runtime_contract": _RUNTIME_CONTRACT,
    })
    try:
        paths, evidence = _resolve_upload_files_hardened(
            ["fixture-ref"],
            {
                "ui_file_bindings": {
                    "fixture-ref": {
                        "approved": True,
                        "path": str(fixture.relative_to(tmp_path)),
                        "sha256": digest,
                        "content_type": "application/pdf",
                    }
                }
            },
        )
    finally:
        complex_ui._RUNTIME_CONTEXT.reset(token)

    assert paths == [str(fixture.resolve())]
    assert evidence[0]["sha256"] == digest
    assert evidence[0]["size_bytes"] == len(b"governed upload fixture")
    assert evidence[0]["streaming_hash_used"] is True
    assert evidence[0]["symlink_components_allowed"] is False
    serialized = json.dumps(evidence[0])
    assert str(fixture) not in serialized
    assert "fixture-ref" not in serialized


def test_upload_binding_outside_project_scope_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    token = complex_ui._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": "demo",
        "runtime_contract": _RUNTIME_CONTRACT,
    })
    try:
        with pytest.raises(RuntimeError, match="UI_UPLOAD_FILE_OUTSIDE_PROJECT_SCOPE"):
            _resolve_upload_files_hardened(
                ["outside"],
                {
                    "ui_file_bindings": {
                        "outside": {
                            "approved": True,
                            "path": str(outside),
                            "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                        }
                    }
                },
            )
    finally:
        complex_ui._RUNTIME_CONTEXT.reset(token)


def test_upload_binding_symlink_is_rejected(tmp_path: Path) -> None:
    project = "demo"
    real = tmp_path / "platform_inputs" / project / "fixtures" / "real.txt"
    real.parent.mkdir(parents=True)
    real.write_text("real", encoding="utf-8")
    link = real.parent / "linked.txt"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    token = complex_ui._RUNTIME_CONTEXT.set({
        "root": str(tmp_path),
        "project": project,
        "runtime_contract": _RUNTIME_CONTRACT,
    })
    try:
        with pytest.raises(RuntimeError, match="UI_UPLOAD_FILE_OUTSIDE_PROJECT_SCOPE"):
            _resolve_upload_files_hardened(
                ["linked"],
                {
                    "ui_file_bindings": {
                        "linked": {
                            "approved": True,
                            "path": str(link.relative_to(tmp_path)),
                            "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
                        }
                    }
                },
            )
    finally:
        complex_ui._RUNTIME_CONTEXT.reset(token)


class _Download:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.suggested_filename = "customer-export.csv"

    def failure(self) -> None:
        return None

    def path(self) -> str:
        return str(self._path)


def test_download_observation_is_streamed_hash_only(tmp_path: Path) -> None:
    path = tmp_path / "download.tmp"
    path.write_bytes(b"id,status\n1,ready\n")

    receipt = _hash_download_hardened(_Download(path), 1_000_000)

    assert receipt["download_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["download_size_bytes"] == path.stat().st_size
    assert receipt["suggested_filename_suffix"] == ".csv"
    assert receipt["download_persisted"] is False
    assert receipt["streaming_hash_used"] is True
    assert "customer-export.csv" not in json.dumps(receipt)


class _FrameElement:
    def __init__(self, src: str, count: int = 1) -> None:
        self.src = src
        self._count = count

    def count(self) -> int:
        return self._count

    def get_attribute(self, name: str) -> str | None:
        return self.src if name == "src" else None


class _FramePage:
    url = "https://example.test/records"

    def __init__(self, src: str = "/embedded") -> None:
        self.element = _FrameElement(src)
        self.frame = object()

    def locator(self, _selector: str) -> _FrameElement:
        return self.element

    def frame_locator(self, _selector: str) -> object:
        return self.frame


def test_iframe_scope_requires_unique_approved_exact_origin() -> None:
    page = _FramePage()
    token = complex_ui._RUNTIME_CONTEXT.set({
        "runtime_contract": {
            "approved_base_url": "https://example.test",
            "approved_frame_origins": [],
        }
    })
    try:
        surface, receipt = complex_ui._frame_surface(
            page,
            {
                "frame_selector": "iframe#embedded",
                "frame_origin": "https://example.test",
            },
        )
    finally:
        complex_ui._RUNTIME_CONTEXT.reset(token)

    assert surface is page.frame
    assert receipt["frame_target_unique"] is True
    assert receipt["raw_frame_selector_included"] is False
    assert receipt["raw_frame_origin_included"] is False


def test_iframe_origin_mismatch_is_rejected() -> None:
    page = _FramePage("https://outside.test/frame")
    token = complex_ui._RUNTIME_CONTEXT.set({
        "runtime_contract": {
            "approved_base_url": "https://example.test",
            "approved_frame_origins": ["https://outside.test"],
        }
    })
    try:
        with pytest.raises(RuntimeError, match="UI_FRAME_ORIGIN_MISMATCH"):
            complex_ui._frame_surface(
                page,
                {
                    "frame_selector": "iframe#embedded",
                    "frame_origin": "https://example.test",
                },
            )
    finally:
        complex_ui._RUNTIME_CONTEXT.reset(token)


class _Popup:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.waited_url = ""

    def wait_for_url(self, url: str, **_kwargs: Any) -> None:
        self.waited_url = url

    def title(self) -> str:
        return "Governed popup"

    def close(self) -> None:
        self.closed = True


class _PopupInfo:
    def __init__(self, popup: _Popup) -> None:
        self.value = popup

    def __enter__(self) -> "_PopupInfo":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class _PopupLocator:
    def count(self) -> int:
        return 1

    def click(self, **_kwargs: Any) -> None:
        return None


class _PopupPage:
    url = "https://example.test/records"

    def __init__(self, popup: _Popup) -> None:
        self.popup = popup
        self.locator_value = _PopupLocator()

    def locator(self, _selector: str) -> _PopupLocator:
        return self.locator_value

    def expect_popup(self, **_kwargs: Any) -> _PopupInfo:
        return _PopupInfo(self.popup)


def test_popup_waits_for_declared_final_url_and_closes() -> None:
    popup = _Popup("https://example.test/export")
    page = _PopupPage(popup)

    receipt = _execute_with_final_popup_url(
        page=page,
        step={
            "phase": "treatment",
            "action": complex_ui.CLICK_POPUP,
            "selector": "#open-export",
            "expected_url": "/export",
            "close_after_observation": True,
            "wait_until": "domcontentloaded",
        },
        runtime_contract={
            "approved_base_url": "https://example.test",
        },
    )

    assert popup.waited_url == "https://example.test/export"
    assert popup.closed is True
    assert receipt["waited_for_source_declared_final_url"] is True
    assert receipt["popup_closed_after_observation"] is True
    assert receipt["raw_popup_url_included"] is False
    assert "https://example.test/export" not in json.dumps(receipt)
