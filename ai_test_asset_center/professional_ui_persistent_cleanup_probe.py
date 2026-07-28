"""Persistent-state cleanup probes for governed UI interaction.

Rendered browser state alone cannot prove that a UI write was reversed. This
extension requires at least one source-declared, same-origin, read-only HTTP JSON
probe for every governed interaction. The probe runs through the authenticated
browser context immediately before treatment and after cleanup. Only status,
URL and selected JSON-value fingerprints enter the cleanup receipt.

No response body, header, cookie or selected raw value is persisted.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

from . import professional_ui_interaction_cleanup as _interaction

PERSISTENT_PROBE_PROPERTY = "http_json_pointer"
EQUIVALENCE_SCOPE = "rendered_and_persistent_state"
MAX_RESPONSE_BYTES = 1_000_000
_INSTALL_MARKER = "_qualibug_persistent_ui_cleanup_probe_installed"
_ORIGINAL_VALIDATE_PROBE = "_qualibug_original_ui_probe_validator_before_persistent"
_ORIGINAL_VALIDATE_WRITE = "_qualibug_original_ui_write_validator_before_persistent"
_ORIGINAL_PROBE_MATERIAL = "_qualibug_original_ui_probe_material_before_persistent"
_ORIGINAL_CONTRACT_CHECK = "_qualibug_original_ui_contract_check_before_persistent"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any, *, default: int, maximum: int, code: str) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise _interaction._browser.BrowserExecutionError(code)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _interaction._browser.BrowserExecutionError(code) from exc
    if not 1 <= number <= maximum:
        raise _interaction._browser.BrowserExecutionError(code)
    return number


def _json_pointer(payload: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise RuntimeError("UI_PERSISTENT_PROBE_JSON_POINTER_INVALID")
    current = payload
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise RuntimeError("UI_PERSISTENT_PROBE_JSON_POINTER_NOT_FOUND")
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "UI_PERSISTENT_PROBE_JSON_POINTER_INDEX_INVALID"
                ) from exc
            if index < 0 or index >= len(current):
                raise RuntimeError("UI_PERSISTENT_PROBE_JSON_POINTER_NOT_FOUND")
            current = current[index]
        else:
            raise RuntimeError("UI_PERSISTENT_PROBE_JSON_POINTER_NOT_FOUND")
    return current


def install_persistent_ui_cleanup_probe() -> None:
    # Privacy is installed immediately before this entry in the formal runtime.
    # Complex interaction wrappers must be present before persistent cleanup captures
    # the final write validator and probe-material functions.
    from .professional_ui_complex_interaction_finalizer import (
        install_professional_ui_complex_interaction_finalizer,
    )
    from .professional_ui_complex_interaction_hardening import (
        install_professional_ui_complex_interaction_hardening,
    )
    from .professional_ui_complex_interactions import (
        install_professional_ui_complex_interactions,
    )

    install_professional_ui_complex_interactions()
    install_professional_ui_complex_interaction_hardening()
    if getattr(_interaction, _INSTALL_MARKER, False):
        return
    original_validate_probe = getattr(
        _interaction,
        _ORIGINAL_VALIDATE_PROBE,
        _interaction._validate_probe,
    )
    original_validate_write = getattr(
        _interaction,
        _ORIGINAL_VALIDATE_WRITE,
        _interaction._validate_write_plan,
    )
    original_probe_material = getattr(
        _interaction,
        _ORIGINAL_PROBE_MATERIAL,
        _interaction._probe_material,
    )
    original_contract_check = getattr(
        _interaction,
        _ORIGINAL_CONTRACT_CHECK,
        _interaction._source_cleanup_contract_error,
    )
    setattr(_interaction, _ORIGINAL_VALIDATE_PROBE, original_validate_probe)
    setattr(_interaction, _ORIGINAL_VALIDATE_WRITE, original_validate_write)
    setattr(_interaction, _ORIGINAL_PROBE_MATERIAL, original_probe_material)
    setattr(_interaction, _ORIGINAL_CONTRACT_CHECK, original_contract_check)

    _interaction.PROBE_PROPERTIES = frozenset({
        *_interaction.PROBE_PROPERTIES,
        PERSISTENT_PROBE_PROPERTY,
    })

    def contract_check_with_persistent_scope(plan: dict[str, Any]) -> str:
        error = original_contract_check(plan)
        if error:
            return error
        scope = _text(
            _dict(_interaction._interaction_contract(plan)).get("equivalence_scope")
        )
        if scope != EQUIVALENCE_SCOPE:
            return "UI_INTERACTION_EQUIVALENCE_SCOPE_INVALID"
        if not any(
            _text(row.get("property")).lower() == PERSISTENT_PROBE_PROPERTY
            for row in _list(plan.get("state_probes"))
            if isinstance(row, dict)
        ):
            return "UI_INTERACTION_PERSISTENT_STATE_PROBE_MISSING"
        return ""

    def validate_probe_with_persistent_http(
        raw: dict[str, Any],
        seen: set[str],
    ) -> dict[str, Any]:
        prop = _text(_dict(raw).get("property")).lower()
        if prop != PERSISTENT_PROBE_PROPERTY:
            return original_validate_probe(raw, seen)
        probe = dict(raw)
        probe_id = _text(probe.get("probe_id") or probe.get("id"))
        if not probe_id:
            raise _interaction._browser.BrowserExecutionError(
                "browser_state_probe_id_missing"
            )
        if probe_id in seen:
            raise _interaction._browser.BrowserExecutionError(
                "browser_state_probe_id_duplicate"
            )
        seen.add(probe_id)
        if _text(probe.get("method") or "GET").upper() != "GET":
            raise _interaction._browser.BrowserExecutionError(
                "browser_persistent_probe_get_required"
            )
        url = _text(probe.get("url"))
        if not url:
            raise _interaction._browser.BrowserExecutionError(
                "browser_persistent_probe_url_missing"
            )
        pointer = _text(probe.get("json_pointer"))
        if not pointer.startswith("/"):
            raise _interaction._browser.BrowserExecutionError(
                "browser_persistent_probe_json_pointer_invalid"
            )
        if (
            probe.get("selector")
            or probe.get("locator_intent")
            or probe.get("frame_selector")
            or probe.get("frame_origin")
        ):
            raise _interaction._browser.BrowserExecutionError(
                "browser_persistent_probe_locator_not_allowed"
            )
        expected_class = _positive_int(
            probe.get("expected_status_class"),
            default=2,
            maximum=5,
            code="browser_persistent_probe_status_class_invalid",
        )
        if expected_class != 2:
            raise _interaction._browser.BrowserExecutionError(
                "browser_persistent_probe_status_class_must_be_2"
            )
        max_bytes = _positive_int(
            probe.get("max_response_bytes"),
            default=MAX_RESPONSE_BYTES,
            maximum=MAX_RESPONSE_BYTES,
            code="browser_persistent_probe_response_limit_invalid",
        )
        return {
            **probe,
            "probe_id": probe_id,
            "property": PERSISTENT_PROBE_PROPERTY,
            "method": "GET",
            "url": url,
            "json_pointer": pointer,
            "expected_status_class": 2,
            "max_response_bytes": max_bytes,
        }

    def validate_write_with_persistent_probe(
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = original_validate_write(plan, runtime_contract)
        persistent = [
            row
            for row in _list(normalized.get("state_probes"))
            if isinstance(row, dict)
            and _text(row.get("property")).lower() == PERSISTENT_PROBE_PROPERTY
        ]
        if not persistent:
            raise _interaction._browser.BrowserExecutionError(
                "UI_INTERACTION_PERSISTENT_STATE_PROBE_MISSING"
            )
        base_url = _text(normalized.get("base_url"))
        for probe in persistent:
            resolved = urljoin(base_url.rstrip("/") + "/", _text(probe.get("url")))
            if not _interaction._browser._same_approved_origin(base_url, resolved):
                raise _interaction._browser.BrowserExecutionError(
                    "browser_persistent_probe_outside_approved_base_url"
                )
            probe["url"] = resolved
        return normalized

    def persistent_probe_material(page: Any, probe: dict[str, Any]) -> dict[str, Any]:
        if _text(probe.get("property")).lower() != PERSISTENT_PROBE_PROPERTY:
            return original_probe_material(page, probe)
        response = page.request.get(
            _text(probe.get("url")),
            timeout=int(probe.get("timeout_ms") or 10_000),
        )
        status = int(response.status or 0)
        if status // 100 != int(probe.get("expected_status_class") or 2):
            raise RuntimeError("UI_PERSISTENT_PROBE_STATUS_CLASS_INVALID")
        body = response.body()
        if len(body) > int(probe.get("max_response_bytes") or MAX_RESPONSE_BYTES):
            raise RuntimeError("UI_PERSISTENT_PROBE_RESPONSE_TOO_LARGE")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("UI_PERSISTENT_PROBE_JSON_INVALID") from exc
        value = _json_pointer(payload, _text(probe.get("json_pointer")))
        return {
            "property": PERSISTENT_PROBE_PROPERTY,
            "method": "GET",
            "url_fingerprint": _interaction._fingerprint(_text(probe.get("url"))),
            "status_class": status // 100,
            "json_pointer_fingerprint": _interaction._fingerprint(
                _text(probe.get("json_pointer"))
            ),
            "value_fingerprint": _interaction._fingerprint(value),
            "raw_response_included": False,
            "raw_selected_value_included": False,
        }

    _interaction._source_cleanup_contract_error = contract_check_with_persistent_scope
    _interaction._validate_probe = validate_probe_with_persistent_http
    _interaction._validate_write_plan = validate_write_with_persistent_probe
    _interaction._probe_material = persistent_probe_material
    setattr(_interaction, _INSTALL_MARKER, True)
    install_professional_ui_complex_interaction_finalizer()


__all__ = [
    "EQUIVALENCE_SCOPE",
    "MAX_RESPONSE_BYTES",
    "PERSISTENT_PROBE_PROPERTY",
    "install_persistent_ui_cleanup_probe",
]
