"""Input-grounded candidate facade with shared OpenAPI security authority."""
from __future__ import annotations

from typing import Any

from . import input_grounded_candidate_compiler_mainline_base as _base
from .openapi_security_authority import openapi_operation_security_facts

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_parse_openapi_endpoints = _base.parse_openapi_endpoints
_original_rule_lookup = _base._rule_lookup


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _t(value: Any) -> str:
    return str(value or "").strip()


def parse_openapi_endpoints(input_dir):
    spec = _base._load_openapi(input_dir)
    endpoints = _original_parse_openapi_endpoints(input_dir)
    by_key = {
        (_t(endpoint.method).lower(), _t(endpoint.path)): endpoint
        for endpoint in endpoints
    }
    for path, raw_path_item in _d(spec.get("paths")).items():
        path_item = _d(raw_path_item)
        for method, raw_operation in path_item.items():
            if not isinstance(raw_operation, dict):
                continue
            endpoint = by_key.get((_t(method).lower(), _t(path)))
            if endpoint is None:
                continue
            facts = openapi_operation_security_facts(spec, raw_operation)
            checks = [
                str(check)
                for check in (getattr(endpoint, "checks", []) or [])
                if str(check) != "auth"
            ]
            if facts["security_effective_mode"] == "authenticated":
                checks.append("auth")
            endpoint.checks = sorted(dict.fromkeys(checks))
    return endpoints


def _rule_lookup(rules):
    """Return the concrete rule object expected by candidate generation.

    The mainline helper groups duplicate rule codes into lists, while the
    candidate loop reads ``rule_text`` directly from the lookup result.  That
    type mismatch silently erased rule text from every focused business-rule
    probe.  Preserve first-source ordering and expose the concrete rule here;
    supporting citations still retain all matching rules through ``_rule_quotes``.
    """
    grouped = _original_rule_lookup(rules)
    return {
        code: (matches[0] if isinstance(matches, list) and matches else matches)
        for code, matches in grouped.items()
    }


_base.parse_openapi_endpoints = parse_openapi_endpoints
_base._rule_lookup = _rule_lookup


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
