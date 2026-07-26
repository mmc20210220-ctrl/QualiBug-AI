"""Which observation adapters are available for a target — declared, never inferred.

``compile_observer_requirements`` refuses an observer whose adapter is not in the available
set, which is the correct fail-closed behaviour. But the set was hardcoded ``{"http_api"}``
at every site, so registering a non-http observer could never make it usable on the main
chain: the observer existed, compiled in a test that passed the wider set explicitly, and
was refused by the production compiler.

This module is the single place that answers "what can this target be observed through".

DECLARED, NOT INFERRED
======================
An adapter becomes available because the CUSTOMER declared the thing it observes, in the
same ``multi_service_config.json`` the environment gate reads. Specifically ``db_sql``
becomes available when a service declares a ``db`` block.

It is never inferred from a hostname, a port, a URL scheme or the presence of a driver.
``target_policy`` states the same rule for write safety — "localhost or an environment name
must never imply write safety" — and the reason generalizes: inferring that a database
exists because port 5432 answers would mean reading a store the customer never pointed the
product at.

``http_api`` is always available. It is the adapter the product is defined around, and every
target reached by a base_url is reachable through it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Always available: the product is defined around an HTTP target.
BASELINE_ADAPTERS = frozenset({"http_api"})

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
            # A malformed config must not silently widen or narrow the adapter set. Report
            # it and fall back to the baseline, which is the fail-closed direction.
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
    """Adapters this target may be observed through.

    Returns at least ``BASELINE_ADAPTERS``. An adapter is added only when the customer
    declared what it observes; an empty or unreadable config yields the baseline, so the
    failure direction is always "fewer adapters", never "more".

    ``runtime_contract`` may name additional adapters under ``declared_adapters``, for a
    deployment that configures capability outside the project file. Those are still
    declarations, not inferences, and they are intersected with the adapters this module
    knows how to gate — an unknown adapter name is ignored rather than trusted.
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
    return f"adapter_unknown:{adapter}"


# Adapter name -> Behavior IR observation-surface name. The IR built its surfaces from
# the literal ``available = (surface_id == "http_api")``, so a project with a declared
# and reachable database still carried ``db_snapshot: available=false`` while this
# module was already returning ``db_sql`` to the experiment compiler. Two parts of the
# same run disagreed about the same capability, and the IR's answer is the one the
# observer gate reads -- which is why data-layer assertions blocked as
# BLOCKED_MISSING_OBSERVER on a target whose database was configured and queryable.
ADAPTER_TO_OBSERVATION_SURFACE: dict[str, str] = {
    "http_api": "http_api",
    "db_sql": "db_snapshot",
    "ui_browser": "ui_browser",
}

# The capability node an available adapter justifies, so downstream consumers that read
# capabilities rather than surfaces see the same truth.
ADAPTER_TO_CAPABILITY: dict[str, str] = {
    "http_api": "http_execute",
    "db_sql": "db_read",
    "ui_browser": "ui_execute",
}


def observation_surfaces_for_adapters(adapters: Any) -> dict[str, bool]:
    """Map resolved adapters to ``{surface_name: available}``.

    Every known surface appears, so a caller receives an explicit False rather than a
    missing key -- an absent surface would read as "not considered" instead of
    "considered and unavailable".
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
