"""Bind UI upload scenarios to exact safe prerequisite operations and source roles.

The formal UI Behavior-IR binder accepts one exact, read-only prerequisite
operation and one executable actor identity. Free-text operation/actor values can
therefore survive scenario approval but fail much later during IR binding. This
installer moves validation to scenario registration and repeats it at approval and
run materialization:

* ``operation_ref`` must resolve uniquely to an enterprise interface;
* the canonical reference is the interface id recorded in Behavior IR
  ``source_operation_refs``;
* only GET/HEAD/OPTIONS operations may be prerequisites;
* the prerequisite interface's source id, version and hash are frozen;
* ``actor_role`` must be explicitly declared by the role catalog or permission
  matrix (``public``/``anonymous`` remain explicit built-in roles);
* caller-authored ``actor_ref`` is rejected because Behavior-IR node ids are runtime
  products, not stable source identities.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from . import ui_upload_scenario_registry as _scenarios
from .enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    load_enterprise_business_knowledge_asset,
)

_INSTALL_MARKER = "_qualibug_upload_scenario_semantic_authority_installed"
_ORIGINAL_MARKER = "_qualibug_upload_scenario_builder_before_semantic_authority"
_ORIGINAL_VERIFY_MARKER = "_qualibug_upload_scenario_verify_before_semantic_authority"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_BUILTIN_ROLES = frozenset({"public", "anonymous"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _merged_rows(asset: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Merge aliases without letting an empty preferred list hide a populated one.

    Exact duplicate rows are collapsed. Conflicting rows with the same semantic id
    remain distinct so the later unique-authority checks fail closed.
    """
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        for value in _list(asset.get(key)):
            if not isinstance(value, dict):
                continue
            marker = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if marker in seen:
                continue
            seen.add(marker)
            output.append(value)
    return output


def _knowledge_asset(project: str, root: Path) -> dict[str, Any]:
    asset = load_enterprise_business_knowledge_asset(project, root=Path(root))
    if not isinstance(asset, dict) or not asset:
        asset = build_enterprise_business_knowledge_asset(project, root=Path(root))
    if not isinstance(asset, dict) or not asset:
        raise RuntimeError("ui_upload_scenario_knowledge_asset_missing")
    return asset


def _active_source_identity(
    asset: dict[str, Any],
    source_id: str,
) -> dict[str, str]:
    identity = _text(source_id, limit=160)
    matches = [
        row
        for row in _merged_rows(asset, "sources", "source_inventory", "canonical_source_inventory")
        if _text(row.get("source_id") or row.get("id"), limit=160) == identity
        and _text(row.get("status") or "active", limit=40).lower() == "active"
    ]
    if len(matches) != 1:
        raise RuntimeError("ui_upload_scenario_prerequisite_source_not_active")
    row = matches[0]
    digest = _text(
        row.get("content_hash") or row.get("text_hash") or row.get("hash"),
        limit=64,
    ).lower()
    version = _text(row.get("version"), limit=80)
    if len(digest) != 64 or not version:
        raise RuntimeError("ui_upload_scenario_prerequisite_source_identity_incomplete")
    return {
        "source_id": identity,
        "source_hash": digest,
        "source_version": version,
    }


def _safe_prerequisite_operation(
    asset: dict[str, Any],
    operation_ref: str,
) -> dict[str, str]:
    identity = _text(operation_ref, limit=240)
    if not identity:
        raise ValueError("ui_upload_scenario_operation_ref_required")
    candidates: list[dict[str, Any]] = []
    for row in _merged_rows(asset, "interfaces", "operations"):
        aliases = {
            _text(row.get("interface_id"), limit=240),
            _text(row.get("operation_id"), limit=240),
            _text(row.get("id"), limit=240),
        } - {""}
        if identity in aliases:
            candidates.append(row)
    if len(candidates) != 1:
        raise ValueError(
            "ui_upload_scenario_prerequisite_operation_ambiguous"
            if len(candidates) > 1
            else "ui_upload_scenario_prerequisite_operation_not_found"
        )
    row = candidates[0]
    method = _text(row.get("method") or row.get("http_method"), limit=20).upper()
    if method not in _SAFE_METHODS:
        raise ValueError(
            "ui_upload_scenario_prerequisite_operation_must_be_safe_read"
        )
    path = _text(row.get("path") or row.get("endpoint") or row.get("url"), limit=2000)
    interface_id = _text(row.get("interface_id"), limit=240)
    source = _active_source_identity(
        asset,
        _text(row.get("source_id"), limit=160),
    )
    if not interface_id or not path:
        raise RuntimeError("ui_upload_scenario_prerequisite_identity_incomplete")
    return {
        "interface_id": interface_id,
        "operation_id": _text(row.get("operation_id"), limit=240),
        "method": method,
        "path": path,
        **source,
    }


