
"""
Phase79+: Flow Orchestrator — Multi-Step Business Flow Execution

Executes business flows as sequences of steps with context passing.
Integrates with FixtureAutoConstructor and SemanticStateVerifier.
"""

from __future__ import annotations

import json, re, time, hashlib
from dataclasses import dataclass, field
from typing import Any

from .unified_http_transport import SafeHttpTransport, ExecutionPolicy
from .state_observer_registry import StateObserver, CanonicalStateSnapshot
from .business_invariant_evaluator import BusinessInvariantEvaluator


@dataclass
class FlowStep:
    step_id: str
    kind: str  # create_fixture | observe | action | assert | cleanup
    method: str = "GET"
    path: str = ""
    body: dict | None = None
    capture_as: str = ""  # Variable name for context binding
    snapshot_id: str = ""  # For observe steps
    expected_status: int = 0


@dataclass
class FlowResult:
    flow_id: str
    status: str  # completed | blocked | failed
    steps: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


class FlowOrchestrator:
    """
    Executes multi-step business flows with context passing.

    Usage:
        orch = FlowOrchestrator(transport)
        result = orch.execute(flow_definition)
    """

    def __init__(self, transport: SafeHttpTransport | None = None):
        self.transport = transport or SafeHttpTransport(ExecutionPolicy(environment="test"))
        self.observer = StateObserver(redact_sensitive=True)
        self.evaluator = BusinessInvariantEvaluator()

    def execute(
        self,
        flow_id: str,
        steps: list[dict],
        token: str | None = None,
        initial_context: dict | None = None,
    ) -> FlowResult:
        """Execute a flow definition with context passing between steps."""
        result = FlowResult(flow_id=flow_id, status="completed")
        ctx = dict(initial_context or {})
        snapshots: dict[str, CanonicalStateSnapshot] = {}

        for i, step_raw in enumerate(steps):
            step = FlowStep(
                step_id=step_raw.get("step_id", f"step_{i+1}"),
                kind=step_raw.get("kind", "action"),
                method=step_raw.get("method", "GET"),
                path=self._render(step_raw.get("path", ""), ctx),
                body=step_raw.get("body"),
                capture_as=step_raw.get("capture_as", ""),
                snapshot_id=step_raw.get("snapshot_id", ""),
                expected_status=step_raw.get("expected_status", 0),
            )

            step_record = {"step_id": step.step_id, "kind": step.kind, "status": "executed"}

            try:
                if step.kind == "observe":
                    resp = self.transport.get(step.path, token=token)
                    step_record["response"] = {"status": resp.status_code, "ok": resp.ok}
                    if step.snapshot_id and resp.ok and resp.json:
                        snap = self.observer.observe_from_http(
                            resp.json, resp.status_code, step.path,
                            entity_id=ctx.get("entity_id", ""),
                            observer_id=step.snapshot_id,
                        )
                        snapshots[step.snapshot_id] = snap

                elif step.kind == "action":
                    resp = self.transport.request(step.method, step.path,
                        body=step.body, token=token)
                    step_record["response"] = {"status": resp.status_code, "ok": resp.ok}
                    if step.capture_as and resp.ok and resp.json:
                        data = resp.json.get("data", resp.json)
                        # Auto-capture id fields
                        for id_field in ("id", "code", "number"):
                            if id_field in data and data[id_field]:
                                ctx[step.capture_as] = str(data[id_field])
                                ctx["entity_id"] = str(data[id_field])
                                break

                elif step.kind == "assert":
                    before_id = step_raw.get("before_snapshot", "")
                    after_id = step_raw.get("after_snapshot", "")
                    before = snapshots.get(before_id)
                    after = snapshots.get(after_id)
                    if before and after:
                        obl_kind = step_raw.get("assertion_kind", "state_unchanged_after_rejection")
                        fields = step_raw.get("fields", [])
                        from .business_invariant_evaluator import ProofObligation as EvalObl
                        obl = EvalObl(obligation_id="", kind=obl_kind, title=step.step_id,
                                    severity="P1", fields=fields)
                        eval_result = self.evaluator.evaluate(obl, before, after)
                        if not eval_result.passed:
                            result.findings.append({
                                "step_id": step.step_id,
                                "title": f"Flow assertion failed: {step.step_id}",
                                "verdict": "CONFIRMED",
                                "detail": eval_result.detail,
                            })

                elif step.kind == "cleanup":
                    self.transport.delete(step.path, token=token)

                elif step.kind == "create_fixture":
                    resp = self.transport.request(step.method, step.path,
                        body=step.body, token=token)
                    step_record["response"] = {"status": resp.status_code, "ok": resp.ok}
                    if step.capture_as and resp.ok and resp.json:
                        data = resp.json.get("data", resp.json)
                        for id_field in ("id", "code", "number"):
                            if id_field in data and data[id_field]:
                                ctx[step.capture_as] = str(data[id_field])
                                ctx["entity_id"] = str(data[id_field])
                                break

            except Exception as e:
                step_record["status"] = "error"
                step_record["error"] = str(e)[:200]
                if step.kind in ("observe", "assert"):
                    continue  # Non-fatal
                result.status = "failed"
                break

            result.steps.append(step_record)

        result.context = ctx
        result.evidence = {"snapshots": list(snapshots.keys()), "step_count": len(result.steps)}
        return result

    def _render(self, template: str, ctx: dict) -> str:
        """Render ${var} placeholders in path strings."""
        if not template:
            return ""
        for key, value in ctx.items():
            template = template.replace(f"${{{{{key}}}}}", str(value))
            template = template.replace(f"${{{key}}}", str(value))
        return template
