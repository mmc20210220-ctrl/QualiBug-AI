"""Source-admission guard for governed complex UI interactions."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from . import _formal_ui_contracts as _contracts

SET_INPUT_FILES = "set_input_files"
CLICK_DOWNLOAD = "click_download"
CLICK_POPUP = "click_popup"
COMPLEX_ACTIONS = frozenset({SET_INPUT_FILES, CLICK_DOWNLOAD, CLICK_POPUP})
_MAX_UPLOAD_FILES = 10
_MAX_DOWNLOAD_BYTES = 50_000_000
_PERSISTENT_PROPERTY = "http_json_pointer"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INSTALL_MARKER = "_qualibug_formal_ui_complex_interaction_guard_installed"
_ORIGINAL_VALIDATOR = "_qualibug_ui_validator_before_complex_interactions"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _origin(value: Any) -> str:
    parsed = urlparse(_text(value, limit=2000))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _gap(
    contract: dict[str, Any],
    *,
    source_id: str,
    locator: str,
    missing: list[str],
) -> dict[str, Any]:
    request = _dict(contract.get("ui_request"))
    return {
        "gap_type": "formal_ui_contract_incomplete",
        "reason_code": "FORMAL_UI_CONTRACT_INCOMPLETE",
        "contract_id": _text(
            contract.get("contract_id") or request.get("request_id")
        ),
        "source_id": source_id,
        "source_locator": locator,
        "missing_requirements": list(dict.fromkeys(missing)),
        "status": "unsupported",
    }


def _frame_gaps(row: dict[str, Any], prefix: str) -> list[str]:
    selector = _text(row.get("frame_selector"), limit=500)
    raw_origin = _text(row.get("frame_origin"), limit=2000).rstrip("/")
    origin = _origin(raw_origin)
    missing: list[str] = []
    if bool(selector) != bool(raw_origin):
        missing.append(f"{prefix}.frame_selector_and_origin")
    if raw_origin and (not origin or raw_origin.lower() != origin):
        missing.append(f"{prefix}.frame_origin_exact_http_origin")
    if row.get("frame_locator_intent"):
        missing.append(f"{prefix}.frame_selector_only_v1")
    return missing


def _complex_step_gaps(step: dict[str, Any], index: int) -> list[str]:
    action = _text(step.get("action")).lower()
    phase = _text(step.get("phase")).lower()
    prefix = f"steps[{index}].{action}"
    missing = _frame_gaps(step, f"steps[{index}]")
    if action == SET_INPUT_FILES:
        forbidden = {
            "file_path", "path", "files", "content", "payload", "base64",
        }
        if forbidden & set(step):
            missing.append(f"{prefix}.runtime_file_refs_only")
        refs = step.get("file_refs")
        if not isinstance(refs, list):
            missing.append(f"{prefix}.file_refs_list")
        else:
            normalized = [_text(value, limit=160) for value in refs]
            if any(not value for value in normalized):
                missing.append(f"{prefix}.file_refs_nonempty_values")
            if len(normalized) != len(set(normalized)):
                missing.append(f"{prefix}.file_refs_unique")
            if len(normalized) > _MAX_UPLOAD_FILES:
                missing.append(f"{prefix}.file_count_max_{_MAX_UPLOAD_FILES}")
            if phase == "treatment" and not normalized:
                missing.append(f"{prefix}.treatment_file_refs")
        return missing
    if phase != "treatment":
        missing.append(f"{prefix}.phase=treatment")
    if action == CLICK_DOWNLOAD:
        if step.get("delete_after_observation") is not True:
            missing.append(f"{prefix}.delete_after_observation=true")
        limit = step.get("max_download_bytes", _MAX_DOWNLOAD_BYTES)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 0
        if not 1 <= limit <= _MAX_DOWNLOAD_BYTES:
            missing.append(f"{prefix}.max_download_bytes")
        expected_sha = _text(step.get("expected_sha256"), limit=64).lower()
        if expected_sha and not _SHA256_RE.fullmatch(expected_sha):
            missing.append(f"{prefix}.expected_sha256")
    elif action == CLICK_POPUP:
        if not _text(step.get("expected_url"), limit=2000):
            missing.append(f"{prefix}.expected_url")
        if step.get("close_after_observation") is not True:
            missing.append(f"{prefix}.close_after_observation=true")
        wait = _text(step.get("wait_until") or "domcontentloaded", limit=40)
        if wait not in {"commit", "domcontentloaded", "load", "networkidle"}:
            missing.append(f"{prefix}.wait_until")
    return missing


def install_formal_ui_complex_interaction_guard() -> None:
    if getattr(_contracts, _INSTALL_MARKER, False):
        return
    actions = frozenset({*_contracts.INTERACTIVE_ACTIONS, *COMPLEX_ACTIONS})
    _contracts.INTERACTIVE_ACTIONS = actions
    _contracts._ALLOWED_ACTIONS = frozenset({
        *_contracts._ALLOWED_ACTIONS,
        *COMPLEX_ACTIONS,
    })
    original = getattr(
        _contracts,
        _ORIGINAL_VALIDATOR,
        _contracts._validate_contract,
    )
    setattr(_contracts, _ORIGINAL_VALIDATOR, original)

    def validate_with_complex_interactions(
        raw: dict[str, Any],
        *,
        source_id: str,
        locator: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        contract, gap = original(raw, source_id=source_id, locator=locator)
        if gap or not contract:
            return contract, gap
        request = _dict(contract.get("ui_request"))
        plan = _dict(request.get("browser_plan"))
        steps = [row for row in _list(plan.get("steps")) if isinstance(row, dict)]
        missing: list[str] = []
        for index, step in enumerate(steps, start=1):
            action = _text(step.get("action")).lower()
            if action in COMPLEX_ACTIONS:
                missing.extend(_complex_step_gaps(step, index))
            elif step.get("frame_selector") or step.get("frame_origin"):
                missing.extend(_frame_gaps(step, f"steps[{index}]"))
        for index, probe in enumerate(
            [row for row in _list(plan.get("state_probes")) if isinstance(row, dict)],
            start=1,
        ):
            has_frame = bool(probe.get("frame_selector") or probe.get("frame_origin"))
            if not has_frame:
                continue
            prefix = f"state_probes[{index}]"
            if _text(probe.get("property")).lower() == _PERSISTENT_PROPERTY:
                missing.append(f"{prefix}.persistent_probe_no_frame_scope")
            else:
                missing.extend(_frame_gaps(probe, prefix))
        if missing:
            return None, _gap(
                contract,
                source_id=source_id,
                locator=locator,
                missing=missing,
            )
        return contract, None

    _contracts._validate_contract = validate_with_complex_interactions
    setattr(_contracts, _INSTALL_MARKER, True)


__all__ = [
    "CLICK_DOWNLOAD",
    "CLICK_POPUP",
    "COMPLEX_ACTIONS",
    "SET_INPUT_FILES",
    "install_formal_ui_complex_interaction_guard",
]
