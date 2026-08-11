"""Effect-aware facade for governed non-production writes.

The safety implementation remains in ``sandbox_write_executor_base``. This
facade preserves the original HTTP outcome while promoting no rejected request
to an accepted write. It also installs the same table-scoped DDL UNIQUE
authority used by fixture materialization so governed control POSTs cannot
silently mutate a field merely because another table declares that name UNIQUE.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from . import sandbox_write_executor_base as _base
from .sandbox_write_executor_base import *  # noqa: F401,F403
from .schema_unique_materialization_authority import (
    TableScopedUniqueFields,
    declared_unique_fields_scoped,
    materialize_unique_create_fields_scoped,
)


# Private compatibility exports used by the main experiment executor.
_http_request = _base._http_request
_restore_payload = _base._restore_payload
_base_cleanup_after_write = _base._cleanup_after_write
_base_execute_governed_fixture_lifecycle = _base.execute_governed_fixture_lifecycle


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _load_declared_unique_fields_scoped(root: Any, project: str) -> TableScopedUniqueFields:
    """Load/cache one table-scoped UNIQUE authority for the project schema."""

    key = (str(root or ""), str(project or ""))
    cached = _base._DECLARED_UNIQUE_FIELDS_CACHE.get(key)
    if isinstance(cached, TableScopedUniqueFields):
        return cached
    candidates = [
        Path(root) / "platform_workspace" / str(project) / "input" / "schema.sql",
        Path(root) / "platform_inputs" / str(project) / "schema.sql",
    ]
    authority = TableScopedUniqueFields({})
    for candidate in candidates:
        try:
            if candidate.is_file():
                authority = declared_unique_fields_scoped(
                    candidate.read_text(encoding="utf-8")
                )
                break
        except OSError:
            continue
    _base._DECLARED_UNIQUE_FIELDS_CACHE[key] = authority
    return authority


def _materialize_unique_create_fields_scoped(*args: Any, **kwargs: Any) -> tuple[Any, list[str]]:
    return materialize_unique_create_fields_scoped(*args, **kwargs)


# ``sandbox_write_executor_base`` imported the historical helpers directly into
# module globals. Replace those exact authorities so its internal loader and
# governed-control implementation cannot retain the global flattened field set.
_base.declared_unique_fields = declared_unique_fields_scoped
_base._load_declared_unique_fields = _load_declared_unique_fields_scoped
_base.materialize_unique_create_fields = _materialize_unique_create_fields_scoped


def _cleanup_after_write(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run cleanup with the facade transport binding used by callers/tests."""

    import sys

    self_module = sys.modules[__name__]
    saved_http = _base._http_request
    try:
        _base._http_request = getattr(self_module, "_http_request")
        return _base_cleanup_after_write(*args, **kwargs)
    finally:
        _base._http_request = saved_http


def execute_governed_fixture_lifecycle(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run fixture governance with the facade transport binding."""

    import sys

    self_module = sys.modules[__name__]
    saved_http = _base._http_request
    try:
        _base._http_request = getattr(self_module, "_http_request")
        return _base_execute_governed_fixture_lifecycle(*args, **kwargs)
    finally:
        _base._http_request = saved_http


def execute_with_sandbox_write(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Facade over the governed base implementation with composable transport."""
    import sys

    _self = sys.modules[__name__]
    saved_http = _base._http_request
    saved_cleanup = _base._cleanup_after_write
    try:
        _base._http_request = getattr(_self, "_http_request")
        _base._cleanup_after_write = getattr(_self, "_cleanup_after_write")
        return _base.execute_with_sandbox_write(*args, **kwargs)
    finally:
        _base._http_request = saved_http
        _base._cleanup_after_write = saved_cleanup


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
    runtime_body_plan: dict[str, Any] | None = None,
    restorable_identity_mutation: bool = False,
) -> dict[str, Any]:
    import sys

    self_module = sys.modules[__name__]
    saved_http = _base._http_request
    saved_allowed = _base.sandbox_write_allowed
    try:
        _base._http_request = getattr(self_module, "_http_request")
        _base.sandbox_write_allowed = getattr(self_module, "sandbox_write_allowed")
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
            runtime_body_plan=runtime_body_plan,
            restorable_identity_mutation=restorable_identity_mutation,
        )
    finally:
        _base._http_request = saved_http
        _base.sandbox_write_allowed = saved_allowed
    receipt = deepcopy(_dict(original))
    transport_accepted = receipt.get("accepted") is True
    receipt["transport_accepted"] = transport_accepted
    receipt["effectively_accepted"] = transport_accepted
    if transport_accepted:
        return receipt

    write_status = _snapshot_status(receipt.get("write"))
    if write_status <= 0 or not _audited_business_state_changed(receipt):
        return receipt

    receipt["accepted"] = False
    receipt["effectively_accepted"] = False
    receipt["indeterminate_side_effect_detected"] = True
    receipt["status"] = "rejected_with_indeterminate_side_effect"
    receipt["reason"] = "rejected_transport_but_business_state_changed"

    audit_record = deepcopy(_dict(receipt.get("audit_record")))
    audit_record.update({
        "record_type": "rejected_transport_indeterminate_side_effect",
        "operation_accepted": False,
        "transport_accepted": False,
        "effectively_accepted": False,
        "indeterminate_side_effect_detected": True,
        "cleanup_status": "required",
        "cleanup_reason": "rejected_transport_side_effect_requires_cleanup",
        "http_status": write_status,
    })
    audit_path = _base._append_audit(root, project, audit_record)
    receipt["audit_record"] = audit_record
    receipt["audit_path"] = str(audit_path)
    return receipt


# Preserve the proven governed-write implementation and extend only its
# post-write readback phase. The lambda resolves the facade transport at call
# time so existing tests/consumers that substitute ``_http_request`` still work.
from .governed_async_readback import wrap_governed_write as _wrap_governed_write

execute_governed_control_write = _wrap_governed_write(
    execute_governed_control_write,
    lambda *args, **kwargs: _http_request(*args, **kwargs),
)
