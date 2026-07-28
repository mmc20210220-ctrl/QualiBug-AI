"""Source-admission guard for persistent UI cleanup probes.

Governed interaction contracts are not source-complete when they only compare
rendered browser state. This guard requires one relative same-target GET JSON
pointer probe and an explicit rendered-plus-persistent equivalence scope before
an enterprise source contract is admitted.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from . import _formal_ui_contracts as _contracts

_INSTALL_MARKER = "_qualibug_formal_ui_persistent_probe_guard_installed"
_ORIGINAL_MARKER = "_qualibug_original_formal_ui_contract_validator_before_persistent"
_PERSISTENT_PROPERTY = "http_json_pointer"
_EQUIVALENCE_SCOPE = "rendered_and_persistent_state"
_MAX_RESPONSE_BYTES = 1_000_000


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _gap_from_contract(
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
        "contract_id": _text(contract.get("contract_id") or request.get("request_id")),
        "source_id": source_id,
        "source_locator": locator,
        "missing_requirements": list(dict.fromkeys(missing)),
        "status": "unsupported",
    }


def install_formal_ui_persistent_probe_guard() -> None:
    if getattr(_contracts, _INSTALL_MARKER, False):
        return
    original = getattr(
        _contracts,
        _ORIGINAL_MARKER,
        _contracts._validate_contract,
    )
    setattr(_contracts, _ORIGINAL_MARKER, original)

    def validate_with_persistent_probe(
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
        interactive = any(
            _text(row.get("action")).lower() in _contracts.INTERACTIVE_ACTIONS
            for row in steps
        )
        if not interactive:
            return contract, None

        missing: list[str] = []
        interaction_contract = _dict(plan.get("interaction_contract"))
        if _text(interaction_contract.get("equivalence_scope")) != _EQUIVALENCE_SCOPE:
            missing.append(
                f"interaction_contract.equivalence_scope={_EQUIVALENCE_SCOPE}"
            )
        probes = [
            row
            for row in _list(plan.get("state_probes"))
            if isinstance(row, dict)
            and _text(row.get("property")).lower() == _PERSISTENT_PROPERTY
        ]
        if not probes:
            missing.append("browser_plan.persistent_state_probe")
        seen: set[str] = set()
        for index, probe in enumerate(probes, start=1):
            probe_id = _text(probe.get("probe_id") or probe.get("id"))
            if not probe_id:
                missing.append(f"persistent_probe[{index}].probe_id")
            elif probe_id in seen:
                missing.append(f"persistent_probe[{index}].probe_id_unique")
            seen.add(probe_id)
            if _text(probe.get("method") or "GET").upper() != "GET":
                missing.append(f"persistent_probe[{index}].method=GET")
            url = _text(probe.get("url"))
            parsed = urlparse(url)
            if not url or parsed.scheme or parsed.netloc or not url.startswith("/"):
                missing.append(f"persistent_probe[{index}].relative_same_target_url")
            if not _text(probe.get("json_pointer")).startswith("/"):
                missing.append(f"persistent_probe[{index}].json_pointer")
            expected_class = probe.get("expected_status_class", 2)
            try:
                expected_class = int(expected_class)
            except (TypeError, ValueError):
                expected_class = 0
            if expected_class != 2:
                missing.append(f"persistent_probe[{index}].expected_status_class=2")
            limit = probe.get("max_response_bytes", _MAX_RESPONSE_BYTES)
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = 0
            if not 1 <= limit <= _MAX_RESPONSE_BYTES:
                missing.append(f"persistent_probe[{index}].max_response_bytes")
            if probe.get("selector") or probe.get("locator_intent"):
                missing.append(f"persistent_probe[{index}].no_dom_locator")
        if missing:
            return None, _gap_from_contract(
                contract,
                source_id=source_id,
                locator=locator,
                missing=missing,
            )
        return contract, None

    _contracts._validate_contract = validate_with_persistent_probe
    setattr(_contracts, _INSTALL_MARKER, True)


__all__ = ["install_formal_ui_persistent_probe_guard"]
