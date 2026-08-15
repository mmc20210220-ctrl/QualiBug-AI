from __future__ import annotations

"""Bounded, evidence-linked Harness proposal generation.

Proposals may edit only declarative StrategyBundle fields listed here. The
proposer cannot edit code, evaluator logic, evidence thresholds, safety
boundaries, ports, or hidden evaluation assets.
"""

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .discovery_weakness_miner import WEAKNESS_REPORT_SCHEMA
from .policy_registry import (
    DiscoveryPolicy,
    ExecutionPolicy,
    PolicyRecord,
    ReasonerPolicy,
    StrategyBundle,
    VerificationPolicy,
)
from .policy_wiring import _REASONER_MAX_HYPOTHESES_PER_ENGINE


HARNESS_PROPOSAL_SCHEMA = "qualibug.discovery-harness-proposals.v1"


class HarnessProposalError(ValueError):
    """A proposed Harness edit is unsafe, unbounded, or not evidence-linked."""


_ALLOWED_APPEND_VALUES: dict[str, set[str]] = {
    "discovery.candidate_ranking_signals": {
        "weakness_recurrence",
        "cross_industry_recurrence",
        "runtime_executability",
        "cleanup_risk",
        "evidence_completion_probability",
    },
    "discovery.endpoint_binding_strategy": {
        "schema_parameter_compatibility",
        "documented_example_binding",
        "observed_operation_binding",
    },
    "verification.evidence_collection_order": {
        "valid_success_control",
        "runtime_trace_join",
        "before_after",
        "cleanup_receipt",
        "multi_observer",
        "async_settlement",
    },
    "execution.runtime_binding_sources": {
        "documented_example",
        "documented_schema_generated_value",
        "observed_list_response",
        "prior_success_receipt",
        "test_data_receipt",
    },
    "execution.cleanup_created_resource_id_sources": {
        "response_body_nested_id",
        "observer_diff_created_id",
        "location_header",
        "audit_receipt",
    },
    "execution.trace_join_key_order": {
        "campaign_round_slice_id",
        "request_trace_id",
        "audit_receipt_id",
    },
}

_ALLOWED_INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "discovery.max_hypotheses_execute": (1, 200),
    "discovery.max_rounds": (1, 24),
    "verification.async_window_seconds": (0, 300),
    "execution.precondition_resolution_attempts": (1, 5),
    "execution.cleanup_retry_count": (0, 3),
}

_FROZEN_PATHS = {
    "reasoner.timeout_seconds",
    "reasoner.max_tokens",
    "reasoner.max_workers",
    "reasoner.max_hypotheses_per_engine",
    "verification.verifier_relaxed",
    "verification.reject_non_execution_oracle_votes",
    "verification.require_valid_success_control_for_5xx",
    "verification.require_joinable_execution_trace",
    "verification.require_cleanup_success_for_customer_delivery",
    "discovery.require_documented_endpoint",
    "execution.require_cleanup_receipt",
    "execution.persist_cross_round_traces",
    "execution.require_runtime_receipt_for_slice_confirmation",
}


def _signature(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:20]


def _rehydrate(raw: dict[str, Any]) -> StrategyBundle:
    return StrategyBundle(
        reasoner=ReasonerPolicy(**dict(raw.get("reasoner") or {})),
        discovery=DiscoveryPolicy(**dict(raw.get("discovery") or {})),
        verification=VerificationPolicy(**dict(raw.get("verification") or {})),
        execution=ExecutionPolicy(**dict(raw.get("execution") or {})),
    )


def _strategy_value(strategy: StrategyBundle, path: str) -> Any:
    section_name, field_name = path.split(".", 1)
    section = getattr(strategy, section_name, None)
    if section is None or not hasattr(section, field_name):
        raise HarnessProposalError(f"unknown StrategyBundle path: {path}")
    return getattr(section, field_name)