def _declared_roles(asset: dict[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for row in _merged_rows(asset, "roles"):
        role = _text(row.get("role") or row.get("name") or row.get("id"), limit=160)
        if role:
            roles.setdefault(role.casefold(), role)
    for row in _merged_rows(asset, "permission_matrix", "permissions"):
        role = _text(row.get("role") or row.get("actor") or row.get("principal"), limit=160)
        if role:
            roles.setdefault(role.casefold(), role)
    for role in _BUILTIN_ROLES:
        roles.setdefault(role, role)
    return roles


def _source_actor_role(asset: dict[str, Any], value: Any) -> str:
    role = _text(value, limit=160)
    if not role:
        raise ValueError("ui_upload_scenario_actor_role_required")
    declared = _declared_roles(asset)
    canonical = declared.get(role.casefold())
    if not canonical:
        raise ValueError("ui_upload_scenario_actor_role_not_source_declared")
    return canonical


def install_ui_upload_scenario_semantic_authority() -> None:
    # Both prerequisites are installed before checking our own marker. This makes
    # the entry point safe for fresh processes and hot-loaded processes alike.
    from .ui_upload_scenario_source_authority import (
        install_ui_upload_scenario_source_authority,
    )
    from .ui_upload_scenario_submission_authority import (
        install_ui_upload_scenario_submission_authority,
    )

    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_submission_authority()
    if getattr(_scenarios, _INSTALL_MARKER, False):
        return
    original = getattr(
        _scenarios,
        _ORIGINAL_MARKER,
        _scenarios.build_upload_scenario_contract,
    )
    original_verify = getattr(
        _scenarios,
        _ORIGINAL_VERIFY_MARKER,
        _scenarios._verify_candidate,
    )
    setattr(_scenarios, _ORIGINAL_MARKER, original)
    setattr(_scenarios, _ORIGINAL_VERIFY_MARKER, original_verify)

    def build_semantically_bound_upload_scenario(
        project_id: str,
        payload: dict[str, Any],
        *,
        root: Path | None = None,
    ) -> dict[str, Any]:
        effective_root = Path(root or _scenarios.ROOT)
        project = _scenarios._safe_project_id(project_id)
        data = copy.deepcopy(_dict(payload))
        if _text(data.get("actor_ref"), limit=240):
            raise ValueError("ui_upload_scenario_actor_ref_not_source_stable")
        asset = _knowledge_asset(project, effective_root)
        operation = _safe_prerequisite_operation(
            asset,
            _text(data.get("operation_ref"), limit=240),
        )
        actor_role = _source_actor_role(asset, data.get("actor_role"))
        data["operation_ref"] = operation["interface_id"]
        # The legacy deterministic seed expects actor_ref. Feed it the canonical
        # source role, then replace the semantic field before the contract is frozen.
        data["actor_ref"] = actor_role
        contract = copy.deepcopy(
            original(project, data, root=effective_root)
        )
        contract.pop("actor_ref", None)
        contract["actor_role"] = actor_role
        contract["safe_prerequisite_operation"] = copy.deepcopy(operation)
        request = copy.deepcopy(_dict(contract.get("ui_request")))
        request.pop("actor_ref", None)
        request["actor_role"] = actor_role
        request["operation_ref"] = operation["interface_id"]
        metadata = copy.deepcopy(_dict(request.get("metadata")))
        metadata.update({
            "safe_prerequisite_operation_bound": True,
            "prerequisite_interface_id": operation["interface_id"],
            "prerequisite_method": operation["method"],
            "prerequisite_path": operation["path"],
            "prerequisite_source_id": operation["source_id"],
            "prerequisite_source_version": operation["source_version"],
            "actor_role_source_declared": True,
        })
        request["metadata"] = metadata
        contract["ui_request"] = request
        return contract

    def verify_semantically_bound_candidate(
        project: str,
        root: Path,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        contract = copy.deepcopy(original_verify(project, root, record))
        asset = _knowledge_asset(project, Path(root))
        stored_operation = copy.deepcopy(
            _dict(contract.get("safe_prerequisite_operation"))
        )
        if not stored_operation:
            raise RuntimeError("ui_upload_scenario_prerequisite_contract_missing")
        try:
            current_operation = _safe_prerequisite_operation(
                asset,
                _text(stored_operation.get("interface_id"), limit=240),
            )
        except (ValueError, RuntimeError) as exc:
            raise RuntimeError(
                "ui_upload_scenario_prerequisite_operation_version_changed"
            ) from exc
        if current_operation != stored_operation:
            raise RuntimeError(
                "ui_upload_scenario_prerequisite_operation_version_changed"
            )
        stored_role = _text(contract.get("actor_role"), limit=160)
        try:
            current_role = _source_actor_role(asset, stored_role)
        except ValueError as exc:
            raise RuntimeError("ui_upload_scenario_actor_role_changed") from exc
        if current_role != stored_role:
            raise RuntimeError("ui_upload_scenario_actor_role_changed")
        return contract

    _scenarios.build_upload_scenario_contract = (
        build_semantically_bound_upload_scenario
    )
    _scenarios._verify_candidate = verify_semantically_bound_candidate
    setattr(_scenarios, _INSTALL_MARKER, True)


__all__ = [
    "install_ui_upload_scenario_semantic_authority",
]
