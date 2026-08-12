"""Blind-runner facade preserving source security through OpenAPI round trips."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import blind_project_runner_mainline_base as _base
from .openapi_security_authority import openapi_operation_security_facts

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_merge_openapi_with_knowledge_asset = _base._merge_openapi_with_knowledge_asset


def _t(value: Any) -> str:
    return str(value or "").strip()


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _key(method: Any, path: Any) -> tuple[str, str]:
    return (_t(method).lower(), _t(path) or "/")


def _source_openapi_security(
    openapi: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    facts: dict[tuple[str, str], dict[str, Any]] = {}
    document = _d(openapi)
    for path, raw_path_item in _d(document.get("paths")).items():
        path_item = _d(raw_path_item)
        for method, raw_operation in path_item.items():
            if not isinstance(raw_operation, dict):
                continue
            method_l = _t(method).lower()
            if method_l not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            facts[_key(method_l, path)] = openapi_operation_security_facts(
                document, raw_operation
            )
    return facts


def _asset_security_assertions(
    asset: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    assertions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in _l(_d(asset).get("interfaces")):
        if not isinstance(row, dict):
            continue
        authority = _t(row.get("security_provenance_authority"))
        mode = _t(row.get("security_effective_mode")).lower()
        if (
            row.get("security_provenance_valid") is not True
            or authority
            not in {"OPENAPI_OPERATION_SECURITY", "OPENAPI_DOCUMENT_SECURITY_INHERITED"}
            or mode not in {"anonymous", "authenticated"}
        ):
            continue
        assertions.setdefault(
            _key(row.get("method"), row.get("path")), []
        ).append(
            {
                "mode": mode,
                "scope": _t(row.get("security_declaration_scope")),
                "security": deepcopy(_l(row.get("security"))),
                "authority": authority,
                "source_id": _t(row.get("source_id")),
            }
        )
    return assertions


def _heuristic_hint(operation: dict[str, Any]) -> None:
    operation["x-qualibug-security-hint"] = {
        "kind": "auth_keyword",
        "authority": "heuristic_only",
        "execution_authority": False,
    }


def _apply_security_facts(
    merged: dict[str, Any],
    operation: dict[str, Any],
    facts: dict[str, Any],
) -> None:
    scope = _t(facts.get("security_declaration_scope"))
    selected = deepcopy(_l(facts.get("security")))
    if scope == "operation":
        operation["security"] = selected
        operation.pop("x-qualibug-security-hint", None)
        return
    if scope == "document":
        merged["security"] = selected
        operation.pop("security", None)
        operation.pop("x-qualibug-security-hint", None)
        return
    # Source exists but declares no security at either level. The old merge may
    # have created bearerAuth from route/summary keywords; retain that only as
    # a non-executable hint, never as OpenAPI contract authority.
    if "security" in operation:
        operation.pop("security", None)
        _heuristic_hint(operation)


def _merge_openapi_with_knowledge_asset(
    openapi: dict[str, Any],
    asset: dict[str, Any] | None,
) -> dict[str, Any]:
    source = _d(openapi)
    merged = _original_merge_openapi_with_knowledge_asset(openapi, asset)
    if not isinstance(merged, dict):
        return merged

    source_facts = _source_openapi_security(source)
    asset_assertions = _asset_security_assertions(_d(asset))

    for path, raw_path_item in _d(merged.get("paths")).items():
        path_item = _d(raw_path_item)
        for method, raw_operation in path_item.items():
            if not isinstance(raw_operation, dict):
                continue
            method_l = _t(method).lower()
            if method_l not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            operation = raw_operation
            identity = _key(method_l, path)

            # The original OpenAPI document is the highest-fidelity source and
            # wins even when the selected value is an explicit empty list.
            if identity in source_facts:
                _apply_security_facts(merged, operation, source_facts[identity])
                continue

            rows = asset_assertions.get(identity, [])
            if rows:
                modes = {row["mode"] for row in rows}
                if len(modes) > 1:
                    operation.pop("security", None)
                    operation["x-qualibug-security-provenance-conflict"] = True
                    continue
                operation.pop("x-qualibug-security-provenance-conflict", None)
                chosen = rows[0]
                _apply_security_facts(
                    merged,
                    operation,
                    {
                        "security_declaration_scope": chosen["scope"],
                        "security": chosen["security"],
                    },
                )
                continue

            # Asset-only/markdown-only rows have no structured security source.
            # Undo the legacy keyword rewrite but preserve it as a visible hint.
            if "security" in operation:
                operation.pop("security", None)
                _heuristic_hint(operation)

    return merged


_base._merge_openapi_with_knowledge_asset = _merge_openapi_with_knowledge_asset


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
