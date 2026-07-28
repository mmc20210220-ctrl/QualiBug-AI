"""Which observation adapters are available for a target — declared, never inferred.

``compile_observer_requirements`` refuses an observer whose adapter is not in the
available set. Target adapters are enabled only by customer declarations. The
internal process ledger is a separate baseline adapter because the governed
executor itself creates it for every formal execution; it is not inferred from
the customer target or network.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Always available product-owned surfaces. ``http_api`` is the target transport;
# ``process_ledger`` is the governed executor's own immutable step timeline.
BASELINE_ADAPTERS = frozenset({"http_api", "process_ledger"})

# adapter -> what a service must declare for it to become available.
DECLARATION_REQUIRED: dict[str, str] = {
    "db_sql": "services[].db",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _config_candidates(root: Path, project: str) -> list[Path]:
    """Same customer-declared files the environment gate reads, in the same order."""
    return [
        root / "platform_workspace" / project / "multi_service_config.json",
        root / "platform_inputs" / project / "multi_service_config.json",
    ]


def _declared_config(root: Path, project: str) -> dict[str, Any]:
    for path in _config_candidates(root, project):
        if not path.is_file():
            continue
        try:
            return _dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.warning(
                "adapter capability: declared config unreadable at %s (%s); "
                "falling back to baseline adapters",
                path.name, type(exc).__name__,
            )
            return {}
    return {}


def resolve_available_adapters(
    root: Path | str,
    project: str,
    runtime_contract: dict[str, Any] | None = None,
) -> frozenset[str]:
    """Adapters this target and the governed executor may be observed through.

    Target-facing adapters are added only when the customer declares what they
    observe. Product-owned baseline adapters are always available because their
    evidence is generated inside the formal executor rather than discovered on
    the target.
    """
    available = set(BASELINE_ADAPTERS)
    config = _declared_config(Path(root), project)

    if any(_dict(_dict(service).get("db")) for service in _list(config.get("services"))):
        available.add("db_sql")

    for name in _list(_dict(runtime_contract).get("declared_adapters")):
        adapter = _text(name)
        if adapter in DECLARATION_REQUIRED or adapter in BASELINE_ADAPTERS:
            available.add(adapter)
        elif adapter:
            logger.warning(
                "adapter capability: runtime contract declares unknown adapter %r; ignored",
                adapter,
            )
    return frozenset(available)


def missing_declaration_reason(adapter: str) -> str:
    """Why an adapter is unavailable, in terms of what the customer would declare."""
    requirement = DECLARATION_REQUIRED.get(_text(adapter))
    if requirement:
        return f"adapter_not_declared:{adapter}:requires:{requirement}"
    if _text(adapter) in BASELINE_ADAPTERS:
        return f"baseline_adapter_unavailable:{adapter}"
    return f"adapter_unknown:{adapter}"


# Adapter name -> Behavior IR observation-surface name.
ADAPTER_TO_OBSERVATION_SURFACE: dict[str, str] = {
    "http_api": "http_api",
    "process_ledger": "process_timeline",
    "db_sql": "db_snapshot",
    "ui_browser": "ui_browser",
}

# The capability node an available adapter justifies.
ADAPTER_TO_CAPABILITY: dict[str, str] = {
    "http_api": "http_execute",
    "process_ledger": "process_timeline_observe",
    "db_sql": "db_read",
    "ui_browser": "ui_execute",
}


def observation_surfaces_for_adapters(adapters: Any) -> dict[str, bool]:
    """Map resolved adapters to ``{surface_name: available}``.

    Every known surface appears, so a caller receives an explicit False rather
    than a missing key.
    """
    resolved = {_text(name) for name in _list(list(adapters or []))} if adapters else set()
    return {
        surface: adapter in resolved
        for adapter, surface in ADAPTER_TO_OBSERVATION_SURFACE.items()
    }


def capabilities_for_adapters(adapters: Any) -> list[str]:
    """Capability names justified by the resolved adapters, in a stable order."""
    resolved = {_text(name) for name in _list(list(adapters or []))} if adapters else set()
    return sorted(
        capability
        for adapter, capability in ADAPTER_TO_CAPABILITY.items()
        if adapter in resolved
    )
