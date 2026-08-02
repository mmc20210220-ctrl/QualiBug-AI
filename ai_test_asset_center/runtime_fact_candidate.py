"""Runtime Fact Candidates from governed observations (SPEC §7.8).

Candidates are low-authority, fingerprint-bound, and never silently become
ACCEPTED Business World Model facts. They feed Experimentability re-projection
and blocked/abstract obligation recompile on the existing expansion mainline.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .abstract_experiment import is_capability_gap_reason
from .enterprise_knowledge_center.enterprise_understanding.fact_experimentability_projection import (
    project_fact_experimentability,
)

CANDIDATE_SCHEMA = "qualibug.runtime-fact-candidate.v1"
LEDGER_SCHEMA = "qualibug.runtime-fact-candidate-ledger.v1"
FEEDBACK_RECEIPT_SCHEMA = "qualibug.runtime-feedback-receipt.v1"

_CANDIDATE_STATUSES = frozenset({
    "CANDIDATE",
    "CONFLICTED",
    "REJECTED",
    "NEEDS_AUTHORITY",
})
_CANDIDATE_KINDS = frozenset({
    "runtime_operation",
    "runtime_observation_path",
    "runtime_cleanup_capability",
    "runtime_state_fingerprint",
    "doc_impl_conflict",
})
_RECOMPILE_STATUSES = frozenset({"BLOCKED", "ABSTRACT"})
_BODY_BINDING_REASON = "BLOCKED_MISSING_BINDING"
_FEEDBACK_CAPABILITY_REASONS = frozenset({
    "BLOCKED_MISSING_BINDING",
    "BLOCKED_MISSING_OBSERVER",
    "BLOCKED_CONTROL_ARM_NOT_PROVEN",
    "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
    "BLOCKED_MISSING_CLEANUP",
    "BLOCKED_MISSING_OPERATION",
    "BLOCKED_MISSING_FIXTURE",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:24]


def _seal_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    status = _text(payload.get("status")).upper()
    if status not in _CANDIDATE_STATUSES:
        raise ValueError(f"runtime_fact_candidate_status_invalid:{status}")
    kind = _text(payload.get("kind")).lower()
    if kind not in _CANDIDATE_KINDS:
        raise ValueError(f"runtime_fact_candidate_kind_invalid:{kind}")
    body = {"schema_version": CANDIDATE_SCHEMA, **payload, "status": status, "kind": kind}
    return {
        **body,
        "candidate_id": "rfc_" + _fingerprint(body),
    }


def _from_observation_receipt(row: dict[str, Any]) -> dict[str, Any] | None:
    method = _text(row.get("method") or _dict(row.get("candidate")).get("method")).upper()
    path = _text(row.get("path") or _dict(row.get("candidate")).get("path"))
    status_code = row.get("status_code")
    if not method or not path.startswith("/"):
        return None
    conclusive = status_code in {200, 201, 204, 401, 403, 404, 405, 409, 422}
    if not conclusive and _text(row.get("status")).upper() not in {
        "OBSERVED",
        "CONFIRMED",
        "PROVEN",
    }:
        return None
    return _seal_candidate(
        {
            "status": "CANDIDATE",
            "kind": "runtime_operation",
            "authority_grade": "RUNTIME_OBSERVED",
            "method": method,
            "path_fingerprint": _fingerprint(path),
            "path": path,
            "evidence_refs": [
                _text(row.get("receipt_id") or row.get("request_receipt_id"))
            ],
            "observation_fingerprint": _text(
                row.get("response_fingerprint") or row.get("receipt_fingerprint")
            ),
            "source_refs": [
                {
                    "source_id": "runtime_observation",
                    "locator": f"{method} {path}",
                    "kind": "runtime_observation",
                }
            ],
            "conflict_refs": [],
            "notes": "Low-authority runtime operation candidate; not an ACCEPTED fact.",
        }
    )


def _from_execution_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for obs in _list(result.get("observer_receipts")):
        row = _dict(obs)
        evidence = _dict(row.get("evidence"))
        path = _text(
            evidence.get("observation_path")
            or row.get("observation_path")
            or evidence.get("path")
        )
        if path.startswith("/") and _text(row.get("status")).upper() == "OBSERVED":
            out.append(
                _seal_candidate(
                    {
                        "status": "CANDIDATE",
                        "kind": "runtime_observation_path",
                        "authority_grade": "RUNTIME_OBSERVED",
                        "method": "GET",
                        "path": path,
                        "path_fingerprint": _fingerprint(path),
                        "observer_id": _text(row.get("observer_id")),
                        "evidence_refs": [_text(row.get("receipt_id"))],
                        "observation_fingerprint": _text(
                            evidence.get("after_fingerprint")
                            or evidence.get("entity_identity_fingerprint")
                        ),
                        "source_refs": [
                            {
                                "source_id": "runtime_observer",
                                "locator": path,
                                "kind": "runtime_observation",
                            }
                        ],
                        "conflict_refs": [],
                        "notes": "Independent readback surface observed at runtime.",
                    }
                )
            )
    graph = _dict(result.get("effect_observation_graph"))
    for node in _list(graph.get("nodes")):
        row = _dict(node)
        if _text(row.get("kind")) != "readback":
            continue
        path = _text(row.get("observation_path"))
        if not path.startswith("/"):
            continue
        out.append(
            _seal_candidate(
                {
                    "status": "CANDIDATE",
                    "kind": "runtime_observation_path",
                    "authority_grade": "RUNTIME_OBSERVED",
                    "method": "GET",
                    "path": path,
                    "path_fingerprint": _fingerprint(path),
                    "evidence_refs": [_text(graph.get("receipt_id"))],
                    "source_refs": [
                        {
                            "source_id": "effect_observation_graph",
                            "locator": path,
                            "kind": "runtime_observation",
                        }
                    ],
                    "conflict_refs": [],
                    "notes": "Declared independent readback from effect observation graph.",
                }
            )
        )
    for raw in _list(result.get("contract_evidence_receipts")):
        row = _dict(raw)
        if _text(row.get("kind")).lower() != "cleanup":
            continue
        if _text(row.get("status")).upper() not in {"COMPLETED", "OBSERVED", "ACTIVE"}:
            continue
        evidence = _dict(row.get("evidence"))
        method = _text(evidence.get("method")).upper() or "DELETE"
        path = _text(evidence.get("path"))
        if not path.startswith("/"):
            continue
        out.append(
            _seal_candidate(
                {
                    "status": "CANDIDATE",
                    "kind": "runtime_cleanup_capability",
                    "authority_grade": "RUNTIME_OBSERVED",
                    "method": method,
                    "path": path,
                    "path_fingerprint": _fingerprint(path),
                    "evidence_refs": [_text(row.get("receipt_id"))],
                    "source_refs": [
                        {
                            "source_id": "runtime_cleanup",
                            "locator": f"{method} {path}",
                            "kind": "runtime_observation",
                        }
                    ],
                    "conflict_refs": [],
                    "notes": "Cleanup capability observed; not a permanent business rule.",
                }
            )
        )
    return out


def project_runtime_fact_candidates(
    *,
    observation_receipts: list[dict[str, Any]] | None = None,
    execution_results: dict[str, Any] | list[Any] | None = None,
    campaign_id: str = "",
) -> dict[str, Any]:
    """Build a fingerprint-only Runtime Fact Candidate ledger."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _list(observation_receipts):
        sealed = _from_observation_receipt(_dict(raw))
        if not sealed:
            continue
        key = sealed["candidate_id"]
        if key in seen:
            continue
        seen.add(key)
        candidates.append(sealed)

    results: list[dict[str, Any]]
    if isinstance(execution_results, dict):
        results = [row for row in execution_results.values() if isinstance(row, dict)]
    else:
        results = [row for row in _list(execution_results) if isinstance(row, dict)]
    for result in results:
        for sealed in _from_execution_result(result):
            key = sealed["candidate_id"]
            if key in seen:
                continue
            seen.add(key)
            candidates.append(sealed)

    body = {
        "schema_version": LEDGER_SCHEMA,
        "campaign_id": _text(campaign_id),
        "candidate_count": len(candidates),
        "status_counts": {
            status: sum(1 for row in candidates if row.get("status") == status)
            for status in sorted(_CANDIDATE_STATUSES)
        },
        "candidates": candidates,
    }
    return {
        **body,
        "ledger_id": "rfcl_" + _fingerprint(body),
    }


