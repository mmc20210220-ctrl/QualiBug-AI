"""Source-grounded scenarios for the existing V12 behavior graph.

No default business entity, API path, actor, request body or cleanup action is
created here. Missing executable prerequisites are represented as plan gaps.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .auto_test_data_factory import _markdown_request_example
from .business_state_graph import BusinessStateGraph, StateEdge, StateTransition, _api_facts, behavior_slice_id
from .real_id_resolver import normalize_path_placeholders, path_has_placeholders, collection_path


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


class SemanticScenarioGenerator:
    """Plan only source-backed obligations selected by the incremental scheduler."""

    def generate(
        self,
        graphs: dict[str, BusinessStateGraph],
        api_doc: str = "",
        active_slice_ids: set[str] | None = None,
        discovery_round: int = 1,
        active_slices: list[dict[str, Any]] | None = None,
        allow_source_runtime: bool = False,
        root: Any = None,
        project: str = "",
    ) -> list[ExecutableScenario]:
        round_number = max(1, int(discovery_round or 1))
        active_slice_map = {
            str(item.get("slice_id") or ""): dict(item)
            for item in active_slices or []
            if isinstance(item, dict) and str(item.get("slice_id") or "")
        }
        results: list[ExecutableScenario] = []
        for entity, graph in sorted((graphs or {}).items()):
            if not isinstance(graph, BusinessStateGraph):
                continue
            for transition in graph.transitions:
                item = self._transition(entity, transition, graph, round_number, api_doc, root, project)
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
            for edge in graph.edges:
                slice_id = behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation)
                item = self._dependency(
                    entity,
                    edge,
                    round_number,
                    slice_meta=active_slice_map.get(slice_id) if allow_source_runtime else None,
                    api_doc=api_doc if allow_source_runtime else "",
                )
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
            for state, node in graph.states.items():
                for invariant in node.invariants:
                    slice_id = behavior_slice_id("invariant", entity, state, invariant)
                    items = self._invariant(
                        entity,
                        state,
                        invariant,
                        node.source_refs,
                        round_number,
                        slice_meta=active_slice_map.get(slice_id) if allow_source_runtime else None,
                        api_doc=api_doc if allow_source_runtime else "",
                    )
                    for item in items:
                        if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                            results.append(item)
        if allow_source_runtime:
            for item in self._source_observations(api_doc, round_number):
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
        emitted_slice_ids = {item.behavior_slice_id for item in results if str(item.behavior_slice_id or "").strip()}
        for slice_id, slice_meta in active_slice_map.items():
            if slice_id in emitted_slice_ids:
                continue
            item = self._fallback_active_slice(
                slice_meta,
                round_number,
                api_doc if allow_source_runtime else "",
                allow_source_runtime=allow_source_runtime,
            )
            if item is None:
                continue
            if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                results.append(item)
        deduped: list[ExecutableScenario] = []
        seen: set[str] = set()
        for item in results:
            fingerprint = f"{item.behavior_slice_id}|{item.entity}|{item.title}|{item.expected_state}"
            if fingerprint not in seen:
                seen.add(fingerprint)
                deduped.append(item)
        return deduped

    def _source_observations(self, api_doc: str, discovery_round: int) -> list[ExecutableScenario]:
        _entities, _states, endpoints = _api_facts(api_doc, __import__("re").compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", __import__("re").I))
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in endpoints:
            if str(item.get("method") or "").upper() not in {"GET", "HEAD", "OPTIONS"}:
                continue
            entity = str(item.get("entity") or "")
            path = str(item.get("path") or "")
            if entity and path.startswith("/"):
                grouped.setdefault(entity, []).append(item)
        results: list[ExecutableScenario] = []
        for entity, items in sorted(grouped.items()):
            paths = list(dict.fromkeys(str(item.get("path") or "") for item in items if str(item.get("path") or "")))
            if not paths:
                continue
            first = paths[0]
            results.append(ExecutableScenario(
                id=self._id(entity, "source_observation", first),
                title=f"[Source observation] {entity}: {first}",
                description="Read-only source-bound endpoint observation for runtime evidence capture.",
                category="source_observation",
                severity="P2",
                entity=entity,
                steps=[ScenarioStep(order=1, action="observe_source_endpoint", api_method="GET", api_path=first, expected_status=200, actor="readonly")],
                oracle_rules=["RuntimeObservation.source_endpoint_reachable"],
                confidence=0.5,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=[{"source_type": "openapi", "locator": first, "quote": first}],
                behavior_slice_id=behavior_slice_id("source_observation", entity, ",".join(paths)),
                behavior_slice_kind="source_observation",
                discovery_round=discovery_round,
            ))
        return results

    def _fallback_active_slice(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
        allow_source_runtime: bool = True,
    ) -> ExecutableScenario | None:
        kind = str(slice_meta.get("kind") or "").strip().lower()
        if kind == "source_observation":
            item = self._source_observation_from_meta(slice_meta, discovery_round)
        elif kind == "invariant":
            item = self._invariant_from_meta(slice_meta, discovery_round, api_doc)
        elif kind == "permission":
            item = self._permission_slice(slice_meta, discovery_round)
        elif kind == "isolation":
            item = self._isolation_slice(slice_meta, discovery_round, api_doc)
        elif kind == "concurrency":
            item = self._concurrency_slice(slice_meta, discovery_round)
        elif kind == "money":
            item = self._money_slice(slice_meta, discovery_round)
        else:
            return None
        if item is None or allow_source_runtime:
            return item
        # Plan-only intent: the runtime contract is not approved, so the
        # source-grounded coverage metadata is preserved but the executable
        # steps are stripped — otherwise the scenario would be miscounted as an
        # executed probe. This keeps the planning/execution boundary honest.
        #
        # Exception: system behavior space scenarios carry an authoritative
        # execution_policy determined by the slice metadata (safe_read_only when
        # a source-bound GET/HEAD/OPTIONS route exists, plan_only otherwise).
        # The enrichment already strips steps for plan_only promises, so
        # overriding it here would lose the safe_read_only decision.
        if getattr(item, "selection_origin", "") == "system_behavior_space":
            return item
        item.steps = []
        item.cleanup_steps = []
        item.execution_policy = "plan_only_requires_fixture"
        item.actor_token = ""
        if "RUNTIME_CONTRACT_NOT_APPROVED" not in item.evidence_gaps:
            item.evidence_gaps = list(item.evidence_gaps) + ["RUNTIME_CONTRACT_NOT_APPROVED"]
        return item

    def _source_observation_from_meta(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
    ) -> ExecutableScenario | None:
        entity = str(slice_meta.get("entity") or "").strip()
        endpoints = [str(item or "").strip() for item in (slice_meta.get("endpoints") or []) if str(item or "").strip()]
        if not entity or not endpoints:
            return None
        first = self._preferred_read_endpoint(endpoints) or endpoints[0]
        if not first.startswith("/"):
            return None
        return ExecutableScenario(
            id=self._id(entity, "source_observation", first),
            title=f"[Source observation] {entity}: {first}",
            description="Read-only source-bound endpoint observation for runtime evidence capture.",
            category="source_observation",
            severity="P2",
            entity=entity,
            steps=[ScenarioStep(order=1, action="observe_source_endpoint", api_method="GET", api_path=first, expected_status=200, actor="readonly")],
            oracle_rules=["RuntimeObservation.source_endpoint_reachable"],
            confidence=float(slice_meta.get("priority") or 0.45),
            execution_policy="safe_read_only",
            evidence_gaps=[str(item) for item in (slice_meta.get("evidence_gaps") or []) if str(item).strip()],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or []) if isinstance(item, dict)],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="source_observation",
            discovery_round=discovery_round,
            selection_origin="active_slice_fallback_materialized",
        )

    def _invariant_from_meta(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
    ) -> ExecutableScenario | None:
        entity = str(slice_meta.get("entity") or "").strip()
        refs = [dict(item) for item in (slice_meta.get("source_refs") or []) if isinstance(item, dict)]
        states = [str(item or "").strip() for item in (slice_meta.get("states") or []) if str(item or "").strip()]
        invariant = self._slice_meta_invariant_text(slice_meta)
        observation_path = self._preferred_read_endpoint(list(slice_meta.get("endpoints") or []))
        if not entity:
            return None
        runtime_upgrade = self._invariant_runtime_upgrade(
            entity,
            states[0] if states else "",
            invariant,
            refs,
            discovery_round,
            slice_id=str(slice_meta.get("slice_id") or ""),
            observation_path=observation_path,
            api_doc=api_doc,
        )
        if runtime_upgrade is not None:
            return runtime_upgrade
        if not observation_path:
            return None
        state_or_rule = states[0] if states else invariant[:120]
        return ExecutableScenario(
            id=self._id(entity, state_or_rule or "invariant"),
            title=f"[来源约束不变量] {entity}: {state_or_rule}",
            description=invariant[:300],
            category="invariant",
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
            actors=["readonly"],
            steps=[ScenarioStep(order=1, action="observe_bound_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly")],
            expected_state=states[0] if states else "",
            oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.55 if refs else 0.3),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=refs,
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
            selection_origin="active_slice_fallback_materialized",
        )

    @staticmethod
    def _slice_meta_invariant_text(slice_meta: dict[str, Any]) -> str:
        for ref in slice_meta.get("source_refs") or []:
            if not isinstance(ref, dict):
                continue
            quote = str(ref.get("quote") or "").strip()
            if quote:
                return quote
        states = [str(item or "").strip() for item in (slice_meta.get("states") or []) if str(item or "").strip()]
        if states:
            return states[0]
        return str(slice_meta.get("entity") or "source_invariant")

    def _transition(
        self,
        entity: str,
        transition: StateTransition,
        graph: "BusinessStateGraph | None",
        discovery_round: int,
        api_doc: str = "",
        root: Any = None,
        project: str = "",
    ) -> ExecutableScenario:
        """Turn a source-bound state transition into an EXECUTABLE scenario.

        Previously this emitted a step-less "plan only" scenario, so routed
        transitions never ran and the StateOracle short-circuited to pass. Now we
        build real steps: login (generic account) -> create an entity in its
        initial state -> drive it toward ``from_state`` when needed -> apply the
        transition's action endpoint -> observe the resulting state. No hardcoded
        role, path, body field or entity name; everything is derived from the
        endpoint catalog and the documented request examples.
        """
        forbidden = bool(transition.is_forbidden)
        kind = "禁止流转" if forbidden else ("边界流转" if transition.is_boundary else "状态流转")
        slice_id = transition.behavior_slice_id or behavior_slice_id(
            "transition",
            entity,
            transition.from_state,
            transition.to_state,
            transition.action,
            transition.api_endpoint,
            "forbidden" if forbidden else "normal",
        )
        # Unroutable transition: honest plan-only coverage gap. Never pretends to
        # execute (that would be a symptom patch on top of a structural miss).
        if not transition.action or not transition.api_endpoint:
            return ExecutableScenario(
                id=self._id(entity, transition.from_state, transition.to_state, transition.action),
                title=f"[来源约束{kind}] {entity}: {transition.from_state} -> {transition.to_state}",
                description="未解析到可执行端点：仅记录覆盖缺口，不自动发起请求。",
                category="state_machine",
                severity="P0" if forbidden else "P2",
                entity=entity,
                preconditions=[f"已通过可追溯数据证明 {entity} 处于 {transition.from_state}"],
                expected_state=transition.from_state if forbidden else transition.to_state,
                oracle_rules=["StateOracle.source_grounded_transition", f"{transition.from_state}->{transition.to_state}"],
                is_forbidden_path=forbidden,
                is_boundary_path=bool(transition.is_boundary),
                confidence=0.2,
                execution_policy="plan_only_requires_fixture",
                evidence_gaps=["ACTION_ROUTE_NOT_SOURCE_BOUND"],
                source_refs=list(transition.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="transition",
                discovery_round=discovery_round,
            )
        _, _, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        steps: list[ScenarioStep] = []
        order = 1
        gaps: list[str] = []
        # 1) Authenticate with a generic account (settings -> test_accounts).
        role, email, password, login_path = self._generic_transition_auth(api_doc, root, project)
        if login_path and email and password:
            login_body = _markdown_request_example(api_doc, "POST", login_path)
            if not isinstance(login_body, dict) or not login_body:
                login_body = {"email": email, "password": password}
            ls = self._build_login_step(login_path, login_body, email, password, order=order)
            if ls is not None:
                steps.append(ls)
                order += 1
        # 2) Create an entity instance (lands in its initial state).
        create_ep = self._entity_create_endpoint(entity, endpoints)
        if create_ep:
            cpath, cmethod = create_ep
            cbody = _markdown_request_example(api_doc, cmethod, cpath)
            steps.append(ScenarioStep(
                order=order,
                action="create_entity",
                api_method=cmethod,
                api_path=cpath,
                body_template=cbody if isinstance(cbody, dict) else {},
                expected_status=200,
                actor=role,
                extract_from_response=["id", "orderId", "order_id", "refundId", "paymentId"],
            ))
            order += 1
        else:
            gaps.append("CREATE_ENDPOINT_NOT_SOURCE_BOUND")
        # 3) Drive the entity toward from_state when it is not the initial state.
        if graph is not None and transition.from_state:
            order = self._drive_to_state(entity, transition.from_state, graph, endpoints, role, api_doc, steps, order, gaps)
        # 4) Apply the transition's action endpoint.
        method = self._endpoint_method(transition.api_endpoint, endpoints) or "POST"
        action_path = normalize_path_placeholders(transition.api_endpoint) if path_has_placeholders(normalize_path_placeholders(transition.api_endpoint)) else transition.api_endpoint
        action_body = self._action_body_for(transition.api_endpoint, method, endpoints, api_doc)
        steps.append(ScenarioStep(
            order=order,
            action=f"transition_{transition.action or 'mutate'}",
            api_method=method,
            api_path=action_path,
            body_template=action_body,
            expected_status=(200 if not forbidden else 409),
            actor=role,
            extract_from_response=["id", "status", "state", "order_status"],
        ))
        order += 1
        # 5) Observe the resulting state.
        read_ep = self._entity_read_endpoint(entity, endpoints)
        if read_ep:
            obs_path = normalize_path_placeholders(read_ep) if path_has_placeholders(normalize_path_placeholders(read_ep)) else read_ep
            steps.append(ScenarioStep(
                order=order,
                action="observe_transition_result",
                api_method="GET",
                api_path=obs_path,
                expected_status=200,
                actor=role,
                extract_from_response=["status", "state", "order_status"],
            ))
        else:
            gaps.append("READ_ENDPOINT_NOT_SOURCE_BOUND")
        return ExecutableScenario(
            id=self._id(entity, transition.from_state, transition.to_state, transition.action),
            title=f"[来源约束{kind}] {entity}: {transition.from_state} -> {transition.to_state}",
            description="依据源约束（PRD/API）驱动实体经历状态流转，并以 StateOracle 校验流转是否被正确执行或拒绝。",
            category="state_machine",
            severity="P0" if forbidden else "P2",
            entity=entity,
            preconditions=[f"已通过可追溯数据证明 {entity} 处于 {transition.from_state}"],
            expected_state=transition.to_state,
            oracle_rules=["StateOracle.source_grounded_transition", f"{transition.from_state}->{transition.to_state}"],
            is_forbidden_path=forbidden,
            is_boundary_path=bool(transition.is_boundary),
            confidence=0.55 if transition.source_refs else 0.35,
            execution_policy="approved_sandbox_write",
            steps=steps,
            evidence_gaps=gaps,
            source_refs=list(transition.source_refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="transition",
            discovery_round=discovery_round,
        )

    # ── Generic helpers for state-transition scenarios (no hardcoding) ──

    def _generic_transition_auth(self, api_doc: str, root: Any, project: str) -> tuple[str, str, str, str]:
        accounts: list[dict[str, str]] = []
        login_path = ""
        try:
            from pathlib import Path
            from .supplementary_behavior_slices import load_settings_accounts
            if root is not None and project:
                accounts, login_path = load_settings_accounts(Path(str(root)), str(project))
        except Exception:
            accounts, login_path = [], ""
        if not accounts and root is not None and project:
            try:
                from pathlib import Path
                p = Path(str(root)) / "platform_workspace" / str(project) / "input" / "test_accounts.json"
                if p.exists():
                    accounts = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                accounts = []
        acct = accounts[0] if accounts else {}
        role = str(acct.get("role") or "user").strip()
        email = str(acct.get("email") or acct.get("username") or "").strip()
        password = str(acct.get("password") or "").strip()
        if not login_path:
            login_path = self._discover_login_endpoint(api_doc)
        return role, email, password, login_path

    @staticmethod
    def _discover_login_endpoint(api_doc: str) -> str:
        _, _, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        for item in endpoints:
            p = str(item.get("path") or "").lower()
            if str(item.get("method") or "").upper() == "POST" and ("login" in p or "auth" in p or "signin" in p):
                return str(item.get("path") or "")
        return "/api/auth/login"

    @staticmethod
    def _entity_create_endpoint(entity: str, endpoints: list[dict[str, str]]) -> tuple[str, str] | None:
        for item in endpoints:
            if str(item.get("entity") or "") == entity and str(item.get("method") or "").upper() == "POST" and not path_has_placeholders(str(item.get("path") or "")):
                return str(item.get("path") or ""), "POST"
        return None

    @staticmethod
    def _entity_read_endpoint(entity: str, endpoints: list[dict[str, str]]) -> str:
        cands = [
            str(item.get("path") or "")
            for item in endpoints
            if str(item.get("entity") or "") == entity
            and str(item.get("method") or "").upper() == "GET"
            and str(item.get("path") or "").startswith("/")
        ]
        with_ph = [p for p in cands if path_has_placeholders(p)]
        no_ph = [p for p in cands if not path_has_placeholders(p)]
        # Prefer the :id read endpoint (we have an id from the create step).
        if with_ph:
            return with_ph[0]
        if no_ph:
            return no_ph[0]
        return ""

    @staticmethod
    def _endpoint_method(path: str, endpoints: list[dict[str, str]]) -> str:
        for item in endpoints:
            if str(item.get("path") or "") == str(path):
                return str(item.get("method") or "POST").upper()
        return "POST"

    @staticmethod
    def _initial_state(graph: "BusinessStateGraph") -> str:
        # A true initial state is one that is NEVER the TARGET of a normal
        # transition (nothing leads INTO it).  Using from_state here is wrong:
        # it treats any transition's source as "having an incoming edge" and so
        # mis-selects sink states (e.g. REFUNDED) as the initial state, which
        # then breaks path_to_state and prevents driving to real source states.
        incoming = {t.to_state for t in graph.transitions if t.is_normal and not t.is_forbidden}
        initials = [s for s in graph.states if s not in incoming]
        return initials[0] if initials else ""

    def _drive_to_state(
        self,
        entity: str,
        target_state: str,
        graph: "BusinessStateGraph",
        endpoints: list[dict[str, str]],
        actor: str,
        api_doc: str,
        steps: list[ScenarioStep],
        order: int,
        gaps: list[str],
    ) -> int:
        """Emit generic steps to drive an entity from its initial state to target_state."""
        initial = self._initial_state(graph)
        if not initial or target_state == initial:
            return order
        path = graph.path_to_state(initial, target_state)
        if not path:
            gaps.append("DRIVE_TO_SOURCE_STATE_NOT_ROUTABLE")
            return order
        for t in path:
            if not t.action or not t.api_endpoint:
                gaps.append("DRIVE_STEP_UNROUTED")
                continue
            method = self._endpoint_method(t.api_endpoint, endpoints) or "POST"
            ep = normalize_path_placeholders(t.api_endpoint) if path_has_placeholders(normalize_path_placeholders(t.api_endpoint)) else t.api_endpoint
            body = self._action_body_for(t.api_endpoint, method, endpoints, api_doc)
            steps.append(ScenarioStep(
                order=order,
                action=f"drive_{t.action or 'mutate'}",
                api_method=method,
                api_path=ep,
                body_template=body,
                expected_status=200,
                actor=actor,
                extract_from_response=["id", "status", "state", "orderId", "order_id"],
            ))
            order += 1
        return order

    @staticmethod
    def _action_body_for(path: str, method: str, endpoints: list[dict[str, str]], api_doc: str) -> dict[str, Any]:
        example = _markdown_request_example(api_doc, method, path) if api_doc else {}
        if isinstance(example, dict) and example:
            return example
        return {}

    def _dependency(
        self,
        entity: str,
        edge: StateEdge,
        discovery_round: int,
        *,
        slice_meta: dict[str, Any] | None = None,
        api_doc: str = "",
    ) -> ExecutableScenario:
        slice_id = behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation)
        observation_path = self._preferred_read_endpoint((slice_meta or {}).get("endpoints") or [])
        title = f"[跨实体依赖] {entity} -> {edge.target_entity}"
        write_path = self._preferred_write_endpoint(api_doc, entity)
        write_body = self._dependency_write_body(api_doc, entity, edge.target_entity)
        if observation_path and write_path and isinstance(write_body, dict) and write_body:
            extract_fields = ["id", "status", "state", "amount", "totalAmount", "total_amount"]
            return ExecutableScenario(
                id=self._id(entity, edge.target_entity, edge.relation, write_path),
                title=title,
                description=f"先观察 {edge.target_entity} 的真实对象，再执行 {entity} 的来源写入口，验证跨实体依赖链是否可执行。",
                category="dependency",
                severity="P1",
                entity=entity,
                preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
                actors=["readonly"],
                steps=[
                    ScenarioStep(order=1, action="observe_dependency_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=["id"]),
                    ScenarioStep(order=2, action="execute_dependency_write", api_method="POST", api_path=write_path, expected_status=200, actor="readonly", body_template=write_body),
                    ScenarioStep(order=3, action="verify_dependency_effect_after_write", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=extract_fields),
                ],
                expected_state=edge.target_state,
                oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
                confidence=0.6 if edge.source_refs else 0.35,
                execution_policy="approved_sandbox_write",
                evidence_gaps=[],
                source_refs=list(edge.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="dependency",
                discovery_round=discovery_round,
            )
        if observation_path:
            return ExecutableScenario(
                id=self._id(entity, edge.target_entity, edge.relation, observation_path),
                title=title,
                description=f"观察 {edge.target_entity} 的来源绑定运行时路径，验证 {entity} 的跨实体依赖前置是否可达。",
                category="dependency",
                severity="P1",
                entity=entity,
                preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
                actors=["readonly"],
                steps=[ScenarioStep(order=1, action="observe_dependency_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly")],
                expected_state=edge.target_state,
                oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
                confidence=0.5 if edge.source_refs else 0.3,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=list(edge.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="dependency",
                discovery_round=discovery_round,
            )
        return ExecutableScenario(
            id=self._id(entity, edge.target_entity, edge.relation),
            title=title,
            description=f"当前资料缺少 {entity} -> {edge.target_entity} 的可观察运行时路由。",
            category="dependency",
            severity="P1",
            entity=entity,
            preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
            expected_state=edge.target_state,
            oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
            confidence=0.4 if edge.source_refs else 0.2,
            evidence_gaps=["CROSS_ENTITY_OBSERVATION_CONTRACT_MISSING", "ACTOR_BINDING_MISSING"],
            source_refs=list(edge.source_refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="dependency",
            discovery_round=discovery_round,
        )

    def _invariant(
        self,
        entity: str,
        state: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        *,
        slice_meta: dict[str, Any] | None = None,
        api_doc: str = "",
    ) -> list[ExecutableScenario]:
        slice_id = behavior_slice_id("invariant", entity, state, invariant)
        observation_path = self._preferred_read_endpoint((slice_meta or {}).get("endpoints") or [])
        runtime_upgrade = self._invariant_runtime_upgrade(
            entity,
            state,
            invariant,
            refs,
            discovery_round,
            slice_id=slice_id,
            observation_path=observation_path,
            api_doc=api_doc,
        )
        if runtime_upgrade is not None:
            return [runtime_upgrade]
        if observation_path:
            return [ExecutableScenario(
                id=self._id(entity, state, invariant),
                title=f"[来源约束不变量] {entity}: {state}",
                description=invariant[:300],
                category="invariant",
                severity="P1",
                entity=entity,
                preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
                actors=["readonly"],
                steps=[ScenarioStep(order=1, action="observe_bound_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly")],
                expected_state=state,
                oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
                confidence=0.55 if refs else 0.3,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=list(refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="invariant",
                discovery_round=discovery_round,
            )]
        return [ExecutableScenario(
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
            behavior_slice_id=slice_id,
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
        )]

    def _invariant_runtime_upgrade(
        self,
        entity: str,
        state: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        *,
        slice_id: str,
        observation_path: str,
        api_doc: str,
    ) -> ExecutableScenario | None:
        if not str(api_doc or "").strip():
            return None
        action_plan = self._match_invariant_action(api_doc, entity, invariant, refs, state=state)
        if not action_plan:
            return None
        extract_fields = ["id", "status", "state", "amount", "totalAmount", "total_amount", "payableAmount", "payable_amount"]
        validation_only = bool(action_plan.get("validation_only"))
        # A validation-only route (e.g. POST /validate) is itself the probe and
        # needs no separate read endpoint to bind a state prerequisite. Only
        # state-precondition drivers (forbidden/duplicate writes) require a
        # source-bound observation path, so only block those when it is missing.
        if not validation_only and not observation_path:
            return None
        write_step = ScenarioStep(
            order=1 if validation_only else 2,
            action=str(action_plan.get("scenario_action") or "execute_invariant_write"),
            api_method=str(action_plan.get("method") or "POST"),
            api_path=str(action_plan.get("path") or ""),
            expected_status=int(action_plan.get("expected_status") or 200),
            actor="readonly",
            body_template=action_plan.get("body") if isinstance(action_plan.get("body"), dict) else {},
        )
        title_suffix = str(action_plan.get("title_suffix") or str(action_plan.get("path") or "")).strip()
        oracle_rules = ["ConsistencyOracle.source_grounded_invariant", invariant[:300]]
        rule_key = str(action_plan.get("rule_key") or "").strip()
        if rule_key:
            oracle_rules.insert(0, f"CouponOracle.{rule_key}")
        if validation_only:
            steps = [write_step]
        else:
            # State precondition driver: when the invariant is anchored to a
            # concrete lifecycle status (e.g. PAID / CANCELLED), bind an entity
            # that is genuinely in that state via a filtered extraction.  If no
            # such entity exists at runtime the executor marks the trace
            # precondition_not_met and the finding cannot be confirmed — so we
            # never confirm a transition from a state the system never reached.
            observe_where: dict[str, Any] = {}
            state_token = str(state or "").strip()
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", state_token):
                observe_where = {"status": state_token}
            steps = [
                ScenarioStep(order=1, action="observe_bound_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=extract_fields, extract_where=observe_where),
                write_step,
            ]
        if not validation_only and str(action_plan.get("mode") or "") == "duplicate_write":
            steps.append(ScenarioStep(
                order=3,
                action=str(action_plan.get("scenario_action") or "repeat_invariant_write"),
                api_method=str(action_plan.get("method") or "POST"),
                api_path=str(action_plan.get("path") or ""),
                expected_status=int(action_plan.get("expected_status") or 200),
                actor="readonly",
                body_template=action_plan.get("body") if isinstance(action_plan.get("body"), dict) else {},
            ))
        if not validation_only:
            verify_order = len(steps) + 1
            steps.append(ScenarioStep(
                order=verify_order,
                action="verify_bound_entity_after_write",
                api_method="GET",
                api_path=observation_path,
                expected_status=200,
                actor="readonly",
                extract_from_response=extract_fields,
            ))
        return ExecutableScenario(
            id=self._id(entity, state, invariant, title_suffix),
            title=f"[来源约束不变量] {entity}: {state} -> {title_suffix}",
            description=invariant[:300],
            category=str(action_plan.get("category") or "state_machine"),
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本", f"约束: {invariant[:120]}"],
            actors=["readonly"],
            steps=steps,
            expected_state=state,
            oracle_rules=oracle_rules,
            confidence=0.7 if refs else 0.4,
            execution_policy="approved_sandbox_write",
            evidence_gaps=[],
            source_refs=list(refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
            is_forbidden_path=bool(action_plan.get("forbidden")),
            runtime_hints=dict(action_plan.get("runtime_hints") or {}),
        )

    @staticmethod
    def _locator_line(locator: Any) -> int | None:
        match = re.search(r"line:(\d+)", str(locator or ""), re.I)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _invariant_action_contexts(cls, invariant: str, refs: list[dict[str, str]] | None) -> list[str]:
        anchor_text = str(invariant or "").strip()
        anchor_line: int | None = None
        normalized_refs = [item for item in (refs or []) if isinstance(item, dict)]
        for ref in normalized_refs:
            if str(ref.get("quote") or "").strip() == anchor_text:
                anchor_line = cls._locator_line(ref.get("locator"))
                break

        ranked: list[tuple[int, int, str]] = []
        seen: set[str] = set()
        if anchor_text:
            ranked.append((-1, 0, anchor_text))
            seen.add(anchor_text)
        for index, ref in enumerate(normalized_refs, start=1):
            quote = str(ref.get("quote") or "").strip()
            if not quote or quote in seen:
                continue
            line = cls._locator_line(ref.get("locator"))
            distance = abs(line - anchor_line) if line is not None and anchor_line is not None else 10_000 + index
            ranked.append((distance, index, quote))
            seen.add(quote)
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [text for _, _, text in ranked]

    def _match_invariant_action(self, api_doc: str, entity: str, invariant: str, refs: list[dict[str, str]] | None = None, state: str = "") -> dict[str, Any]:
        contexts = self._invariant_action_contexts(invariant, refs)
        # Action-verb detection must not be driven by database schema DDL. A CHECK
        # enum such as status IN ('ACTIVE', 'DISABLED') is a structural declaration,
        # not an action instruction; letting "DISABLED" substring-match the
        # "disable" action profile hijacks the classification and drops the real
        # source-bound validation route. So detect the action from non-DDL context.
        action_refs = [
            ref for ref in (refs or [])
            if isinstance(ref, dict) and str(ref.get("source_type") or "") != "database_schema"
        ]
        action_contexts = self._invariant_action_contexts(invariant, action_refs)
        mode = ""
        forbidden = False
        for text in action_contexts:
            lowered = text.lower()
            if any(token in text for token in ("不能", "禁止", "不应", "不可", "不得")) or any(token in lowered for token in ("must not", "forbidden", "cannot", "should not")):
                mode, forbidden = "forbidden_write", True
                break
            if any(token in text for token in ("只能成功一次", "重复成功", "不能重复", "只能成功支付一次")) or any(token in lowered for token in ("only once", "duplicate", "idempotent")):
                mode = "duplicate_write"
                break
        action_profiles: list[dict[str, Any]] = [
            {"tokens": ["取消", "cancel"], "endpoint_tokens": ["cancel"]},
            {"tokens": ["支付", "pay", "payment"], "endpoint_tokens": ["pay", "payment"]},
            {"tokens": ["退款", "refund"], "endpoint_tokens": ["refund"]},
            {"tokens": ["审批", "approve", "approval"], "endpoint_tokens": ["approve", "approval"]},
            {"tokens": ["驳回", "reject"], "endpoint_tokens": ["reject"]},
            {"tokens": ["关闭", "close"], "endpoint_tokens": ["close"]},
            {"tokens": ["撤销", "revoke"], "endpoint_tokens": ["revoke"]},
            {"tokens": ["回滚", "rollback"], "endpoint_tokens": ["rollback"]},
            {"tokens": ["释放", "release"], "endpoint_tokens": ["release"]},
            {"tokens": ["归档", "archive"], "endpoint_tokens": ["archive"]},
            {"tokens": ["禁用", "disable"], "endpoint_tokens": ["disable"]},
            {"tokens": ["恢复", "restore", "reopen"], "endpoint_tokens": ["restore", "reopen"]},
            {"tokens": ["校验", "验证", "validate", "apply", "redeem"], "endpoint_tokens": ["validate", "apply", "redeem"]},
        ]
        profile = None
        for text in action_contexts:
            lowered = text.lower()
            profile = next((item for item in action_profiles if any(token.lower() in lowered for token in item["tokens"])), None)
            if profile:
                break
        if not profile:
            # Endpoint-grounded fallback: the invariant text may only assert a
            # state constraint (e.g. "必须处于 ACTIVE 状态") without an explicit
            # action verb, while the source API exposes a validation-style route
            # (validate/apply/redeem). When the invariant is an affirmative
            # constraint and such a source-bound route exists, treat it as a
            # validation-only assertion. This stays industry-agnostic: the verbs
            # come from the real API doc, not hardcoded business rules.
            validate_profile = next(
                (item for item in action_profiles if "validate" in item["endpoint_tokens"]),
                None,
            )
            affirmative_tokens = (
                "必须", "应当", "应", "需要", "must", "should", "required",
                "within", "active", "有效期", "类目", "状态",
            )
            invariant_is_affirmative = any(
                any(token.lower() in str(text or "").lower() for token in affirmative_tokens)
                for text in contexts
            )
            _, _, _fallback_endpoints = _api_facts(api_doc, re.compile(r"", re.I))
            has_validation_route = any(
                str(ep.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
                and any(
                    tok in " ".join(
                        str(part or "").lower()
                        for part in (ep.get("path"), ep.get("action"), ep.get("summary"))
                    )
                    for tok in ("validate", "apply", "redeem")
                )
                for ep in (_fallback_endpoints or [])
            )
            if validate_profile and invariant_is_affirmative and has_validation_route:
                profile = validate_profile
            else:
                return {}
        if not mode and any(token in profile["endpoint_tokens"] for token in ("validate", "apply", "redeem")):
            affirmative_tokens = ("必须", "应当", "应", "需要", "must", "should", "required", "within", "active", "有效期", "类目")
            if any(any(token.lower() in str(text or "").lower() for token in affirmative_tokens) for text in contexts):
                mode = "validation_only"
        if not mode:
            return {}

        _entities, _states, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        for endpoint in endpoints:
            method = str(endpoint.get("method") or "").upper()
            if method not in {"POST", "PUT", "PATCH"}:
                continue
            path = str(endpoint.get("path") or "")
            haystack = " ".join(
                str(part or "").lower()
                for part in (endpoint.get("path"), endpoint.get("action"), endpoint.get("summary"), endpoint.get("entity"))
                if str(part or "").strip()
            )
            if not any(token.lower() in haystack for token in profile["endpoint_tokens"]):
                continue
            normalized_path = normalize_path_placeholders(path)
            if path_has_placeholders(normalized_path) and str(endpoint.get("entity") or "") != entity:
                continue
            body = self._invariant_write_body(api_doc, method, path, entity)
            if body is None:
                continue
            rule_key = self._coupon_rule_key(entity, state, invariant, contexts)
            return {
                "mode": mode,
                "validation_only": mode == "validation_only",
                "forbidden": forbidden,
                "method": method,
                "path": normalized_path,
                "body": body,
                "expected_status": 409 if forbidden else 200,
                "scenario_action": f"invariant_{str(profile['endpoint_tokens'][0])}_write",
                "title_suffix": f"{normalized_path}#{rule_key}" if rule_key else normalized_path,
                "category": "state_machine" if forbidden else ("concurrency" if mode == "duplicate_write" else "invariant"),
                "rule_key": rule_key,
                "runtime_hints": {"coupon_validation_rule": rule_key} if rule_key else {},
            }
        return {}

    @staticmethod
    def _coupon_rule_key(entity: str, state: str, invariant: str, contexts: list[str]) -> str:
        if str(entity or "").strip().lower() != "coupon":
            return ""

        def has_any(text: str, tokens: tuple[str, ...]) -> bool:
            lowered = str(text or "").lower()
            return any(token in lowered for token in tokens)

        invariant_text = str(invariant or "").lower()
        state_text = str(state or "").strip().upper()
        merged = " ".join(str(text or "").lower() for text in contexts)

        if has_any(invariant_text, ("类目", "category", "scope")):
            return "coupon_category_scope_must_match"
        if has_any(invariant_text, ("最低订单金额", "min order", "minimum order", "门槛")):
            return "coupon_min_order_amount_must_match"
        if has_any(invariant_text, ("有效期", "过期", "expire", "expired")):
            return "expired_coupon_must_be_invalid"
        if has_any(invariant_text, ("active", "停用", "禁用", "disabled", "状态")):
            return "inactive_coupon_must_be_invalid"

        if state_text == "DISABLED":
            return "inactive_coupon_must_be_invalid"

        if has_any(merged, ("类目", "category", "scope")):
            return "coupon_category_scope_must_match"
        if has_any(merged, ("最低订单金额", "min order", "minimum order", "门槛")):
            return "coupon_min_order_amount_must_match"
        if has_any(merged, ("active", "停用", "禁用", "disabled", "状态")):
            return "inactive_coupon_must_be_invalid"
        if has_any(merged, ("有效期", "过期", "expire", "expired")):
            return "expired_coupon_must_be_invalid"
        return "coupon_validation_rule_must_be_enforced"

    @staticmethod
    def _invariant_write_body(api_doc: str, method: str, path: str, entity: str) -> dict[str, Any] | None:
        example = _markdown_request_example(api_doc, method, path)
        if not example:
            return {}
        if not isinstance(example, dict):
            return None
        rendered = SemanticScenarioGenerator._bind_dependency_placeholders(example, entity)
        if SemanticScenarioGenerator._has_unresolved_dependency_placeholder(rendered):
            return None
        return rendered

    @staticmethod
    def _preferred_read_endpoint(endpoints: list[Any]) -> str:
        candidates = [str(item or "").strip() for item in endpoints if str(item or "").strip().startswith("/")]
        for path in candidates:
            if not path_has_placeholders(path):
                return path
        return ""

    @staticmethod
    def _preferred_write_endpoint(api_doc: str, entity: str) -> str:
        _entities, _states, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        candidates = [
            str(item.get("path") or "")
            for item in endpoints
            if str(item.get("entity") or "") == entity
            and str(item.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
            and str(item.get("path") or "").startswith("/")
            and not path_has_placeholders(str(item.get("path") or ""))
        ]
        return candidates[0] if candidates else ""

    @staticmethod
    def _dependency_write_body(api_doc: str, entity: str, target_entity: str) -> dict[str, Any]:
        write_path = SemanticScenarioGenerator._preferred_write_endpoint(api_doc, entity)
        if not write_path:
            return {}
        example = _markdown_request_example(api_doc, "POST", write_path)
        if not isinstance(example, dict) or not example:
            return {}
        rendered = SemanticScenarioGenerator._bind_dependency_placeholders(example, target_entity)
        if SemanticScenarioGenerator._has_unresolved_dependency_placeholder(rendered):
            return {}
        return rendered

    @staticmethod
    def _bind_dependency_placeholders(value: Any, target_entity: str, field_name: str = "") -> Any:
        if isinstance(value, dict):
            return {
                str(key): SemanticScenarioGenerator._bind_dependency_placeholders(child, target_entity, str(key))
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [SemanticScenarioGenerator._bind_dependency_placeholders(child, target_entity, field_name) for child in value]
        if not isinstance(value, str):
            return value
        normalized_target = re.sub(r"[^a-z0-9]+", "", str(target_entity or "").lower())
        normalized_field = re.sub(r"[^a-z0-9]+", "", str(field_name or "").lower())

        def repl(match: re.Match[str]) -> str:
            placeholder = re.sub(r"[^a-z0-9]+", "", str(match.group(1) or "").lower())
            if normalized_target and normalized_target in placeholder and "id" in placeholder:
                return "{id}"
            return match.group(0)

        rendered = re.sub(r"<([A-Za-z_]\w*)>", repl, value)
        if normalized_target and normalized_target in normalized_field and "id" in normalized_field and rendered == value:
            return "{id}"
        return rendered

    @staticmethod
    def _has_unresolved_dependency_placeholder(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, dict):
            return any(
                SemanticScenarioGenerator._has_unresolved_dependency_placeholder(key)
                or SemanticScenarioGenerator._has_unresolved_dependency_placeholder(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(SemanticScenarioGenerator._has_unresolved_dependency_placeholder(child) for child in value)
        if not isinstance(value, str):
            return False
        normalized = normalize_path_placeholders(value)
        placeholders = re.findall(r"\{([A-Za-z_]\w*)\}", normalized)
        return any(str(name or "").strip().lower() != "id" for name in placeholders)

    @staticmethod
    def _id(*parts: Any) -> str:
        return "SCN_SRC_" + hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]

    # ── Supplementary scenario builders for non-state-machine slice kinds ──

    @staticmethod
    def _fill_login_body(template: dict[str, Any], identifier: str, password: str) -> dict[str, Any]:
        """Fill a login request body using the endpoint's documented field names.

        No hardcoded {email,password} assumption. Maps by field-name semantics:
          - a key that looks password-like        → the password value
          - the remaining identity key(s)          → the account identifier
        Falls back to {email, password} only when the API declares no schema.
        """
        tpl = dict(template or {})
        if not tpl:
            return {"email": identifier, "password": password}
        pass_tokens = ("pass", "pwd", "secret", "credential", "token", "密码")
        out: dict[str, Any] = {}
        for key in tpl:
            kl = str(key).lower()
            if any(tok in kl for tok in pass_tokens):
                out[key] = password
            else:
                out[key] = identifier
        return out

    @staticmethod
    def _build_login_step(login_path: str, body_template: dict[str, Any], identifier: str, password: str, order: int = 1) -> ScenarioStep | None:
        """Build a login step using the project-documented field names, not hardcoded {email,password}."""
        if not login_path.startswith("/") or not identifier:
            return None
        return ScenarioStep(
            order=order, action="login",
            api_method="POST", api_path=login_path,
            body_template=SemanticScenarioGenerator._fill_login_body(body_template, identifier, password),
            extract_from_response=["token"],
            expected_status=200, actor="readonly",
        )

    @staticmethod
    def _permission_slice(
        slice_meta: dict[str, Any], discovery_round: int,
    ) -> ExecutableScenario | None:
        """Build an actor-permission scenario from a permission slice.

        The scenario logs in as the declared actor, hits the target write
        endpoint, and expects a 401/403.  The PermissionOracle flags a
        200 as a privilege-escalation defect.
        """
        entity = str(slice_meta.get("entity") or "").strip()
        if not entity:
            return None
        actor_label = str(slice_meta.get("_permission_actor") or "").strip()
        email = str(slice_meta.get("_permission_email") or "").strip()
        password = str(slice_meta.get("_permission_password") or "").strip()
        method = str(slice_meta.get("_permission_method") or "").upper()
        path = str(slice_meta.get("_permission_path") or "")
        expected_permitted = slice_meta.get("_permission_expected_permitted") or []
        denied = "*" not in expected_permitted and method not in expected_permitted
        if not actor_label or not method or not path.startswith("/"):
            return None
        steps: list[ScenarioStep] = []
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, email, password, order=1)
        if step:
            steps.append(step)
        # If the target path contains a :param placeholder, insert a pre-step
        # that list-observes the collection endpoint to bind a real id at
        # runtime — otherwise the probe would send a literal :id to the server.
        probe_path = path
        if path_has_placeholders(normalize_path_placeholders(path)):
            coll = collection_path(normalize_path_placeholders(path))
            if coll and coll != "/" and not coll.endswith("/api"):
                steps.append(ScenarioStep(
                    order=len(steps) + 1,
                    action="resolve_entity_id",
                    api_method="GET", api_path=coll,
                    extract_from_response=["id"],
                    expected_status=200, actor=actor_label,
                ))
                probe_path = normalize_path_placeholders(path)
        steps.append(ScenarioStep(
            order=len(steps) + 1,
            action=f"permission_probe_{actor_label}",
            api_method=method, api_path=probe_path,
            expected_status=(200 if not denied else 403),
            actor=actor_label,
        ))
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "permission", actor_label, method, path),
            title=f"[Actor permission probe] {actor_label} → {method} {path}",
            description=f"验证角色 {actor_label} 是否被允许执行 {method} {path}",
            category="permission",
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[actor_label],
            steps=steps,
            oracle_rules=[
                "PermissionOracle.role_boundary_check",
                f"expected_permitted={','.join(expected_permitted) if expected_permitted else 'runtime_observed'}",
            ],
            confidence=float(slice_meta.get("priority") or 0.85),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="permission",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )

    @staticmethod
    def _isolation_slice(
        slice_meta: dict[str, Any], discovery_round: int, api_doc: str,
    ) -> ExecutableScenario | None:
        """Build a cross-user isolation scenario.

        UserA authenticates and reads an endpoint that lists UserB's resources.
        The TenantIsolationOracle flags cross-user data in the response.
        """
        entity = str(slice_meta.get("entity") or "").strip()
        if not entity:
            return None
        viewer_label = str(slice_meta.get("_isolation_viewer_role") or "").strip()
        viewer_email = str(slice_meta.get("_isolation_viewer_email") or "").strip()
        viewer_password = str(slice_meta.get("_isolation_viewer_password") or "").strip()
        path = str(slice_meta.get("_isolation_path") or "")
        if not viewer_label or not path.startswith("/"):
            return None
        steps: list[ScenarioStep] = []
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, viewer_email, viewer_password, order=1)
        if step:
            steps.append(step)
        # Resolve entity id if path has placeholders
        probe_path = path
        if path_has_placeholders(normalize_path_placeholders(path)):
            coll = collection_path(normalize_path_placeholders(path))
            if coll and coll != "/" and not coll.endswith("/api"):
                steps.append(ScenarioStep(
                    order=len(steps) + 1,
                    action="resolve_entity_id",
                    api_method="GET", api_path=coll,
                    extract_from_response=["id"],
                    expected_status=200, actor=viewer_label,
                ))
                probe_path = normalize_path_placeholders(path)
        steps.append(ScenarioStep(
            order=len(steps) + 1,
            action=f"isolation_probe_{viewer_label}",
            api_method="GET", api_path=probe_path,
            expected_status=200, actor=viewer_label,
        ))
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "isolation", viewer_label, path),
            title=f"[Data isolation probe] {viewer_label} → GET {path}",
            description=f"验证用户 {viewer_label} 不应看到其他用户的私有数据",
            category="isolation",
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[viewer_label],
            steps=steps,
            oracle_rules=["TenantIsolationOracle.cross_user_isolation"],
            confidence=float(slice_meta.get("priority") or 0.88),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="isolation",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )

    @staticmethod
    def _concurrency_slice(
        slice_meta: dict[str, Any], discovery_round: int,
    ) -> ExecutableScenario | None:
        """Build a double-write scenario to probe concurrency/mutual exclusion."""
        entity = str(slice_meta.get("entity") or "").strip()
        method = str(slice_meta.get("_concurrency_method") or "").upper()
        path = str(slice_meta.get("_concurrency_path") or "")
        if not entity or not method or not path.startswith("/"):
            return None
        actor_label = str(slice_meta.get("_default_actor") or "readonly").strip() or "readonly"
        email = str(slice_meta.get("_default_email") or "").strip()
        password = str(slice_meta.get("_default_password") or "").strip()
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        steps: list[ScenarioStep] = []
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, email, password, order=1)
        if step:
            steps.append(step)
        probe_path = path
        if path_has_placeholders(normalize_path_placeholders(path)):
            coll = collection_path(normalize_path_placeholders(path))
            if coll and coll != "/" and not coll.endswith("/api"):
                steps.append(ScenarioStep(
                    order=len(steps) + 1, action="resolve_entity_id",
                    api_method="GET", api_path=coll,
                    extract_from_response=["id"], expected_status=200, actor=actor_label,
                ))
                probe_path = normalize_path_placeholders(path)
        base = len(steps)
        for i in (1, 2):
            steps.append(ScenarioStep(
                order=base + i,
                action=f"concurrent_{method}_{i}",
                api_method=method, api_path=probe_path,
                expected_status=(200 if i == 1 else 409),
                actor=actor_label,
            ))
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "concurrency", method, path),
            title=f"[Concurrency probe] double {method} {path}",
            description=f"并发双发 {method} {path} 验证互斥或幂等行为",
            category="concurrency",
            is_concurrent=True,
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[actor_label],
            steps=steps,
            oracle_rules=["ConcurrencyOracle.race_condition_check"],
            confidence=float(slice_meta.get("priority") or 0.78),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="concurrency",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )

    @staticmethod
    def _money_slice(
        slice_meta: dict[str, Any], discovery_round: int,
    ) -> ExecutableScenario | None:
        """Build a financial-integrity observation scenario.

        Hits a write endpoint and lets MoneyOracle detect negative amounts,
        double-refund patterns, and balance anomalies in the responses. No
        assumption about which endpoints are financial — the oracle decides.
        """
        entity = str(slice_meta.get("entity") or "").strip()
        method = str(slice_meta.get("_money_method") or "").upper()
        path = str(slice_meta.get("_money_path") or "")
        if not entity or not method or not path.startswith("/"):
            return None
        actor_label = str(slice_meta.get("_default_actor") or "readonly").strip() or "readonly"
        email = str(slice_meta.get("_default_email") or "").strip()
        password = str(slice_meta.get("_default_password") or "").strip()
        login_path = str(slice_meta.get("_login_path") or "").strip()
        login_body = dict(slice_meta.get("_login_body") or {})
        # Observation endpoint = the parent collection of the write path,
        # derived purely structurally (no per-project endpoint map).
        read_path = _adjacent_read_for_entity(entity, path)
        probe_read = read_path
        probe_write = path
        steps: list[ScenarioStep] = []
        step = SemanticScenarioGenerator._build_login_step(
            login_path, login_body, email, password, order=1)
        if step:
            steps.append(step)
        if path_has_placeholders(normalize_path_placeholders(read_path)):
            coll = collection_path(normalize_path_placeholders(read_path))
            if coll and coll != "/" and not coll.endswith("/api"):
                steps.append(ScenarioStep(
                    order=len(steps) + 1, action="resolve_entity_id",
                    api_method="GET", api_path=coll,
                    extract_from_response=["id"], expected_status=200, actor=actor_label,
                ))
                probe_read = normalize_path_placeholders(read_path)
                probe_write = normalize_path_placeholders(path)
        steps.append(ScenarioStep(
            order=len(steps) + 1, action="observe_money_endpoint",
            api_method="GET", api_path=probe_read, expected_status=200, actor=actor_label,
        ))
        steps.append(ScenarioStep(
            order=len(steps) + 1, action=f"money_probe_{method}",
            api_method=method, api_path=probe_write, expected_status=200, actor=actor_label,
        ))
        steps.append(ScenarioStep(
            order=len(steps) + 1, action="observe_money_after",
            api_method="GET", api_path=probe_read, expected_status=200, actor=actor_label,
        ))
        return ExecutableScenario(
            id=SemanticScenarioGenerator._id(entity, "money", method, path),
            title=f"[Financial integrity probe] {method} {path}",
            description="验证资金操作的余额一致性、金额非负、无重复扣款",
            category="money",
            severity="P1",
            entity=entity,
            preconditions=[],
            actors=[actor_label],
            steps=steps,
            oracle_rules=["MoneyOracle.financial_integrity"],
            confidence=float(slice_meta.get("priority") or 0.82),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or [])],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="money",
            discovery_round=discovery_round,
            actor_token="",
            selection_origin="supplementary_active_slice",
        )


def _adjacent_read_for_entity(entity: str, write_path: str) -> str:
    """Derive the observation (GET) endpoint for a write path — structurally.

    A write like `/api/payments/pay` or `/api/orders/{id}/cancel` has its read
    counterpart at the resource collection `/api/payments` or `/api/orders`.
    We compute that purely from path structure — the first two segments form
    the resource collection — with no per-project or per-industry endpoint map.
    """
    normalized = normalize_path_placeholders(str(write_path or ""))
    parts = [p for p in normalized.strip("/").split("/") if p and "{" not in p]
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    coll = collection_path(normalized)
    return coll if coll.startswith("/") else normalized
