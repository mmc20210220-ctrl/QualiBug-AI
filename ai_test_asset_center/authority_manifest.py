"""Versioned production mainline authority manifest and contract checks.

The manifest answers one question only: which implementation owns each required
production capability on the real private-pilot call path?  It does not create a
second engine or infer authority from filenames.  PRODUCT mode is fail-closed;
COMPATIBILITY is opt-in and may expose unresolved slots without hiding them.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "qualibug.authority-manifest.v1"
AUTHORITY_MODE_ENV = "QUALIBUG_AUTHORITY_MODE"
AUTHORITY_MODES = ("PRODUCT", "COMPATIBILITY", "BENCHMARK", "TEST")
AUTHORITY_SLOTS = (
    "behavior_ir",
    "obligation_source",
    "planner",
    "experiment_compiler",
    "executor",
    "evidence_pipeline",
    "oracle",
    "finding_pipeline",
    "delivery_gate",
    "release_decision",
)
_ALLOWED_STATUSES = {"RESOLVED", "UNRESOLVED"}
_ALLOWED_USAGE = {"CALL", "CALLABLE_BINDING"}
_DEFAULT_MANIFEST_PATH = Path(__file__).with_name("authority_manifest.v1.json")
_PRODUCT_FORBIDDEN_TARGET_MARKERS = (
    "benchmark_evaluator",
    "benchmark_runtime",
    "._private_eval",
    "core.engine:",
    "backend.main:",
    "mockengine",
)


class AuthorityManifestError(RuntimeError):
    """The checked-in authority manifest violates its structural contract."""


class AuthorityStartupError(RuntimeError):
    """The selected runtime mode cannot establish its required authority."""


@dataclass(frozen=True)
class AuthorityBinding:
    capability: str
    status: str
    target: str | None
    production_callers: tuple[str, ...]
    usage: str
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target,
            "production_callers": list(self.production_callers),
            "usage": self.usage,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class ProductMainlineAuthority:
    behavior_ir: AuthorityBinding
    obligation_source: AuthorityBinding
    planner: AuthorityBinding
    experiment_compiler: AuthorityBinding
    executor: AuthorityBinding
    evidence_pipeline: AuthorityBinding
    oracle: AuthorityBinding
    finding_pipeline: AuthorityBinding
    delivery_gate: AuthorityBinding
    release_decision: AuthorityBinding

    def entries(self) -> tuple[AuthorityBinding, ...]:
        return tuple(getattr(self, name) for name in AUTHORITY_SLOTS)

    def unresolved(self) -> tuple[str, ...]:
        return tuple(
            entry.capability
            for entry in self.entries()
            if entry.status != "RESOLVED"
        )

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {entry.capability: entry.as_dict() for entry in self.entries()}


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


def _target_parts(target: str) -> tuple[str, str]:
    module_name, _, attribute_path = str(target or "").partition(":")
    return module_name, attribute_path.split(".", 1)[0]


def _module_import_aliases(
    caller: Any,
    authority_target: str,
) -> tuple[set[str], set[str]]:
    """Resolve module-level/local aliases that can reference the target."""
    target_module, target_attribute = _target_parts(authority_target)
    caller_module_name = str(getattr(caller, "__module__", "") or "")
    caller_module = importlib.import_module(caller_module_name)
    module_tree = ast.parse(inspect.getsource(caller_module))
    direct_aliases: set[str] = set()
    module_aliases: set[str] = set()
    if target_module == caller_module_name:
        direct_aliases.add(target_attribute)

    target_tail = target_module.rsplit(".", 1)[-1]
    for node in ast.walk(module_tree):
        if isinstance(node, ast.ImportFrom):
            imported_module = node.module or ""
            module_matches = bool(imported_module) and (
                imported_module == target_module
                or target_module.endswith(f".{imported_module}")
            )
            for alias in node.names:
                if module_matches and alias.name == target_attribute:
                    direct_aliases.add(alias.asname or alias.name)
                # ``from . import contract_oracles as _outcome_oracles``
                if not imported_module and alias.name == target_tail:
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target_module:
                    module_aliases.add(alias.asname or alias.name.split(".")[-1])

    # Support a governed composition alias such as
    # ``evaluate_contract_oracle = _outcome_oracles.evaluate_contract_oracle``.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(module_tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            names: list[str] = []
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node.target, ast.Name):
                names = [node.target.id]
            if not names:
                continue
            if (
                isinstance(value, ast.Attribute)
                and value.attr == target_attribute
                and isinstance(value.value, ast.Name)
                and value.value.id in module_aliases
            ):
                for name in names:
                    if name not in direct_aliases:
                        direct_aliases.add(name)
                        changed = True
            elif isinstance(value, ast.Name) and value.id in direct_aliases:
                for name in names:
                    if name not in direct_aliases:
                        direct_aliases.add(name)
                        changed = True
    return direct_aliases, module_aliases


def _caller_uses_target(
    caller_target: str,
    authority_target: str,
    *,
    usage: str,
) -> bool:
    """Prove executable use of one authority from a declared production caller."""
    caller = load_target(caller_target)
    caller_tree = ast.parse(textwrap.dedent(inspect.getsource(caller)))
    direct_aliases, module_aliases = _module_import_aliases(caller, authority_target)
    _, target_attribute = _target_parts(authority_target)

    if usage == "CALL":
        for node in ast.walk(caller_tree):
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

    if usage == "CALLABLE_BINDING":
        for node in ast.walk(caller_tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in direct_aliases:
                    return True
            if (
                isinstance(node, ast.Attribute)
                and node.attr == target_attribute
                and isinstance(node.value, ast.Name)
                and node.value.id in module_aliases
            ):
                return True
        return False
    return False


def _binding_from_row(capability: str, row: dict[str, Any]) -> AuthorityBinding:
    return AuthorityBinding(
        capability=capability,
        status=str(row.get("status") or ""),
        target=row.get("target") if isinstance(row.get("target"), str) else None,
        production_callers=tuple(row.get("production_callers") or ()),
        usage=str(row.get("usage") or "CALL"),
        evidence=str(row.get("evidence") or "").strip(),
    )


def build_product_mainline_authority(
    manifest: dict[str, Any] | None = None,
) -> ProductMainlineAuthority:
    payload = manifest if manifest is not None else load_authority_manifest()
    slots = payload.get("slots") if isinstance(payload, dict) else None
    if not isinstance(slots, dict):
        raise AuthorityManifestError("authority_manifest_slots_missing")
    bindings = {
        slot: _binding_from_row(slot, slots.get(slot) if isinstance(slots.get(slot), dict) else {})
        for slot in AUTHORITY_SLOTS
    }
    return ProductMainlineAuthority(**bindings)


def _validate_product_target(capability: str, target: str) -> None:
    lowered = target.lower()
    if any(marker in lowered for marker in _PRODUCT_FORBIDDEN_TARGET_MARKERS):
        raise AuthorityManifestError(
            f"product_authority_target_forbidden:{capability}:{target}"
        )
    if capability == "delivery_gate" and "customer_delivery_gate_v2:" not in target:
        raise AuthorityManifestError("product_delivery_gate_must_use_v2")
    if capability == "release_decision" and ".release_gate:" not in target:
        raise AuthorityManifestError("product_release_decision_must_use_release_gate")


def validate_manifest_contract(
    manifest: dict[str, Any] | None = None,
    *,
    verify_production_usage: bool = True,
) -> dict[str, Any]:
    """Validate exact slots, loadability and real production-use evidence."""
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
        binding = _binding_from_row(slot, row)
        if binding.status not in _ALLOWED_STATUSES:
            raise AuthorityManifestError(f"authority_slot_status_invalid:{slot}")
        if binding.usage not in _ALLOWED_USAGE:
            raise AuthorityManifestError(f"authority_slot_usage_invalid:{slot}")
        if any(not isinstance(item, str) or not item for item in binding.production_callers):
            raise AuthorityManifestError(f"authority_slot_callers_invalid:{slot}")

        if binding.status == "UNRESOLVED":
            if binding.target is not None or binding.production_callers:
                raise AuthorityManifestError(
                    f"unresolved_authority_must_not_claim_target:{slot}"
                )
            unresolved.append(slot)
            continue

        if not binding.target:
            raise AuthorityManifestError(f"resolved_authority_target_missing:{slot}")
        if not binding.production_callers:
            raise AuthorityManifestError(
                f"resolved_authority_production_caller_missing:{slot}"
            )
        if not binding.evidence:
            raise AuthorityManifestError(f"resolved_authority_evidence_missing:{slot}")
        _validate_product_target(slot, binding.target)
        try:
            load_target(binding.target)
        except Exception as exc:
            raise AuthorityManifestError(
                f"resolved_authority_target_unloadable:{slot}:{binding.target}"
            ) from exc
        if verify_production_usage:
            for caller_target in binding.production_callers:
                try:
                    used = _caller_uses_target(
                        caller_target,
                        binding.target,
                        usage=binding.usage,
                    )
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


def resolve_authority_mode(mode: str | None = None) -> str:
    raw = str(mode if mode is not None else os.environ.get(AUTHORITY_MODE_ENV, "PRODUCT"))
    resolved = raw.strip().upper() or "PRODUCT"
    if resolved not in AUTHORITY_MODES:
        raise AuthorityStartupError(f"authority_mode_invalid:{resolved}")
    return resolved


def validate_product_mainline_authority(
    manifest: dict[str, Any] | None = None,
    *,
    mode: str | None = None,
    verify_production_usage: bool = False,
) -> dict[str, Any]:
    """Validate one runtime authority mode; PRODUCT requires all ten slots."""
    payload = manifest if manifest is not None else load_authority_manifest()
    contract = validate_manifest_contract(
        payload,
        verify_production_usage=verify_production_usage,
    )
    authority = build_product_mainline_authority(payload)
    authority_mode = resolve_authority_mode(mode)
    unresolved = list(authority.unresolved())
    if authority_mode == "PRODUCT" and unresolved:
        raise AuthorityStartupError(
            "missing_required_product_authority:" + ",".join(unresolved)
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "authority_mode": authority_mode,
        "required_count": len(AUTHORITY_SLOTS),
        "resolved_count": len(contract["resolved"]),
        "resolved": list(contract["resolved"]),
        "unresolved": unresolved,
        "strict_product": authority_mode == "PRODUCT",
        "slots": authority.as_dict(),
    }


def validate_resolved_authorities_for_startup(
    manifest: dict[str, Any] | None = None,
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible startup name for the strict product validator."""
    return validate_product_mainline_authority(
        manifest,
        mode=mode,
        verify_production_usage=False,
    )