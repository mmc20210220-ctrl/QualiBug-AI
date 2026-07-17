"""Legacy V12 oracle finding materialization.

Moved out of ``v12_pipeline`` so the compatibility wrapper stays a thin
mainline facade. Symbols remain importable from ``v12_pipeline``.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any

from .v12_legacy_scenario_exec import (
    _count_by,
    _dict,
    _extract,
    _json_or_text,
    _redact,
    _summarize_execution_skip_telemetry,
)

FindingEnricher = Callable[..., dict[str, Any]]
_FINDING_ENRICHER: FindingEnricher | None = None


def register_finding_enricher(hook: FindingEnricher | None) -> None:
    """First-class System Behavior Space finding enricher — no monkey-patch."""
    global _FINDING_ENRICHER
    _FINDING_ENRICHER = hook


def clear_finding_enricher() -> None:
    register_finding_enricher(None)


def _scenario_executable(scenario: Any) -> bool:
    return bool(getattr(scenario, "steps", []) or []) and str(
        getattr(scenario, "execution_policy", "") or ""
    ) in {
        "safe_read_only",
        "approved_test_write",
        "approved_sandbox_write",
        "runtime_approved",
    }


def _evidence_quality_score(gate_passed: bool, evidence_strength: str, *, full_runtime_receipt: bool = False) -> int:
    """Grade confidence by the strongest evidence layer actually captured.

    Avoids a flat 95 for every finding: HTTP-status-only inferences score
    lower than data-layer-confirmed ones so reviewers can triage honestly.
    Confirmed runtime receipts with reproduction steps pass the customer gate.
    """
    if not gate_passed:
        return 55
    return {
        "runtime_and_db": 95,
        "runtime_before_after": 92,
        "db": 90,
        "runtime": 92 if full_runtime_receipt else 65,
    }.get(evidence_strength, 92 if full_runtime_receipt else 65)


def _is_harness_support_step(step: dict[str, Any]) -> bool:
    """True for fixture/auth/resolver calls that are not the tested action."""
    action = str(step.get("action") or "").strip().lower()
    path = str(step.get("path") or "").split("?", 1)[0].rstrip("/").lower()
    return (
        action == "login"
        or action.startswith("login_")
        or action.startswith("resolve_")
        or action.startswith("bootstrap_create_")
        or path.endswith("/login")
    )


def _trace_primary_step(trace: dict[str, Any], oracle_result: Any) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    if not steps:
        return {}
    rule = str(getattr(oracle_result, "violated_rule", "") or "").strip().lower()
    _writes = {"POST", "PUT", "PATCH", "DELETE"}
    # For idempotency/replay violations the primary evidence must be the
    # repeated *write* call (the duplicate that should have been rejected),
    # never a trailing read-only observation appended for state capture.
    if rule in {"non_idempotent", "replay", "idempotency"}:
        write_steps = [
            s
            for s in steps
            if isinstance(s, dict)
            and not _is_harness_support_step(s)
            and str(s.get("method") or "").upper() in _writes
        ]
        if len(write_steps) >= 2:
            return write_steps[-1]
        if write_steps:
            return write_steps[-1]
    # Otherwise prefer the last step whose observed status contradicts the
    # expected status (the actual assertion failure).
    for step in reversed(steps):
        if not isinstance(step, dict) or _is_harness_support_step(step):
            continue
        status = int(step.get("status") or 0)
        expected = int(step.get("expected_status") or 0)
        if expected and status != expected:
            return step
    # Fall back to the last *write* step before any trailing observe read.
    for step in reversed(steps):
        if (
            isinstance(step, dict)
            and not _is_harness_support_step(step)
            and str(step.get("method") or "").upper() in _writes
        ):
            return step
    for step in reversed(steps):
        if isinstance(step, dict) and not _is_harness_support_step(step):
            return step
    return {}


def _oracle_primary_step_gap(step: dict[str, Any], oracle_result: Any) -> str:
    """Ensure an HTTP oracle verdict describes the selected target step.

    This is deliberately a delivery gate in addition to the oracle-level
    support-step filter.  It protects persisted/third-party oracle results and
    future oracle implementations from attaching a bootstrap failure to a
    successful target mutation.
    """
    oracle_name = str(getattr(oracle_result, "oracle_name", "") or "").strip()
    if oracle_name != "HttpStatusOracle":
        return ""
    if not step:
        return "ORACLE_PRIMARY_STEP_MISSING"
    method = str(step.get("method") or "").upper()
    path = str(step.get("path") or "")
    response = step.get("response") if isinstance(step.get("response"), dict) else {}
    status = int(response.get("status_code") or step.get("status") or 0)
    if not method or not path or not status:
        return "ORACLE_PRIMARY_STEP_MISSING"

    rule = str(getattr(oracle_result, "violated_rule", "") or "").strip().lower()
    expected = int(step.get("expected_status") or 0)
    body = response.get("body")
    if rule == "server_5xx" and status < 500:
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    if rule == "expected_status_mismatch" and (not expected or status == expected):
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    if rule == "wrong_create_status" and not (
        method in {"POST", "PUT"} and expected == 201 and status == 204
    ):
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    if rule == "200_with_error" and not (
        status == 200 and isinstance(body, dict) and body.get("ok") is False
    ):
        return "ORACLE_PRIMARY_STEP_MISMATCH"

    actual = str(getattr(oracle_result, "actual", "") or "")
    actual_status_match = re.search(r"\bHTTP\s+(\d{3})\b", actual, flags=re.IGNORECASE)
    if actual_status_match and int(actual_status_match.group(1)) != status:
        return "ORACLE_PRIMARY_STEP_MISMATCH"
    return ""


def _status_confirmation_gap(
    step: dict[str, Any],
    trace: dict[str, Any],
    oracle_result: Any,
) -> str:
    """Require a proven valid control before calling an expected-2xx 4xx a Bug."""
    oracle_name = str(getattr(oracle_result, "oracle_name", "") or "").strip()
    rule = str(getattr(oracle_result, "violated_rule", "") or "").strip().lower()
    if oracle_name != "HttpStatusOracle" or rule != "expected_status_mismatch":
        return ""
    expected = int(step.get("expected_status") or 0)
    response = step.get("response") if isinstance(step.get("response"), dict) else {}
    actual = int(response.get("status_code") or step.get("status") or 0)
    if not (200 <= expected < 300 and 400 <= actual < 500):
        return ""
    validation = trace.get("request_contract_validation")
    if isinstance(validation, dict) and validation.get("valid_success_control") is True:
        return ""
    if _trace_has_valid_success_control(trace, step):
        return ""
    # A 4xx can be caused by missing fixtures, stale identities, incomplete
    # payloads or credentials. It remains a real observation, but without a
    # successful control proving the request contract it is not a customer
    # deliverable defect.
    return "VALID_SUCCESS_CONTROL_REQUIRED"


def _trace_has_valid_success_control(trace: dict[str, Any], failing_step: dict[str, Any]) -> bool:
    """True only when the same endpoint contract already succeeded in-trace."""
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    failing_id = id(failing_step)
    failing_method = str(failing_step.get("method") or "").upper()
    failing_path = str(failing_step.get("path") or "").split("?", 1)[0]
    if not failing_method or not failing_path:
        return False

    def _control_path_shape(path: str) -> str:
        # Compare contracts by shape so a prior success on the same route with a
        # different concrete id still counts as a valid control (UUID / long int).
        text = str(path or "").split("?", 1)[0]
        text = re.sub(
            r"/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            "/{id}",
            text,
        )
        text = re.sub(r"/\d{6,}", "/{id}", text)
        return text

    failing_shape = _control_path_shape(failing_path)
    for item in steps:
        if not isinstance(item, dict) or id(item) == failing_id:
            continue
        status = int(
            (item.get("response") or {}).get("status_code")
            if isinstance(item.get("response"), dict)
            else item.get("status")
            or 0
        )
        if not (200 <= status < 300):
            continue
        method = str(item.get("method") or "").upper()
        path = str(item.get("path") or "").split("?", 1)[0]
        # A successful bootstrap on another endpoint proves only that some
        # authentication and fixture operation worked. It does not prove the
        # failing endpoint's payload, permissions or state preconditions.
        action = str(item.get("action") or "").strip().lower()
        if action.startswith("bootstrap_create_"):
            continue
        if method == failing_method and _control_path_shape(path) == failing_shape:
            return True
    return False


def _trace_before_after_snapshot(trace: dict[str, Any], primary_step: dict[str, Any] | None = None) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    runtime_steps = [step for step in steps if isinstance(step, dict) and isinstance(step.get("response"), dict)]
    if not runtime_steps:
        return {}

    def _snapshot(step: dict[str, Any]) -> dict[str, Any]:
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        return {
            "action": str(step.get("action") or ""),
            "method": str(step.get("method") or "").upper(),
            "path": str(step.get("path") or ""),
            "status_code": int(response.get("status_code") or step.get("status") or 0),
            "body": response.get("body"),
            "expected_status": int(step.get("expected_status") or 0),
        }

    def _successful_observer_read(step: dict[str, Any]) -> bool:
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        status_code = int(response.get("status_code") or step.get("status") or 0)
        return (
            str(step.get("method") or "").upper() in {"GET", "HEAD"}
            and not _is_harness_support_step(step)
            and 200 <= status_code < 300
        )

    before_step = runtime_steps[0]
    after_step = runtime_steps[-1]
    if primary_step:
        primary_index = next(
            (index for index, item in enumerate(steps) if isinstance(item, dict) and item is primary_step),
            -1,
        )
        if primary_index >= 0:
            before_candidates = [
                item
                for item in steps[:primary_index]
                if isinstance(item, dict) and isinstance(item.get("response"), dict) and _successful_observer_read(item)
            ]
            after_candidates = [
                item
                for item in steps[primary_index + 1 :]
                if isinstance(item, dict) and isinstance(item.get("response"), dict) and _successful_observer_read(item)
            ]
            before_step = before_candidates[-1] if before_candidates else before_step
            after_step = after_candidates[-1] if after_candidates else primary_step
    return {
        "before": _snapshot(before_step),
        "after": _snapshot(after_step),
    }


def _runtime_contract_evidence_from_snapshot(
    before_after_snapshot: dict[str, Any],
    primary_step: dict[str, Any],
) -> dict[str, Any]:
    """Expose source-bound before/after observations to the contract gate."""

    before = before_after_snapshot.get("before") if isinstance(before_after_snapshot.get("before"), dict) else {}
    after = before_after_snapshot.get("after") if isinstance(before_after_snapshot.get("after"), dict) else {}
    if not after:
        return {}
    after_body = after.get("body")
    after_status = int(after.get("status_code") or 0)
    if not (200 <= after_status < 300) or after_body in (None, {}, []):
        return {}
    evidence: dict[str, Any] = {
        "final_state_observation": after_body,
        "treatment_observation": after,
        "business_effect_observed": True,
    }
    before_body = before.get("body")
    before_status = int(before.get("status_code") or 0)
    if 200 <= before_status < 300 and before_body not in (None, {}, []):
        evidence["control_observation"] = before
    response = primary_step.get("response") if isinstance(primary_step.get("response"), dict) else {}
    primary_status = int(response.get("status_code") or primary_step.get("status") or 0)
    if primary_status:
        evidence["treatment_result"] = {
            "method": str(primary_step.get("method") or "").upper(),
            "path": str(primary_step.get("path") or ""),
            "status_code": primary_status,
            "body": response.get("body"),
        }
    return evidence


def _compact_semantic_text(value: Any, *, max_len: int = 240) -> str:
    """Return a compact, redaction-aware string for customer/evaluator semantics."""

    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1<REDACTED>",
        text,
    )
    text = re.sub(
        r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*[^,\s;}\]]+",
        r"\1=<REDACTED>",
        text,
    )
    if max_len > 0 and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _semantic_signature_terms(*values: Any, limit: int = 32) -> list[str]:
    """Derive generic defect signature tokens from runtime semantics, not GT."""

    stop_words = {
        "api",
        "http",
        "https",
        "post",
        "get",
        "put",
        "patch",
        "delete",
        "expected",
        "actual",
        "oracle",
        "status",
        "response",
        "request",
        "should",
        "must",
        "with",
        "when",
        "from",
        "that",
        "this",
        "true",
        "false",
    }
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _compact_semantic_text(value, max_len=500).lower()
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text):
            normalized = token.strip("_-")
            if not normalized or normalized in stop_words or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(normalized)
            if len(terms) >= limit:
                return terms
    return terms


def _oracle_semantic_signature(
    scenario: Any,
    oracle_result: Any,
    *,
    method: str,
    path: str,
    actor_label: str,
    status: int,
    assertion: str,
    actual: str,
) -> dict[str, Any]:
    """Preserve the concrete defect meaning carried by a confirmed runtime oracle."""

    oracle_name = _compact_semantic_text(getattr(oracle_result, "oracle_name", "Oracle"), max_len=80)
    scenario_title = _compact_semantic_text(getattr(scenario, "title", ""), max_len=160)
    violated_rule = _compact_semantic_text(getattr(oracle_result, "violated_rule", ""), max_len=160)
    explanation = _compact_semantic_text(getattr(oracle_result, "explanation", ""), max_len=260)
    expected_behavior = _compact_semantic_text(assertion or violated_rule, max_len=220)
    actual_behavior = _compact_semantic_text(actual or (f"HTTP {status}" if status else ""), max_len=220)
    request = f"{method} {path}".strip()
    signature = {
        "oracle_name": oracle_name,
        "scenario_title": scenario_title,
        "request": request,
        "method": method,
        "path": path,
        "actor": _compact_semantic_text(actor_label, max_len=80),
        "response_status": status,
        "expected_behavior": expected_behavior,
        "actual_behavior": actual_behavior,
        "violated_rule": violated_rule,
        "explanation": explanation,
        "defect_signature_terms": _semantic_signature_terms(
            oracle_name,
            scenario_title,
            method,
            path,
            actor_label,
            status,
            expected_behavior,
            actual_behavior,
            violated_rule,
            explanation,
        ),
    }
    return {key: value for key, value in signature.items() if value not in ("", [], 0)}


def _semantic_v12_title(
    scenario: Any,
    oracle_result: Any,
    *,
    method: str,
    path: str,
    assertion: str,
    actual: str,
    status: int,
) -> str:
    oracle_name = _compact_semantic_text(getattr(oracle_result, "oracle_name", "Oracle"), max_len=80) or "Oracle"
    scenario_title = _compact_semantic_text(getattr(scenario, "title", ""), max_len=120)
    request = f"{method} {path}".strip()
    expected_behavior = _compact_semantic_text(
        assertion or getattr(oracle_result, "violated_rule", ""),
        max_len=100,
    )
    actual_behavior = _compact_semantic_text(actual or (f"HTTP {status}" if status else ""), max_len=100)
    parts = [f"[V12 {oracle_name}]"]
    if scenario_title:
        parts.append(scenario_title)
    if request:
        parts.append(request)
    if expected_behavior:
        parts.append(f"expected {expected_behavior}")
    if actual_behavior:
        parts.append(f"actual {actual_behavior}")
    return _compact_semantic_text(" | ".join(parts), max_len=360)


def _semantic_v12_description(
    oracle_result: Any,
    *,
    method: str,
    path: str,
    actor_label: str,
    status: int,
    assertion: str,
    actual: str,
) -> str:
    lines: list[str] = []
    expected_behavior = _compact_semantic_text(assertion, max_len=320)
    actual_behavior = _compact_semantic_text(actual, max_len=320)
    explanation = _compact_semantic_text(getattr(oracle_result, "explanation", ""), max_len=420)
    violated_rule = _compact_semantic_text(getattr(oracle_result, "violated_rule", ""), max_len=180)
    request = f"{method} {path}".strip()
    if explanation:
        lines.append(explanation)
    if expected_behavior:
        lines.append(f"Expected: {expected_behavior}")
    if actual_behavior:
        lines.append(f"Actual: {actual_behavior}")
    if request:
        observed = f"Observed request: {request}"
        if actor_label:
            observed += f" as {actor_label}"
        if status:
            observed += f" -> HTTP {status}"
        lines.append(observed)
    if violated_rule:
        lines.append(f"Violated rule: {violated_rule}")
    return "\n".join(lines)


def _trace_errors_block_runtime_confirmation(trace: dict[str, Any]) -> bool:
    """Return True when trace errors should block customer delivery confirmation."""

    errors = [
        str(value or "").strip()
        for value in (trace.get("errors") if isinstance(trace.get("errors"), list) else [])
        if str(value or "").strip()
    ]
    if not errors:
        return False
    non_blocking_prefixes = (
        "missing_runtime_path_binding",
        "missing_runtime_body_binding",
        "invalid_source_bound_step",
        "write_cleanup_operation_not_declared",
    )
    if all(
        any(error.startswith(prefix) for prefix in non_blocking_prefixes)
        for error in errors
    ):
        return False
    steps = [
        step
        for step in (trace.get("steps") if isinstance(trace.get("steps"), list) else [])
        if isinstance(step, dict)
    ]
    if any(int(step.get("status") or 0) > 0 for step in steps):
        return False
    return True


def _confirmed_oracle_finding(
    scenario: Any,
    trace: dict[str, Any],
    oracle_result: Any,
    evidence: Any,
    *,
    campaign_id: str,
    discovery_round: int,
    base_url: str,
) -> dict[str, Any]:
    step = _trace_primary_step(trace, oracle_result)
    # 主链 6 × 主链 1/5: a finding whose primary evidence step was deliberately
    # blocked by the customer's production-data safety boundary carries NO real
    # evidence (the request was never sent). It must never be claimed as a
    # reproduced/confirmed defect — only as an auditable, blocked candidate.
    _step_blocked = bool(step.get("execution_blocked"))
    _trace_blocked = bool(trace.get("production_data_blocked"))
    safety_boundary_blocked = _step_blocked or _trace_blocked
    safety_boundary_reason = (
        str(step.get("skipped_reason") or "") if _step_blocked
        else str(trace.get("production_data_block_reason") or "")
    )
    path = str(step.get("path") or "")
    method = str(step.get("method") or "").upper()
    response = step.get("response") if isinstance(step.get("response"), dict) else {}
    status = int(response.get("status_code") or step.get("status") or 0)
    actor = str(getattr(scenario, "actor_token", "") or "")
    actor_label = str((getattr(scenario, "actors", []) or ["readonly"])[0] or "readonly")
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    reproduction_steps = [line for line in str(getattr(evidence, "reproduction_steps", "") or "").splitlines() if str(line).strip()]
    if not reproduction_steps and method and path:
        reproduction_steps = [f"{method} {path}"]
    assertion = str(getattr(oracle_result, "expected", "") or "").strip()
    actual = str(getattr(oracle_result, "actual", "") or "").strip()
    target = (base_url.rstrip("/") + path) if base_url and path.startswith("/") else (base_url or path)
    before_after_snapshot = _trace_before_after_snapshot(trace, primary_step=step)
    if not before_after_snapshot and isinstance(trace.get("before_after_snapshot"), dict):
        before_after_snapshot = dict(trace.get("before_after_snapshot") or {})
    runtime_contract_evidence = _runtime_contract_evidence_from_snapshot(before_after_snapshot, step)
    business_invariant_evaluation = (
        dict(trace.get("business_invariant_evaluation") or {})
        if isinstance(trace.get("business_invariant_evaluation"), dict)
        else {}
    )
    db_evidence = dict(trace.get("db_evidence") or {}) if isinstance(trace.get("db_evidence"), dict) else {}
    # Only real DB evidence counts; an "unavailable" marker must not inflate the
    # evidence strength. Accept both canonical shapes: the runtime verifier shape
    # (status == "captured") and an explicit before/after snapshot pair.
    db_status = str(db_evidence.get("status") or "").strip().lower()
    _has_before_after_db = isinstance(db_evidence.get("before_db_snapshot"), dict) and isinstance(
        db_evidence.get("after_db_snapshot"), dict
    )
    db_captured = db_status == "captured" or (_has_before_after_db and db_status != "unavailable")
    status_confirmation_gap = _status_confirmation_gap(step, trace, oracle_result)
    oracle_primary_step_gap = _oracle_primary_step_gap(step, oracle_result)
    evidence_strength = "runtime"
    if before_after_snapshot and db_captured:
        evidence_strength = "runtime_and_db"
    elif before_after_snapshot:
        evidence_strength = "runtime_before_after"
    elif db_captured:
        evidence_strength = "db"
    # L1 protocol crashes (HTTP 5xx on the target step) are confirmed from the
    # response itself. State-precondition bookskeeping belonging to other oracles
    # must not demote a real server error into a candidate.
    violated_rule = str(getattr(oracle_result, "violated_rule", "") or "").strip().lower()
    oracle_name = str(getattr(oracle_result, "oracle_name", "") or "").strip()
    server_protocol_crash = (
        oracle_name == "HttpStatusOracle"
        and violated_rule == "server_5xx"
        and status >= 500
    )
    runtime_confirmable = (
        _scenario_executable(scenario)
        and bool(method and path and status)
        and bool(reproduction_steps)
        and not _trace_errors_block_runtime_confirmation(
            trace if isinstance(trace, dict) else {}
        )
        and bool(getattr(evidence, "vote_summary", {}).get("confirmation_threshold_met"))
        # A path that still carries an unresolved {param}/:param placeholder means
        # the probe never bound a real entity id — the request was malformed, so a
        # resulting 4xx/5xx is a probe artifact, not a confirmed target defect.
        and "{" not in path and not re.search(r"/:[A-Za-z_]", path)
        # A declared state precondition (e.g. status=PAID) that could not be
        # satisfied at runtime means the tested transition was never actually
        # exercised from the claimed state — do not confirm on fabricated state.
        # Exception: observed HTTP 5xx on the target step is independent of
        # state-precondition bookkeeping.
        and (
            server_protocol_crash
            or not (trace.get("precondition_not_met") if isinstance(trace, dict) else None)
        )
        # Expected-success 4xx mismatches need a known-valid control. Otherwise
        # they are usually probe/test-data artifacts and must stay candidates.
        and not status_confirmation_gap
        # The oracle violation must be evidenced by the selected target step,
        # never by a failed fixture/bootstrap request elsewhere in the trace.
        and not oracle_primary_step_gap
        # 主链 6 × 主链 1/5: a step blocked by the production-data safety
        # boundary was never executed, so any "violation" derived from its
        # absent response is not a confirmed target defect. Force candidate.
        and not safety_boundary_blocked
    )
    confirmation_status = "confirmed" if runtime_confirmable else "candidate"
    gate_passed = bool(runtime_confirmable)
    if safety_boundary_blocked:
        # Defense in depth: even if a future change drops the implicit errors
        # marker, a blocked step can never become a confirmed defect.
        confirmation_status = "candidate"
        gate_passed = False
    bug_status = "reproduced" if gate_passed else "suspected"
    oracle_tier = str(getattr(oracle_result, "oracle_tier", "") or "").strip()
    oracle_customer_deliverable = getattr(oracle_result, "customer_deliverable", None)
    if oracle_customer_deliverable is False or oracle_tier == "internal_clue":
        # Contract-gated heuristic business oracles must not enter customer delivery.
        confirmation_status = "candidate"
        gate_passed = False
        bug_status = "suspected"
    raw_request = {"method": method, "path": path}
    if actor_label:
        raw_request["actor"] = actor_label
    step_request = step.get("request") if isinstance(step.get("request"), dict) else {}
    if "body" in step_request:
        # The executor already redacts request bodies before placing them on the
        # trace. Preserve that source-bound payload in the evidence receipt so
        # protocol failures from materially different mutations are not merged.
        raw_request["body"] = step_request.get("body")
    raw_response = {"status_code": status, "body": response.get("body")}
    delivery_status = (
        "blocked_safety_boundary"
        if safety_boundary_blocked
        else (
            "clue"
            if oracle_customer_deliverable is False or oracle_tier == "internal_clue"
            else ("defect" if gate_passed else "candidate")
        )
    )
    trace_evidence = trace.get("evidence") if isinstance(trace.get("evidence"), dict) else {}
    contract_observation_keys = (
        "control_succeeded",
        "authorized_control",
        "effect_count",
        "invariant_held",
        "control_observation",
        "treatment_observation",
        "treatment_result",
        "observer_ids",
    )
    semantic_signature = _oracle_semantic_signature(
        scenario,
        oracle_result,
        method=method,
        path=path,
        actor_label=actor_label,
        status=status,
        assertion=assertion,
        actual=actual,
    )
    _contract_keys_present = [key for key in contract_observation_keys if key in trace_evidence]
    if _contract_keys_present:
        semantic_signature["contract_observation_keys"] = _contract_keys_present
    finding = {
        "severity": getattr(oracle_result, "severity", "P1"),
        "title": _semantic_v12_title(
            scenario,
            oracle_result,
            method=method,
            path=path,
            assertion=assertion,
            actual=actual,
            status=status,
        ),
        "category": getattr(scenario, "category", "scenario_flow"),
        "source": "v12_state_graph",
        "description": _semantic_v12_description(
            oracle_result,
            method=method,
            path=path,
            actor_label=actor_label,
            status=status,
            assertion=assertion,
            actual=actual,
        ),
        "confidence_score": float(getattr(oracle_result, "confidence", 0.0) or 0.0),
        "evidence_id": str(getattr(evidence, "evidence_id", "") or ""),
        "oracle": oracle_result.to_dict() if hasattr(oracle_result, "to_dict") else {},
        "behavior_slice_id": getattr(scenario, "behavior_slice_id", ""),
        "discovery_round": discovery_round,
        "campaign_id": campaign_id,
        "source_refs": [
            dict(item)
            for item in (getattr(scenario, "source_refs", []) or [])
            if isinstance(item, dict)
        ],
        "execution_status": "executed",
        "confirmation_status": confirmation_status,
        "gate_passed": gate_passed,
        "bug_status": bug_status,
        "customer_delivery_status": delivery_status,
        "oracle_tier": oracle_tier or ("internal_clue" if delivery_status == "clue" else ""),
        "blocked_by_safety_boundary": safety_boundary_blocked,
        "blocked_reason": safety_boundary_reason if safety_boundary_blocked else "",
        "expected": assertion,
        "actual": actual,
        "semantic_signature": semantic_signature,
        "timestamp": timestamp,
        "failed_assertions": [actual] if actual else [],
        "evidence": {
            "request": f"{method} {path}",
            "response": f"HTTP {status}",
            "assertion": assertion or actual or str(getattr(oracle_result, "violated_rule", "") or "oracle_violation"),
            "semantic_signature": semantic_signature,
            "expected_behavior": semantic_signature.get("expected_behavior", ""),
            "actual_behavior": semantic_signature.get("actual_behavior", ""),
            "defect_signature_terms": semantic_signature.get("defect_signature_terms", []),
            "timestamp": timestamp,
            "target": target,
            "actor": actor_label,
            "reproduction_steps": reproduction_steps,
            "dual_2xx": bool(
                oracle_tier == "internal_clue"
                and (
                    "idempot" in str(getattr(oracle_result, "oracle_name", "") or "").lower()
                    or "concurr" in str(getattr(oracle_result, "oracle_name", "") or "").lower()
                )
            ),
        },
        "raw_evidence": {
            "has_real_evidence": bool(method and path and status),
            "timestamp": timestamp,
            "request_raw": raw_request,
            "response_raw": raw_response,
            "execution_trace": {"evidence_id": str(getattr(evidence, "evidence_id", "") or ""), "layers": list(getattr(evidence, "layers_triggered", []) or [])},
            "db_snapshot": db_evidence if db_evidence else {},
        },
        "reproduction": {
            "method": method,
            "path": path,
            "is_synthetic": False,
            "har_evidence": {"status_code": status, "response_body": response.get("body")},
        },
        "evidence_quality": {
            "level": "validated" if gate_passed else "needs_evidence",
            "score": _evidence_quality_score(
                gate_passed,
                evidence_strength,
                full_runtime_receipt=bool(gate_passed and method and path and status and reproduction_steps),
            ),
            "can_reproduce": bool(gate_passed),
            "evidence_strength": evidence_strength,
        },
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED" if gate_passed else "SEMANTIC_CANDIDATE",
            "business_evidence_status": "VALIDATED" if gate_passed else "PENDING_EVIDENCE",
            "final_review_status": "VALIDATED_CANDIDATE" if gate_passed else "NEEDS_MORE_EVIDENCE",
            "missing_requirements": [
                gap for gap in (status_confirmation_gap, oracle_primary_step_gap) if gap
            ],
        },
        "final_review_status": "VALIDATED_CANDIDATE" if gate_passed else "NEEDS_MORE_EVIDENCE",
        "business_evidence_status": "VALIDATED" if gate_passed else "PENDING_EVIDENCE",
        "reproduction_steps": reproduction_steps,
        "before_after_snapshot": before_after_snapshot,
        "business_invariant_evaluation": business_invariant_evaluation,
        "db_evidence": db_evidence,
        "evidence_strength": evidence_strength,
    }
    if runtime_contract_evidence:
        finding["evidence"].update(runtime_contract_evidence)
    # Attach sandbox write evidence (before/after/cleanup) when present on the trace.
    sandbox = trace.get("sandbox_write") if isinstance(trace.get("sandbox_write"), dict) else {}
    sandbox_evidence = sandbox.get("evidence") if isinstance(sandbox.get("evidence"), dict) else {}
    # Preserve typed contract observations for the downstream contract-oracle
    # gate.  A runtime State/Permission/Concurrency result is not customer
    # deliverable merely because an HTTP response looked wrong; when the
    # governed observer explicitly records a control or invariant result, keep
    # that fact attached to the finding instead of dropping it at normalization.
    for contract_key in contract_observation_keys:
        if contract_key in trace_evidence:
            finding["evidence"][contract_key] = trace_evidence[contract_key]
    before_ref = str(
        sandbox_evidence.get("before_snapshot_ref")
        or trace_evidence.get("before_snapshot_ref")
        or ""
    )
    after_ref = str(
        sandbox_evidence.get("after_snapshot_ref")
        or trace_evidence.get("after_snapshot_ref")
        or ""
    )
    cleanup = (
        sandbox_evidence.get("cleanup")
        or trace_evidence.get("cleanup")
        or sandbox.get("cleanup")
        or {}
    )
    if isinstance(cleanup, dict) and (before_ref or after_ref or cleanup):
        finding["evidence"]["before_snapshot_ref"] = before_ref
        finding["evidence"]["after_snapshot_ref"] = after_ref
        finding["evidence"]["cleanup"] = {
            "status": str(cleanup.get("status") or ""),
            "receipt_ref": str(cleanup.get("receipt_ref") or ""),
        }
    if actor:
        finding["evidence"]["actor_token_present"] = True
    if _FINDING_ENRICHER is not None:
        finding = _FINDING_ENRICHER(
            finding,
            scenario,
            trace,
            oracle_result,
            evidence,
            campaign_id=campaign_id,
            discovery_round=discovery_round,
            base_url=base_url,
        )
        if not isinstance(finding, dict):
            raise TypeError("finding_enricher_must_return_dict")
    return finding



