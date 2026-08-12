"""Project-context facade with source-truthful OpenAPI security semantics."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import project_context_compiler_mainline_base as _base
from .openapi_security_authority import openapi_operation_security_facts

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_BaseProjectContextCompiler = _base.ProjectContextCompiler


class ProjectContextCompiler(_BaseProjectContextCompiler):
    """Preserve explicit security overrides after the historical extractor."""

    def _extract_api_capabilities(self, spec: dict, entities: list[Any]):
        capabilities = super()._extract_api_capabilities(spec, entities)
        paths = spec.get("paths") if isinstance(spec, dict) else None
        paths = paths if isinstance(paths, dict) else {}
        for capability in capabilities:
            path = str(getattr(capability, "path", "") or "")
            method = str(getattr(capability, "method", "") or "").lower()
            path_item = paths.get(path)
            operation = path_item.get(method) if isinstance(path_item, dict) else None
            if not isinstance(operation, dict):
                continue
            facts = openapi_operation_security_facts(spec, operation)
            capability.security = deepcopy(facts["security"])
            evidence = list(getattr(capability, "evidence", []) or [])
            evidence.append({
                "kind": "openapi_security_provenance",
                "authority": facts["security_provenance_authority"],
                "effective_mode": facts["security_effective_mode"],
                "operation_declaration_present": facts["security_operation_declaration_present"],
                "document_declaration_present": facts["security_document_declaration_present"],
                "inherited_from_document": facts["security_inherited_from_document"],
                "effective_anonymous": facts["security_effective_anonymous"],
            })
            capability.evidence = evidence
        return capabilities


_base.ProjectContextCompiler = ProjectContextCompiler


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
