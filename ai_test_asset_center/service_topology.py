from __future__ import annotations

"""
Multi-Service Topology Auto-Discovery & Contract Testing.

Advanced upgrade over enterprise_project_config.py:
1. Auto-discovery: parse OpenAPI specs → infer service dependencies from $ref links
2. Contract test generation: for each cross-service dependency, generate executable tests
3. Dependency graph: build + visualize the full service topology
4. Impact blast radius: when service X changes, what tests must run?
"""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _safe_project_id, _load_json, _read_text, _write_json


def auto_discover_topology(
    services: list[dict[str, Any]],
) -> dict[str, Any]:
    """Auto-discover service topology from OpenAPI specs.

    For each service, parse its OpenAPI spec and detect:
    - Outbound dependencies: paths that reference other services' entities
    - Inbound dependencies: who depends on this service
    - Shared schemas: entities used by multiple services
    - Circular dependencies: potential deadlock risks
    """
    topology: dict[str, dict[str, Any]] = {}
    entity_ownership: dict[str, str] = {}  # entity_name → owning_service
    all_entities: dict[str, set[str]] = defaultdict(set)  # service → {entities}

    for svc in services:
        name = svc.get("name", "unknown")
        spec_path = svc.get("openapi_spec", "")
        topology[name] = {
            "name": name,
            "base_url": svc.get("base_url", ""),
            "entities": [],
            "depends_on": [],
            "depended_by": [],
            "shared_entities": [],
            "endpoints": [],
        }

        # Parse OpenAPI spec
        spec = _read_text(Path(spec_path)) if spec_path and Path(spec_path).exists() else ""
        if not spec:
            continue

        # Extract entities from paths (e.g., /orders, /payments, /inventory/{sku})
        paths = re.findall(r"/(\w+)", spec)
        entities = set(p for p in paths if p not in {"api", "v1", "v2", "v3", "internal", "public", "health", "metrics"})
        topology[name]["entities"] = sorted(entities)
        all_entities[name] = entities

        for entity in entities:
            if entity not in entity_ownership:
                entity_ownership[entity] = name

        # Extract endpoints
        endpoints = []
        for match in re.finditer(r"(GET|POST|PUT|DELETE|PATCH)\s+/(\S+)", spec):
            endpoints.append({"method": match.group(1), "path": "/" + match.group(2)})
        topology[name]["endpoints"] = endpoints[:50]

    # Detect cross-service dependencies
    for name, info in topology.items():
        for other_name, other_info in topology.items():
            if name == other_name:
                continue
            # Service A depends on Service B if A references B's entities
            for entity in info["entities"]:
                if entity in other_info["entities"] and entity_ownership.get(entity) == other_name:
                    if other_name not in info["depends_on"]:
                        info["depends_on"].append(other_name)
                    if name not in other_info["depended_by"]:
                        other_info["depended_by"].append(name)
                    if entity not in info["shared_entities"]:
                        info["shared_entities"].append(entity)

    # Detect circular dependencies
    circular = _detect_circular_deps(topology)

    return {
        "services": topology,
        "service_count": len(topology),
        "entity_ownership": entity_ownership,
        "cross_service_dependencies": sum(len(info["depends_on"]) for info in topology.values()),
        "circular_dependencies": circular,
    }


def _detect_circular_deps(topology: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Detect circular dependencies using DFS."""
    circular: list[list[str]] = []
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> None:
        if node in stack:
            cycle = stack[stack.index(node):] + [node]
            circular.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        stack.append(node)
        for dep in topology.get(node, {}).get("depends_on", []):
            if dep in topology:
                dfs(dep)
        stack.pop()

    for node in topology:
        if node not in visited:
            dfs(node)
    return circular


def generate_contract_tests(
    topology: dict[str, Any],
    cross_service_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate executable contract tests for cross-service dependencies.

    For each cross-service contract, produce:
    - Setup: what data to create in the source service
    - Trigger: what action triggers the dependency
    - Verify: how to check the target service
    - Cleanup: how to restore state
    """
    tests: list[dict[str, Any]] = []

    for contract in cross_service_contracts:
        from_svc = contract.get("from_service", "")
        to_svc = contract.get("to_service", "")
        invariant = contract.get("invariant", "")
        verification = contract.get("verification", "")

        test = {
            "test_id": f"contract_{contract.get('contract_id', 'unknown')}",
            "name": f"跨服务契约: {from_svc} → {to_svc}",
            "invariant": invariant,
            "services_involved": [from_svc, to_svc],
            "environment": "test_only",
            "steps": [],
        }

        # Parse verification instructions into test steps
        if verification:
            parts = verification.split("→")
            for i, part in enumerate(parts):
                part = part.strip()
                if part.startswith("GET "):
                    test["steps"].append({
                        "step": i + 1,
                        "action": "http_get",
                        "endpoint": part.replace("GET ", "").strip(),
                        "assert": "status == 200",
                    })
                elif part.startswith("POST "):
                    test["steps"].append({
                        "step": i + 1,
                        "action": "http_post",
                        "endpoint": part.replace("POST ", "").strip(),
                        "note": "sandbox_required_if_write",
                    })
                elif "assert" in part.lower():
                    test["steps"].append({
                        "step": i + 1,
                        "action": "assert",
                        "condition": part,
                    })
                else:
                    test["steps"].append({
                        "step": i + 1,
                        "action": "verify",
                        "description": part,
                    })

        tests.append(test)

    return tests


def impact_blast_radius(
    changed_service: str,
    topology: dict[str, Any],
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Calculate the blast radius when a service changes.

    Returns:
    - Direct dependents: services that directly call this service
    - Transitive dependents: full closure of who might be affected
    - Affected contracts: cross-service contracts that need re-validation
    - Test priority: which tests to run first (closest to the change)
    """
    services = topology.get("services", {})
    direct = services.get(changed_service, {}).get("depended_by", [])
    transitives: set[str] = set(direct)
    queue = list(direct)
    while queue:
        svc = queue.pop(0)
        for dep in services.get(svc, {}).get("depended_by", []):
            if dep not in transitives:
                transitives.add(dep)
                queue.append(dep)

    # Priority: changed service first, then direct dependents, then transitives
    test_order = [changed_service] + direct + sorted(transitives - set(direct))

    return {
        "changed_service": changed_service,
        "direct_dependents": direct,
        "transitive_dependents": sorted(transitives),
        "total_affected_services": len(transitives) + 1,
        "test_execution_order": test_order,
        "risk_level": "high" if len(transitives) > 2 else "medium" if direct else "low",
    }
