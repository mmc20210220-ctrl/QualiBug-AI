"""Source-grounded scenarios for the existing V12 behavior graph.

No default business entity, API path, actor, request body or cleanup action is
created here. Missing executable prerequisites are represented as plan gaps.

A runtime scenario becomes executable only when the caller supplies an explicit
runtime_scenario_contract with customer-approved actors, steps and execution
policy. The default path remains plan-only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .business_state_graph import BusinessStateGraph, StateTransition, behavior_slice_id


_READ_ONLY_METHODS = {"GET", "HEAD"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_ALLOWED_POLICIES = {"safe_read_only", "approved_sandbox_write", "runtime_approved"}


@dataclass
class ScenarioStep:
    order: int
    action: str
    api_method: str = ""
    api_path: str = ""
    body_template: dict[str, Any] = field(default_factory=dict)
    extract_from_response: list[str] = field(default_factory=list)
    expected_status: int = 0
    actor: str = ""


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
                    "expected": step.expected_status,
                    "actor": step.actor,
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
        }


class SemanticScenarioGenerator:
    """Plan only source-backed obligations selected by the incremental scheduler."""

    def generate(
        self,
        graphs: dict[str, BusinessStateGraph],
        api_doc: str = "",
        active_slice_ids: set[str] | None = None,
        discovery_round: int = 1,
        runtime_scenario_contract: dict[str, Any] | None = None,
    ) -> list[ExecutableScenario]:
        del api_doc  # Source bindings already live on the graph; never infer another route here.
        round_number = max(1, int(discovery_round or 1))
        results: list[ExecutableScenario] = []
        contracted = self._contract_scenarios(runtime_scenario_contract or {}, active_slice_ids, round_number)
        results.extend(contracted)
        contracted_slice_ids = {item.behavior_slice_id for item in contracted if item.behavior_slice_id}
        for entity, graph in sorted((graphs or {}).items()):
            if not isinstance(graph, BusinessStateGraph):
                continue
            for transition in graph.transitions:
                item = self._transition(entity, transition, round_number)
                if item.behavior_slice_id in contracted_slice_ids:
                    continue
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
            for state, node in graph.states.items():
                for invariant in node.invariants:
                    item = self._invariant(entity, state, invariant, node.source_refs, round_number)
                    if item.behavior_slice_id in contracted_slice_ids:
                        continue
                    if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                        results.append(item)
        deduped: list[ExecutableScenario] = []
        seen: set[str] = set()
        for item in results:
            fingerprint = f"{item.behavior_slice_id}|{item.entity}|{item.title}|{item.expected_state}|{item.execution_policy}"
            if fingerprint not in seen:
                seen.add(fingerprint)
                deduped.append(item)
        return deduped

    def _contract_scenarios(
        self,
        contract: dict[str, Any],
        active_slice_ids: set[str] | None,
        discovery_round: int,
    ) -> list[ExecutableScenario]:
        if not isinstance(contract, dict) or not contract:
            return []
        policy = str(contract.get("execution_policy") or "safe_read_only").strip()
        if policy not in _ALLOWED_POLICIES:
            return []
        actor = contract.get("actor") if isinstance(contract.get("actor"), dict) else {}
        actor_id = str(actor.get("id") or actor.get("name") or actor.get("actor") or "").strip()
        if not actor_id:
            return []
        token = str(actor.get("token") or "").strip()
        scenarios: list[ExecutableScenario] = []
        for index, row in enumerate(contract.get("scenarios") or []):
            if not isinstance(row, dict):
                continue
            item = self._contract_scenario(row, index, policy, actor_id, token, active_slice_ids, discovery_round)
            if item is not None:
                scenarios.append(item)
        return scenarios

    def _contract_scenario(
        self,
        row: dict[str, Any],
        index: int,
        policy: str,
        actor_id: str,
        token: str,
        active_slice_ids: set[str] | None,
        discovery_round: int,
    ) -> ExecutableScenario | None:
        steps = self._contract_steps(row.get("steps"), policy, actor_id)
        if not steps:
            return None
        cleanup_steps = self._contract_steps(row.get("cleanup_steps") or row.get("cleanup"), "approved_sandbox_write", actor_id)
        slice_id = str(row.get("behavior_slice_id") or "").strip()
        if not slice_id:
            first = steps[0]
            slice_id = behavior_slice_id("runtime_contract", str(row.get("entity") or "runtime"), first.api_method, first.api_path)
        if active_slice_ids is not None and slice_id not in active_slice_ids:
            # Contract scenario is explicit but not selected in this discovery round.
            return None
        return ExecutableScenario(
            id=str(row.get("id") or self._id("runtime_contract", index, slice_id)),
            title=str(row.get("title") or f"[运行合同] {steps[0].api_method} {steps[0].api_path}")[:160],
            description=str(row.get("description") or "Customer-approved runtime scenario contract."),
            category=str(row.get("category") or "runtime_contract"),
            severity=str(row.get("severity") or "P2"),
            entity=str(row.get("entity") or "runtime"),
            preconditions=[str(item) for item in row.get("preconditions", []) if str(item)] or ["runtime_scenario_contract_approved"],
            actors=[actor_id],
            steps=steps,
            expected_state=str(row.get("expected_state") or "runtime_observed"),
            oracle_rules=[str(item) for item in row.get("oracle_rules", []) if str(item)] or ["RuntimeContract.approved_step_executes"],
            cleanup_steps=cleanup_steps,
            confidence=float(row.get("confidence") or 0.9),
            actor_token=token,
            execution_policy=policy,
            evidence_gaps=[],
            source_refs=[{"source": "runtime_scenario_contract", "scenario_id": str(row.get("id") or index)}],
            behavior_slice_id=slice_id,
            behavior_slice_kind="runtime_contract",
            discovery_round=discovery_round,
        )

    def _contract_steps(self, raw_steps: Any, policy: str, actor_id: str) -> list[ScenarioStep]:
        steps: list[ScenarioStep] = []
        for index, value in enumerate(raw_steps or []):
            if not isinstance(value, dict):
                continue
            method = str(value.get("method") or value.get("api_method") or "").upper().strip()
            path = str(value.get("path") or value.get("api_path") or "").strip()
            if not method or not path.startswith("/"):
                continue
            if policy == "safe_read_only" and method not in _READ_ONLY_METHODS:
                continue
            if policy == "approved_sandbox_write" and method not in _READ_ONLY_METHODS | _WRITE_METHODS:
                continue
            if policy == "runtime_approved" and method not in _READ_ONLY_METHODS | _WRITE_METHODS:
                continue
            steps.append(
                ScenarioStep(
                    order=int(value.get("order") or index + 1),
                    action=str(value.get("action") or f"{method} {path}"),
                    api_method=method,
                    api_path=path,
                    body_template=value.get("body") if isinstance(value.get("body"), dict) else (value.get("body_template") if isinstance(value.get("body_template"), dict) else {}),
                    extract_from_response=[str(item) for item in value.get("extract", value.get("extract_from_response", [])) if str(item)],
                    expected_status=int(value.get("expected_status") or value.get("expected") or (200 if method in _READ_ONLY_METHODS else 201)),
                    actor=str(value.get("actor") or actor_id),
                )
            )
        return steps

    def _transition(self, entity: str, transition: StateTransition, discovery_round: int) -> ExecutableScenario:
        forbidden = bool(transition.is_forbidden)
        kind = "禁止流转" if forbidden else ("边界流转" if transition.is_boundary else "状态流转")
        gaps = ["FIXTURE_CONTRACT_MISSING", "ACTOR_BINDING_MISSING", "CLEANUP_CONTRACT_MISSING"]
        if not transition.action or not transition.api_endpoint:
            gaps.insert(0, "ACTION_ROUTE_NOT_SOURCE_BOUND")
        slice_id = transition.behavior_slice_id or behavior_slice_id(
            "transition",
            entity,
            transition.from_state,
            transition.to_state,
            transition.action,
            transition.api_endpoint,
            "forbidden" if forbidden else "normal",
        )
        return ExecutableScenario(
            id=self._id(entity, transition.from_state, transition.to_state, transition.action),
            title=f"[来源约束{kind}] {entity}: {transition.from_state} -> {transition.to_state}",
            description="当前资料未提供完整运行时前置数据和身份绑定；仅产生计划，不自动发起请求。",
            severity="P0" if forbidden else "P2",
            entity=entity,
            preconditions=[f"已通过可追溯数据证明 {entity} 处于 {transition.from_state}"],
            expected_state=transition.from_state if forbidden else transition.to_state,
            oracle_rules=["StateOracle.source_grounded_transition", f"{transition.from_state}->{transition.to_state}"],
            is_forbidden_path=forbidden,
            is_boundary_path=bool(transition.is_boundary),
            confidence=0.55 if transition.source_refs else 0.2,
            evidence_gaps=gaps,
            source_refs=list(transition.source_refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="transition",
            discovery_round=discovery_round,
        )

    def _invariant(
        self,
        entity: str,
        state: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
    ) -> ExecutableScenario:
        return ExecutableScenario(
            id=self._id(entity, state, invariant),
            title=f"[来源约束不变量] {entity}: {state}",
            description=invariant[:300],
            category="invariant",
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
            expected_state=state,
            oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
            confidence=0.45 if refs else 0.2,
            evidence_gaps=["OBSERVATION_ROUTE_NOT_SOURCE_BOUND", "ACTOR_BINDING_MISSING"],
            source_refs=list(refs),
            behavior_slice_id=behavior_slice_id("invariant", entity, state, invariant),
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
        )

    @staticmethod
    def _id(*parts: Any) -> str:
        return "SCN_SRC_" + hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]
