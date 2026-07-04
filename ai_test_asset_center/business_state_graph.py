"""
BusinessStateGraph — Multi-entity state graph with conditions, conflicts, and risk scoring.

V12.2 upgrade: StateDependency edges, trigger conditions, conflict detection,
risk_score per node, and cross-entity transition validation.

Part of QualiBug V12.2: "业务状态空间的自动探索与失败发现系统"
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StateNode:
    entity: str
    state: str
    invariants: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)     # Pre-conditions to enter this state
    constraints: list[str] = field(default_factory=list)    # Must-hold invariants
    risk_score: float = 0.0                                  # 0-1, higher = riskier
    depends_on: list[tuple[str, str]] = field(default_factory=list)  # [(entity, state), ...]
    conflicts_with: list[str] = field(default_factory=list)          # Conflicting state names
    observed_from_api: bool = False
    observed_from_doc: bool = False


@dataclass
class StateTransition:
    from_state: str
    to_state: str
    action: str
    api_endpoint: str = ""
    guard_conditions: list[str] = field(default_factory=list)
    trigger_conditions: list[str] = field(default_factory=list)  # What must be true to fire
    is_normal: bool = True
    is_forbidden: bool = False
    is_boundary: bool = False
    is_concurrent: bool = False
    risk_score: float = 0.0
    depends_on_entity: str = ""   # Cross-entity dependency
    depends_on_state: str = ""


@dataclass
class StateEdge:
    """Dependency edge between two entities' states."""
    source_entity: str
    source_state: str
    target_entity: str
    target_state: str
    relation: str = "depends_on"   # depends_on | conflicts | triggers | blocks
    condition: str = ""
    risk_score: float = 0.0


@dataclass
class BusinessStateGraph:
    entity: str
    states: dict[str, StateNode] = field(default_factory=dict)
    transitions: list[StateTransition] = field(default_factory=list)
    edges: list[StateEdge] = field(default_factory=list)  # Cross-entity dependencies

    def add_state(self, name: str, invariants: list[str] | None = None,
                  conditions: list[str] | None = None, risk_score: float = 0.0):
        if name not in self.states:
            self.states[name] = StateNode(entity=self.entity, state=name,
                                          invariants=invariants or [],
                                          conditions=conditions or [],
                                          risk_score=risk_score)
        else:
            node = self.states[name]
            if invariants: node.invariants.extend(invariants)
            if conditions: node.conditions.extend(conditions)
            if risk_score > 0: node.risk_score = max(node.risk_score, risk_score)

    def add_transition(self, t: StateTransition):
        self.transitions.append(t)
        self.add_state(t.from_state)
        self.add_state(t.to_state)
        # Set risk score from transition type
        if t.is_forbidden:
            self.states[t.to_state].risk_score = max(self.states[t.to_state].risk_score, 0.9)
        if t.is_boundary:
            self.states[t.to_state].risk_score = max(self.states[t.to_state].risk_score, 0.5)

    def add_edge(self, source_entity: str, source_state: str, target_entity: str,
                 target_state: str, relation: str = "depends_on", condition: str = ""):
        risk = 0.7 if relation == "conflicts" else 0.4
        self.edges.append(StateEdge(source_entity, source_state, target_entity,
                                    target_state, relation, condition, risk))

    def conflict_states(self) -> list[StateTransition]:
        """Find transitions that conflict (same from_state, different to_state on same action)."""
        by_action = defaultdict(list)
        for t in self.transitions:
            by_action[(t.from_state, t.action)].append(t)
        return [t for pairs in by_action.values() if len(pairs) > 1 for t in pairs]

    def top_risk_states(self, n: int = 5) -> list[StateNode]:
        return sorted(self.states.values(), key=lambda s: s.risk_score, reverse=True)[:n]

    def forbidden_paths(self) -> list[StateTransition]:
        return [t for t in self.transitions if t.is_forbidden]

    def normal_paths(self) -> list[StateTransition]:
        return [t for t in self.transitions if t.is_normal and not t.is_forbidden]

    def boundary_paths(self) -> list[StateTransition]:
        return [t for t in self.transitions if t.is_boundary]

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "states": {k: {"state": v.state, "invariants": v.invariants,
                            "conditions": v.conditions, "constraints": v.constraints,
                            "risk_score": v.risk_score, "depends_on": v.depends_on,
                            "conflicts_with": v.conflicts_with}
                       for k, v in self.states.items()},
            "transitions": [{"from": t.from_state, "to": t.to_state, "action": t.action,
                             "endpoint": t.api_endpoint, "normal": t.is_normal,
                             "forbidden": t.is_forbidden, "boundary": t.is_boundary,
                             "risk_score": t.risk_score, "triggers": t.trigger_conditions,
                             "depends_on": f"{t.depends_on_entity}/{t.depends_on_state}" if t.depends_on_entity else ""}
                            for t in self.transitions],
            "edges": [{"source": f"{e.source_entity}/{e.source_state}",
                       "target": f"{e.target_entity}/{e.target_state}",
                       "relation": e.relation, "condition": e.condition}
                      for e in self.edges],
            "stats": {
                "total_states": len(self.states),
                "total_transitions": len(self.transitions),
                "cross_entity_edges": len(self.edges),
                "forbidden_paths": len(self.forbidden_paths()),
                "conflict_paths": len(self.conflict_states()),
                "top_risk": [(s.state, s.risk_score) for s in self.top_risk_states(5)],
            }
        }


