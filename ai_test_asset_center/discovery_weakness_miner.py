from __future__ import annotations

"""Verifier-grounded weakness clustering over redacted discovery traces."""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .discovery_trace_ledger import TRACE_LEDGER_SCHEMA


WEAKNESS_REPORT_SCHEMA = "qualibug.discovery-weakness-report.v1"


class WeaknessMiningError(ValueError):
    """Trace evidence is missing or inconsistent."""


_SIGNATURE_CATALOG: dict[str, dict[str, str]] = {
    "ENDPOINT_BINDING_MISSING": {
        "surface": "endpoint_binding",
        "mechanism": "candidate has no documented executable endpoint binding",
        "severity": "high",
    },
    "ENDPOINT_BINDING_DROPPED_AGGREGATE": {
        "surface": "endpoint_binding",
        "mechanism": "generated candidates are dropped before behavior-slice materialization",
        "severity": "high",
    },
    "SOURCE_GROUNDING_MISSING": {
        "surface": "candidate_compilation",
        "mechanism": "candidate lineage lacks source evidence",
        "severity": "high",
    },
    "CANDIDATE_NOT_SELECTED": {
        "surface": "candidate_ranking",
        "mechanism": "candidate is repeatedly excluded by the execution budget",
        "severity": "medium",
    },
    "RUNTIME_PATH_BINDING_MISSING": {
        "surface": "runtime_binding",
        "mechanism": "documented path placeholders cannot be resolved from observed target state",
        "severity": "high",
    },
    "SELECTED_WITHOUT_EXECUTION_TRACE": {
        "surface": "scenario_execution",
        "mechanism": "selected candidate produces no scenario execution trace",
        "severity": "high",
    },
    "NO_HTTP_EXECUTION": {
        "surface": "scenario_execution",
        "mechanism": "scenario steps do not produce an observable HTTP request",
        "severity": "high",
    },
    "ZERO_STATUS_NON_EXECUTION": {
        "surface": "scenario_execution",
        "mechanism": "skipped or unbound steps are represented as status zero",
        "severity": "high",
    },
    "EXPERIMENT_COMPILE_BLOCKED": {
        "surface": "experiment_compiler",
        "mechanism": "obligation failed experiment compilation with a stable block reason",
        "severity": "high",
    },
    "OBLIGATION_BINDING_MISSING": {
        "surface": "runtime_binding",
        "mechanism": "compiled experiment still has unresolved path/body bindings",
        "severity": "high",
    },
    "CONTRACT_ORACLE_ACTIVATION_MISSING": {
        "surface": "contract_oracle",
        "mechanism": "business oracle lacked required control/treatment/observer evidence",
        "severity": "high",
    },
    "HEURISTIC_ORACLE_DEMOTED": {
        "surface": "contract_oracle",
        "mechanism": "heuristic business oracle demoted to internal clue without contract evidence",
        "severity": "medium",
    },
    "EXECUTION_ERROR": {
        "surface": "failure_recovery",
        "mechanism": "scenario execution terminates with structured execution errors",
        "severity": "medium",
    },
    "PRECONDITION_NOT_MET": {
        "surface": "test_data_and_preconditions",
        "mechanism": "candidate cannot establish its source-grounded runtime precondition",
        "severity": "high",
    },
    "TARGET_5XX_REQUIRES_CONTROL": {
        "surface": "verification_controls",
        "mechanism": "target 5xx response lacks enough valid-input control evidence for Bug attribution",
        "severity": "high",
    },
    "SANDBOX_WRITE_INCOMPLETE": {
        "surface": "sandbox_write_policy",
        "mechanism": "governed write lifecycle does not reach a clean terminal state",
        "severity": "critical",
    },
    "MULTI_WRITE_AUDIT_INCOMPLETE": {
        "surface": "sandbox_write_policy",
        "mechanism": "one scenario executes more write steps than governed write audit receipts",
        "severity": "critical",
    },
    "CLEANUP_FAILED": {
        "surface": "sandbox_write_policy",
        "mechanism": "non-production write probe cleanup fails",
        "severity": "critical",
    },
    "CLEANUP_NOT_REVERSIBLE": {
        "surface": "sandbox_write_policy",
        "mechanism": "non-production write probe has no reversible cleanup path",
        "severity": "critical",
    },
    "CLEANUP_RECEIPT_MISSING": {
        "surface": "sandbox_write_policy",
        "mechanism": "write cleanup has no immutable audit receipt",
        "severity": "critical",
    },
    "CLEANUP_EVIDENCE_MISSING": {
        "surface": "sandbox_write_policy",
        "mechanism": "write-shaped customer finding omits its cleanup contract",
        "severity": "critical",
    },
    "ORACLE_CONFIRMED_NON_EXECUTION": {
        "surface": "verifier_orchestration",
        "mechanism": "oracle failure vote is produced from a skipped or non-executed step",
        "severity": "critical",
    },
    "FORMAL_DEFECT_FROM_NON_EXECUTION": {
        "surface": "formal_promotion_gate",
        "mechanism": "non-execution is promoted to a customer-deliverable defect",
        "severity": "critical",
    },
    "FORMAL_DEFECT_TRACE_MISSING": {
        "surface": "trace_observability",
        "mechanism": "formal defect cannot be joined to its scenario execution trace",
        "severity": "high",
    },
    "FORMAL_DEFECT_WITH_CLEANUP_FAILURE": {
        "surface": "formal_promotion_gate",
        "mechanism": "finding is promoted despite failed governed-write cleanup",
        "severity": "critical",
    },
    "EVIDENCE_GATE_INCOMPLETE": {
        "surface": "evidence_collection",
        "mechanism": "valid runtime signal lacks the complete business evidence contract",
        "severity": "medium",
    },
    "REPLAY_EVIDENCE_MISSING": {
        "surface": "evidence_collection",
        "mechanism": "finding lacks a real replay asset or hard runtime evidence",
        "severity": "high",
    },
    "VALID_VIOLATION_NOT_PROMOTED": {
        "surface": "evidence_collection",
        "mechanism": "valid executed oracle violation is held back before formal accounting",
        "severity": "high",
    },
}

_SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_SINGLE_OBSERVATION_HARD_FAILURES = {
    "CLEANUP_FAILED",
    "CLEANUP_RECEIPT_MISSING",
    "ORACLE_CONFIRMED_NON_EXECUTION",
    "FORMAL_DEFECT_FROM_NON_EXECUTION",
    "FORMAL_DEFECT_WITH_CLEANUP_FAILURE",
}


def _stable_id(*parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts)
    return f"WEAK_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _validate_ledgers(ledgers: list[dict[str, Any]]) -> None:
    if not ledgers:
        raise WeaknessMiningError("weakness mining requires at least one trace ledger")
    for ledger in ledgers:
        if not isinstance(ledger, dict) or ledger.get("schema_version") != TRACE_LEDGER_SCHEMA:
            raise WeaknessMiningError("weakness mining received an unsupported trace ledger")
        redaction = ledger.get("redaction_contract") or {}
        if any(
            redaction.get(key) is not False
            for key in (
                "raw_request_bodies_persisted",
                "raw_response_bodies_persisted",
                "credentials_persisted",
                "ground_truth_persisted",
            )
        ):
            raise WeaknessMiningError("trace ledger does not prove the required redaction contract")


def _good_trace_ids_by_surface(traces: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for trace in traces:
        if trace.get("outcome") not in {"customer_deliverable_defect", "valid_success_control"}:
            continue
        failed_surfaces = {
            _SIGNATURE_CATALOG[signature]["surface"]
            for signature in trace.get("failure_signatures") or []
            if signature in _SIGNATURE_CATALOG
        }
        for surface in {item["surface"] for item in _SIGNATURE_CATALOG.values()} - failed_surfaces:
            result.setdefault(surface, []).append(str(trace.get("trace_id") or ""))
    return result


def _pattern(
    signature: str,
    rows: list[dict[str, Any]],
    *,
    total_trace_count: int,
    preserved_good: dict[str, list[str]],
    aggregate_count: int = 0,
) -> dict[str, Any]:
    catalog = _SIGNATURE_CATALOG.get(signature)
    if catalog is None:
        catalog = {
            "surface": "unclassified_harness_surface",
            "mechanism": "structured failure signature is not yet mapped to an editable Harness surface",
            "severity": "medium",
        }
    observed_count = len(rows) + max(0, int(aggregate_count))
    run_ids = sorted({str(item.get("run_id") or "") for item in rows if str(item.get("run_id") or "")})
    industries = sorted({str(item.get("industry") or "") for item in rows if str(item.get("industry") or "")})
    families = sorted(
        {
            str((item.get("generation") or {}).get("family") or "unclassified")
            for item in rows
        }
    )
    outcomes: dict[str, int] = {}
    for item in rows:
        outcome = str(item.get("outcome") or "unresolved")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    severity = catalog["severity"]
    recurrence_rate = round(observed_count / total_trace_count, 4) if total_trace_count else 0.0
    proposal_eligible = (
        signature in _SINGLE_OBSERVATION_HARD_FAILURES
        or observed_count >= 2
    )
    priority_score = (
        _SEVERITY_WEIGHT.get(severity, 1) * 100
        + min(observed_count, 99)
        + min(len(run_ids), 9) * 10
        + min(len(industries), 9) * 5
    )
    return {
        "pattern_id": _stable_id(signature, catalog["surface"]),
        "failure_signature": signature,
        "harness_surface": catalog["surface"],
        "failure_mechanism": catalog["mechanism"],
        "severity": severity,
        "priority_score": priority_score,
        "observed_count": observed_count,
        "trace_backed_count": len(rows),
        "aggregate_only_count": max(0, int(aggregate_count)),
        "recurrence_rate": recurrence_rate,
        "affected_run_count": len(run_ids),
        "affected_industry_count": len(industries),
        "affected_families": families,
        "outcome_counts": dict(sorted(outcomes.items())),
        "example_trace_ids": [str(item.get("trace_id") or "") for item in rows[:5]],
        "preserved_good_trace_ids": preserved_good.get(catalog["surface"], [])[:5],
        "verifier_grounded": bool(rows),
        "proposal_eligible": proposal_eligible,
        "proposal_block_reason": "" if proposal_eligible else "insufficient_recurrence",
        "privacy": {
            "contains_raw_requests": False,
            "contains_raw_responses": False,
            "contains_ground_truth": False,
            "contains_customer_titles_or_paths": False,
        },
    }


def mine_discovery_weaknesses(ledgers: list[dict[str, Any]]) -> dict[str, Any]:
    """Cluster recurring structured failure signatures across runs/industries."""

    _validate_ledgers(ledgers)
    traces = [
        item
        for ledger in ledgers
        for item in (ledger.get("traces") or [])
        if isinstance(item, dict)
    ]
    if not traces:
        raise WeaknessMiningError("trace ledgers contain no candidate traces")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trace in traces:
        for signature in trace.get("failure_signatures") or []:
            grouped.setdefault(str(signature), []).append(trace)

    aggregate_counts: dict[str, int] = {}
    dropped_no_endpoint = sum(
        int((ledger.get("aggregate_stage_events") or {}).get("dropped_no_endpoint") or 0)
        for ledger in ledgers
    )
    if dropped_no_endpoint:
        aggregate_counts["ENDPOINT_BINDING_DROPPED_AGGREGATE"] = dropped_no_endpoint

    preserved_good = _good_trace_ids_by_surface(traces)
    signatures = sorted(set(grouped) | set(aggregate_counts))
    patterns = [
        _pattern(
            signature,
            grouped.get(signature, []),
            total_trace_count=len(traces),
            preserved_good=preserved_good,
            aggregate_count=aggregate_counts.get(signature, 0),
        )
        for signature in signatures
    ]
    patterns.sort(key=lambda item: (-int(item["priority_score"]), -int(item["observed_count"]), item["failure_signature"]))
    eligible = [item for item in patterns if item["proposal_eligible"]]
    return {
        "schema_version": WEAKNESS_REPORT_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_run_ids": sorted({str(item.get("run_id") or "") for item in ledgers}),
        "source_policy_ids": sorted({str(item.get("policy_id") or "") for item in ledgers}),
        "source_industries": sorted({str(item.get("industry") or "") for item in ledgers}),
        "trace_count": len(traces),
        "pattern_count": len(patterns),
        "proposal_eligible_pattern_count": len(eligible),
        "patterns": patterns,
        "selected_patterns_for_proposal": [item["pattern_id"] for item in eligible[:12]],
        "selection_rule": "severity_then_recurrence_without_ground_truth_or_customer_content",
        "privacy_contract": {
            "raw_requests_used": False,
            "raw_responses_used": False,
            "ground_truth_used": False,
            "customer_titles_or_paths_emitted": False,
        },
    }


def persist_weakness_report(report: dict[str, Any], output_root: Path | str) -> Path:
    if report.get("schema_version") != WEAKNESS_REPORT_SCHEMA:
        raise WeaknessMiningError("cannot persist unsupported weakness report schema")
    policy_ids = "_".join(str(item) for item in report.get("source_policy_ids") or []) or "unknown-policy"
    safe = re.compile(r"[^A-Za-z0-9_.-]+")
    name = safe.sub("_", policy_ids)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "source_run_ids": report.get("source_run_ids") or [],
                "source_policy_ids": report.get("source_policy_ids") or [],
                "patterns": report.get("patterns") or [],
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:16]
    path = Path(output_root) / f"{name}_{fingerprint}.weakness-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != json.loads(payload):
            raise WeaknessMiningError(f"immutable weakness report already exists with different content: {path}")
        return path
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)
    return path
