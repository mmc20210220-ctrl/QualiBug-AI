from __future__ import annotations

"""Private, versioned evaluation contract for discovery-harness evolution.

The discovery runtime receives only ``runtime_view`` data. Ground truth stays in
the evaluator-only part of the manifest and is opened only after a completed
scan. This module deliberately refuses to turn missing ground truth, missing
pipeline health, or incomplete target coverage into zero-valued quality claims.
"""

import hashlib
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .benchmark_compute import compute_benchmark, compute_stage_loss_matrix
from .customer_delivery_gate import is_customer_deliverable_defect


MANIFEST_SCHEMA = "qualibug.discovery-evaluation-dataset.v1"
RECEIPT_SCHEMA = "qualibug.discovery-evaluation-receipt.v1"
REPORT_SCHEMA = "qualibug.discovery-evaluation-report.v1"

NON_PRODUCTION_ENVIRONMENTS = {
    "local",
    "development",
    "dev",
    "test",
    "testing",
    "qa",
    "sit",
    "uat",
    "staging",
    "pre-release",
    "prerelease",
    "sandbox",
}
VALID_SPLITS = {"held_in", "held_out"}
VALID_EXPECTATIONS = {"seeded_defects", "clean"}
VALID_EVALUATION_MODES = {"replay", "shadow"}


class EvaluationContractError(ValueError):
    """The evaluation contract is invalid and must not be used."""


@dataclass(frozen=True)
class EvaluationTarget:
    target_id: str
    project_id: str
    industry: str
    split: str
    expectation: str
    environment_ref: str
    environment_type: str
    input_bundle_ref: str
    fixture_snapshot_ref: str
    context_artifact_ref: str
    ground_truth_ref: str = ""


@dataclass(frozen=True)
class EvaluationManifest:
    dataset_id: str
    dataset_version: str
    targets: tuple[EvaluationTarget, ...]
    manifest_path: Path
    manifest_fingerprint: str
    target_fingerprints: dict[str, dict[str, str]]

    def target(self, target_id: str) -> EvaluationTarget:
        for item in self.targets:
            if item.target_id == target_id:
                return item
        raise EvaluationContractError(f"evaluation target not found: {target_id}")


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EvaluationContractError(f"missing required evaluation field: {field_name}")
    return text


def _as_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationContractError(f"{field_name} must be an object")
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _resolve_ref(ref: str, manifest_path: Path) -> Path:
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return candidate.resolve()


def _artifact_fingerprint(ref: str, manifest_path: Path, field_name: str) -> str:
    path = _resolve_ref(ref, manifest_path)
    if not path.is_file():
        raise EvaluationContractError(f"{field_name} does not exist or is not a file: {path}")
    return _sha256_bytes(path.read_bytes())


def _parse_target(raw: Any, index: int) -> EvaluationTarget:
    item = _as_dict(raw, f"targets[{index}]")
    runtime = _as_dict(item.get("runtime"), f"targets[{index}].runtime")
    evaluator = item.get("evaluator") or {}
    evaluator = _as_dict(evaluator, f"targets[{index}].evaluator")

    split = _required_text(item.get("split"), f"targets[{index}].split").lower()
    if split not in VALID_SPLITS:
        raise EvaluationContractError(f"targets[{index}].split must be one of {sorted(VALID_SPLITS)}")
    expectation = _required_text(item.get("expectation"), f"targets[{index}].expectation").lower()
    if expectation not in VALID_EXPECTATIONS:
        raise EvaluationContractError(
            f"targets[{index}].expectation must be one of {sorted(VALID_EXPECTATIONS)}"
        )
    environment_type = _required_text(
        runtime.get("environment_type"), f"targets[{index}].runtime.environment_type"
    ).lower()
    if environment_type not in NON_PRODUCTION_ENVIRONMENTS:
        raise EvaluationContractError(
            f"evaluation target must be explicitly non-production; got {environment_type!r}"
        )

    ground_truth_ref = str(evaluator.get("ground_truth_ref") or "").strip()
    if expectation == "seeded_defects" and not ground_truth_ref:
        raise EvaluationContractError(
            f"targets[{index}].evaluator.ground_truth_ref is required for seeded_defects"
        )
    if expectation == "clean" and ground_truth_ref:
        raise EvaluationContractError(
            f"targets[{index}] is clean and must not declare a ground_truth_ref"
        )

    return EvaluationTarget(
        target_id=_required_text(item.get("target_id"), f"targets[{index}].target_id"),
        project_id=_required_text(item.get("project_id"), f"targets[{index}].project_id"),
        industry=_required_text(item.get("industry"), f"targets[{index}].industry"),
        split=split,
        expectation=expectation,
        environment_ref=_required_text(
            runtime.get("environment_ref"), f"targets[{index}].runtime.environment_ref"
        ),
        environment_type=environment_type,
        input_bundle_ref=_required_text(
            runtime.get("input_bundle_ref"), f"targets[{index}].runtime.input_bundle_ref"
        ),
        fixture_snapshot_ref=_required_text(
            runtime.get("fixture_snapshot_ref"), f"targets[{index}].runtime.fixture_snapshot_ref"
        ),
        context_artifact_ref=_required_text(
            runtime.get("context_artifact_ref"), f"targets[{index}].runtime.context_artifact_ref"
        ),
        ground_truth_ref=ground_truth_ref,
    )


