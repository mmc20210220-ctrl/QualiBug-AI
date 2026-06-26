from __future__ import annotations

"""Phase92P: automatic business-invariant before/after adjudicator.

The executor already creates disposable ``qb_auto_*`` data and captures
before/after snapshots.  This module turns those observed snapshots into
runtime proof obligations.  It is deliberately deterministic and evidence-first:

* obligations are derived from the grounded probe/risk plan plus fields actually
  observed in before/after payloads;
* a candidate is validated only when an observed after-state violates an
  inferred business invariant;
* no benchmark oracle/ground-truth/seed files are read.
"""

import re
from dataclasses import dataclass, field
from typing import Any

NUMERIC_RESOURCE_RE = re.compile(
    r"(?:amount|price|balance|total|subtotal|discount|refund|paid|payable|fee|"
    r"inventory|stock|quantity|qty|count|quota|credit|limit|points|score|"
    r"金额|价格|余额|库存|数量|额度|积分|退款|支付|总额)",
    re.I,
)
STATUS_FIELD_RE = re.compile(r"(?:^|[._\[\]])(?:status|state|lifecycle_state|phase|状态)(?:$|[._\[\]])", re.I)
OWNER_SCOPE_RE = re.compile(r"(?:tenant|org|owner|user|account|merchant|shop|workspace|project|租户|组织|归属|用户|商户)", re.I)
ID_FIELD_RE = re.compile(r"(?:^|[._\[\]])(?:id|uuid|code|order_id|order_no|resource_id|event_id|业务单号|单号)(?:$|[._\[\]])", re.I)
VOLATILE_PATH_RE = re.compile(r"(?:trace|request|duration|timestamp|time|date|nonce|random|签名|日志|log)", re.I)
SIGNED_PROJECTION_KIND_RE = re.compile(r"(?:ledger|journal|transaction|history|workflow|event|流水|历史)", re.I)
SIGNED_DELTA_FIELD_RE = re.compile(r"(?:amount|quantity|qty|delta|change|movement|refund|fee|金额|数量|退款)", re.I)
HARD_NON_NEGATIVE_FIELD_RE = re.compile(r"(?:stock|inventory|balance|quota|credit|limit|points|库存|余额|额度|积分)", re.I)
TERMINAL_FALLBACK = {"cancelled", "canceled", "closed", "completed", "complete", "finished", "done", "terminated", "refunded", "voided", "终止", "取消", "已取消", "完成", "已完成", "关闭", "已关闭"}


@dataclass
class BeforeAfterInvariantResult:
    invariant_id: str
    kind: str
    verdict: str  # passed | failed | undetermined
    reason: str
    confidence: float
    failed_fields: list[str] = field(default_factory=list)
    computed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "kind": self.kind,
            "verdict": self.verdict,
            "reason": self.reason,
            "confidence": self.confidence,
            "failed_fields": self.failed_fields,
            "computed": self.computed,
        }


def _payload_from_snapshot(item: dict[str, Any]) -> Any:
    response = item.get("response") if isinstance(item.get("response"), dict) else {}
    return response.get("payload")


def _first_payload(snapshots: dict[str, Any], phase: str) -> Any:
    raw = snapshots.get(phase) if isinstance(snapshots, dict) else []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return None
    for item in raw:
        if isinstance(item, dict):
            payload = _payload_from_snapshot(item)
            if payload not in (None, {}, []):
                return payload
    return None


def _joined_payloads_from_snapshots(snapshots: dict[str, Any]) -> tuple[Any, Any, dict[str, Any]]:
    """Use Phase92R semantic graph when multi-observer evidence exists."""
    before_fallback = _first_payload(snapshots, "before")
    after_fallback = _first_payload(snapshots, "after")
    try:
        from .observer_response_semantic_joiner import join_snapshot_observer_responses

        graph = join_snapshot_observer_responses(snapshots)
        joined = graph.get("joined_payloads") if isinstance(graph, dict) else {}
        before_joined = (joined or {}).get("before") if isinstance(joined, dict) else None
        after_joined = (joined or {}).get("after") if isinstance(joined, dict) else None
        if before_joined not in (None, {}, []) or after_joined not in (None, {}, []):
            return before_joined, after_joined, graph
        return before_fallback, after_fallback, graph if isinstance(graph, dict) else {}
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        return before_fallback, after_fallback, {
            "engine": "observer_response_semantic_joiner_v1_phase92r",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _flatten(value: Any, prefix: str = "", *, max_items: int = 30) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(child, path, max_items=max_items))
        return out
    if isinstance(value, list):
        out[prefix + ".__len__" if prefix else "__len__"] = len(value)
        for idx, child in enumerate(value[:max_items]):
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            out.update(_flatten(child, path, max_items=max_items))
        return out
    out[prefix or "$"] = value
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_resource_numeric_path(path: str) -> bool:
    return bool(NUMERIC_RESOURCE_RE.search(path or ""))


