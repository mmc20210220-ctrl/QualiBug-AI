"""Authorization delivery evidence authority with finding-collection support.

The stable single-finding mechanics remains private. Causal evidence is attached to every
fanned-out finding occurrence that belongs to the authorization experiment, while the
legacy ``finding`` field remains the first deterministic projection.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import _authorization_delivery_gate_single_finding_mechanics as _core
from ._authorization_delivery_gate_single_finding_mechanics import *  # noqa: F401,F403

_original_attach_authorization_delivery_evidence = (
    _core.attach_authorization_delivery_evidence
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def attach_authorization_delivery_evidence(
    result: dict[str, Any],
    *,
    experiment: dict[str, Any],
) -> dict[str, Any]:
    output = deepcopy(_dict(result))
    findings = [
        dict(row)
        for row in _list(output.get("findings"))
        if isinstance(row, dict)
    ]
    if not findings:
        return _original_attach_authorization_delivery_evidence(
            output,
            experiment=experiment,
        )
    attached: list[dict[str, Any]] = []
    for finding in findings:
        single = dict(output)
        single["finding"] = finding
        single.pop("findings", None)
        governed = _original_attach_authorization_delivery_evidence(
            single,
            experiment=experiment,
        )
        attached_finding = _dict(governed.get("finding"))
        if not attached_finding:
            raise _core.AuthorizationDeliveryGateError(
                "authorization_delivery_fanout_finding_missing"
            )
        attached.append(dict(attached_finding))
    output["findings"] = attached
    output["finding"] = attached[0] if attached else None
    return output


__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