def validate_strategy_guardrails(strategy: StrategyBundle) -> dict[str, Any]:
    checks = [
        ("reasoner_timeout_floor", strategy.reasoner.timeout_seconds >= 300),
        ("reasoner_token_floor", strategy.reasoner.max_tokens >= 32768),
        ("reasoner_worker_cap", strategy.reasoner.max_workers <= 4),
        (
            "reasoner_hypothesis_guardrail",
            strategy.reasoner.max_hypotheses_per_engine
            == _REASONER_MAX_HYPOTHESES_PER_ENGINE,
        ),
        ("verifier_not_relaxed", strategy.verification.verifier_relaxed is False),
        ("reject_non_execution_oracle_votes", strategy.verification.reject_non_execution_oracle_votes is True),
        ("valid_success_control_required", strategy.verification.require_valid_success_control_for_5xx is True),
        ("joinable_trace_required", strategy.verification.require_joinable_execution_trace is True),
        ("cleanup_success_required", strategy.verification.require_cleanup_success_for_customer_delivery is True),
        ("documented_endpoint_required", strategy.discovery.require_documented_endpoint is True),
        ("cleanup_receipt_required", strategy.execution.require_cleanup_receipt is True),
        ("cross_round_trace_persistence", strategy.execution.persist_cross_round_traces is True),
        ("runtime_receipt_required", strategy.execution.require_runtime_receipt_for_slice_confirmation is True),
    ]
    return {
        "passed": all(passed for _, passed in checks),
        "checks": [{"name": name, "passed": passed} for name, passed in checks],
    }


def apply_bounded_harness_edit(
    strategy: StrategyBundle,
    edit: dict[str, Any],
) -> StrategyBundle:
    """Apply exactly one registry-approved declarative edit."""

    if not isinstance(edit, dict):
        raise HarnessProposalError("Harness edit must be an object")
    path = str(edit.get("path") or "").strip()
    operation = str(edit.get("operation") or "").strip()
    if not path or not operation:
        raise HarnessProposalError("Harness edit requires path and operation")
    if path in _FROZEN_PATHS:
        raise HarnessProposalError(f"Harness path is frozen and cannot be proposed: {path}")
    current = _strategy_value(strategy, path)
    proposed = copy.deepcopy(strategy)
    section_name, field_name = path.split(".", 1)
    section = getattr(proposed, section_name)

    if operation == "append_unique":
        allowed = _ALLOWED_APPEND_VALUES.get(path)
        if allowed is None:
            raise HarnessProposalError(f"append_unique is not allowed for path: {path}")
        value = str(edit.get("value") or "").strip()
        if value not in allowed:
            raise HarnessProposalError(f"unsupported value {value!r} for {path}")
        existing = list(current) if isinstance(current, list) else None
        if existing is None:
            raise HarnessProposalError(f"append_unique target is not a list: {path}")
        if value in existing:
            raise HarnessProposalError(f"Harness edit has no effect: {path} already contains {value}")
        setattr(section, field_name, existing + [value])
    elif operation == "set_integer":
        bounds = _ALLOWED_INTEGER_RANGES.get(path)
        if bounds is None:
            raise HarnessProposalError(f"set_integer is not allowed for path: {path}")
        value = edit.get("value")
        if isinstance(value, bool) or not isinstance(value, int):
            raise HarnessProposalError(f"set_integer value must be an integer: {path}")
        if not bounds[0] <= value <= bounds[1]:
            raise HarnessProposalError(f"value for {path} must be between {bounds[0]} and {bounds[1]}")
        if value == current:
            raise HarnessProposalError(f"Harness edit has no effect: {path} already equals {value}")
        setattr(section, field_name, value)
    else:
        raise HarnessProposalError(f"unsupported Harness edit operation: {operation}")

    normalized = _rehydrate(asdict(proposed))
    guardrails = validate_strategy_guardrails(normalized)
    if not guardrails["passed"]:
        failed = [item["name"] for item in guardrails["checks"] if not item["passed"]]
        raise HarnessProposalError(f"Harness edit violates frozen guardrails: {failed}")
    return normalized