def _business_diff(before: Any, after: Any) -> dict[str, dict[str, Any]]:
    b = _flatten(before)
    a = _flatten(after)
    diff: dict[str, dict[str, Any]] = {}
    for path in sorted(set(b) | set(a)):
        if VOLATILE_PATH_RE.search(path):
            continue
        if b.get(path) != a.get(path):
            diff[path] = {"before": b.get(path), "after": a.get(path)}
    return diff


def _status_values(payload: Any) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for path, value in _flatten(payload).items():
        if STATUS_FIELD_RE.search(path) and value not in (None, "", [], {}):
            values.append((path, str(value)))
    return values[:20]


def _terminal_states(probe: dict[str, Any]) -> set[str]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    raw = plan.get("terminal_states") or plan.get("final_states") or plan.get("immutable_states") or []
    if isinstance(raw, str):
        raw = [raw]
    states = {str(x).strip().lower() for x in raw if str(x).strip()}
    # Keep a small universal fallback only when the grounded probe is explicitly
    # about terminal-state/state-transition risk; ordinary writes do not inherit
    # these defaults.
    if str(probe.get("risk_type") or "") == "state_transition_probe":
        states |= {s.lower() for s in TERMINAL_FALLBACK}
    return states


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "records", "list", "content", "rows", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = _extract_records(value)
            if nested:
                return nested
    return [payload]


def _id_values(payload: Any) -> list[str]:
    ids: list[str] = []
    for path, value in _flatten(payload).items():
        if ID_FIELD_RE.search(path) and value not in (None, "", [], {}):
            ids.append(str(value))
    return list(dict.fromkeys(ids))[:50]


def _negative_resource_values(payload: Any) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    # Phase92R/92S joined graphs preserve observer_kind next to each record.
    # Ledger/history projections commonly contain signed deltas (for example
    # quantity=-1 or amount=-20) that are legitimate evidence, not negative
    # resource state.  Hard state fields such as stock/balance/quota/points stay
    # non-negative obligations even inside joined graphs.
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        for idx, record in enumerate(payload.get("records") or []):
            if not isinstance(record, dict):
                continue
            kind = str(record.get("observer_kind") or "")
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else record
            for path, value in _flatten(fields).items():
                full_path = f"records[{idx}].fields.{path}"
                if not (_is_number(value) and value < 0 and _is_resource_numeric_path(path)):
                    continue
                if SIGNED_PROJECTION_KIND_RE.search(kind) and SIGNED_DELTA_FIELD_RE.search(path) and not HARD_NON_NEGATIVE_FIELD_RE.search(path):
                    continue
                hits.append({"path": full_path, "value": value, "observer_kind": kind})
        return hits[:30]
    for path, value in _flatten(payload).items():
        if _is_number(value) and value < 0 and _is_resource_numeric_path(path):
            hits.append({"path": path, "value": value})
    return hits[:30]


