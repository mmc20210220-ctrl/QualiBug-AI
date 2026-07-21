"""Source-grounded scenarios for the existing V12 behavior graph.

No default business entity, API path, actor, request body or cleanup action is
created here. Missing executable prerequisites are represented as plan gaps.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..auto_test_data_factory import _markdown_request_example
from ..business_state_graph import BusinessStateGraph, StateEdge, StateTransition, _api_facts, behavior_slice_id
from ..real_id_resolver import (
    alternate_collection_paths,
    body_field_collection_paths,
    extract_body_binding_fields,
    extract_fields_for_path,
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
    collection_path,
)

# First-class System Behavior Space scenario enricher — no method replacement.
ScenarioEnricher = Callable[..., Any]
_SCENARIO_ENRICHER: ScenarioEnricher | None = None


def register_scenario_enricher(hook: ScenarioEnricher | None) -> None:
    """Post-``_invariant_from_meta`` enricher for system-behavior slices."""
    global _SCENARIO_ENRICHER
    _SCENARIO_ENRICHER = hook


def clear_scenario_enricher() -> None:
    register_scenario_enricher(None)


@dataclass
class ScenarioStep:
    order: int
    action: str
    api_method: str = ""
    api_path: str = ""
    body_template: dict[str, Any] = field(default_factory=dict)
    extract_from_response: list[str] = field(default_factory=list)
    extract_where: dict[str, Any] = field(default_factory=dict)
    expected_status: int = 0
    actor: str = ""
    body_provenance: str = ""


@dataclass
class ExecutableScenario:
    id: str
    title: str
    description: str = ""
    category: str = "state_machine"
    severity: str = "P2"
    entity: str = ""
    preconditions: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    steps: list[ScenarioStep] = field(default_factory=list)
    expected_state: str = ""
    oracle_rules: list[str] = field(default_factory=list)
    cleanup_steps: list[ScenarioStep] = field(default_factory=list)
    is_forbidden_path: bool = False
    is_boundary_path: bool = False
    is_concurrent: bool = False
    confidence: float = 0.0
    actor_token: str = ""
    execution_policy: str = "plan_only_requires_fixture"
    evidence_gaps: list[str] = field(default_factory=list)
    source_refs: list[dict[str, str]] = field(default_factory=list)
    behavior_slice_id: str = ""
    behavior_slice_kind: str = ""
    discovery_round: int = 1
    selection_origin: str = ""
    runtime_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "severity": self.severity,
            "entity": self.entity,
            "preconditions": self.preconditions,
            "actors": self.actors,
            "steps": [
                {
                    "order": step.order,
                    "action": step.action,
                    "method": step.api_method,
                    "path": step.api_path,
                    "body": step.body_template,
                    "extract": step.extract_from_response,
                    "extract_where": step.extract_where,
                    "expected": step.expected_status,
                    "actor": step.actor,
                    "body_provenance": step.body_provenance,
                }
                for step in self.steps
            ],
            "expected_state": self.expected_state,
            "oracle_rules": self.oracle_rules,
            "cleanup": [step.action for step in self.cleanup_steps],
            "flags": {
                "forbidden": self.is_forbidden_path,
                "boundary": self.is_boundary_path,
                "concurrent": self.is_concurrent,
            },
            "confidence": self.confidence,
            "execution_policy": self.execution_policy,
            "evidence_gaps": self.evidence_gaps,
            "source_refs": self.source_refs,
            "behavior_slice_id": self.behavior_slice_id,
            "behavior_slice_kind": self.behavior_slice_kind,
            "discovery_round": self.discovery_round,
            "selection_origin": self.selection_origin,
            "runtime_hints": self.runtime_hints,
        }