def _edit_templates(signature: str, strategy: StrategyBundle) -> list[dict[str, Any]]:
    if signature in {"ENDPOINT_BINDING_MISSING", "ENDPOINT_BINDING_DROPPED_AGGREGATE"}:
        return [
            {"path": "discovery.endpoint_binding_strategy", "operation": "append_unique", "value": "schema_parameter_compatibility"},
            {"path": "discovery.endpoint_binding_strategy", "operation": "append_unique", "value": "documented_example_binding"},
            {"path": "discovery.candidate_ranking_signals", "operation": "append_unique", "value": "runtime_executability"},
        ]
    if signature == "RUNTIME_PATH_BINDING_MISSING":
        return [
            {"path": "execution.runtime_binding_sources", "operation": "append_unique", "value": "documented_schema_generated_value"},
            {"path": "execution.runtime_binding_sources", "operation": "append_unique", "value": "observed_list_response"},
            {"path": "execution.runtime_binding_sources", "operation": "append_unique", "value": "prior_success_receipt"},
            {
                "path": "execution.precondition_resolution_attempts",
                "operation": "set_integer",
                "value": min(5, strategy.execution.precondition_resolution_attempts + 1),
            },
        ]
    if signature == "PRECONDITION_NOT_MET":
        return [
            {"path": "execution.runtime_binding_sources", "operation": "append_unique", "value": "test_data_receipt"},
            {
                "path": "execution.precondition_resolution_attempts",
                "operation": "set_integer",
                "value": min(5, strategy.execution.precondition_resolution_attempts + 1),
            },
        ]
    if signature in {"CLEANUP_FAILED", "CLEANUP_NOT_REVERSIBLE", "SANDBOX_WRITE_INCOMPLETE"}:
        return [
            {
                "path": "execution.cleanup_retry_count",
                "operation": "set_integer",
                "value": min(3, strategy.execution.cleanup_retry_count + 1),
            },
            {"path": "execution.cleanup_created_resource_id_sources", "operation": "append_unique", "value": "observer_diff_created_id"},
            {"path": "discovery.candidate_ranking_signals", "operation": "append_unique", "value": "cleanup_risk"},
        ]
    if signature in {"CLEANUP_EVIDENCE_MISSING", "CLEANUP_RECEIPT_MISSING"}:
        return [
            {"path": "execution.cleanup_created_resource_id_sources", "operation": "append_unique", "value": "response_body_nested_id"},
            {"path": "verification.evidence_collection_order", "operation": "append_unique", "value": "cleanup_receipt"},
        ]
    if signature in {"EVIDENCE_GATE_INCOMPLETE", "VALID_VIOLATION_NOT_PROMOTED", "REPLAY_EVIDENCE_MISSING"}:
        return [
            {"path": "verification.evidence_collection_order", "operation": "append_unique", "value": "runtime_trace_join"},
            {"path": "verification.evidence_collection_order", "operation": "append_unique", "value": "before_after"},
            {"path": "discovery.candidate_ranking_signals", "operation": "append_unique", "value": "evidence_completion_probability"},
        ]
    if signature == "TARGET_5XX_REQUIRES_CONTROL":
        return [
            {"path": "verification.evidence_collection_order", "operation": "append_unique", "value": "valid_success_control"},
        ]
    if signature in {"SELECTED_WITHOUT_EXECUTION_TRACE", "FORMAL_DEFECT_TRACE_MISSING"}:
        return [
            {"path": "execution.trace_join_key_order", "operation": "append_unique", "value": "campaign_round_slice_id"},
            {"path": "execution.trace_join_key_order", "operation": "append_unique", "value": "request_trace_id"},
        ]
    if signature == "CANDIDATE_NOT_SELECTED":
        return [
            {"path": "discovery.candidate_ranking_signals", "operation": "append_unique", "value": "weakness_recurrence"},
            {"path": "discovery.candidate_ranking_signals", "operation": "append_unique", "value": "cross_industry_recurrence"},
        ]
    if signature == "HYPOTHESIS_COVERAGE_GAP":
        return [
            {
                "path": "discovery.max_hypotheses_execute",
                "operation": "set_integer",
                "value": min(200, strategy.discovery.max_hypotheses_execute + 20),
            },
        ]
    # Critical promotion/verifier safety rules are frozen true and must be fixed
    # in code, not toggled by an autonomous proposal.
    return []


