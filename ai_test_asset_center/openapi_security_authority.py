"""Source-truthful OpenAPI security declaration provenance.

A normalized ``security=[]`` cannot prove anonymous access because multiple
parsers historically collapse a missing declaration to the same value.  This
module preserves declaration presence separately from the effective security
requirements and exposes one fail-closed anonymous-access predicate shared by
planning and exploration.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _t(value: Any) -> str:
    return str(value or "").strip()


def openapi_operation_security_facts(
    document: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any]:
    """Return security provenance without interpreting absence as public."""

    doc = _d(document)
    op = _d(operation)
    operation_declared = "security" in op
    document_declared = "security" in doc
    inherited = not operation_declared and document_declared
    if operation_declared:
        selected = op.get("security")
        scope = "operation"
        authority = "OPENAPI_OPERATION_SECURITY"
    elif document_declared:
        selected = doc.get("security")
        scope = "document"
        authority = "OPENAPI_DOCUMENT_SECURITY_INHERITED"
    else:
        selected = None
        scope = "none"
        authority = "OPENAPI_SECURITY_UNDECLARED"

    valid = selected is None or isinstance(selected, list)
    requirements = deepcopy(selected) if isinstance(selected, list) else []
    anonymous_allowed = bool(
        isinstance(selected, list)
        and (
            len(selected) == 0
            or any(isinstance(requirement, dict) and not requirement for requirement in selected)
        )
    )
    if selected is None:
        mode = "unknown"
    elif not isinstance(selected, list):
        mode = "invalid"
    elif anonymous_allowed:
        mode = "anonymous"
    else:
        mode = "authenticated"

    return {
        "security": requirements,
        "security_operation_declaration_present": operation_declared,
        "security_document_declaration_present": document_declared,
        "security_inherited_from_document": inherited,
        "security_operation_anonymous_override": bool(operation_declared and anonymous_allowed),
        "security_effective_anonymous": anonymous_allowed,
        "security_effective_mode": mode,
        "security_declaration_scope": scope,
        "security_provenance_authority": authority,
        "security_provenance_valid": valid,
    }


def operation_has_source_declared_anonymous_access(operation: dict[str, Any]) -> bool:
    """Return True only when structured source provenance proves anonymity."""

    row = _d(operation)
    if row.get("security_provenance_conflict") is True:
        return False
    if row.get("security_source_declared_anonymous") is True:
        return True
    return bool(
        row.get("security_provenance_valid") is True
        and row.get("security_effective_anonymous") is True
        and _t(row.get("security_provenance_authority"))
        in {"OPENAPI_OPERATION_SECURITY", "OPENAPI_DOCUMENT_SECURITY_INHERITED"}
    )


def _json_pointer_token(value: Any) -> str:
    return _t(value).replace("~", "~0").replace("/", "~1")


def stamp_openapi_operation_security(
    rows: Iterable[dict[str, Any]],
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Add exact security facts to method/path operation rows in place."""

    output = [row for row in rows if isinstance(row, dict)]
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in output:
        by_key.setdefault((_t(row.get("method")).upper(), _t(row.get("path"))), []).append(row)
    for path, raw_path_item in _d(_d(document).get("paths")).items():
        path_item = _d(raw_path_item)
        for method, raw_operation in path_item.items():
            if not isinstance(raw_operation, dict):
                continue
            key = (_t(method).upper(), _t(path))
            for row in by_key.get(key, []):
                facts = openapi_operation_security_facts(document, raw_operation)
                if facts["security_declaration_scope"] == "operation":
                    facts["security_source_pointer"] = (
                        f"/paths/{_json_pointer_token(path)}/"
                        f"{_json_pointer_token(str(method).lower())}/security"
                    )
                elif facts["security_declaration_scope"] == "document":
                    facts["security_source_pointer"] = "/security"
                else:
                    facts["security_source_pointer"] = ""
                row.update(facts)
    return output


def project_operation_security_provenance(
    model: dict[str, Any],
    *,
    asset: dict[str, Any] | None = None,
    api_operations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project source security facts onto Behavior IR without guessing conflicts."""

    source_rows: list[dict[str, Any]] = []
    source_rows.extend(row for row in (api_operations or []) if isinstance(row, dict))
    data = _d(asset)
    for key in ("operations", "interfaces"):
        source_rows.extend(row for row in _l(data.get(key)) if isinstance(row, dict))

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in source_rows:
        if not _t(row.get("security_provenance_authority")):
            continue
        key = (
            _t(row.get("method") or row.get("http_method")).upper(),
            _t(row.get("path") or row.get("endpoint") or row.get("url")),
        )
        if key[0] and key[1]:
            by_key.setdefault(key, []).append(row)

    for operation in _l(_d(model).get("operations")):
        if not isinstance(operation, dict):
            continue
        key = (
            _t(operation.get("method")).upper(),
            _t(operation.get("path") or operation.get("raw_path")),
        )
        candidates = by_key.get(key, [])
        assertions: list[dict[str, Any]] = []
        for row in candidates:
            mode = _t(row.get("security_effective_mode")).lower()
            valid = row.get("security_provenance_valid") is True
            if not valid or mode not in {"anonymous", "authenticated"}:
                continue
            assertions.append(
                {
                    "source_id": _t(row.get("source_id")),
                    "mode": mode,
                    "authority": _t(row.get("security_provenance_authority")),
                    "scope": _t(row.get("security_declaration_scope")),
                    "operation_declaration_present": row.get("security_operation_declaration_present") is True,
                    "document_declaration_present": row.get("security_document_declaration_present") is True,
                    "inherited_from_document": row.get("security_inherited_from_document") is True,
                    "source_pointer": _t(row.get("security_source_pointer")),
                }
            )
        if not assertions:
            operation["security_source_declared_anonymous"] = False
            operation["security_provenance_conflict"] = False
            continue
        modes = {row["mode"] for row in assertions}
        conflict = len(modes) > 1
        anonymous = modes == {"anonymous"}
        operation.update(
            {
                "security_source_declared_anonymous": bool(anonymous and not conflict),
                "security_provenance_conflict": conflict,
                "security_provenance_authority": "OPENAPI_SOURCE_SECURITY_CONSENSUS",
                "security_provenance": assertions,
                "security_operation_declaration_present": any(
                    row["operation_declaration_present"] for row in assertions
                ),
                "security_document_declaration_present": any(
                    row["document_declaration_present"] for row in assertions
                ),
                "security_inherited_from_document": all(
                    row["inherited_from_document"] for row in assertions
                ),
            }
        )
    return model


__all__ = [
    "openapi_operation_security_facts",
    "operation_has_source_declared_anonymous_access",
    "stamp_openapi_operation_security",
    "project_operation_security_provenance",
]
