"""Fail-close legacy Probe builders behind formal scenario design contracts."""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from .schema import as_dict


def _wrap(builder: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(builder, "_qualibug_execution_contract_gate_guard", False):
        return builder

    @wraps(builder)
    def guarded(asset: dict[str, Any], max_count: int = 140):
        planning_gate = as_dict(asset.get("scenario_planning_gate"))
        scenario_gate = as_dict(asset.get("scenario_ir_gate"))
        contract_gate = as_dict(asset.get("scenario_execution_contract_gate"))
        if planning_gate:
            if not bool(planning_gate.get("scenario_planning_allowed")):
                return []
            if not scenario_gate or not bool(scenario_gate.get("entry_allowed")):
                return []
            if not contract_gate or not bool(contract_gate.get("entry_allowed")):
                return []
        return builder(asset, max_count)

    guarded._qualibug_execution_contract_gate_guard = True  # type: ignore[attr-defined]
    guarded._qualibug_original_probe_builder = builder  # type: ignore[attr-defined]
    return guarded


def install_scenario_execution_probe_guard() -> None:
    """Guard both the public API aggregation and direct linking entrypoint."""
    from .. import _api, _linking

    _api._probes_from_asset = _wrap(_api._probes_from_asset)
    _linking._probes_from_asset = _wrap(_linking._probes_from_asset)


__all__ = ["install_scenario_execution_probe_guard"]