def _resource_numeric_paths(payload: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        for idx, record in enumerate(payload.get("records") or []):
            if not isinstance(record, dict):
                continue
            kind = str(record.get("observer_kind") or "")
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else record
            for path, value in _flatten(fields).items():
                if not (_is_number(value) and _is_resource_numeric_path(path)):
                    continue
                if SIGNED_PROJECTION_KIND_RE.search(kind) and SIGNED_DELTA_FIELD_RE.search(path) and not HARD_NON_NEGATIVE_FIELD_RE.search(path):
                    continue
                paths.append(f"records[{idx}].fields.{path}")
        return paths[:50]
    return [p for p, v in _flatten(payload).items() if _is_number(v) and _is_resource_numeric_path(p)][:50]


def _response_ids(responses: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for response in responses or []:
        code = response.get("status_code")
        try:
            ok = code is not None and 200 <= int(code) < 300
        except Exception:
            ok = False
        if not ok:
            continue
        for value in _id_values(response.get("payload")):
            ids.append(value)
    return list(dict.fromkeys(ids))[:20]


def _changed_scope_fields(diff: dict[str, dict[str, Any]]) -> list[str]:
    return [path for path in diff if OWNER_SCOPE_RE.search(path)][:30]


def _is_expected_negative_response(probe: dict[str, Any], responses: list[dict[str, Any]]) -> bool:
    if not responses:
        return False
    first = responses[0]
    status = first.get("status_code")
    if status is None:
        return False
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    raw = plan.get("expected_status") or plan.get("expected_statuses") or []
    if isinstance(raw, (int, str)):
        raw = [raw]
    expected: set[int] = set()
    for value in raw:
        try:
            expected.add(int(value))
        except Exception:
            pass
    if not expected:
        risk = str(probe.get("risk_type") or "")
        if risk in {"auth_boundary_probe", "ownership_scope_probe"}:
            expected = {401, 403, 404}
        elif risk in {"state_transition_probe", "idempotency_replay_probe", "async_external_event_probe"}:
            expected = {409, 422}
    try:
        return int(status) in expected
    except Exception:
        return False


def _evaluate_non_negative(after_payload: Any) -> BeforeAfterInvariantResult | None:
    if after_payload in (None, {}, []):
        return None
    hits = _negative_resource_values(after_payload)
    if hits:
        return BeforeAfterInvariantResult(
            invariant_id="BAI-NONNEG-001",
            kind="non_negative_resource_fields",
            verdict="failed",
            reason="after snapshot contains negative resource-like numeric values",
            confidence=0.9,
            failed_fields=[h["path"] for h in hits],
            computed={"negative_values": hits},
        )
    numeric_paths = _resource_numeric_paths(after_payload)
    if numeric_paths:
        return BeforeAfterInvariantResult(
            invariant_id="BAI-NONNEG-001",
            kind="non_negative_resource_fields",
            verdict="passed",
            reason="observed resource-like numeric fields stayed non-negative in after snapshot",
            confidence=0.58,
            computed={"checked_fields": numeric_paths[:30]},
        )
    return None


def _evaluate_rejected_non_mutation(probe: dict[str, Any], responses: list[dict[str, Any]], before_payload: Any, after_payload: Any) -> BeforeAfterInvariantResult | None:
    if before_payload in (None, {}, []) or after_payload in (None, {}, []):
        return None
    if not _is_expected_negative_response(probe, responses):
        return None
    diff = _business_diff(before_payload, after_payload)
    if diff:
        return BeforeAfterInvariantResult(
            invariant_id="BAI-REJECT-NOMUT-001",
            kind="state_unchanged_after_rejection",
            verdict="failed",
            reason="operation was rejected/forbidden but observable business state changed",
            confidence=0.92,
            failed_fields=list(diff.keys())[:30],
            computed={"diff": dict(list(diff.items())[:30])},
        )
    return BeforeAfterInvariantResult(
        invariant_id="BAI-REJECT-NOMUT-001",
        kind="state_unchanged_after_rejection",
        verdict="passed",
        reason="operation was rejected/forbidden and before/after business state stayed unchanged",
        confidence=0.7,
        computed={"diff": {}},
    )


def _evaluate_terminal_immutability(probe: dict[str, Any], before_payload: Any, after_payload: Any) -> BeforeAfterInvariantResult | None:
    if before_payload in (None, {}, []) or after_payload in (None, {}, []):
        return None
    states = _terminal_states(probe)
    if not states:
        return None
    before_statuses = _status_values(before_payload)
    terminal_hits = [(path, value) for path, value in before_statuses if value.strip().lower() in states]
    if not terminal_hits:
        return None
    diff = _business_diff(before_payload, after_payload)
    if diff:
        return BeforeAfterInvariantResult(
            invariant_id="BAI-TERM-IMMUT-001",
            kind="terminal_object_immutability",
            verdict="failed",
            reason="object was already in a documented terminal state before the action, but after snapshot changed",
            confidence=0.91,
            failed_fields=list(diff.keys())[:30],
            computed={"terminal_statuses": terminal_hits, "diff": dict(list(diff.items())[:30])},
        )
    return BeforeAfterInvariantResult(
        invariant_id="BAI-TERM-IMMUT-001",
        kind="terminal_object_immutability",
        verdict="passed",
        reason="terminal-state object stayed unchanged across before/after snapshots",
        confidence=0.72,
        computed={"terminal_statuses": terminal_hits},
    )


def _evaluate_ownership_non_mutation(probe: dict[str, Any], before_payload: Any, after_payload: Any) -> BeforeAfterInvariantResult | None:
    risk = str(probe.get("risk_type") or "")
    if risk not in {"ownership_scope_probe", "auth_boundary_probe"}:
        return None
    if before_payload in (None, {}, []) or after_payload in (None, {}, []):
        return None
    diff = _business_diff(before_payload, after_payload)
    scope_changes = _changed_scope_fields(diff)
    if scope_changes:
        return BeforeAfterInvariantResult(
            invariant_id="BAI-SCOPE-NOMUT-001",
            kind="tenant_owner_scope_non_mutation",
            verdict="failed",
            reason="tenant/owner/scope fields changed during a boundary probe",
            confidence=0.9,
            failed_fields=scope_changes,
            computed={"diff": {k: diff[k] for k in scope_changes}},
        )
    if diff and risk == "ownership_scope_probe":
        return BeforeAfterInvariantResult(
            invariant_id="BAI-SCOPE-NOMUT-001",
            kind="tenant_owner_scope_non_mutation",
            verdict="failed",
            reason="cross-tenant/ownership boundary probe caused observable business mutation",
            confidence=0.86,
            failed_fields=list(diff.keys())[:30],
            computed={"diff": dict(list(diff.items())[:30])},
        )
    if OWNER_SCOPE_RE.search(" ".join(_flatten(before_payload).keys())):
        return BeforeAfterInvariantResult(
            invariant_id="BAI-SCOPE-NOMUT-001",
            kind="tenant_owner_scope_non_mutation",
            verdict="passed",
            reason="observed tenant/owner/scope fields did not change during boundary probe",
            confidence=0.66,
            computed={"checked_scope_fields": [p for p in _flatten(before_payload) if OWNER_SCOPE_RE.search(p)][:30]},
        )
    return None


def _evaluate_idempotency(probe: dict[str, Any], responses: list[dict[str, Any]], before_payload: Any, after_payload: Any) -> BeforeAfterInvariantResult | None:
    if str(probe.get("risk_type") or "") not in {"idempotency_replay_probe", "async_external_event_probe"}:
        return None
    ids = _response_ids(responses)
    ok_response_count = 0
    for response in responses or []:
        try:
            ok_response_count += 1 if response.get("status_code") is not None and 200 <= int(response.get("status_code")) < 300 else 0
        except Exception:
            pass
    if ok_response_count >= 2 and len(ids) >= 2:
        return BeforeAfterInvariantResult(
            invariant_id="BAI-IDEMP-001",
            kind="idempotency_replay_single_side_effect",
            verdict="failed",
            reason="replayed request returned multiple distinct resource identifiers",
            confidence=0.9,
            failed_fields=["responses[*].payload.id"],
            computed={"response_ids": ids, "ok_response_count": ok_response_count},
        )
    if before_payload not in (None, {}, []) and after_payload not in (None, {}, []):
        before_records = _extract_records(before_payload)
        after_records = _extract_records(after_payload)
        if len(after_records) > len(before_records) + 1 and ok_response_count >= 2:
            return BeforeAfterInvariantResult(
                invariant_id="BAI-IDEMP-001",
                kind="idempotency_replay_single_side_effect",
                verdict="failed",
                reason="before/after collection grew by more than one record during replay",
                confidence=0.86,
                failed_fields=["records.__len__"],
                computed={"before_record_count": len(before_records), "after_record_count": len(after_records), "ok_response_count": ok_response_count},
            )
    if ok_response_count >= 2:
        return BeforeAfterInvariantResult(
            invariant_id="BAI-IDEMP-001",
            kind="idempotency_replay_single_side_effect",
            verdict="undetermined",
            reason="replay was accepted but snapshots/response IDs were insufficient to prove duplicate side effect",
            confidence=0.45,
            computed={"response_ids": ids, "ok_response_count": ok_response_count},
        )
    return None


def _evaluate_cross_observer_conservation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any], semantic_graph: dict[str, Any]) -> BeforeAfterInvariantResult | None:
    if str(probe.get("risk_type") or "") != "conservation_probe":
        return None
    try:
        from .cross_observer_conservation_reconciler import reconcile_cross_observer_conservation

        report = reconcile_cross_observer_conservation(probe, responses, snapshots, semantic_graph)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        return BeforeAfterInvariantResult(
            invariant_id="BAI-XOBS-CONS-001",
            kind="cross_observer_conservation_reconciliation",
            verdict="undetermined",
            reason=f"cross-observer conservation reconciler error: {type(exc).__name__}: {exc}",
            confidence=0.0,
            computed={},
        )
    verdict = str(report.get("verdict") or "")
    if verdict == "failed":
        return BeforeAfterInvariantResult(
            invariant_id="BAI-XOBS-CONS-001",
            kind="cross_observer_conservation_reconciliation",
            verdict="failed",
            reason=str(report.get("reason") or "cross-observer conservation failed"),
            confidence=max(0.86, float(report.get("confidence") or 0.0)),
            failed_fields=[str(f.get("kind")) for f in (report.get("failures") or []) if isinstance(f, dict)][:10],
            computed=report,
        )
    if verdict == "passed":
        return BeforeAfterInvariantResult(
            invariant_id="BAI-XOBS-CONS-001",
            kind="cross_observer_conservation_reconciliation",
            verdict="passed",
            reason=str(report.get("reason") or "cross-observer conservation passed"),
            confidence=float(report.get("confidence") or 0.7),
            computed=report,
        )
    if verdict == "undetermined":
        return BeforeAfterInvariantResult(
            invariant_id="BAI-XOBS-CONS-001",
            kind="cross_observer_conservation_reconciliation",
            verdict="undetermined",
            reason=str(report.get("reason") or "cross-observer conservation undetermined"),
            confidence=float(report.get("confidence") or 0.4),
            computed=report,
        )
    return None