class BusinessStateGraphBuilder:
    """Build BusinessStateGraph from project context and API documentation.

    Extraction sources (in priority order):
    1. PRD/MRD text — explicit state descriptions
    2. API endpoint enums — status fields with enumerated values
    3. Database schema — status columns with CHECK constraints
    4. Common patterns — inferred from entity names
    """

    # Common state transition patterns per entity type
    ENTITY_PATTERNS = {
        "order": {
            "states": ["created", "pending", "confirmed", "paid", "shipped", "completed",
                       "cancelled", "refunding", "refunded", "disputed"],
            "normal": [
                ("created", "confirmed", "confirm"),
                ("confirmed", "paid", "pay"),
                ("paid", "shipped", "ship"),
                ("shipped", "completed", "complete"),
                ("created", "cancelled", "cancel"),
                ("paid", "refunding", "request_refund"),
                ("refunding", "refunded", "confirm_refund"),
            ],
            "forbidden": [
                ("cancelled", "paid", "pay"),          # Payment after cancel
                ("refunded", "refunded", "refund"),    # Double refund
                ("completed", "cancelled", "cancel"),  # Cancel after complete
                ("cancelled", "shipped", "ship"),      # Ship after cancel
                ("refunded", "shipped", "ship"),       # Ship after refund
            ],
            "boundary": [
                ("pending", "cancelled", "timeout_cancel"),
                ("refunding", "cancelled", "reject_refund"),
            ],
        },
        "payment": {
            "states": ["pending", "processing", "completed", "failed", "refunded", "charged_back"],
            "normal": [
                ("pending", "processing", "process"),
                ("processing", "completed", "complete"),
                ("processing", "failed", "fail"),
                ("completed", "refunded", "refund"),
            ],
            "forbidden": [
                ("refunded", "refunded", "refund"),     # Double refund
                ("failed", "completed", "force_complete"),
                ("completed", "charged_back", "chargeback"),
                ("charged_back", "refunded", "refund"),  # Refund after chargeback
            ],
        },
        "inventory": {
            "states": ["available", "reserved", "deducted", "returned", "expired", "damaged"],
            "normal": [
                ("available", "reserved", "reserve"),
                ("reserved", "deducted", "deduct"),
                ("deducted", "returned", "return"),
            ],
            "forbidden": [
                ("deducted", "deducted", "deduct"),     # Double deduction
                ("expired", "reserved", "reserve"),     # Reserve expired stock
            ],
        },
        "user": {
            "states": ["active", "inactive", "suspended", "deleted"],
            "normal": [
                ("active", "inactive", "deactivate"),
                ("inactive", "active", "reactivate"),
                ("active", "suspended", "suspend"),
            ],
            "forbidden": [
                ("suspended", "suspended", "suspend"),  # Double suspend
                ("deleted", "active", "reactivate"),    # Reactivate deleted
            ],
        },
    }

    def __init__(self):
        self.graphs: dict[str, BusinessStateGraph] = {}

    def build(self, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, BusinessStateGraph]:
        """Build state graphs for all detected entities."""

        # 1. Extract entities from PRD
        entities = self._extract_entities(prd_text)

        # 1.5 Extract available API actions per entity
        api_actions = self._extract_api_actions(api_spec_text)

        # 2. Extract states from API enum values
        api_states = self._extract_api_states(api_spec_text)

        # 3. Build graph per entity
        for entity in entities:
            graph = BusinessStateGraph(entity=entity)
            pattern = self.ENTITY_PATTERNS.get(entity, self._generic_pattern(entity))

            # Add known states
            for state_name in pattern.get("states", []):
                invariants = self._extract_invariants(prd_text, entity, state_name)
                graph.add_state(state_name, invariants)

            # Merge API-observed states
            for state_name in api_states.get(entity, []):
                graph.add_state(state_name)
                if state_name in graph.states:
                    graph.states[state_name].observed_from_api = True

            # Filter transitions to only include actions available in the API
            available = api_actions.get(entity, set()) | api_actions.get(entity + 's', set())
            # Add normal transitions
            for from_s, to_s, action in pattern.get("normal", []):
                if available and action not in available:
                    continue  # Skip transitions with no matching API endpoint
                graph.add_transition(StateTransition(
                    from_state=from_s, to_state=to_s, action=action,
                    is_normal=True, is_forbidden=False,
                    api_endpoint=self._find_endpoint(api_spec_text, entity, action),
                ))

            # Add forbidden transitions (high-value bug sources)
            for from_s, to_s, action in pattern.get("forbidden", []):
                graph.add_transition(StateTransition(
                    from_state=from_s, to_state=to_s, action=action,
                    is_normal=False, is_forbidden=True, is_boundary=False,
                    guard_conditions=[f"State must NOT be '{from_s}' when calling '{action}'"],
                ))

            # Add boundary transitions
            for from_s, to_s, action in pattern.get("boundary", []):
                if available and action not in available:
                    continue
                graph.add_transition(StateTransition(
                    from_state=from_s, to_state=to_s, action=action,
                    is_normal=False, is_forbidden=False, is_boundary=True,
                ))

            self.graphs[entity] = graph

        return self.graphs

    def _extract_entities(self, prd: str) -> list[str]:
        """Extract business entities from PRD text."""
        entities = set()

        # Look for entity keywords
        entity_keywords = [
            "订单", "order", "支付", "payment", "退款", "refund",
            "库存", "inventory", "商品", "product", "用户", "user",
            "会员", "member", "优惠券", "coupon", "地址", "address",
            "物流", "shipment", "账单", "invoice", "账户", "account",
            "审批", "approval", "工单", "ticket", "任务", "task",
        ]

        for kw in entity_keywords:
            if kw.lower() in prd.lower():
                # Map Chinese/English to internal entity names
                mapping = {
                    "订单": "order", "order": "order",
                    "支付": "payment", "payment": "payment",
                    "退款": "payment", "refund": "payment",
                    "库存": "inventory", "inventory": "inventory",
                    "商品": "product", "product": "product",
                    "用户": "user", "user": "user",
                    "会员": "user", "member": "user",
                }
                entities.add(mapping.get(kw, kw))

        # Only include entities actually detected; never inject default entities
        # (injecting order/payment/user into non-ecommerce projects creates false state machines)
        return list(entities)[:20]  # raised from 8 to 20 — practical upper bound

    def _extract_api_states(self, api: str) -> dict[str, list[str]]:
        """Extract state/status values from API enum definitions."""
        states: dict[str, list[str]] = {}
        if not api:
            return states

        # Try OpenAPI enum extraction
        try:
            spec = json.loads(api)
            schemas = spec.get("components", {}).get("schemas", {})
            for name, schema in schemas.items():
                if isinstance(schema, dict):
                    props = schema.get("properties", {})
                    for prop_name, prop in props.items():
                        if "status" in prop_name.lower() or "state" in prop_name.lower():
                            enum_vals = prop.get("enum", [])
                            if enum_vals:
                                entity = name.lower().replace(" ", "_")
                                states[entity] = [str(v) for v in enum_vals]
        except (json.JSONDecodeError, TypeError, KeyError, AttributeError) as e:
            import sys
            print(f"  [WARN] business_state_graph: OpenAPI enum extraction failed: {e}", flush=True, file=sys.stderr)

        # Try Markdown table extraction
        enum_pattern = re.findall(r'(?:status|状态|state)[：:\s]*([\w\s,、，/]+)', api, re.IGNORECASE)
        for match in enum_pattern:
            vals = re.split(r'[,、，/\s]+', match.strip())
            vals = [v for v in vals if v]
            if vals:
                # Try to determine entity from context
                if "order" in api.lower():
                    states.setdefault("order", []).extend(vals)
                elif "payment" in api.lower():
                    states.setdefault("payment", []).extend(vals)

        return states

    def _extract_invariants(self, prd: str, entity: str, state: str) -> list[str]:
        """Extract business invariants for a given entity state."""
        invariants = []
        # Extract mandatory/prohibited rules (language-agnostic)
        rule_pattern = re.compile(
            r'(?:必须|不得|禁止|不可|不能|must\s+not|shall\s+not|should\s+not|'
            r'need\s+to|should|require).{3,200}?(?:[。.!！\n]|$)',
            re.IGNORECASE,
        )
        for match in rule_pattern.finditer(prd):
            text = match.group(0).strip()
            if len(text) > 10:
                invariants.append(text[:200])
        # Entity-specific invariants: scan for entity name near rule keywords
        # Use both English and common Chinese translations
        cn_map = {"order": "订单", "payment": "支付", "user": "用户", "inventory": "库存",
                  "product": "商品", "refund": "退款", "shipment": "发货", "task": "任务"}
        entity_names = {entity, cn_map.get(entity, entity)}
        for ename in entity_names:
            if not ename:
                continue
            ent_pattern = re.compile(
                re.escape(ename) + r'.{0,100}?(?:不可|不能|must\s+not|should\s+not).{0,200}',
                re.IGNORECASE,
            )
            for match in ent_pattern.finditer(prd):
                text = match.group(0).strip()
                if len(text) > 10 and text not in invariants:
                    invariants.append(text[:200])
        return invariants[:10]  # raised from 5

    def _find_endpoint(self, api: str, entity: str, action: str) -> str:
        """Find API endpoint matching entity + action."""
        if not api:
            return ""
        # Try to parse as OpenAPI JSON first
        try:
            import json as _json
            spec = _json.loads(api)
            if isinstance(spec, dict) and "paths" in spec:
                paths = spec["paths"]
                for path, methods in paths.items():
                    if not isinstance(methods, dict):
                        continue
                    path_lower = path.lower()
                    entity_lower = entity.lower()
                    action_lower = action.lower()
                    # Match: path contains entity AND ends with /action
                    if entity_lower in path_lower and path_lower.rstrip("/").endswith("/" + action_lower):
                        for method in ("POST", "PUT", "PATCH"):
                            if method.lower() in methods:
                                return f"{method} {path}"
                        # Return first matching method
                        first_method = next(iter(methods), "POST")
                        return f"{first_method.upper()} {path}"
        except Exception:
            pass
        
        # Fallback: regex match against raw text
        pattern = rf'(?:POST|PUT|PATCH)\s+/api/{entity}s?(?:/\{{[^}}]+\}})?/{action}'
        match = re.search(pattern, api, re.IGNORECASE)
        if match:
            return match.group(0)
        pattern2 = rf'(?:POST|PUT|PATCH)\s+/api/{entity}s?(?:/[^/\s]+)?/{action}'
        match2 = re.search(pattern2, api, re.IGNORECASE)
        return match2.group(0) if match2 else ""

    def _extract_api_actions(self, api_spec: str) -> dict[str, set[str]]:
        """Extract available actions per entity from OpenAPI paths."""
        actions: dict[str, set[str]] = {}
        if not api_spec:
            return actions
        try:
            import json as _json
            spec = _json.loads(api_spec) if api_spec.strip().startswith('{') else None
            if not spec or 'paths' not in spec:
                return actions
            for path, methods in spec['paths'].items():
                if not isinstance(methods, dict):
                    continue
                clean = path.strip('/')
                if clean.startswith('api/'):
                    clean = clean[4:]
                segs = [s for s in clean.split('/') if s and not s.startswith('{') and not s.startswith(':')]
                if not segs:
                    continue
                entity = segs[0].rstrip('s')
                entity_plural = segs[0]
                action = segs[-1] if len(segs) > 1 else 'create'
                for e in (entity, entity_plural):
                    if e not in actions:
                        actions[e] = set()
                    actions[e].add(action)
                    # Also add common REST verbs
                    for method in methods:
                        if method.upper() == 'GET' and len(segs) == 1:
                            actions[e].add('list')
                        elif method.upper() == 'POST' and len(segs) == 1:
                            actions[e].add('create')
        except Exception:
            pass
        return actions

    def _generic_pattern(self, entity: str) -> dict:
        """Generate generic state pattern for unknown entities."""
        return {
            "states": ["active", "inactive", "processing", "completed"],
            "normal": [
                ("active", "processing", "process"),
                ("processing", "completed", "complete"),
                ("active", "inactive", "deactivate"),
            ],
            "forbidden": [
                ("completed", "processing", "reprocess"),
                ("inactive", "completed", "force_complete"),
            ],
            "boundary": [],
        }