def load_evaluation_manifest(path: Path | str) -> EvaluationManifest:
    """Load and freeze an evaluator-private dataset manifest.

    Every referenced input, fixture, context artifact, and ground-truth file must
    exist. A typo therefore fails before any expensive champion/challenger run.
    """

    manifest_path = Path(path).resolve()
    if not manifest_path.is_file():
        raise EvaluationContractError(f"evaluation manifest not found: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(f"invalid evaluation manifest {manifest_path}: {exc}") from exc
    raw = _as_dict(raw, "manifest")
    if raw.get("schema_version") != MANIFEST_SCHEMA:
        raise EvaluationContractError(
            f"unsupported evaluation schema: {raw.get('schema_version')!r}; expected {MANIFEST_SCHEMA!r}"
        )

    raw_targets = raw.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise EvaluationContractError("evaluation manifest must contain at least one target")
    targets = tuple(_parse_target(item, index) for index, item in enumerate(raw_targets))
    target_ids = [item.target_id for item in targets]
    if len(target_ids) != len(set(target_ids)):
        raise EvaluationContractError("evaluation target_id values must be unique")

    target_fingerprints: dict[str, dict[str, str]] = {}
    private_fingerprint_material: dict[str, Any] = {}
    for target in targets:
        input_fingerprint = _artifact_fingerprint(
            target.input_bundle_ref, manifest_path, f"{target.target_id}.input_bundle_ref"
        )
        fixture_fingerprint = _artifact_fingerprint(
            target.fixture_snapshot_ref, manifest_path, f"{target.target_id}.fixture_snapshot_ref"
        )
        context_fingerprint = _artifact_fingerprint(
            target.context_artifact_ref, manifest_path, f"{target.target_id}.context_artifact_ref"
        )
        runtime_fingerprint = _sha256_bytes(
            _canonical_json(
                {
                    "target_id": target.target_id,
                    "project_id": target.project_id,
                    "industry": target.industry,
                    "split": target.split,
                    "expectation": target.expectation,
                    "environment_ref": target.environment_ref,
                    "environment_type": target.environment_type,
                    "input_fingerprint": input_fingerprint,
                    "fixture_fingerprint": fixture_fingerprint,
                    "context_fingerprint": context_fingerprint,
                }
            )
        )
        fingerprints = {
            "input_fingerprint": input_fingerprint,
            "fixture_fingerprint": fixture_fingerprint,
            "context_fingerprint": context_fingerprint,
            "runtime_fingerprint": runtime_fingerprint,
        }
        if target.ground_truth_ref:
            fingerprints["ground_truth_fingerprint"] = _artifact_fingerprint(
                target.ground_truth_ref, manifest_path, f"{target.target_id}.ground_truth_ref"
            )
        target_fingerprints[target.target_id] = fingerprints
        private_fingerprint_material[target.target_id] = fingerprints

    dataset_id = _required_text(raw.get("dataset_id"), "dataset_id")
    dataset_version = _required_text(raw.get("dataset_version"), "dataset_version")
    manifest_fingerprint = _sha256_bytes(
        _canonical_json(
            {
                "schema_version": MANIFEST_SCHEMA,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "targets": [asdict(item) for item in targets],
                "artifact_fingerprints": private_fingerprint_material,
            }
        )
    )
    return EvaluationManifest(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        targets=targets,
        manifest_path=manifest_path,
        manifest_fingerprint=manifest_fingerprint,
        target_fingerprints=target_fingerprints,
    )


def build_runtime_view(manifest: EvaluationManifest, target_id: str) -> dict[str, Any]:
    """Return the discovery-safe target view with evaluator data removed."""

    target = manifest.target(target_id)
    fingerprints = manifest.target_fingerprints[target_id]
    return {
        "schema_version": MANIFEST_SCHEMA,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "target": {
            "target_id": target.target_id,
            "project_id": target.project_id,
            "industry": target.industry,
            "runtime": {
                "environment_ref": target.environment_ref,
                "environment_type": target.environment_type,
                "input_bundle_ref": target.input_bundle_ref,
                "fixture_snapshot_ref": target.fixture_snapshot_ref,
                "context_artifact_ref": target.context_artifact_ref,
            },
            "runtime_fingerprint": fingerprints["runtime_fingerprint"],
        },
    }


def assess_commercial_dataset_shape(manifest: EvaluationManifest) -> dict[str, Any]:
    """Check dataset diversity independently from run completeness."""

    held_in_seeded = [
        item for item in manifest.targets if item.split == "held_in" and item.expectation == "seeded_defects"
    ]
    held_out_seeded = [
        item for item in manifest.targets if item.split == "held_out" and item.expectation == "seeded_defects"
    ]
    clean_targets = [item for item in manifest.targets if item.expectation == "clean"]
    held_out_industries = sorted({item.industry for item in held_out_seeded})
    checks = [
        {
            "name": "held_in_seeded_target",
            "passed": bool(held_in_seeded),
            "detail": "at least one held-in target with hidden seeded defects is required",
        },
        {
            "name": "held_out_seeded_target",
            "passed": bool(held_out_seeded),
            "detail": "at least one held-out target with hidden seeded defects is required",
        },
        {
            "name": "clean_target",
            "passed": bool(clean_targets),
            "detail": "at least one intentionally clean target is required for false-positive measurement",
        },
        {
            "name": "held_out_industry_diversity",
            "passed": len(held_out_industries) >= 3,
            "detail": "at least three held-out industries are required for a commercial generalization claim",
            "value": len(held_out_industries),
        },
    ]
    return {
        "commercial_shape_ready": all(item["passed"] for item in checks),
        "checks": checks,
        "held_in_seeded_target_count": len(held_in_seeded),
        "held_out_seeded_target_count": len(held_out_seeded),
        "clean_target_count": len(clean_targets),
        "held_out_industries": held_out_industries,
    }


def _pipeline_health_status(pipeline_health: Any) -> tuple[str, str]:
    if not isinstance(pipeline_health, dict):
        return "NOT_MEASURED", "pipeline_health_missing"
    status = str(pipeline_health.get("status") or "").strip().upper()
    if not status:
        return "NOT_MEASURED", "pipeline_health_status_missing"
    if status == "DEGRADED":
        execution_status = str(pipeline_health.get("execution_status") or "").strip().lower()
        if execution_status == "completed":
            return "MEASURED", ""
        return "NOT_MEASURED", "pipeline_health_degraded_without_completed_execution"
    if status != "OK":
        return "NOT_MEASURED", f"pipeline_health_{status.lower()}"
    return "MEASURED", ""


def _validate_trace_ledger(
    trace_ledger: dict[str, Any] | None,
    *,
    target: EvaluationTarget,
    run_id: str,
    policy_id: str,
    evaluation_mode: str,
) -> dict[str, Any] | None:
    if trace_ledger is None:
        return None
    if not isinstance(trace_ledger, dict):
        raise EvaluationContractError("trace_ledger must be an object when supplied")

    from .discovery_trace_ledger import TRACE_LEDGER_SCHEMA

    if trace_ledger.get("schema_version") != TRACE_LEDGER_SCHEMA:
        raise EvaluationContractError("trace_ledger uses an unsupported schema")
    expected = {
        "target_id": target.target_id,
        "project_id": target.project_id,
        "run_id": run_id,
        "policy_id": policy_id,
        "evaluation_mode": evaluation_mode,
    }
    for field, value in expected.items():
        if str(trace_ledger.get(field) or "").strip() != str(value or "").strip():
            raise EvaluationContractError(
                f"trace_ledger.{field} does not match the evaluated run"
            )
    redaction = trace_ledger.get("redaction_contract")
    if not isinstance(redaction, dict) or any(
        redaction.get(field) is not False
        for field in (
            "raw_request_bodies_persisted",
            "raw_response_bodies_persisted",
            "credentials_persisted",
            "ground_truth_persisted",
        )
    ):
        raise EvaluationContractError(
            "trace_ledger does not prove the required redaction contract"
        )
    if not isinstance(trace_ledger.get("traces"), list):
        raise EvaluationContractError("trace_ledger.traces must be a list")
    return trace_ledger


def evaluate_completed_scan(
    manifest: EvaluationManifest,
    target_id: str,
    *,
    run_id: str,
    policy_id: str,
    evaluation_mode: str,
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None,
    pipeline_health: dict[str, Any],
    operational_metrics: dict[str, Any],
    fixture_governance: dict[str, Any] | None = None,
    trace_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one completed scan without exposing hidden answers to runtime."""

    run_id = _required_text(run_id, "run_id")
    policy_id = _required_text(policy_id, "policy_id")
    evaluation_mode = _required_text(evaluation_mode, "evaluation_mode").lower()
    if evaluation_mode not in VALID_EVALUATION_MODES:
        raise EvaluationContractError(
            f"evaluation_mode must be one of {sorted(VALID_EVALUATION_MODES)}"
        )
    target = manifest.target(target_id)
    measurement_status, not_measured_reason = _pipeline_health_status(pipeline_health)
    fingerprints = manifest.target_fingerprints[target_id]
    validated_trace_ledger = _validate_trace_ledger(
        trace_ledger,
        target=target,
        run_id=run_id,
        policy_id=policy_id,
        evaluation_mode=evaluation_mode,
    )

    metrics: dict[str, Any] = {}
    if measurement_status == "MEASURED" and target.expectation == "seeded_defects":
        ground_truth_path = _resolve_ref(target.ground_truth_ref, manifest.manifest_path)
        deliverable_findings = [
            item
            for item in findings
            if isinstance(item, dict) and is_customer_deliverable_defect(item)
        ]
        metrics = compute_benchmark(
            target.project_id,
            deliverable_findings,
            # Candidates and internal clues are intentionally excluded. Only
            # defects that passed the formal customer-delivery gate may become
            # a true or false positive in the commercial quality score.
            candidates=[],
            root=manifest.manifest_path.parent,
            ground_truth_path=str(ground_truth_path),
        )
        if metrics.get("benchmark_active") is not True:
            measurement_status = "NOT_MEASURED"
            not_measured_reason = str(metrics.get("reason") or "benchmark_not_active")
        metrics.pop("ground_truth_source", None)
        metrics["ground_truth_fingerprint"] = fingerprints.get("ground_truth_fingerprint", "")
        metrics["formal_findings_evaluated"] = len(deliverable_findings)
        metrics["non_delivery_findings_excluded"] = max(0, len(findings) - len(deliverable_findings))
        if validated_trace_ledger is not None and metrics.get("benchmark_active") is True:
            metrics["stage_loss_diagnostics"] = compute_stage_loss_matrix(
                ground_truth_path=ground_truth_path,
                candidates=candidates or [],
                trace_ledger=validated_trace_ledger,
                delivered_bug_ids=metrics.get("matched_bug_ids") or [],
            )
        else:
            metrics["stage_loss_diagnostics"] = {
                "schema_version": "qualibug.discovery-stage-loss-matrix.v1",
                "status": "NOT_AVAILABLE",
                "reason": (
                    "trace_ledger_missing"
                    if validated_trace_ledger is None
                    else "benchmark_not_active"
                ),
                "scoring_contract": "diagnostic_only_never_changes_tp_fp_fn",
            }
    elif measurement_status == "MEASURED":
        deliverable = [item for item in findings if isinstance(item, dict) and is_customer_deliverable_defect(item)]
        high_value = [
            item
            for item in deliverable
            if str(item.get("severity") or "").strip().lower() in {"p0", "p1", "critical", "high"}
        ]
        metrics = {
            "benchmark_active": False,
            "ground_truth_available": False,
            "clean_evaluation_active": True,
            "customer_deliverable_false_positives": len(deliverable),
            "critical_high_false_positives": len(high_value),
        }

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "dataset_manifest_fingerprint": manifest.manifest_fingerprint,
        "target_id": target.target_id,
        "project_id": target.project_id,
        "industry": target.industry,
        "split": target.split,
        "expectation": target.expectation,
        "environment_ref": target.environment_ref,
        "environment_type": target.environment_type,
        "runtime_fingerprint": fingerprints["runtime_fingerprint"],
        "input_fingerprint": fingerprints["input_fingerprint"],
        "fixture_fingerprint": fingerprints["fixture_fingerprint"],
        "context_fingerprint": fingerprints["context_fingerprint"],
        "run_id": run_id,
        "policy_id": policy_id,
        "evaluation_mode": evaluation_mode,
        "measurement_status": measurement_status,
        "not_measured_reason": not_measured_reason,
        "pipeline_health": dict(pipeline_health),
        "metrics": metrics,
        "operational_metrics": dict(operational_metrics),
        "fixture_governance": dict(fixture_governance or {}),
    }
    receipt["receipt_fingerprint"] = _sha256_bytes(_canonical_json(receipt))
    return receipt


def _assert_receipt_integrity(receipt: dict[str, Any], *, target_id: str) -> None:
    claimed = _required_text(receipt.get("receipt_fingerprint"), f"{target_id}.receipt_fingerprint")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_fingerprint"}
    actual = _sha256_bytes(_canonical_json(unsigned))
    if claimed != actual:
        raise EvaluationContractError(
            f"target fingerprints differ or receipt integrity failed for target: {target_id}"
        )


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _f1(tp: int, fp: int, fn: int) -> float | None:
    denominator = 2 * tp + fp + fn
    if denominator <= 0:
        return None
    return round((2 * tp) / denominator, 4)


def _mean(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    if not rows:
        return None
    return round(sum(rows) / len(rows), 4)


def _aggregate_seeded(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [
        item
        for item in receipts
        if item.get("expectation") == "seeded_defects" and item.get("measurement_status") == "MEASURED"
    ]
    tp = sum(int((item.get("metrics") or {}).get("true_positives") or 0) for item in measured)
    fp = sum(int((item.get("metrics") or {}).get("false_positives") or 0) for item in measured)
    fn = sum(int((item.get("metrics") or {}).get("false_negatives") or 0) for item in measured)
    macro_recall = _mean(
        float((item.get("metrics") or {}).get("recall"))
        for item in measured
        if (item.get("metrics") or {}).get("recall") is not None
    )
    macro_precision = _mean(
        float((item.get("metrics") or {}).get("precision"))
        for item in measured
        if (item.get("metrics") or {}).get("precision") is not None
    )
    return {
        "target_count": len(receipts),
        "measured_seeded_target_count": len(measured),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "micro_recall": _ratio(tp, tp + fn),
        "micro_precision": _ratio(tp, tp + fp),
        "micro_f1": _f1(tp, fp, fn),
        "macro_recall": macro_recall,
        "macro_precision": macro_precision,
    }


_REQUIRED_OPERATIONAL_FIELDS = (
    "wall_clock_seconds",
    "estimated_cost_usd",
    "request_count",
    "production_http_requests",
    "cleanup_failures",
    "safety_incidents",
    "dirty_test_environments",
    "execution_success_rate",
    "engine_success_rate",
    "duplicate_rate",
)


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise EvaluationContractError(f"{field_name} must be numeric, not boolean")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationContractError(f"{field_name} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise EvaluationContractError(f"{field_name} must be finite and non-negative")
    return parsed


def _aggregate_operational(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    normalized: list[dict[str, float]] = []
    for receipt in receipts:
        raw = receipt.get("operational_metrics")
        raw = raw if isinstance(raw, dict) else {}
        missing_fields = [field for field in _REQUIRED_OPERATIONAL_FIELDS if field not in raw]
        if missing_fields:
            missing.append({"target_id": receipt.get("target_id"), "fields": missing_fields})
            continue
        row = {
            field: _finite_number(raw[field], f"{receipt.get('target_id')}.operational_metrics.{field}")
            for field in _REQUIRED_OPERATIONAL_FIELDS
        }
        for rate_field in ("execution_success_rate", "engine_success_rate", "duplicate_rate"):
            if row[rate_field] > 1:
                raise EvaluationContractError(f"{receipt.get('target_id')}.{rate_field} must be between 0 and 1")
        normalized.append(row)

    complete = bool(receipts) and not missing and len(normalized) == len(receipts)
    seeded_true_positives = sum(
        int((item.get("metrics") or {}).get("true_positives") or 0)
        for item in receipts
        if item.get("expectation") == "seeded_defects" and item.get("measurement_status") == "MEASURED"
    )
    total_cost = sum(item["estimated_cost_usd"] for item in normalized) if complete else None
    return {
        "complete": complete,
        "missing_fields": missing,
        "total_wall_clock_seconds": round(sum(item["wall_clock_seconds"] for item in normalized), 4) if complete else None,
        "total_estimated_cost_usd": round(float(total_cost), 6) if total_cost is not None else None,
        "total_request_count": int(sum(item["request_count"] for item in normalized)) if complete else None,
        "production_http_requests": int(sum(item["production_http_requests"] for item in normalized)) if complete else None,
        "cleanup_failures": int(sum(item["cleanup_failures"] for item in normalized)) if complete else None,
        "safety_incidents": int(sum(item["safety_incidents"] for item in normalized)) if complete else None,
        "dirty_test_environments": int(sum(item["dirty_test_environments"] for item in normalized)) if complete else None,
        "execution_success_rate": _mean(item["execution_success_rate"] for item in normalized) if complete else None,
        "engine_success_rate": _mean(item["engine_success_rate"] for item in normalized) if complete else None,
        "duplicate_rate": _mean(item["duplicate_rate"] for item in normalized) if complete else None,
        "cost_per_true_positive_usd": (
            round(float(total_cost) / seeded_true_positives, 6)
            if total_cost is not None and seeded_true_positives > 0
            else None
        ),
    }


def _aggregate_evidence_quality(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [
        item
        for item in receipts
        if item.get("expectation") == "seeded_defects" and item.get("measurement_status") == "MEASURED"
    ]
    evidence_complete = sum(int((item.get("metrics") or {}).get("evidence_complete_count") or 0) for item in measured)
    evidence_total = sum(int((item.get("metrics") or {}).get("evidence_total_count") or 0) for item in measured)
    reproduction_success = sum(
        int((item.get("metrics") or {}).get("reproduction_success_count") or 0) for item in measured
    )
    reproduction_total = sum(
        int((item.get("metrics") or {}).get("reproduction_total_count") or 0) for item in measured
    )
    return {
        "evidence_complete_count": evidence_complete,
        "evidence_total_count": evidence_total,
        "evidence_completeness_rate": _ratio(evidence_complete, evidence_total),
        "reproduction_success_count": reproduction_success,
        "reproduction_total_count": reproduction_total,
        "reproduction_success_rate": _ratio(reproduction_success, reproduction_total),
    }


def aggregate_evaluation_receipts(
    manifest: EvaluationManifest,
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the single promotion/reporting metric source for one policy."""

    by_target: dict[str, dict[str, Any]] = {}
    policy_ids = {str(item.get("policy_id") or "").strip() for item in receipts if isinstance(item, dict)}
    if not receipts:
        policy_ids = set()
    if "" in policy_ids or len(policy_ids) > 1:
        raise EvaluationContractError("all evaluation receipts in one report must use one non-empty policy_id")
    evaluation_modes = {
        str(item.get("evaluation_mode") or "").strip().lower()
        for item in receipts
        if isinstance(item, dict)
    }
    if "" in evaluation_modes or len(evaluation_modes) > 1:
        raise EvaluationContractError("all evaluation receipts in one report must use one evaluation_mode")
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA:
            raise EvaluationContractError("all evaluation receipts must use the current receipt schema")
        if receipt.get("dataset_id") != manifest.dataset_id or receipt.get("dataset_version") != manifest.dataset_version:
            raise EvaluationContractError("evaluation receipt dataset identity does not match manifest")
        if receipt.get("dataset_manifest_fingerprint") != manifest.manifest_fingerprint:
            raise EvaluationContractError("evaluation receipt manifest fingerprint does not match frozen manifest")
        target_id = _required_text(receipt.get("target_id"), "receipt.target_id")
        _assert_receipt_integrity(receipt, target_id=target_id)
        if target_id in by_target:
            raise EvaluationContractError(f"duplicate evaluation receipt for target: {target_id}")
        target = manifest.target(target_id)
        expected_fingerprints = manifest.target_fingerprints[target.target_id]
        for field in (
            "runtime_fingerprint",
            "input_fingerprint",
            "fixture_fingerprint",
            "context_fingerprint",
        ):
            if receipt.get(field) != expected_fingerprints[field]:
                raise EvaluationContractError(f"{field} drift for target: {target_id}")
        if receipt.get("environment_ref") != target.environment_ref:
            raise EvaluationContractError(f"environment_ref drift for target: {target_id}")
        if receipt.get("environment_type") != target.environment_type:
            raise EvaluationContractError(f"environment_type drift for target: {target_id}")
        by_target[target_id] = receipt

    missing_target_ids = [item.target_id for item in manifest.targets if item.target_id not in by_target]
    not_measured = [
        {
            "target_id": item.target_id,
            "reason": str((by_target.get(item.target_id) or {}).get("not_measured_reason") or "receipt_missing"),
        }
        for item in manifest.targets
        if item.target_id not in by_target
        or (by_target[item.target_id].get("measurement_status") != "MEASURED")
    ]
    held_in = [by_target[item.target_id] for item in manifest.targets if item.split == "held_in" and item.target_id in by_target]
    held_out = [by_target[item.target_id] for item in manifest.targets if item.split == "held_out" and item.target_id in by_target]
    clean = [
        by_target[item.target_id]
        for item in manifest.targets
        if item.expectation == "clean" and item.target_id in by_target and by_target[item.target_id].get("measurement_status") == "MEASURED"
    ]

    industry_rows: dict[str, list[dict[str, Any]]] = {}
    for item in held_out:
        if item.get("expectation") == "seeded_defects" and item.get("measurement_status") == "MEASURED":
            industry_rows.setdefault(str(item.get("industry") or ""), []).append(item)
    industry_metrics = {
        industry: _aggregate_seeded(rows)
        for industry, rows in sorted(industry_rows.items())
    }
    held_out_industry_recalls = [
        float(row["micro_recall"])
        for row in industry_metrics.values()
        if row.get("micro_recall") is not None
    ]
    shape = assess_commercial_dataset_shape(manifest)
    degraded_targets = [
        str(item.get("target_id") or "")
        for item in receipts
        if str(_as_dict(item.get("pipeline_health"), "pipeline_health").get("status") or "").strip().upper()
        == "DEGRADED"
    ]
    evaluation_complete = not missing_target_ids and not not_measured
    operational = _aggregate_operational(receipts)
    evidence_quality = _aggregate_evidence_quality(receipts)
    clean_fp = sum(
        int((item.get("metrics") or {}).get("customer_deliverable_false_positives") or 0)
        for item in clean
    )
    clean_high_fp = sum(
        int((item.get("metrics") or {}).get("critical_high_false_positives") or 0)
        for item in clean
    )
    return {
        "schema_version": REPORT_SCHEMA,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "dataset_manifest_fingerprint": manifest.manifest_fingerprint,
        "policy_id": next(iter(policy_ids), ""),
        "evaluation_mode": next(iter(evaluation_modes), ""),
        "claim_status": "MEASURED" if evaluation_complete else "NOT_MEASURED",
        "evaluation_complete": evaluation_complete,
        "commercial_shape": shape,
        "commercial_promotion_evidence_ready": (
            evaluation_complete and bool(shape["commercial_shape_ready"]) and bool(operational["complete"])
        ),
        "expected_target_count": len(manifest.targets),
        "evaluated_target_count": len(by_target),
        "missing_target_ids": missing_target_ids,
        "not_measured_targets": not_measured,
        "pipeline_degraded_target_ids": degraded_targets,
        "pipeline_degraded_target_count": len(degraded_targets),
        "held_in": _aggregate_seeded(held_in),
        "held_out": _aggregate_seeded(held_out),
        "held_out_industries": industry_metrics,
        "held_out_macro_industry_recall": _mean(held_out_industry_recalls),
        "held_out_min_industry_recall": min(held_out_industry_recalls) if held_out_industry_recalls else None,
        "clean": {
            "measured_target_count": len(clean),
            "customer_deliverable_false_positives": clean_fp,
            "critical_high_false_positives": clean_high_fp,
        },
        "evidence_quality": evidence_quality,
        "operational": operational,
        "run_ids": [str(item.get("run_id") or "") for item in receipts],
        "target_receipts": receipts,
    }


def _assert_report(
    manifest: EvaluationManifest,
    report: dict[str, Any],
    *,
    evaluation_mode: str,
) -> None:
    if not isinstance(report, dict) or report.get("schema_version") != REPORT_SCHEMA:
        raise EvaluationContractError("paired evaluation requires current-schema reports")
    if report.get("dataset_id") != manifest.dataset_id or report.get("dataset_version") != manifest.dataset_version:
        raise EvaluationContractError("paired evaluation report dataset identity mismatch")
    if report.get("dataset_manifest_fingerprint") != manifest.manifest_fingerprint:
        raise EvaluationContractError("paired evaluation report manifest fingerprint mismatch")
    if report.get("evaluation_mode") != evaluation_mode:
        raise EvaluationContractError(
            f"expected {evaluation_mode} evaluation report, got {report.get('evaluation_mode')!r}"
        )
    if report.get("evaluation_complete") is not True:
        raise EvaluationContractError(f"{evaluation_mode} evaluation report is incomplete")


def _report_target_fingerprints(report: dict[str, Any]) -> dict[str, tuple[str, str, str, str]]:
    result: dict[str, tuple[str, str, str, str]] = {}
    for receipt in report.get("target_receipts") or []:
        if not isinstance(receipt, dict):
            raise EvaluationContractError("target receipt must be an object")
        target_id = _required_text(receipt.get("target_id"), "target_receipt.target_id")
        _assert_receipt_integrity(receipt, target_id=target_id)
        result[target_id] = (
            _required_text(receipt.get("runtime_fingerprint"), f"{target_id}.runtime_fingerprint"),
            _required_text(receipt.get("input_fingerprint"), f"{target_id}.input_fingerprint"),
            _required_text(receipt.get("fixture_fingerprint"), f"{target_id}.fixture_fingerprint"),
            _required_text(receipt.get("context_fingerprint"), f"{target_id}.context_fingerprint"),
        )
    return result


def build_paired_evaluation_evidence(
    manifest: EvaluationManifest,
    *,
    champion_replay: dict[str, Any],
    challenger_replay: dict[str, Any],
    champion_shadow: dict[str, Any],
    challenger_shadow: dict[str, Any],
) -> dict[str, Any]:
    """Prove four real, dataset-identical runs before policy promotion."""

    reports = (
        (champion_replay, "replay"),
        (challenger_replay, "replay"),
        (champion_shadow, "shadow"),
        (challenger_shadow, "shadow"),
    )
    for report, mode in reports:
        _assert_report(manifest, report, evaluation_mode=mode)
        if report.get("commercial_promotion_evidence_ready") is not True:
            raise EvaluationContractError(f"{mode} report lacks commercial promotion evidence")

    champion_policy_ids = {champion_replay.get("policy_id"), champion_shadow.get("policy_id")}
    challenger_policy_ids = {challenger_replay.get("policy_id"), challenger_shadow.get("policy_id")}
    if len(champion_policy_ids) != 1 or "" in champion_policy_ids or None in champion_policy_ids:
        raise EvaluationContractError("champion replay and shadow reports must use one policy_id")
    if len(challenger_policy_ids) != 1 or "" in challenger_policy_ids or None in challenger_policy_ids:
        raise EvaluationContractError("challenger replay and shadow reports must use one policy_id")
    if champion_policy_ids == challenger_policy_ids:
        raise EvaluationContractError("champion and challenger policy_id values must differ")

    fingerprints = [_report_target_fingerprints(report) for report, _ in reports]
    if any(item != fingerprints[0] for item in fingerprints[1:]):
        raise EvaluationContractError("champion/challenger replay/shadow target fingerprints differ")
    expected_target_ids = {item.target_id for item in manifest.targets}
    if set(fingerprints[0]) != expected_target_ids:
        raise EvaluationContractError("paired reports do not cover the exact frozen target set")

    combined = {
        key: _sha256_bytes(_canonical_json({target_id: value[index] for target_id, value in fingerprints[0].items()}))
        for index, key in enumerate(
            ("same_runtime_fingerprint", "same_input_fingerprint", "same_fixture_fingerprint", "same_context_artifact_id")
        )
    }
    environments = {
        item.target_id: {"environment_ref": item.environment_ref, "environment_type": item.environment_type}
        for item in manifest.targets
    }
    replay_run_ids = tuple(
        str(item)
        for item in (champion_replay.get("run_ids") or []) + (challenger_replay.get("run_ids") or [])
        if str(item).strip()
    )
    shadow_run_ids = tuple(
        str(item)
        for item in (champion_shadow.get("run_ids") or []) + (challenger_shadow.get("run_ids") or [])
        if str(item).strip()
    )
    paired_target_count = len(manifest.targets)
    expected_run_count = paired_target_count * 2
    return {
        "replay_executed": len(replay_run_ids) == expected_run_count,
        "shadow_executed": len(shadow_run_ids) == expected_run_count,
        "held_in_executed": any(item.split == "held_in" for item in manifest.targets),
        "held_out_executed": any(item.split == "held_out" for item in manifest.targets),
        "clean_executed": any(item.expectation == "clean" for item in manifest.targets),
        "dataset_version": manifest.dataset_version,
        "dataset_manifest_fingerprint": manifest.manifest_fingerprint,
        "replay_run_ids": replay_run_ids,
        "shadow_run_ids": shadow_run_ids,
        "paired_target_count": paired_target_count,
        **combined,
        "same_environment_id": _sha256_bytes(_canonical_json(environments)),
        "target_receipt_fingerprints": tuple(
            f"{target_id}:" + _sha256_bytes(
                _canonical_json(
                    [
                        next(
                            str(receipt.get("receipt_fingerprint") or "")
                            for receipt in report.get("target_receipts") or []
                            if receipt.get("target_id") == target_id
                        )
                        for report, _ in reports
                    ]
                )
            )
            for target_id in sorted(fingerprints[0])
        ),
    }


def policy_metrics_from_evaluation_reports(
    replay_report: dict[str, Any],
    shadow_report: dict[str, Any],
) -> dict[str, Any]:
    """Flatten the SSOT reports into the strict policy-promotion metric schema."""

    if replay_report.get("evaluation_mode") != "replay" or shadow_report.get("evaluation_mode") != "shadow":
        raise EvaluationContractError("policy metrics require one replay and one shadow report")
    if replay_report.get("policy_id") != shadow_report.get("policy_id"):
        raise EvaluationContractError("replay and shadow reports must belong to the same policy")
    if replay_report.get("dataset_manifest_fingerprint") != shadow_report.get("dataset_manifest_fingerprint"):
        raise EvaluationContractError("replay and shadow reports must use the same frozen dataset")

    replay_held_in = replay_report.get("held_in") or {}
    replay_held_out = replay_report.get("held_out") or {}
    shadow_held_in = shadow_report.get("held_in") or {}
    shadow_held_out = shadow_report.get("held_out") or {}
    replay_clean = replay_report.get("clean") or {}
    shadow_clean = shadow_report.get("clean") or {}
    replay_quality = replay_report.get("evidence_quality") or {}
    shadow_quality = shadow_report.get("evidence_quality") or {}
    replay_operational = replay_report.get("operational") or {}
    shadow_operational = shadow_report.get("operational") or {}

    def _minimum_rate(first: Any, second: Any) -> float:
        values = [float(value) for value in (first, second) if value is not None]
        return min(values) if values else 0.0

    replay_tp = int(replay_held_in.get("true_positives") or 0) + int(replay_held_out.get("true_positives") or 0)
    replay_fp = int(replay_held_in.get("false_positives") or 0) + int(replay_held_out.get("false_positives") or 0)
    replay_fn = int(replay_held_in.get("false_negatives") or 0) + int(replay_held_out.get("false_negatives") or 0)
    total_cost = sum(
        float(value)
        for value in (
            replay_operational.get("total_estimated_cost_usd"),
            shadow_operational.get("total_estimated_cost_usd"),
        )
        if value is not None
    )
    total_wall_clock = sum(
        float(value)
        for value in (
            replay_operational.get("total_wall_clock_seconds"),
            shadow_operational.get("total_wall_clock_seconds"),
        )
        if value is not None
    )
    clean_fp = max(
        int(replay_clean.get("customer_deliverable_false_positives") or 0),
        int(shadow_clean.get("customer_deliverable_false_positives") or 0),
    )
    clean_high_fp = max(
        int(replay_clean.get("critical_high_false_positives") or 0),
        int(shadow_clean.get("critical_high_false_positives") or 0),
    )
    return {
        "evaluation_complete": bool(replay_report.get("evaluation_complete") and shadow_report.get("evaluation_complete")),
        "commercial_shape_ready": bool(
            (replay_report.get("commercial_shape") or {}).get("commercial_shape_ready")
            and (shadow_report.get("commercial_shape") or {}).get("commercial_shape_ready")
        ),
        "operational_metrics_complete": bool(replay_operational.get("complete") and shadow_operational.get("complete")),
        "sample_count": int(replay_report.get("evaluated_target_count") or 0) + int(shadow_report.get("evaluated_target_count") or 0),
        "confirmed_bugs": replay_tp,
        "total_bugs": replay_tp + replay_fp,
        "true_positives": replay_tp,
        "false_positives": replay_fp + clean_fp,
        "false_negatives": replay_fn,
        "held_in_recall": float(replay_held_in.get("micro_recall") or 0),
        "held_in_precision": float(replay_held_in.get("micro_precision") or 0),
        "held_in_f1": float(replay_held_in.get("micro_f1") or 0),
        "held_out_recall": float(replay_held_out.get("micro_recall") or 0),
        "held_out_precision": float(replay_held_out.get("micro_precision") or 0),
        "held_out_f1": float(replay_held_out.get("micro_f1") or 0),
        "shadow_held_in_f1": float(shadow_held_in.get("micro_f1") or 0),
        "shadow_held_out_f1": float(shadow_held_out.get("micro_f1") or 0),
        "macro_industry_recall": float(replay_report.get("held_out_macro_industry_recall") or 0),
        "min_industry_recall": float(replay_report.get("held_out_min_industry_recall") or 0),
        "unique_industry_count": len(replay_report.get("held_out_industries") or {}),
        "clean_false_positives": clean_fp,
        "clean_critical_high_false_positives": clean_high_fp,
        "evidence_quality_score": _minimum_rate(
            replay_quality.get("evidence_completeness_rate"), shadow_quality.get("evidence_completeness_rate")
        ),
        "reproducibility_rate": _minimum_rate(
            replay_quality.get("reproduction_success_rate"), shadow_quality.get("reproduction_success_rate")
        ),
        "engine_success_rate": _minimum_rate(
            replay_operational.get("engine_success_rate"), shadow_operational.get("engine_success_rate")
        ),
        "execution_success_rate": _minimum_rate(
            replay_operational.get("execution_success_rate"), shadow_operational.get("execution_success_rate")
        ),
        "duplicate_rate": max(
            float(replay_operational.get("duplicate_rate") or 0),
            float(shadow_operational.get("duplicate_rate") or 0),
        ),
        "production_http_requests": int(replay_operational.get("production_http_requests") or 0)
        + int(shadow_operational.get("production_http_requests") or 0),
        "cleanup_failures": int(replay_operational.get("cleanup_failures") or 0)
        + int(shadow_operational.get("cleanup_failures") or 0),
        "safety_incidents": int(replay_operational.get("safety_incidents") or 0)
        + int(shadow_operational.get("safety_incidents") or 0),
        "dirty_test_environments": int(replay_operational.get("dirty_test_environments") or 0)
        + int(shadow_operational.get("dirty_test_environments") or 0),
        "pipeline_degraded_targets": int(replay_report.get("pipeline_degraded_target_count") or 0)
        + int(shadow_report.get("pipeline_degraded_target_count") or 0),
        "total_cost_usd": round(total_cost, 6),
        "cost_per_true_positive_usd": round(total_cost / replay_tp, 6) if replay_tp > 0 else 0.0,
        "wall_clock_seconds": round(total_wall_clock, 4),
    }


def persist_evaluation_receipt(receipt: dict[str, Any], output_root: Path | str) -> Path:
    """Persist an immutable receipt using an atomic replace."""

    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise EvaluationContractError("cannot persist a receipt with an unsupported schema")
    safe = re.compile(r"[^A-Za-z0-9_.-]+")
    dataset = safe.sub("_", _required_text(receipt.get("dataset_id"), "dataset_id"))
    version = safe.sub("_", _required_text(receipt.get("dataset_version"), "dataset_version"))
    policy = safe.sub("_", _required_text(receipt.get("policy_id"), "policy_id"))
    target = safe.sub("_", _required_text(receipt.get("target_id"), "target_id"))
    run_id = safe.sub("_", _required_text(receipt.get("run_id"), "run_id"))
    path = Path(output_root) / dataset / version / policy / f"{target}_{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if _canonical_json(existing) != _canonical_json(receipt):
            raise EvaluationContractError(f"immutable evaluation receipt already exists with different content: {path}")
        return path
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def persist_evaluation_report(report: dict[str, Any], path: Path | str) -> Path:
    """Persist an immutable aggregate report at an evaluator-owned path."""

    if report.get("schema_version") != REPORT_SCHEMA:
        raise EvaluationContractError("cannot persist a report with an unsupported schema")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if _canonical_json(existing) != _canonical_json(report):
            raise EvaluationContractError(
                f"immutable evaluation report already exists with different content: {destination}"
            )
        return destination
    temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return destination
