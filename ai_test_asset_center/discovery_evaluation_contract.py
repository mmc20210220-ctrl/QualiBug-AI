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

from .benchmark_compute import compute_benchmark
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
            "split": target.split,
            "expectation": target.expectation,
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
    if status != "OK":
        return "NOT_MEASURED", f"pipeline_health_{status.lower()}"
    return "MEASURED", ""


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
    }
    return receipt


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
        if target_id in by_target:
            raise EvaluationContractError(f"duplicate evaluation receipt for target: {target_id}")
        target = manifest.target(target_id)
        if receipt.get("runtime_fingerprint") != manifest.target_fingerprints[target.target_id]["runtime_fingerprint"]:
            raise EvaluationContractError(f"runtime fingerprint drift for target: {target_id}")
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
    evaluation_complete = not missing_target_ids and not not_measured
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
        "commercial_promotion_evidence_ready": evaluation_complete and bool(shape["commercial_shape_ready"]),
        "expected_target_count": len(manifest.targets),
        "evaluated_target_count": len(by_target),
        "missing_target_ids": missing_target_ids,
        "not_measured_targets": not_measured,
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
        "run_ids": [str(item.get("run_id") or "") for item in receipts],
        "target_receipts": receipts,
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
