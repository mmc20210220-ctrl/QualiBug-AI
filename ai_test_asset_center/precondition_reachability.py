"""Generic Precondition Reachability and State Construction.

Compiles rule preconditions into structured goals, searches operation paths
via state graph BFS, executes stepwise state transitions with verification,
and generates field-level Precondition Proofs.

No project-specific hardcoding. All paths derived from Behavior IR operations,
state transitions, and rule conditions.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

# ─── Constants ───
MAX_PRECONDITION_PATH_STEPS = 12
MAX_PRECONDITION_REPLANS = 2

# Standardized blocked reasons
PRECONDITION_GOAL_NOT_COMPILED = "PRECONDITION_GOAL_NOT_COMPILED"
PRECONDITION_FIELD_NOT_RESOLVED = "PRECONDITION_FIELD_NOT_RESOLVED"
TARGET_STATE_UNREACHABLE = "TARGET_STATE_UNREACHABLE"
NO_TRANSITION_OPERATION = "NO_TRANSITION_OPERATION"
REQUIRED_ACTOR_UNAVAILABLE = "REQUIRED_ACTOR_UNAVAILABLE"
REQUIRED_RELATION_UNCREATABLE = "REQUIRED_RELATION_UNCREATABLE"
AGGREGATE_TARGET_UNREACHABLE = "AGGREGATE_TARGET_UNREACHABLE"
HISTORY_CONDITION_UNREACHABLE = "HISTORY_CONDITION_UNREACHABLE"
PRECONDITION_PATH_TOO_LONG = "PRECONDITION_PATH_TOO_LONG"
PRECONDITION_STEP_FAILED = "PRECONDITION_STEP_FAILED"
PRECONDITION_OBSERVER_UNAVAILABLE = "PRECONDITION_OBSERVER_UNAVAILABLE"
PRECONDITION_VALUE_MISMATCH = "PRECONDITION_VALUE_MISMATCH"
PRECONDITION_SCOPE_MISMATCH = "PRECONDITION_SCOPE_MISMATCH"
PRECONDITION_REPLAN_EXHAUSTED = "PRECONDITION_REPLAN_EXHAUSTED"
PRECONDITION_PROOF_INCOMPLETE = "PRECONDITION_PROOF_INCOMPLETE"
PRECONDITION_DATA_CONTAMINATED = "PRECONDITION_DATA_CONTAMINATED"


# ─── Data Structures ───

@dataclass
class StateTransition:
    """A state transition edge in the state graph."""
    from_status: str
    to_status: str
    operation_id: str
    operation_method: str
    operation_path: str
    required_role: str = ""
    preconditions: dict = field(default_factory=dict)
    effects: dict = field(default_factory=dict)


@dataclass
class OperationDef:
    """An operation from the Behavior IR catalog."""
    operation_id: str
    method: str
    path: str
    required_role: str = ""
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    effects: list = field(default_factory=list)
    source_entity: str = ""
    target_entity: str = ""


@dataclass
class PreconditionGoal:
    """Structured precondition goal compiled from rule conditions."""
    goal_id: str
    internal_rule_id: str
    violation_condition_id: str = ""

    root_entity_type: str = ""
    root_entity_id: str = ""

    # Required field conditions
    required_conditions: list = field(default_factory=list)
    # Cross-entity relation conditions
    relation_conditions: list = field(default_factory=list)
    # Aggregate conditions (SUM, COUNT, etc.)
    aggregate_conditions: list = field(default_factory=list)
    # History conditions (operation must have been executed N times)
    history_conditions: list = field(default_factory=list)
    # Actor conditions
    actor_conditions: list = field(default_factory=list)

    verification_fields: list = field(default_factory=list)


@dataclass
class ReachabilityResult:
    """Result of reachability analysis."""
    goal_id: str
    reachable: bool
    candidate_paths: list = field(default_factory=list)
    selected_path: list = field(default_factory=list)
    estimated_steps: int = 0
    required_actors: list = field(default_factory=list)
    required_entities: list = field(default_factory=list)
    unresolved_conditions: list = field(default_factory=list)
    blocked_reason: str = ""


@dataclass
class StepTrace:
    """Trace of a single precondition path step execution."""
    step_id: str
    operation_id: str
    actor_id: str = ""
    request_evidence: dict = field(default_factory=dict)
    response_evidence: dict = field(default_factory=dict)
    expected_effects: dict = field(default_factory=dict)
    observed_effects: dict = field(default_factory=dict)
    step_passed: bool = False
    blocked_reason: str = ""


@dataclass
class PreconditionProof:
    """Field-level proof that all preconditions are satisfied."""
    proof_id: str
    internal_rule_id: str
    experiment_id: str = ""
    path_id: str = ""

    root_entity_id: str = ""
    related_entity_ids: list = field(default_factory=list)
    receipt_ids: list = field(default_factory=list)

    conditions: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    aggregate_proofs: list = field(default_factory=list)
    history_proofs: list = field(default_factory=list)

    all_conditions_satisfied: bool = False
    proof_hash: str = ""


# ─── Goal Compiler ───

class PreconditionGoalCompiler:
    """Compiles rule preconditions into structured PreconditionGoal objects.

    Works from field-level rules, violation conditions, and experiment plans.
    No project-specific logic - all derived from rule structure.
    """

    def compile_goal(
        self,
        internal_rule_id: str,
        rule_type: str,
        expression: str,
        target_operation: dict,
        invariants: list,
        state_graph: list[StateTransition],
        violation_condition: dict = None,
    ) -> PreconditionGoal:
        """Compile a precondition goal from rule metadata."""
        goal = PreconditionGoal(
            goal_id=f"goal_{uuid.uuid4().hex[:12]}",
            internal_rule_id=internal_rule_id,
            violation_condition_id=(violation_condition or {}).get("id", ""),
            root_entity_type=target_operation.get("source_entity", ""),
        )

        # Parse conditions from expression and invariants
        self._compile_state_conditions(goal, expression, target_operation, state_graph)
        self._compile_aggregate_conditions(goal, expression, invariants)
        self._compile_relation_conditions(goal, expression, invariants)
        self._compile_history_conditions(goal, expression, invariants)

        # Determine verification fields
        goal.verification_fields = self._extract_verification_fields(goal)

        return goal

    def _compile_state_conditions(
        self, goal: PreconditionGoal, expression: str,
        target_op: dict, state_graph: list[StateTransition]
    ):
        """Extract required entity state from expression and target operation."""
        # Look for state requirements in the expression
        # e.g., "must be ACTIVE", "status = LEGAL_REVIEW"
        import re
        state_patterns = [
            re.compile(r"(?i)(?:status|state)\s*(?:=|==|is|must\s+be)\s*(\w+)"),
            re.compile(r"(?i)(?:must|require|need)\s+(\w+)\s+(?:status|state)"),
            re.compile(r"(?i)(?:in|at)\s+(\w+)\s+(?:status|state|phase)"),
        ]
        for pat in state_patterns:
            m = pat.search(expression)
            if m:
                target_state = m.group(1).upper()
                goal.required_conditions.append({
                    "condition_id": f"state_{target_state.lower()}",
                    "entity_id": goal.root_entity_type,
                    "field_id": "status",
                    "operator": "eq",
                    "expected_expression": target_state,
                    "source": "expression",
                    "confidence": "HIGH",
                })
                return

        # Infer from target operation preconditions in state graph
        op_id = target_op.get("operation_id", "")
        for trans in state_graph:
            if trans.operation_id == op_id:
                # The operation requires from_status
                goal.required_conditions.append({
                    "condition_id": f"state_{trans.from_status.lower()}",
                    "entity_id": goal.root_entity_type,
                    "field_id": "status",
                    "operator": "eq",
                    "expected_expression": trans.from_status,
                    "source": "state_graph",
                    "confidence": "HIGH",
                })
                return

    def _compile_aggregate_conditions(
        self, goal: PreconditionGoal, expression: str, invariants: list
    ):
        """Extract aggregate conditions (SUM, COUNT, etc.)."""
        import re
        # Look for aggregate patterns
        agg_patterns = [
            (re.compile(r"(?i)sum\s*\(\s*(\w+)\.(\w+)\s*\)\s*([<>=!]+)\s*([\d.]+)"), "sum"),
            (re.compile(r"(?i)count\s*\(\s*(\w+)\s*\)\s*([<>=!]+)\s*(\d+)"), "count"),
            (re.compile(r"(?i)(\w+)\s+remaining\s*([<>=!]+)\s*([\d.]+)"), "remaining"),
        ]
        for pat, agg_type in agg_patterns:
            m = pat.search(expression)
            if m:
                goal.aggregate_conditions.append({
                    "expression": m.group(0),
                    "type": agg_type,
                    "operator": m.group(2) if agg_type != "remaining" else m.group(2),
                    "expected_value": m.group(3) if agg_type != "remaining" else m.group(3),
                    "scope": goal.root_entity_type,
                })

    def _compile_relation_conditions(
        self, goal: PreconditionGoal, expression: str, invariants: list
    ):
        """Extract cross-entity relation conditions."""
        import re
        # Look for relation requirements
        rel_patterns = [
            re.compile(r"(?i)(\w+)\s+(?:must|should)\s+(?:be|have)\s+(\w+)"),
            re.compile(r"(?i)(\w+)\.(\w+)\s*(?:=|==|!=)\s*(\w+)"),
        ]
        for inv in invariants:
            inv_expr = inv.get("expression", "")
            if "related" in inv_expr.lower() or "milestone" in inv_expr.lower():
                goal.relation_conditions.append({
                    "parent_entity": goal.root_entity_type,
                    "child_entity": inv.get("related_entity", ""),
                    "relation_key": inv.get("relation_key", ""),
                    "cardinality": inv.get("cardinality", "any"),
                    "filters": inv.get("filters", {}),
                })

    def _compile_history_conditions(
        self, goal: PreconditionGoal, expression: str, invariants: list
    ):
        """Extract history operation conditions."""
        import re
        hist_pat = re.compile(r"(?i)(\w+)\s+(?:must|should)\s+(?:have\s+been\s+)?(\w+)\s+(?:at\s+least\s+)?(\d+)\s+times?")
        m = hist_pat.search(expression)
        if m:
            goal.history_conditions.append({
                "operation_id": m.group(2),
                "minimum_count": int(m.group(3)),
                "required_result": "success",
            })

    def _extract_verification_fields(self, goal: PreconditionGoal) -> list:
        """Determine which fields need verification after path execution."""
        fields = set()
        for cond in goal.required_conditions:
            fields.add(cond.get("field_id", "status"))
        for agg in goal.aggregate_conditions:
            fields.add(agg.get("expression", ""))
        return list(fields)


# ─── Reachability Analyzer ───

class ReachabilityAnalyzer:
    """Analyzes whether a precondition goal is reachable via available operations."""

    def __init__(self, state_graph: list[StateTransition], operations: list[OperationDef]):
        self._state_graph = state_graph
        self._operations = {op.operation_id: op for op in operations}
        # Build adjacency: from_status -> [(to_status, transition)]
        self._adj: dict[str, list[tuple[str, StateTransition]]] = {}
        for trans in state_graph:
            self._adj.setdefault(trans.from_status, []).append(
                (trans.to_status, trans)
            )

    def analyze(
        self,
        goal: PreconditionGoal,
        current_state: str,
        available_actors: list[dict],
        existing_entities: dict = None,
    ) -> ReachabilityResult:
        """Perform reachability analysis for a goal."""
        result = ReachabilityResult(goal_id=goal.goal_id, reachable=False)

        # Find target state from conditions
        target_state = self._get_target_state(goal)
        if not target_state:
            # No state condition - check aggregate/relation only
            result.reachable = True
            result.selected_path = []
            result.blocked_reason = ""
            return result

        # BFS from current_state to target_state
        path = self._bfs_path(current_state, target_state)
        if path is None:
            result.blocked_reason = TARGET_STATE_UNREACHABLE
            result.unresolved_conditions.append(
                f"Cannot reach {target_state} from {current_state}"
            )
            return result

        if len(path) > MAX_PRECONDITION_PATH_STEPS:
            result.blocked_reason = PRECONDITION_PATH_TOO_LONG
            return result

        # Check actor availability for each step
        required_actors = set()
        for _, trans in path:
            if trans.required_role:
                required_actors.add(trans.required_role)
                if not self._actor_available(trans.required_role, available_actors):
                    result.blocked_reason = REQUIRED_ACTOR_UNAVAILABLE
                    result.unresolved_conditions.append(
                        f"Role {trans.required_role} not available"
                    )
                    return result

        result.reachable = True
        result.selected_path = path
        result.estimated_steps = len(path)
        result.required_actors = list(required_actors)
        result.candidate_paths = [path]  # Could expand with alternatives
        return result

    def _get_target_state(self, goal: PreconditionGoal) -> str:
        """Extract target state from goal conditions.

        The condition's field_id names the entity's state field (status/state/
        stage/phase/... resolved from the Behavior IR). Any non-empty field_id
        is a state condition; gating on the literal ``"status"`` would silently
        drop every machine whose state field has a different name.
        """
        for cond in goal.required_conditions:
            if str(cond.get("field_id") or "").strip():
                return cond.get("expected_expression", "")
        return ""

    def _bfs_path(
        self, start: str, target: str
    ) -> Optional[list[tuple[str, StateTransition]]]:
        """BFS shortest path from start to target status."""
        if start == target:
            return []
        visited = {start}
        queue = deque([(start, [])])
        while queue:
            current, path = queue.popleft()
            for next_state, trans in self._adj.get(current, []):
                if next_state in visited:
                    continue
                new_path = path + [(next_state, trans)]
                if next_state == target:
                    return new_path
                visited.add(next_state)
                queue.append((next_state, new_path))
        return None

    def _actor_available(self, role: str, available_actors: list[dict]) -> bool:
        """Check if an actor with the required role is available."""
        if not role:
            return True
        return any(a.get("role") == role for a in available_actors)


# ─── Path Executor ───

class PreconditionPathExecutor:
    """Executes precondition paths step by step with verification.

    Uses provided HTTP executor function - does NOT make HTTP calls directly.
    """

    def __init__(self, http_executor, state_observer=None):
        """
        Args:
            http_executor: callable(method, path, token, body, headers) -> (status, body)
            state_observer: callable(entity_type, entity_id, token) -> dict (current state)
        """
        self._http = http_executor
        self._observe = state_observer
        self._step_traces: list[StepTrace] = []
        self._entity_receipts: dict[str, dict] = {}
        self._replans_used = 0

    def execute_path(
        self,
        path: list[tuple[str, StateTransition]],
        actors: list[dict],
        entity_context: dict,
        verify_after_each: bool = True,
    ) -> tuple[bool, list[StepTrace], dict]:
        """Execute a precondition path step by step.

        Returns: (success, step_traces, updated_entity_context)
        """
        self._step_traces = []
        ctx = dict(entity_context)

        for i, (target_status, trans) in enumerate(path):
            step = self._execute_step(i, trans, actors, ctx)
            self._step_traces.append(step)

            if not step.step_passed:
                # Attempt replan if allowed
                if self._replans_used < MAX_PRECONDITION_REPLANS:
                    self._replans_used += 1
                    # Simple replan: retry with adjusted parameters
                    step2 = self._execute_step(i, trans, actors, ctx, replan=True)
                    self._step_traces.append(step2)
                    if not step2.step_passed:
                        return False, self._step_traces, ctx
                    step = step2
                else:
                    return False, self._step_traces, ctx

            # Update context from response
            resp_body = step.response_evidence.get("body", {})
            if resp_body.get("id"):
                ctx[f"{trans.operation_id}_id"] = resp_body["id"]
            if resp_body.get("status"):
                ctx["current_status"] = resp_body["status"]

            # Verify state after step
            if verify_after_each and self._observe:
                entity_type = ctx.get("root_entity_type", "")
                entity_id = ctx.get("root_entity_id", "")
                if entity_id:
                    observed = self._observe(entity_type, entity_id, ctx.get("token", ""))
                    step.observed_effects = observed

        return True, self._step_traces, ctx

    def _execute_step(
        self, index: int, trans: StateTransition,
        actors: list[dict], ctx: dict, replan: bool = False,
    ) -> StepTrace:
        """Execute a single path step."""
        step = StepTrace(
            step_id=f"step_{index}_{uuid.uuid4().hex[:6]}",
            operation_id=trans.operation_id,
        )

        # Resolve actor
        token = self._resolve_actor(trans.required_role, actors, ctx)
        step.actor_id = token or "unresolved"
        if not token:
            step.blocked_reason = REQUIRED_ACTOR_UNAVAILABLE
            return step

        # Build request
        path = self._resolve_path(trans.operation_path, ctx)
        body = self._resolve_body(trans, ctx, replan)
        method = trans.operation_method

        step.request_evidence = {
            "method": method,
            "path": path,
            "body": body,
        }

        # Execute
        status, resp_body = self._http(method, path, token, body)
        step.response_evidence = {"status": status, "body": resp_body}

        # Validate
        if status in (200, 201):
            step.step_passed = True
            step.expected_effects = {"status": trans.to_status}
            step.observed_effects = resp_body if isinstance(resp_body, dict) else {}
        else:
            step.blocked_reason = PRECONDITION_STEP_FAILED

        return step

    def _resolve_actor(self, role: str, actors: list[dict], ctx: dict) -> str:
        """Resolve actor token for a role."""
        if not role:
            return ctx.get("token", actors[0]["token"] if actors else "")
        for a in actors:
            if a.get("role") == role:
                return a.get("token", "")
        # Fallback to admin if role not found
        for a in actors:
            if a.get("role") == "admin":
                return a.get("token", "")
        return ctx.get("token", "")

    def _resolve_path(self, path_template: str, ctx: dict) -> str:
        """Resolve path template with entity IDs from context."""
        result = path_template
        for key, val in ctx.items():
            if isinstance(val, str):
                result = result.replace(f"{{{key}}}", val)
        return result

    def _resolve_body(self, trans: StateTransition, ctx: dict, replan: bool) -> dict:
        """Resolve request body from operation preconditions and context."""
        body = {}
        for key, val in trans.preconditions.items():
            if isinstance(val, str) and val.startswith("$"):
                # Reference to context
                ctx_key = val[1:]
                body[key] = ctx.get(ctx_key, val)
            else:
                body[key] = val
        return body


# ─── Proof Generator ───

class PreconditionProofGenerator:
    """Generates field-level precondition proofs from execution traces."""

    def generate_proof(
        self,
        goal: PreconditionGoal,
        step_traces: list[StepTrace],
        entity_context: dict,
        observed_state: dict = None,
    ) -> PreconditionProof:
        """Generate a precondition proof from execution evidence."""
        proof = PreconditionProof(
            proof_id=f"proof_{uuid.uuid4().hex[:12]}",
            internal_rule_id=goal.internal_rule_id,
            path_id=entity_context.get("path_id", ""),
            root_entity_id=entity_context.get("root_entity_id", ""),
        )

        # Verify each required condition
        all_passed = True
        for cond in goal.required_conditions:
            field_id = cond.get("field_id", "status")
            expected = cond.get("expected_expression", "")
            actual = ""
            if observed_state:
                actual = str(observed_state.get(field_id, ""))
            passed = actual.upper() == expected.upper() if expected else True
            proof.conditions.append({
                "condition_id": cond.get("condition_id", ""),
                "entity_id": cond.get("entity_id", ""),
                "field_id": field_id,
                "operator": cond.get("operator", "eq"),
                "expected": expected,
                "actual": actual,
                "evidence_id": f"obs_{uuid.uuid4().hex[:8]}",
                "passed": passed,
            })
            if not passed:
                all_passed = False

        # Verify aggregate conditions
        for agg in goal.aggregate_conditions:
            proof.aggregate_proofs.append({
                "expression": agg.get("expression", ""),
                "terms": [],
                "expected": agg.get("expected_value", ""),
                "actual": "",
                "scope": agg.get("scope", ""),
                "passed": False,  # Must be verified by observer
            })

        # Collect receipt IDs from step traces
        for trace in step_traces:
            resp = trace.response_evidence.get("body", {})
            if isinstance(resp, dict) and resp.get("id"):
                proof.receipt_ids.append(resp["id"])

        proof.all_conditions_satisfied = all_passed
        # Compute proof hash
        proof_content = json.dumps({
            "conditions": proof.conditions,
            "receipts": proof.receipt_ids,
        }, sort_keys=True)
        proof.proof_hash = hashlib.sha256(proof_content.encode()).hexdigest()[:16]

        return proof


# ─── Main Orchestrator ───

class PreconditionReachability:
    """Main orchestrator for precondition reachability and state construction.

    Combines goal compilation, reachability analysis, path execution,
    and proof generation into a single workflow.
    """

    def __init__(
        self,
        state_graph: list[StateTransition],
        operations: list[OperationDef],
        http_executor,
        state_observer=None,
        available_actors: list[dict] = None,
    ):
        self._compiler = PreconditionGoalCompiler()
        self._analyzer = ReachabilityAnalyzer(state_graph, operations)
        self._executor = PreconditionPathExecutor(http_executor, state_observer)
        self._proof_gen = PreconditionProofGenerator()
        self._actors = available_actors or []
        self._state_graph = state_graph

    def process_target(
        self,
        internal_rule_id: str,
        rule_type: str,
        expression: str,
        target_operation: dict,
        invariants: list,
        current_state: str = "DRAFT",
        violation_condition: dict = None,
    ) -> dict:
        """Process a single target through the full precondition pipeline.

        Returns a dict with goal, reachability, execution, and proof results.
        """
        result = {
            "internal_rule_id": internal_rule_id,
            "stage": "INIT",
            "blocked_reason": "",
        }

        # Stage 1: Compile Goal
        goal = self._compiler.compile_goal(
            internal_rule_id=internal_rule_id,
            rule_type=rule_type,
            expression=expression,
            target_operation=target_operation,
            invariants=invariants,
            state_graph=self._state_graph,
            violation_condition=violation_condition,
        )
        result["goal"] = {
            "goal_id": goal.goal_id,
            "conditions_count": len(goal.required_conditions),
            "aggregate_count": len(goal.aggregate_conditions),
            "relation_count": len(goal.relation_conditions),
        }
        result["stage"] = "GOAL_COMPILED"

        # Stage 2: Reachability Analysis
        reach = self._analyzer.analyze(
            goal=goal,
            current_state=current_state,
            available_actors=self._actors,
        )
        result["reachability"] = {
            "reachable": reach.reachable,
            "estimated_steps": reach.estimated_steps,
            "blocked_reason": reach.blocked_reason,
            "required_actors": reach.required_actors,
        }
        if not reach.reachable:
            result["stage"] = "BLOCKED"
            result["blocked_reason"] = reach.blocked_reason
            return result
        result["stage"] = "REACHABLE"

        # Stage 3: Execute Path
        if reach.selected_path:
            entity_ctx = {
                "root_entity_type": goal.root_entity_type,
                "current_status": current_state,
                "token": self._actors[0]["token"] if self._actors else "",
            }
            success, traces, updated_ctx = self._executor.execute_path(
                path=reach.selected_path,
                actors=self._actors,
                entity_context=entity_ctx,
            )
            result["execution"] = {
                "success": success,
                "steps_executed": len(traces),
                "steps_passed": sum(1 for t in traces if t.step_passed),
            }
            if not success:
                failed = next((t for t in traces if not t.step_passed), None)
                result["stage"] = "EXECUTION_FAILED"
                result["blocked_reason"] = failed.blocked_reason if failed else PRECONDITION_STEP_FAILED
                return result
            result["stage"] = "PATH_EXECUTED"
        else:
            result["stage"] = "PATH_EXECUTED"  # No path needed (already at target)

        # Stage 4: Generate Proof
        observed = None
        if self._executor._observe and result.get("execution", {}).get("success"):
            # Would observe actual state here
            pass
        proof = self._proof_gen.generate_proof(
            goal=goal,
            step_traces=self._executor._step_traces,
            entity_context=updated_ctx if reach.selected_path else {},
            observed_state=observed,
        )
        result["proof"] = {
            "proof_id": proof.proof_id,
            "all_conditions_satisfied": proof.all_conditions_satisfied,
            "conditions_verified": len(proof.conditions),
            "proof_hash": proof.proof_hash,
        }
        result["stage"] = "PROOF_GENERATED"

        if not proof.all_conditions_satisfied:
            result["blocked_reason"] = PRECONDITION_PROOF_INCOMPLETE

        return result
