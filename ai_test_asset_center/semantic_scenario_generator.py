"""
SemanticScenarioGenerator — Generate executable business scenarios from state graph.

Turns BusinessStateGraph into concrete Scenario objects with:
- Preconditions (what state must the system be in)
- Multi-step action sequence with API call details
- Expected outcomes and oracle rules
- Cleanup/rollback plan

Part of QualiBug V12.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .business_state_graph import BusinessStateGraph, StateTransition


@dataclass
class ScenarioStep:
    """Single step in a scenario."""
    order: int
    action: str
    api_method: str = "POST"
    api_path: str = ""
    body_template: dict[str, Any] = field(default_factory=dict)
    extract_from_response: list[str] = field(default_factory=list)  # Fields to bind
    expected_status: int = 200
    actor: str = "admin"


@dataclass
class ExecutableScenario:
    """A complete, executable business scenario."""
    id: str
    title: str
    description: str = ""
    category: str = "state_machine"  # state_machine | invariant | money | permission | concurrency
    severity: str = "P1"
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
    confidence: float = 0.8
    actor_token: str = ""  # Auth token for scenario execution

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "severity": self.severity,
            "entity": self.entity,
            "preconditions": self.preconditions,
            "actors": self.actors,
            "steps": [{"order": s.order, "action": s.action, "method": s.api_method,
                       "path": s.api_path, "body": s.body_template,
                       "extract": s.extract_from_response, "expected": s.expected_status,
                       "actor": s.actor}
                      for s in self.steps],
            "expected_state": self.expected_state,
            "oracle_rules": self.oracle_rules,
            "cleanup": [s.action for s in self.cleanup_steps],
            "flags": {"forbidden": self.is_forbidden_path, "boundary": self.is_boundary_path,
                      "concurrent": self.is_concurrent},
            "confidence": self.confidence,
        }


class SemanticScenarioGenerator:
    """Generate executable scenarios from business state graphs."""

    def _build_path_map(self, api_doc: str) -> dict[str, dict[str, tuple[str, str]]]:
        """Build {entity: {action: (HTTP_METHOD, /api/path)}} from OpenAPI spec."""
        path_map: dict[str, dict[str, tuple[str, str]]] = {}
        if not api_doc:
            return path_map
        try:
            import json as _json
            spec = _json.loads(api_doc) if api_doc.strip().startswith("{") else None
            if not spec or "paths" not in spec:
                return path_map
            for path, methods in spec["paths"].items():
                if not isinstance(methods, dict):
                    continue
                # Strip /api/ prefix and split into segments
                clean = path.strip("/")
                if clean.startswith("api/"):
                    clean = clean[4:]
                segs = [s for s in clean.split("/") if s and not s.startswith("{") and not s.startswith(":")]
                if not segs:
                    continue
                entity = segs[0].rstrip("s")  # Plural → singular
                # Action is the last non-param segment
                action = segs[-1] if len(segs) > 1 else "list"
                # Map common CRUD verbs
                for method in ("POST", "PUT", "PATCH", "GET", "DELETE"):
                    if method.lower() in methods:
                        if entity not in path_map:
                            path_map[entity] = {}
                        path_map[entity][action] = (method, path)
                        # Also map the plural form
                        entity_plural = segs[0]
                        if entity_plural not in path_map:
                            path_map[entity_plural] = {}
                        path_map[entity_plural][action] = (method, path)
                        break
        except Exception:
            pass
        return path_map

    def _resolve_path(self, entity: str, action: str, path_map: dict,
                      fallback_method: str = "POST",
                      fallback_path: str = "") -> tuple[str, str]:
        """Resolve (method, path) from path map, with fallback."""
        # Try exact match
        for e in (entity, entity.rstrip("s"), entity + "s"):
            if e in path_map and action in path_map[e]:
                return path_map[e][action]
            # Try partial action match
            if e in path_map:
                for act, mp in path_map[e].items():
                    if action in act or act in action:
                        return mp
        # Fallback
        return (fallback_method, fallback_path or f"/api/{entity}s/{{id}}/{action}")

    def generate(self, graphs: dict[str, BusinessStateGraph], api_doc: str = "") -> list[ExecutableScenario]:
        scenarios: list[ExecutableScenario] = []
        self._path_map = self._build_path_map(api_doc)

        for entity, graph in graphs.items():
            if not graph.transitions:
                continue

            # 1. Forbidden path scenarios (highest priority bugs)
            for t in graph.forbidden_paths():
                scenarios.extend(self._forbidden_scenario(entity, t, graph, api_doc))

            # 2. Boundary path scenarios
            for t in graph.boundary_paths():
                scenarios.extend(self._boundary_scenario(entity, t, graph, api_doc))

            # 3. Invariant violation scenarios
            for state_name, node in graph.states.items():
                if node.invariants:
                    scenarios.extend(self._invariant_scenario(entity, state_name, node, graph, api_doc))

            # 4. Money/conservation scenarios
            if entity in ("order", "payment"):
                scenarios.extend(self._money_scenarios(entity, graph, api_doc))

            # 5. Permission scenarios
            if entity == "user":
                scenarios.extend(self._permission_scenarios(graph, api_doc))

            # 6. Concurrency scenarios
            scenarios.extend(self._concurrency_scenarios(entity, graph, api_doc))

            # ── V12.2 NEW: 4 scenario types ──

            # 7. State-destructive: skip intermediate states
            scenarios.extend(self._state_destructive_scenarios(entity, graph, api_doc))

            # 8. Permission-bypass: lower role attempts restricted actions
            scenarios.extend(self._permission_bypass_scenarios(entity, graph, api_doc))

            # 9. Concurrency-conflict: two actors race for same resource
            scenarios.extend(self._concurrency_conflict_scenarios(entity, graph, api_doc))

            # 10. Idempotency-break: same action executed multiple times
            scenarios.extend(self._idempotency_break_scenarios(entity, graph, api_doc))

        # Deduplicate by title
        seen = set()
        unique = []
        for s in scenarios:
            key = s.title.strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(s)

        return unique

    def _forbidden_scenario(self, entity: str, t: StateTransition, graph: BusinessStateGraph,
                            api_doc: str) -> list[ExecutableScenario]:
        """Generate scenario for a forbidden state transition."""
        scenario_id = f"SCN_FORBIDDEN_{entity}_{t.from_state}_{t.action}_{uuid.uuid4().hex[:6]}"
        title = f"[禁止路径] {entity}: 从'{t.from_state}'执行'{t.action}' → '{t.to_state}'"

        # Step 1: Set up precondition (get entity into {from_state})
        setup_steps = self._setup_steps(entity, t.from_state, graph, api_doc)

        # Step 2: The forbidden action itself
        action_step = ScenarioStep(
            order=len(setup_steps) + 1,
            action=t.action,
            api_method="POST",
            api_path=t.api_endpoint or f"/api/{entity}s/{{id}}/{t.action}",
            body_template={},
            extract_from_response=["id", "status"],
            expected_status=200,  # We expect it to succeed (that IS the bug)
            actor="admin",
        )

        # Step 3: Verify state after (it should NOT have changed)
        verify_step = ScenarioStep(
            order=len(setup_steps) + 2,
            action="verify",
            api_method="GET",
            api_path=self._resolve_path(entity, "get", self._path_map, "GET", f"/api/{entity}s/{{id}}")[1],
            body_template={},
            extract_from_response=["status"],
            expected_status=200,
            actor="admin",
        )

        return [ExecutableScenario(
            id=scenario_id, title=title,
            description=f"测试{entity}在'{t.from_state}'状态下调用'{t.action}'是否被错误允许。该操作应被拒绝。",
            category="state_machine", severity="P0", entity=entity,
            preconditions=[f"{entity}处于'{t.from_state}'状态"],
            actors=["admin"],
            steps=setup_steps + [action_step, verify_step],
            expected_state=t.from_state,  # Should NOT have changed
            oracle_rules=["StateOracle.forbidden_transition_blocked",
                         f"forbidden: {t.from_state} ↛ {t.to_state} via {t.action}"],
            cleanup_steps=[ScenarioStep(order=99, action="cleanup", api_method="DELETE",
                                        api_path=self._resolve_path(entity, "get", self._path_map, "GET", f"/api/{entity}s/{{id}}")[1])],
            is_forbidden_path=True,
            confidence=0.92,
        )]

    def _boundary_scenario(self, entity: str, t: StateTransition, graph: BusinessStateGraph,
                           api_doc: str) -> list[ExecutableScenario]:
        scenario_id = f"SCN_BOUNDARY_{entity}_{t.from_state}_{t.action}_{uuid.uuid4().hex[:6]}"
        title = f"[边界路径] {entity}: '{t.from_state}' → '{t.to_state}' via '{t.action}'"

        steps = self._setup_steps(entity, t.from_state, graph, api_doc)
        steps.append(ScenarioStep(
            order=len(steps) + 1, action=t.action, api_method="POST",
            api_path=t.api_endpoint or f"/api/{entity}s/{{id}}/{t.action}",
            body_template={}, extract_from_response=["status"],
            expected_status=200,
        ))

        return [ExecutableScenario(
            id=scenario_id, title=title,
            category="state_machine", severity="P1", entity=entity,
            preconditions=[f"{entity}处于'{t.from_state}'"],
            actors=["admin"], steps=steps,
            expected_state=t.to_state,
            oracle_rules=[f"StateOracle.valid_transition: {t.from_state}→{t.to_state}"],
            is_boundary_path=True, confidence=0.70,
        )]

    def _invariant_scenario(self, entity: str, state_name: str, node, graph: BusinessStateGraph,
                            api_doc: str) -> list[ExecutableScenario]:
        scenarios = []
        for invariant in node.invariants[:3]:
            sid = f"SCN_INV_{entity}_{state_name}_{uuid.uuid4().hex[:6]}"
            scenarios.append(ExecutableScenario(
                id=sid,
                title=f"[不变量] {entity}在'{state_name}'时: {invariant[:80]}",
                category="invariant", severity="P0", entity=entity,
                preconditions=[f"{entity}='{state_name}'"],
                actors=["admin"],
                steps=self._setup_steps(entity, state_name, graph, api_doc),
                expected_state=state_name,
                oracle_rules=["ConsistencyOracle.check_invariant", invariant[:200]],
                confidence=0.75,
            ))
        return scenarios

    def _money_scenarios(self, entity: str, graph: BusinessStateGraph, api_doc: str) -> list[ExecutableScenario]:
        scenarios = []
        # Double refund scenario
        if entity == "payment":
            sid = f"SCN_MONEY_DOUBLE_REFUND_{uuid.uuid4().hex[:6]}"
            steps = [
                ScenarioStep(order=1, action="create_order", api_method="POST",
                            api_path="/api/orders", body_template={"product_id": 1, "quantity": 1},
                            extract_from_response=["order_id"]),
                ScenarioStep(order=2, action="pay", api_method="POST",
                            api_path="/api/orders/{order_id}/pay", body_template={},
                            extract_from_response=["payment_id"]),
                ScenarioStep(order=3, action="refund_1", api_method="POST",
                            api_path="/api/orders/{order_id}/refund", body_template={},
                            extract_from_response=["status"]),
                ScenarioStep(order=4, action="refund_2（重复）", api_method="POST",
                            api_path="/api/orders/{order_id}/refund", body_template={},
                            expected_status=400, extract_from_response=["status", "error"]),
            ]
            scenarios.append(ExecutableScenario(
                id=sid, title="[资金安全] 重复退款 — 已退款订单再次退款",
                category="money", severity="P0", entity=entity,
                preconditions=["存在已支付订单", "已完成一次退款"],
                actors=["admin", "buyer"], steps=steps,
                expected_state="第二次退款被拒绝，金额不变",
                oracle_rules=["MoneyOracle.refund_amount_lte_paid_amount",
                             "IdempotentOracle.no_duplicate_refund"],
                is_forbidden_path=True, confidence=0.95,
            ))

        # Negative amount scenario
        sid2 = f"SCN_MONEY_NEG_{uuid.uuid4().hex[:6]}"
        scenarios.append(ExecutableScenario(
            id=sid2, title="[资金安全] 负金额支付 — 支付金额≤0应被拒绝",
            category="money", severity="P0", entity=entity,
            preconditions=["存在订单"],
            actors=["buyer"],
            steps=[ScenarioStep(order=1, action="pay_negative", api_method="POST",
                               api_path="/api/orders/{id}/pay",
                               body_template={"amount": -100},
                               expected_status=400)],
            expected_state="支付被拒绝",
            oracle_rules=["MoneyOracle.amount_must_be_positive"],
            confidence=0.90,
        ))

        return scenarios

    def _permission_scenarios(self, graph: BusinessStateGraph, api_doc: str) -> list[ExecutableScenario]:
        scenarios = []
        sid = f"SCN_PERM_{uuid.uuid4().hex[:6]}"
        scenarios.append(ExecutableScenario(
            id=sid, title="[权限越权] 买家查看管理统计数据",
            category="permission", severity="P0", entity="user",
            preconditions=["登录为普通买家"],
            actors=["buyer"],
            steps=[ScenarioStep(order=1, action="view_stats", api_method="GET",
                               api_path="/api/admin/stats",
                               expected_status=403, actor="buyer")],
            expected_state="访问被拒绝(403)",
            oracle_rules=["PermissionOracle.buyer_cannot_access_admin_stats"],
            confidence=0.90,
        ))
        return scenarios

    def _concurrency_scenarios(self, entity: str, graph: BusinessStateGraph, api_doc: str) -> list[ExecutableScenario]:
        scenarios = []
        sid = f"SCN_RACE_{entity}_{uuid.uuid4().hex[:6]}"
        scenarios.append(ExecutableScenario(
            id=sid, title=f"[并发] {entity}双重提交 — 同请求两次执行应只成功一次",
            category="concurrency", severity="P0", entity=entity,
            preconditions=[f"可对{entity}执行写操作"],
            actors=["admin"],
            steps=[ScenarioStep(order=1, action=f"create_{entity}", api_method="POST",
                               api_path=self._resolve_path(entity, "list", self._path_map, "GET", f"/api/{entity}s")[1],
                               body_template={}, extract_from_response=["id"])],
            expected_state="第二次创建被拒绝（幂等）",
            oracle_rules=["IdempotentOracle.no_duplicate_create"],
            is_concurrent=True, confidence=0.85,
        ))
        return scenarios

    def _setup_steps(self, entity: str, target_state: str, graph: BusinessStateGraph,
                     api_doc: str) -> list[ScenarioStep]:
        """Generate setup steps using endpoint discovery from API doc — no hardcoded paths."""
        steps = []
        
        # Dynamically discover endpoints from api_doc
        endpoints = self._parse_endpoints(api_doc, entity)
        create_ep = endpoints.get("create") or f"/api/{entity}s"
        actions = {k: v for k, v in endpoints.items() if k != "create"}
        
        # Step 1: Create entity
        steps.append(ScenarioStep(
            order=1, action="create", api_method="POST",
            api_path=create_ep,
            body_template={"items": [{"sku": "SKU-PHONE-001", "qty": 1}], "addressId": "test-addr"} if entity == "order" else (
                {"sku": "SKU-PHONE-001", "title": "Test", "category": "test", "price": "99.00"} if entity == "product" else {}
            ),
            extract_from_response=[entity],  # Extract nested entity object
            expected_status=201,
            actor="admin",
        ))
        
        # Step 2: If target_state requires an action, use discovered endpoint
        if target_state in actions:
            steps.append(ScenarioStep(
                order=2, action=target_state, api_method="POST",
                api_path=actions[target_state],
                body_template={},
                extract_from_response=["status"],
                actor="admin",
            ))
        
        return steps
    
    def _parse_endpoints(self, api_doc: str, entity: str) -> dict:
        """Parse API doc to discover endpoints dynamically.
        
        Extract actions from OpenAPI JSON or markdown heading-style paths.
        Returns {"create": "/api/orders", "cancel": "/api/orders/{id}/cancel", ...}
        """
        import re
        endpoints = {}
        
        # Try OpenAPI JSON first
        if api_doc.strip().startswith("{"):
            try:
                import json as _json
                spec = _json.loads(api_doc)
                paths = spec.get("paths", {})
                for path, methods in paths.items():
                    if not isinstance(methods, dict):
                        continue
                    # Check if path relates to this entity
                    path_lower = path.lower()
                    entity_lower = entity.lower()
                    entity_plural = entity + "s"
                    if not (entity_lower in path_lower or entity_plural in path_lower):
                        continue
                    # Extract action from path
                    clean = path.strip("/")
                    segs = [s for s in clean.split("/") if s and not s.startswith("{") and not s.startswith(":")]
                    if len(segs) >= 2:
                        action = segs[-1]
                    else:
                        # /api/orders → create (POST) or list (GET)
                        has_post = any(m.lower() == "post" for m in methods)
                        action = "create" if has_post else "list"
                    endpoints[action] = path
                    # Also map with method prefix for disambiguation
                    first_method = next(iter(methods), "get")
                    endpoints[f"{first_method}:{action}"] = path
                if endpoints:
                    return endpoints
            except Exception:
                pass
        
        # Fallback: regex against raw text (heading or table format)
        for line in api_doc.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Heading format: ### METHOD /api/entity/action
            m = re.match(r'^#{1,6}\s*(GET|POST|PUT|PATCH|DELETE)\s+(/api/\S+)', line, re.IGNORECASE)
            if m:
                method, path = m.group(1), m.group(2).rstrip('`')
                if entity.lower() in path.lower():
                    segs = [s for s in path.strip("/").split("/") if s and not s.startswith("{") and not s.startswith(":")]
                    action = segs[-1] if len(segs) >= 2 else ("create" if method.upper() == "POST" else "list")
                    endpoints[action] = path
                continue
            # Table format: | METHOD | /path | desc |
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            method, path = parts[1], parts[2]
            if entity.lower() not in path.lower():
                continue
            if "{" in path or ":" in path:
                action_match = re.search(r'/(\w+)$', path.split("?")[0])
                if action_match:
                    endpoints[action_match.group(1)] = path
            elif method.upper() == "POST":
                endpoints["create"] = path
                
        return endpoints

    # ── V12.2: 4 New Scenario Types ──

    def _state_destructive_scenarios(self, entity: str, graph: BusinessStateGraph,
                                     api_doc: str) -> list[ExecutableScenario]:
        """Generate state-destructive scenarios: skip mandatory intermediate states."""
        scenarios = []
        normal = graph.normal_paths()
        if len(normal) >= 3:
            # e.g., created→paid, skip "confirmed" — jump directly
            for i in range(len(normal) - 2):
                sid = f"SCN_DESTROY_{entity}_{uuid.uuid4().hex[:6]}"
                steps = self._setup_steps(entity, normal[i].from_state, graph, api_doc)
                steps.append(ScenarioStep(order=len(steps)+1, action=normal[i+2].action,
                    api_method="POST", api_path=normal[i+2].api_endpoint or f"/api/{entity}s/{{id}}/{normal[i+2].action}",
                    body_template={}, expected_status=403, actor="admin"))
                scenarios.append(ExecutableScenario(
                    id=sid, title=f"[状态破坏] {entity}: 跳过{normal[i+1].from_state}直接{normal[i+2].action}",
                    category="state_machine", severity="P0", entity=entity,
                    preconditions=[f"{entity}在'{normal[i].from_state}'，尝试跳过'{normal[i+1].from_state}'"],
                    actors=["admin"], steps=steps,
                    expected_state=normal[i].from_state,
                    oracle_rules=["StateOracle.forbidden_transition_blocked"],
                    is_forbidden_path=True, confidence=0.88))
        return scenarios

    def _permission_bypass_scenarios(self, entity: str, graph: BusinessStateGraph,
                                     api_doc: str) -> list[ExecutableScenario]:
        """Generate permission-bypass scenarios: buyer tries admin actions."""
        scenarios = []
        actions = ["cancel", "refund", "ship", "complete"]
        for action in actions:
            sid = f"SCN_PERM_BYPASS_{entity}_{action}_{uuid.uuid4().hex[:6]}"
            steps = [
                ScenarioStep(order=1, action="create", api_method="POST",
                    api_path=self._resolve_path(entity, "list", self._path_map, "GET", f"/api/{entity}s")[1], body_template={}, actor="buyer"),
                ScenarioStep(order=2, action=action, api_method="POST",
                    api_path=f"/api/{entity}s/{{id}}/{action}",
                    body_template={}, expected_status=403, actor="buyer"),
            ]
            scenarios.append(ExecutableScenario(
                id=sid, title=f"[权限穿透] {entity}: 买家执行{action}(应403)",
                category="permission", severity="P0", entity=entity,
                preconditions=["登录为买家"], actors=["buyer"], steps=steps,
                expected_state=f"{action}被拒绝",
                oracle_rules=["PermissionOracle.unauthorized_access"],
                confidence=0.92))
        return scenarios

    def _concurrency_conflict_scenarios(self, entity: str, graph: BusinessStateGraph,
                                        api_doc: str) -> list[ExecutableScenario]:
        """Generate concurrency-conflict scenarios: two actors race."""
        scenarios = []
        sid = f"SCN_CONC_CONFLICT_{entity}_{uuid.uuid4().hex[:6]}"
        steps = [
            ScenarioStep(order=1, action="create_A", api_method="POST",
                api_path=self._resolve_path(entity, "list", self._path_map, "GET", f"/api/{entity}s")[1], body_template={}, actor="admin"),
            ScenarioStep(order=2, action="create_B(concurrent)", api_method="POST",
                api_path=self._resolve_path(entity, "list", self._path_map, "GET", f"/api/{entity}s")[1], body_template={}, actor="buyer"),
        ]
        scenarios.append(ExecutableScenario(
            id=sid, title=f"[并发冲突] {entity}: 双角色同时操作同一资源",
            category="concurrency", severity="P0", entity=entity,
            preconditions=["两个不同角色的用户"], actors=["admin","buyer"],
            steps=steps, expected_state="操作互斥或幂等",
            oracle_rules=["ConcurrencyOracle.race_condition", "IdempotencyOracle"],
            is_concurrent=True, confidence=0.85))
        return scenarios

    def _idempotency_break_scenarios(self, entity: str, graph: BusinessStateGraph,
                                     api_doc: str) -> list[ExecutableScenario]:
        """Generate idempotency-break scenarios: same action repeated."""
        scenarios = []
        mutable_actions = ["create", "cancel", "refund", "ship"]
        for action in mutable_actions[:2]:
            sid = f"SCN_IDEM_BREAK_{entity}_{action}_{uuid.uuid4().hex[:6]}"
            steps = [
                ScenarioStep(order=1, action=f"{action}_1st", api_method="POST",
                    api_path=self._resolve_path(entity, "list", self._path_map, "GET", f"/api/{entity}s")[1] if action == "create" else f"/api/{entity}s/{{id}}/{action}",
                    body_template={}, extract_from_response=["id" if action=="create" else "status"],
                    actor="admin"),
                ScenarioStep(order=2, action=f"{action}_2nd(复复)", api_method="POST",
                    api_path=self._resolve_path(entity, "list", self._path_map, "GET", f"/api/{entity}s")[1] if action == "create" else f"/api/{entity}s/{{id}}/{action}",
                    body_template={}, expected_status=409 if action=="create" else 400,
                    actor="admin"),
            ]
            scenarios.append(ExecutableScenario(
                id=sid, title=f"[幂等破坏] {entity}: 重复{action}应幂等拒绝",
                category="concurrency", severity="P0", entity=entity,
                preconditions=[f"已完成一次{action}"], actors=["admin"],
                steps=steps, expected_state="第二次被幂等拒绝",
                oracle_rules=["IdempotencyOracle.duplicate_create"],
                confidence=0.90))
        return scenarios