def evaluate_business_invariants_before_after(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any]) -> dict[str, Any]:
    """Infer and evaluate business invariants from observed before/after state.

    Returns a compact report suitable for embedding in runtime evidence.
    """
    before_payload, after_payload, semantic_graph = _joined_payloads_from_snapshots(snapshots)
    results: list[BeforeAfterInvariantResult] = []

    for maybe in (
        _evaluate_rejected_non_mutation(probe, responses, before_payload, after_payload),
        _evaluate_terminal_immutability(probe, before_payload, after_payload),
        _evaluate_ownership_non_mutation(probe, before_payload, after_payload),
        _evaluate_non_negative(after_payload),
        _evaluate_idempotency(probe, responses, before_payload, after_payload),
        _evaluate_cross_observer_conservation(probe, responses, snapshots, semantic_graph),
    ):
        if maybe is not None:
            results.append(maybe)

    failed = [r for r in results if r.verdict == "failed"]
    passed = [r for r in results if r.verdict == "passed"]
    undetermined = [r for r in results if r.verdict == "undetermined"]
    verdict = "no_observable_invariant"
    reason = "no before/after invariant could be derived from the observed snapshots"
    confidence = 0.0
    if failed:
        verdict = "failed"
        reason = failed[0].reason
        confidence = max(r.confidence for r in failed)
    elif passed:
        verdict = "passed"
        reason = "derived before/after invariants passed on observed snapshots"
        confidence = max(r.confidence for r in passed)
    elif undetermined:
        verdict = "undetermined"
        reason = undetermined[0].reason
        confidence = max(r.confidence for r in undetermined)

    return {
        "engine": "business_invariant_before_after_v3_phase92s",
        "verdict": verdict,
        "reason": reason,
        "confidence": confidence,
        "failed_count": len(failed),
        "passed_count": len(passed),
        "undetermined_count": len(undetermined),
        "checked_count": len(results),
        "snapshot_evidence_present": before_payload not in (None, {}, []) and after_payload not in (None, {}, []),
        "semantic_observer_graph": semantic_graph,
        "results": [r.as_dict() for r in results],
        "grounding": {
            "candidate_id": probe.get("candidate_id"),
            "risk_type": probe.get("risk_type"),
            "derived_from_probe_plan": bool(isinstance(probe.get("probe_plan"), dict) and probe.get("probe_plan")),
            "source_refs_present": bool(probe.get("source_refs")),
        },
    }