def grade_runtime_fact_candidates(
    knowledge_asset: dict[str, Any],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Identity-align and conflict-check candidates without promoting authority."""
    asset = _dict(knowledge_asset)
    model = _dict(asset.get("enterprise_understanding_model"))
    accepted_ops: set[tuple[str, str]] = set()
    for row in _list(model.get("operations")) + _list(
        _dict(asset.get("behavior_ir")).get("operations")
    ):
        op = _dict(row)
        method = _text(op.get("method")).upper()
        path = _text(op.get("path"))
        if method and path:
            accepted_ops.add((method, path))

    conflicted_fact_ids = {
        _text(row.get("fact_id") or row.get("id"))
        for row in _list(model.get("conflicted_facts"))
        if isinstance(row, dict)
    }
    graded: list[dict[str, Any]] = []
    for raw in _list(ledger.get("candidates")):
        row = dict(_dict(raw))
        method = _text(row.get("method")).upper()
        path = _text(row.get("path"))
        evidence_refs = [_text(value) for value in _list(row.get("evidence_refs")) if _text(value)]
        if not evidence_refs:
            row["status"] = "REJECTED"
            row["reject_reason"] = "MISSING_EVIDENCE_REF"
        elif not path.startswith("/"):
            row["status"] = "REJECTED"
            row["reject_reason"] = "PATH_INVALID"
        elif method and (method, path) in accepted_ops:
            # Already source-backed — keep as candidate for feedback but mark
            # that authority confirmation is still required for fact elevation.
            row["status"] = "NEEDS_AUTHORITY"
            row["notes"] = (
                "Overlaps a documented operation; runtime observation cannot "
                "silently elevate or rewrite ACCEPTED facts."
            )
        elif conflicted_fact_ids and _text(row.get("kind")) == "doc_impl_conflict":
            row["status"] = "CONFLICTED"
            row["conflict_refs"] = sorted(conflicted_fact_ids)[:12]
        else:
            row["status"] = "CANDIDATE"
            row["authority_grade"] = "RUNTIME_OBSERVED"
        # Re-seal id after status changes.
        payload = {key: value for key, value in row.items() if key != "candidate_id"}
        graded.append(_seal_candidate(payload))

    body = {
        "schema_version": LEDGER_SCHEMA,
        "campaign_id": _text(ledger.get("campaign_id")),
        "candidate_count": len(graded),
        "status_counts": {
            status: sum(1 for row in graded if row.get("status") == status)
            for status in sorted(_CANDIDATE_STATUSES)
        },
        "candidates": graded,
        "graded": True,
        "high_authority_promotions": 0,
    }
    return {
        **body,
        "ledger_id": "rfcl_" + _fingerprint(body),
    }


def candidates_as_observation_receipts(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """Project CANDIDATE runtime operations into expansion observation shape."""
    rows: list[dict[str, Any]] = []
    for raw in _list(ledger.get("candidates")):
        row = _dict(raw)
        if _text(row.get("status")) != "CANDIDATE":
            continue
        if _text(row.get("kind")) not in {
            "runtime_operation",
            "runtime_observation_path",
            "runtime_cleanup_capability",
        }:
            continue
        method = _text(row.get("method")).upper()
        path = _text(row.get("path"))
        if not method or not path.startswith("/"):
            continue
        rows.append(
            {
                "receipt_id": _text(row.get("candidate_id")),
                "method": method,
                "path": path,
                "status_code": 200,
                "status": "OBSERVED",
                "response_fingerprint": _text(row.get("observation_fingerprint"))
                or ("0" * 64),
                "source_refs": _list(row.get("source_refs")),
                "authority_grade": "RUNTIME_OBSERVED",
                "runtime_fact_candidate": True,
            }
        )
    return rows


def related_blocked_obligation_ids(
    *,
    obligations: list[dict[str, Any]] | Any,
    experiments_by_obligation: dict[str, Any] | Any,
    ledger: dict[str, Any] | None = None,
) -> set[str]:
    """Select BLOCKED/ABSTRACT obligations reopenable by runtime feedback."""
    experiments = _dict(experiments_by_obligation)
    has_runtime_ops = any(
        _text(row.get("status")) == "CANDIDATE"
        and _text(row.get("kind"))
        in {
            "runtime_operation",
            "runtime_observation_path",
            "runtime_cleanup_capability",
        }
        for row in _list(_dict(ledger).get("candidates"))
    )
    retry_ids: set[str] = set()
    for obligation in _list(obligations):
        if not isinstance(obligation, dict):
            continue
        obligation_id = _text(obligation.get("obligation_id"))
        if not obligation_id:
            continue
        experiment = _dict(experiments.get(obligation_id))
        compile_receipt = _dict(experiment.get("compile_receipt"))
        status = _text(compile_receipt.get("status")).upper()
        if status not in _RECOMPILE_STATUSES:
            continue
        reason = _text(compile_receipt.get("reason_code"))
        detail = _text(
            compile_receipt.get("detail") or compile_receipt.get("reason_detail")
        )
        if reason == _BODY_BINDING_REASON and "BODY_PARAMETER_NOT_SOURCE_BOUND" in detail:
            retry_ids.add(obligation_id)
            continue
        if has_runtime_ops and (
            reason in _FEEDBACK_CAPABILITY_REASONS or is_capability_gap_reason(reason)
        ):
            retry_ids.add(obligation_id)
    return retry_ids


def reproject_experimentability_with_candidates(
    knowledge_asset: dict[str, Any],
    ledger: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-run experimentability projection; never promote candidates to ACCEPTED."""
    asset = deepcopy(_dict(knowledge_asset))
    graded = grade_runtime_fact_candidates(asset, ledger)
    asset["runtime_fact_candidate_ledger"] = graded
    model = _dict(asset.get("enterprise_understanding_model"))
    # Re-projection uses the same function as Phase 1. Candidates remain
    # reference-only on the asset and do not mutate ACCEPTED facts.
    if model:
        updated = project_fact_experimentability(asset, model)
        asset["fact_experimentability_ledger"] = updated
    else:
        updated = _dict(asset.get("fact_experimentability_ledger"))
    return asset, graded if not model else {
        **graded,
        "experimentability_reprojected": True,
        "experimentability_receipt_count": int(
            _dict(updated).get("receipt_count")
            or len(_list(_dict(updated).get("receipts")))
        ),
    }


def build_runtime_feedback_receipt(
    *,
    candidate_ledger: dict[str, Any],
    recompile_obligation_ids: list[str] | set[str],
    expansion_status: str,
    planning_round: int,
    campaign_id: str = "",
) -> dict[str, Any]:
    body = {
        "schema_version": FEEDBACK_RECEIPT_SCHEMA,
        "campaign_id": _text(campaign_id),
        "candidate_ledger_id": _text(candidate_ledger.get("ledger_id")),
        "candidate_count": int(candidate_ledger.get("candidate_count") or 0),
        "recompile_obligation_ids": sorted(
            {_text(value) for value in recompile_obligation_ids if _text(value)}
        ),
        "expansion_status": _text(expansion_status).upper() or "NOT_REQUESTED",
        "planning_round": int(planning_round),
        "high_authority_promotions": 0,
    }
    return {
        **body,
        "receipt_id": "rfr_" + _fingerprint(body),
    }


__all__ = [
    "CANDIDATE_SCHEMA",
    "LEDGER_SCHEMA",
    "FEEDBACK_RECEIPT_SCHEMA",
    "project_runtime_fact_candidates",
    "grade_runtime_fact_candidates",
    "candidates_as_observation_receipts",
    "related_blocked_obligation_ids",
    "reproject_experimentability_with_candidates",
    "build_runtime_feedback_receipt",
]