def _proposal(
    pattern: dict[str, Any],
    strategy: StrategyBundle,
    edit: dict[str, Any],
    *,
    weakness_report_fingerprint: str,
) -> dict[str, Any]:
    candidate = apply_bounded_harness_edit(strategy, edit)
    before = _strategy_value(strategy, str(edit["path"]))
    after = _strategy_value(candidate, str(edit["path"]))
    proposal_id = f"HPROP_{_signature([pattern.get('pattern_id'), edit, asdict(strategy)])}"
    return {
        "proposal_id": proposal_id,
        "source_pattern_id": str(pattern.get("pattern_id") or ""),
        "source_failure_signature": str(pattern.get("failure_signature") or ""),
        "harness_surface": str(pattern.get("harness_surface") or ""),
        "weakness_report_fingerprint": weakness_report_fingerprint,
        "proposal_origin": "bounded_template_proposer",
        "minimal_edit_count": 1,
        "edit": {**edit, "before": before, "after": after},
        "failure_mechanism": str(pattern.get("failure_mechanism") or ""),
        "evidence": {
            "observed_count": int(pattern.get("observed_count") or 0),
            "affected_run_count": int(pattern.get("affected_run_count") or 0),
            "affected_industry_count": int(pattern.get("affected_industry_count") or 0),
            "example_trace_ids": list(pattern.get("example_trace_ids") or []),
            "preserved_good_trace_ids": list(pattern.get("preserved_good_trace_ids") or []),
        },
        "expected_effect": {
            "primary": "reduce recurrence of the bound structured failure signature",
            "must_not_change": "formal evidence threshold, evaluator, safety boundary, or hidden dataset",
        },
        "regression_obligations": [
            "paired_champion_challenger_replay",
            "paired_champion_challenger_shadow",
            "held_in_non_regression",
            "held_out_non_regression",
            "clean_target_p0_p1_false_positive_zero",
            "cleanup_and_production_write_safety",
        ],
        "parent_strategy_signature": _signature(asdict(strategy)),
        "candidate_strategy_signature": _signature(asdict(candidate)),
        "candidate_strategy": asdict(candidate),
        "guardrails": validate_strategy_guardrails(candidate),
    }


def propose_harness_candidates(
    weakness_report: dict[str, Any],
    strategy: StrategyBundle,
    *,
    max_proposals: int = 12,
) -> dict[str, Any]:
    """Generate diverse, one-edit candidates from selected weakness patterns."""

    if not isinstance(weakness_report, dict) or weakness_report.get("schema_version") != WEAKNESS_REPORT_SCHEMA:
        raise HarnessProposalError("proposal generation requires a current weakness report")
    guardrails = validate_strategy_guardrails(strategy)
    if not guardrails["passed"]:
        raise HarnessProposalError("current StrategyBundle violates frozen guardrails")
    max_proposals = max(1, min(int(max_proposals or 1), 50))
    patterns = {
        str(item.get("pattern_id") or ""): item
        for item in (weakness_report.get("patterns") or [])
        if isinstance(item, dict) and str(item.get("pattern_id") or "")
    }
    selected_ids = [str(item) for item in (weakness_report.get("selected_patterns_for_proposal") or [])]
    weakness_report_fingerprint = _signature(
        {
            "source_run_ids": weakness_report.get("source_run_ids") or [],
            "patterns": weakness_report.get("patterns") or [],
        }
    )
    proposals: list[dict[str, Any]] = []
    candidate_signatures: set[str] = set()
    blocked_patterns: list[dict[str, Any]] = []
    for pattern_id in selected_ids:
        pattern = patterns.get(pattern_id)
        if pattern is None:
            raise HarnessProposalError(f"selected weakness pattern is missing: {pattern_id}")
        signature = str(pattern.get("failure_signature") or "")
        generated_for_pattern = 0
        no_effect_errors: list[str] = []
        for edit in _edit_templates(signature, strategy):
            if len(proposals) >= max_proposals:
                break
            try:
                candidate = _proposal(
                    pattern,
                    strategy,
                    edit,
                    weakness_report_fingerprint=weakness_report_fingerprint,
                )
                candidate_signature = str(candidate.get("candidate_strategy_signature") or "")
                if candidate_signature in candidate_signatures:
                    no_effect_errors.append("duplicate candidate strategy")
                    continue
                candidate_signatures.add(candidate_signature)
                proposals.append(candidate)
                generated_for_pattern += 1
            except HarnessProposalError as exc:
                if "has no effect" in str(exc):
                    no_effect_errors.append(str(exc))
                    continue
                raise
        if generated_for_pattern == 0:
            blocked_patterns.append(
                {
                    "pattern_id": pattern_id,
                    "failure_signature": signature,
                    "reason": "frozen_safety_surface_or_no_remaining_bounded_edit" if not no_effect_errors else "all_bounded_edits_already_active",
                }
            )
        if len(proposals) >= max_proposals:
            break
    return {
        "schema_version": HARNESS_PROPOSAL_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "weakness_report_fingerprint": weakness_report_fingerprint,
        "parent_strategy_signature": _signature(asdict(strategy)),
        "proposal_count": len(proposals),
        "blocked_pattern_count": len(blocked_patterns),
        "proposals": proposals,
        "blocked_patterns": blocked_patterns,
        "editable_surface_contract": {
            "allowed_paths": sorted(set(_ALLOWED_APPEND_VALUES) | set(_ALLOWED_INTEGER_RANGES)),
            "frozen_paths": sorted(_FROZEN_PATHS),
            "arbitrary_code_edits_allowed": False,
            "evaluator_edits_allowed": False,
            "ground_truth_access_allowed": False,
        },
    }


