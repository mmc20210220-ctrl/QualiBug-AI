"""Fail-closed loader for project-declared multi-service execution topology."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .project_runtime_config import load_real_project_config
from .service_topology_execution_authority import build_service_topology
from .target_policy import normalize_base_url


BLOCKED_SERVICE_TOPOLOGY_INVALID = "BLOCKED_SERVICE_TOPOLOGY_INVALID"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _entry_url(value: Any) -> str:
    if isinstance(value, str):
        return _text(value)
    row = _dict(value)
    return _text(
        row.get("base_url")
        or row.get("approved_base_url")
        or row.get("url")
        or row.get("endpoint")
        or row.get("endpoint_ref")
    )


def load_guarded_project_service_topology(
    project: str,
    root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return topology plus one explicit validation receipt.

    Absence of ``multi_service`` is a valid single-target project. Once a
    services map is declared, malformed entries cannot silently degrade to the
    single-target path because that would send an experiment to the wrong
    service URL.
    """

    try:
        config = load_real_project_config(project, root)
    except Exception as exc:
        return {}, {
            "status": "BLOCKED",
            "reason_code": BLOCKED_SERVICE_TOPOLOGY_INVALID,
            "detail": f"project_runtime_config_unreadable:{type(exc).__name__}:{exc}",
        }

    if "multi_service" not in config or config.get("multi_service") in (None, {}, False):
        return {}, {"status": "NOT_APPLICABLE", "reason_code": "", "detail": ""}

    raw_multi = config.get("multi_service")
    if not isinstance(raw_multi, dict):
        return {}, {
            "status": "BLOCKED",
            "reason_code": BLOCKED_SERVICE_TOPOLOGY_INVALID,
            "detail": "multi_service_must_be_object",
        }
    if raw_multi.get("enabled") is False:
        return {}, {"status": "NOT_APPLICABLE", "reason_code": "", "detail": "disabled"}

    if "services" not in raw_multi:
        if raw_multi.get("enabled") is True:
            return {}, {
                "status": "BLOCKED",
                "reason_code": BLOCKED_SERVICE_TOPOLOGY_INVALID,
                "detail": "multi_service_enabled_without_services",
            }
        return {}, {"status": "NOT_APPLICABLE", "reason_code": "", "detail": "services_not_declared"}

    raw_services = raw_multi.get("services")
    if not isinstance(raw_services, dict):
        return {}, {
            "status": "BLOCKED",
            "reason_code": BLOCKED_SERVICE_TOPOLOGY_INVALID,
            "detail": "multi_service_services_must_be_object",
        }

    invalid: list[str] = []
    for raw_name, raw_value in raw_services.items():
        name = _text(raw_name)
        url = _entry_url(raw_value)
        try:
            normalized = normalize_base_url(url) if url else ""
        except ValueError:
            normalized = ""
        if not name:
            invalid.append("service_name_missing")
        elif not normalized:
            invalid.append(f"service_url_invalid:{name}")
    if invalid:
        return {}, {
            "status": "BLOCKED",
            "reason_code": BLOCKED_SERVICE_TOPOLOGY_INVALID,
            "detail": ";".join(invalid[:20]),
        }

    topology = build_service_topology(config)
    if len(topology) != len(raw_services):
        return {}, {
            "status": "BLOCKED",
            "reason_code": BLOCKED_SERVICE_TOPOLOGY_INVALID,
            "detail": "service_topology_normalization_loss",
        }
    return topology, {
        "status": "VALID",
        "reason_code": "",
        "detail": "",
        "service_count": len(topology),
        "service_refs": sorted(topology),
    }
