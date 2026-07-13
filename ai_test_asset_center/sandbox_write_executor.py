"""Effect-aware facade for governed non-production writes.

The safety implementation remains in ``sandbox_write_executor_base``. This
facade preserves the original HTTP outcome while promoting one special case to
an effectively accepted write for cleanup purposes: the server returned a
non-2xx response, but audited before/after snapshots prove that business state
changed. Without this distinction, validation and authorization probes could
find a real side effect yet lose formal finding eligibility because cleanup was
incorrectly skipped.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from . import sandbox_write_executor_base as _base
from .sandbox_write_executor_base import *  # noqa: F401,F403


# Private compatibility exports used by the main experiment executor.
_http_request = _base._http_request
_restore_payload = _base._restore_payload


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _server_managed_field(value: Any) -> bool:
    normalized = "".join(
        char for char in _text(value).lower() if char.isalnum()
    )
    return normalized in {
        "createdat",
        "updatedat",
        "createdtime",
        "updatedtime",
        "modifiedat",
        "modifiedtime",
        "timestamp",
        "versiontimestamp",
    }


def _business_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _business_projection(child)
            for key, child in sorted(value.items())
            if not _server_managed_field(key)
        }
    if isinstance(value, list):
        projected = [_business_projection(child) for child in value]
        return sorted(
            projected,
            key=lambda child: json.dumps(
                child,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
    return value


def _snapshot_status(snapshot: Any) -> int:
    row = _dict(snapshot)
    try:
        return int(row.get("status") or row.get("status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _audited_business_state_changed(receipt: dict[str, Any]) -> bool:
    row = _dict(receipt)
    before = _dict(row.get("before"))
    after = _dict(row.get("after"))
    if not (
        200 <= _snapshot_status(before) < 300
        and 200 <= _snapshot_status(after) < 300
    ):
        return False
    before_body = before.get("body")
    after_body = after.get("body")
    if not isinstance(before_body, (dict, list)) or not isinstance(
        after_body,
        (dict, list),
    ):
        return False
    return _business_projection(before_body) != _business_projection(after_body)


def execute_governed_control_write(
    *,
    root: Any,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    operation_phase: str,
    actor_identity: str,
    actor_token: str,
    method: str,
    path: str,
    body: Any,
    observation_path: str,
) -> dict[str, Any]:
    original = _base.execute_governed_control_write(
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        campaign_id=campaign_id,
        operation_phase=operation_phase,
        actor_identity=actor_identity,
        actor_token=actor_token,
        method=method,
        path=path,
        body=body,
        observation_path=observation_path,
    )
    receipt = deepcopy(_dict(original))
    transport_accepted = receipt.get("accepted") is True
    receipt["transport_accepted"] = transport_accepted
    receipt["effectively_accepted"] = transport_accepted
    if transport_accepted:
        return receipt

    write_status = _snapshot_status(receipt.get("write"))
    if write_status <= 0 or not _audited_business_state_changed(receipt):
        return receipt

    receipt["accepted"] = True
    receipt["effectively_accepted"] = True
    receipt["accepted_due_to_observed_effect"] = True
    receipt["status"] = "executed_with_rejected_transport_side_effect"
    receipt["reason"] = "rejected_transport_but_business_state_changed"

    audit_record = deepcopy(_dict(receipt.get("audit_record")))
    audit_record.update({
        "record_type": "effectful_rejected_write_correction",
        "operation_accepted": True,
        "transport_accepted": False,
        "effectively_accepted": True,
        "accepted_due_to_observed_effect": True,
        "cleanup_status": "required",
        "cleanup_reason": "rejected_transport_side_effect_requires_cleanup",
        "http_status": write_status,
    })
    audit_path = _base._append_audit(root, project, audit_record)
    receipt["audit_record"] = audit_record
    receipt["audit_path"] = str(audit_path)
    return receipt
