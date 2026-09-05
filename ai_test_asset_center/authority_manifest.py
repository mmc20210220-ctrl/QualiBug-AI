"""Versioned semantic-authority manifest loader and contract checks.

R38-A records only authorities proven on the production call path. An
UNRESOLVED slot is intentionally non-blocking; it is an explicit statement
that no single production authority has been proven yet.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "qualibug.authority-manifest.v1"
AUTHORITY_SLOTS = (
    "syntactic_normalize",
    "semantic_normalize",
    "state_reduce",
    "world_model_reduce",
    "events_projection",
    "reward_projection",
    "behavior_ir",
    "path_predicate",
    "evaluator",
    "candidate_orchestration",
)
_ALLOWED_STATUSES = {"RESOLVED", "UNRESOLVED"}
_DEFAULT_MANIFEST_PATH = Path(__file__).with_name("authority_manifest.v1.json")


class AuthorityManifestError(RuntimeError):
    """The checked-in authority manifest violates its contract."""


class AuthorityStartupError(RuntimeError):
    """A production-declared RESOLVED authority cannot be loaded."""


def load_authority_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or _DEFAULT_MANIFEST_PATH
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityManifestError(
            f"authority_manifest_unreadable:{type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict):
        raise AuthorityManifestError("authority_manifest_not_object")
    return payload


def load_target(target: str) -> Any:
    """Load one ``module:attribute`` target, including nested attributes."""
    module_name, separator, attribute_path = str(target or "").partition(":")
    if not separator or not module_name or not attribute_path:
        raise ValueError(f"invalid authority target: {target!r}")
    value: Any = importlib.import_module(module_name)
    for part in attribute_path.split("."):
        value = getattr(value, part)
    return value


def _caller_uses_target(caller_target: str, authority_target: str) -> bool:
    """Prove a direct Python call from a declared production caller.

    This deliberately checks executable AST calls, not comments/docstrings or a
    second self-declared registry. The current v1 contract accepts either a
    local ``from module import function`` followed by a call, or a module alias
    followed by ``alias.function(...)``.
    """
    caller = load_target(caller_target)
    source = textwrap.dedent(inspect.getsource(caller))
    tree = ast.parse(source)
    target_module, _, target_attribute = authority_target.partition(":")
    target_attribute = target_attribute.split(".", 1)[0]

    direct_aliases: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_module = node.module or ""
            module_matches = (
                imported_module == target_module
                or target_module.endswith(f".{imported_module}")
            )
            if not module_matches:
                continue
            for alias in node.names:
                if alias.name == target_attribute:
                    direct_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target_module:
                    module_aliases.add(alias.asname or alias.name.split(".")[-1])

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in direct_aliases:
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == target_attribute
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in module_aliases
        ):
            return True
    return False


def validate_manifest_contract(
    manifest: dict[str, Any] | None = None,
    *,
    verify_production_usage: bool = True,
) -> dict[str, Any]:
    """Validate schema, loadability and production-use proof for RESOLVED slots."""
    payload = manifest if manifest is not None else load_authority_manifest()
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise AuthorityManifestError("authority_manifest_schema_version_invalid")
    if payload.get("production_entrypoint") != (
        "ai_test_asset_center.private_pilot_entrypoint:run_server"
    ):
        raise AuthorityManifestError("authority_manifest_entrypoint_invalid")

    slots = payload.get("slots")
    if not isinstance(slots, dict) or set(slots) != set(AUTHORITY_SLOTS):
        raise AuthorityManifestError("authority_manifest_slots_must_match_exactly")

    resolved: list[str] = []
    unresolved: list[str] = []
    for slot in AUTHORITY_SLOTS:
        row = slots.get(slot)
        if not isinstance(row, dict):
            raise AuthorityManifestError(f"authority_slot_not_object:{slot}")
        status = row.get("status")
        if status not in _ALLOWED_STATUSES:
            raise AuthorityManifestError(f"authority_slot_status_invalid:{slot}")
        target = row.get("target")
        callers = row.get("production_callers")
        if not isinstance(callers, list) or any(
            not isinstance(item, str) or not item for item in callers
        ):
            raise AuthorityManifestError(f"authority_slot_callers_invalid:{slot}")

        if status == "UNRESOLVED":
            if target is not None or callers:
                raise AuthorityManifestError(
                    f"unresolved_authority_must_not_claim_target:{slot}"
                )
            unresolved.append(slot)
            continue

        if not isinstance(target, str) or not target:
            raise AuthorityManifestError(f"resolved_authority_target_missing:{slot}")
        if not callers:
            raise AuthorityManifestError(
                f"resolved_authority_production_caller_missing:{slot}"
            )
        try:
            load_target(target)
        except Exception as exc:
            raise AuthorityManifestError(
                f"resolved_authority_target_unloadable:{slot}:{target}"
            ) from exc
        if verify_production_usage:
            for caller_target in callers:
                try:
                    used = _caller_uses_target(caller_target, target)
                except Exception as exc:
                    raise AuthorityManifestError(
                        f"authority_production_caller_unverifiable:{slot}:{caller_target}"
                    ) from exc
                if not used:
                    raise AuthorityManifestError(
                        f"authority_not_used_by_production_caller:{slot}:{caller_target}"
                    )
        resolved.append(slot)

    return {
        "schema_version": SCHEMA_VERSION,
        "resolved": resolved,
        "unresolved": unresolved,
    }


def validate_resolved_authorities_for_startup(
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail startup only when a slot explicitly marked RESOLVED cannot load.

    UNRESOLVED slots are deliberately ignored here. Full schema and production
    call-graph claims are enforced by the contract test; startup only protects
    the executable promise made by RESOLVED rows.
    """
    payload = manifest if manifest is not None else load_authority_manifest()
    slots = payload.get("slots") if isinstance(payload, dict) else None
    if not isinstance(slots, dict):
        return {"resolved": [], "unresolved": list(AUTHORITY_SLOTS)}

    loaded: list[str] = []
    unresolved: list[str] = []
    for slot in AUTHORITY_SLOTS:
        row = slots.get(slot)
        if not isinstance(row, dict) or row.get("status") != "RESOLVED":
            unresolved.append(slot)
            continue
        target = row.get("target")
        try:
            load_target(str(target or ""))
        except Exception as exc:
            raise AuthorityStartupError(
                f"resolved_authority_unloadable:{slot}:{target}"
            ) from exc
        loaded.append(slot)
    return {"resolved": loaded, "unresolved": unresolved}
