"""Require explicit submission and browser-compensation steps for upload scenarios.

Selecting a file is only browser-local state.  Most enterprise upload flows mutate
persistent state after either a source-declared submit click or an automatic upload
triggered by file selection.  This installer extends the deterministic scenario
builder without accepting an arbitrary plan:

* submission mode is exactly ``click_submit`` or ``auto_on_file_selection``;
* click mode requires one source-declared submit selector;
* every mode requires one source-declared cleanup selector;
* cleanup clicks the compensating delete/revoke control before clearing the file
  input;
* final request/contract identity includes these fields, so changing submission or
  cleanup never reuses an old approved identity.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import ui_upload_scenario_registry as _scenarios

_INSTALL_MARKER = "_qualibug_upload_scenario_submission_authority_installed"
_ORIGINAL_MARKER = "_qualibug_upload_scenario_builder_before_submission_authority"
_SUBMISSION_MODES = frozenset({"click_submit", "auto_on_file_selection"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def install_ui_upload_scenario_submission_authority() -> None:
    if getattr(_scenarios, _INSTALL_MARKER, False):
        return
    original = getattr(
        _scenarios,
        _ORIGINAL_MARKER,
        _scenarios.build_upload_scenario_contract,
    )
    setattr(_scenarios, _ORIGINAL_MARKER, original)

    def build_upload_scenario_with_submission_and_compensation(
        project_id: str,
        payload: dict[str, Any],
        *,
        root: Path | None = None,
    ) -> dict[str, Any]:
        data = copy.deepcopy(_dict(payload))
        mode = _text(data.get("submission_mode"), limit=80).lower()
        if mode not in _SUBMISSION_MODES:
            raise ValueError("ui_upload_scenario_submission_mode_invalid")
        submit_selector = _text(data.get("submit_selector"), limit=500)
        if mode == "click_submit" and not submit_selector:
            raise ValueError("ui_upload_scenario_submit_selector_required")
        if mode == "auto_on_file_selection" and submit_selector:
            raise ValueError("ui_upload_scenario_submit_selector_not_allowed_for_auto")
        cleanup_selector = _text(data.get("cleanup_selector"), limit=500)
        if not cleanup_selector:
            raise ValueError("ui_upload_scenario_cleanup_selector_required")

        contract = copy.deepcopy(original(project_id, data, root=root))
        request = copy.deepcopy(_dict(contract.get("ui_request")))
        plan = copy.deepcopy(_dict(request.get("browser_plan")))
        steps = [
            copy.deepcopy(row)
            for row in _list(plan.get("steps"))
            if isinstance(row, dict)
        ]
        upload_index = next(
            (
                index
                for index, row in enumerate(steps)
                if _text(row.get("phase")).lower() == "treatment"
                and _text(row.get("action")).lower() == "set_input_files"
            ),
            -1,
        )
        cleanup_clear_index = next(
            (
                index
                for index, row in enumerate(steps)
                if _text(row.get("phase")).lower() == "cleanup"
                and _text(row.get("action")).lower() == "set_input_files"
            ),
            -1,
        )
        if upload_index < 0 or cleanup_clear_index < 0:
            raise RuntimeError("ui_upload_scenario_base_plan_shape_invalid")
        upload_step = _dict(steps[upload_index])
        frame = {
            key: copy.deepcopy(upload_step[key])
            for key in ("frame_selector", "frame_origin")
            if key in upload_step
        }
        if mode == "click_submit":
            steps.insert(
                upload_index + 1,
                {
                    "phase": "treatment",
                    "action": "click",
                    "selector": submit_selector,
                    **frame,
                },
            )
            cleanup_clear_index += 1
        steps.insert(
            cleanup_clear_index,
            {
                "phase": "cleanup",
                "action": "click",
                "selector": cleanup_selector,
                **frame,
            },
        )
        plan["steps"] = steps
        request["browser_plan"] = plan
        metadata = copy.deepcopy(_dict(request.get("metadata")))
        metadata.update({
            "upload_submission_mode": mode,
            "upload_submit_click_required": mode == "click_submit",
            "upload_persistent_compensation_required": True,
            "upload_cleanup_action": "click",
        })
        request["metadata"] = metadata

        identity_seed = {
            "previous_request_id": _text(request.get("request_id"), limit=160),
            "submission_mode": mode,
            "submit_selector": submit_selector,
            "cleanup_selector": cleanup_selector,
            "steps": steps,
        }
        request_id = "ui_upload_" + _scenarios._digest(identity_seed)[:20]
        request["request_id"] = request_id
        contract["contract_id"] = request_id
        contract["ui_request"] = request
        contract["submission_contract"] = {
            "mode": mode,
            "submit_selector": submit_selector,
            "cleanup_action": "click",
            "cleanup_selector": cleanup_selector,
            "persistent_compensation_required": True,
        }
        return contract

    _scenarios.build_upload_scenario_contract = (
        build_upload_scenario_with_submission_and_compensation
    )
    setattr(_scenarios, _INSTALL_MARKER, True)


__all__ = [
    "install_ui_upload_scenario_submission_authority",
]