def materialize_policy_candidate(
    proposal: dict[str, Any],
    parent: PolicyRecord,
) -> PolicyRecord:
    """Convert a validated proposal into a non-active registry candidate."""

    if not isinstance(proposal, dict) or not proposal.get("proposal_id"):
        raise HarnessProposalError("policy materialization requires a proposal")
    if proposal.get("parent_strategy_signature") != _signature(asdict(parent.strategy)):
        raise HarnessProposalError("proposal parent strategy fingerprint is stale")
    edit = proposal.get("edit") if isinstance(proposal.get("edit"), dict) else {}
    canonical_edit = {key: edit[key] for key in ("path", "operation", "value") if key in edit}
    candidate_strategy = apply_bounded_harness_edit(parent.strategy, canonical_edit)
    if proposal.get("candidate_strategy_signature") != _signature(asdict(candidate_strategy)):
        raise HarnessProposalError("proposal candidate strategy fingerprint does not match bounded edit")
    suffix = _signature([proposal["proposal_id"], parent.policy_version])[:10]
    return PolicyRecord(
        policy_id=f"policy-candidate-{suffix}",
        policy_version=f"{parent.policy_version}+candidate.{suffix}",
        parent_policy_version=parent.policy_version,
        project_scope=parent.project_scope,
        status="candidate",
        created_reason=f"weakness_pattern:{proposal.get('source_pattern_id')}",
        strategy=candidate_strategy,
        evaluation_summary={
            "status": "awaiting_paired_replay_shadow",
            "proposal_id": proposal["proposal_id"],
            "source_pattern_id": proposal.get("source_pattern_id"),
            "source_failure_signature": proposal.get("source_failure_signature"),
            "regression_obligations": list(proposal.get("regression_obligations") or []),
        },
    )


def persist_harness_proposals(report: dict[str, Any], output_root: Path | str) -> Path:
    if report.get("schema_version") != HARNESS_PROPOSAL_SCHEMA:
        raise HarnessProposalError("cannot persist unsupported Harness proposal schema")
    parent = str(report.get("parent_strategy_signature") or "").strip()
    weakness = str(report.get("weakness_report_fingerprint") or "").strip()
    if not parent or not weakness:
        raise HarnessProposalError("Harness proposal report is missing immutable fingerprints")
    safe = re.compile(r"[^A-Za-z0-9_.-]+")
    path = Path(output_root) / f"{safe.sub('_', parent)}_{safe.sub('_', weakness)}.harness-proposals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != json.loads(payload):
            raise HarnessProposalError(f"immutable Harness proposal report already exists with different content: {path}")
        return path
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)
    return path
