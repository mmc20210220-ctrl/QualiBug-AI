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
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .canonical_defect_registry import (
    CanonicalDefectRegistryError,
    build_defect_identity_consistency,
    canonical_representative_findings,
    validate_canonical_defect_registry,
    validate_defect_identity_consistency,
)
from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from .discovery_quality_projection import (
    SCHEMA_VERSION as QUALITY_PROJECTION_SCHEMA,
    formal_customer_deliverable_findings,
    validated_delivery_gate_finding_ids,
)
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    derive_campaign_terminal_status,
    validate_obligation_attempt_ledger,
)
from .formal_delivery_authority import (
    FormalDeliveryAuthorityError,
    build_formal_delivery_authority_receipt,
    validate_formal_delivery_authority_receipt,
)
from .evaluator_receipt_auth import (
    EvaluatorReceiptAuthError,
    seal_evaluator_artifact,
    verify_evaluator_artifact,
)
from .evaluator_execution_attestation import (
    ATTESTATION_AUTHENTICATION_FIELD,
    ATTESTATION_FINGERPRINT_FIELD,
    EXECUTION_ATTESTATION_SCHEMA,
    ExecutionAttestationError,
    validate_execution_attestation,
)
from .policy_evaluation_gate import PolicyPromotionGate


MANIFEST_SCHEMA = "qualibug.discovery-evaluation-dataset.v1"
RECEIPT_SCHEMA = "qualibug.discovery-evaluation-receipt.v3"
RECEIPT_V2_SCHEMA = "qualibug.discovery-evaluation-receipt.v2"
RECEIPT_V1_SCHEMA = "qualibug.discovery-evaluation-receipt.v1"
REPORT_SCHEMA = "qualibug.discovery-evaluation-report.v2"
REPORT_V1_SCHEMA = "qualibug.discovery-evaluation-report.v1"
RECEIPT_AUTHENTICATION_FIELD = "receipt_authentication"
REPORT_AUTHENTICATION_FIELD = "report_authentication"
RECEIPT_FINGERPRINT_FIELD = "receipt_fingerprint"
REPORT_FINGERPRINT_FIELD = "report_fingerprint"
EVALUATION_RUN_ENVELOPE_SCHEMA = (
    "qualibug.discovery-evaluation-run-envelope.v2"
)
POLICY_COMPARISON_SCHEMA = "qualibug.discovery-policy-comparison.v3"
POLICY_COMPARISON_FINGERPRINT_FIELD = "comparison_fingerprint"
POLICY_COMPARISON_AUTHENTICATION_FIELD = "comparison_authentication"
POLICY_COMPARISON_REPORT_KEYS = frozenset(
    {
        "champion_replay",
        "challenger_replay",
        "champion_shadow",
        "challenger_shadow",
    }
)

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
VALID_EVALUATION_MODES = {"operational", "replay", "shadow"}


def _load_external_benchmark_compute() -> tuple[Any, Any]:
    """Load evaluator-only scoring code only for an authenticated evaluation.

    Product scans must remain runnable without the evaluator package.  The
    evaluator dependency is intentionally resolved at the private scoring
    boundary, after the caller has supplied a completed evaluation contract;
    a missing evaluator is an explicit evaluation failure, never a product
    runtime fallback or an implicit quality claim.
    """

    try:
        from benchmark_evaluator.benchmark_compute import (
            compute_benchmark,
            compute_stage_loss_matrix,
        )
    except ModuleNotFoundError as exc:
        raise EvaluationContractError(
            "external_evaluator_dependency_missing:benchmark_evaluator"
        ) from exc
    return compute_benchmark, compute_stage_loss_matrix


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
            # Enforce declared ground_truth_bug_count when present (prevents 71↔131 swaps).
            # Enforce per-target declared ground_truth_bug_count (prevents 71↔131 swaps).
            evaluator_declared = None
            for item in raw_targets:
                if isinstance(item, dict) and str(item.get("target_id") or "") == target.target_id:
                    evaluator = item.get("evaluator") if isinstance(item.get("evaluator"), dict) else {}
                    evaluator_declared = evaluator.get("ground_truth_bug_count")
                    break
            if evaluator_declared is not None:
                gt_path = _resolve_ref(target.ground_truth_ref, manifest_path)
                try:
                    gt_raw = json.loads(gt_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise EvaluationContractError(
                        f"unable to load ground truth for count check: {gt_path}: {exc}"
                    ) from exc
                bugs = gt_raw.get("bugs") if isinstance(gt_raw, dict) else gt_raw
                actual_count = len(bugs) if isinstance(bugs, list) else -1
                if int(evaluator_declared) != actual_count:
                    raise EvaluationContractError(
                        f"ground_truth_bug_count mismatch for {target.target_id}: "
                        f"declared {evaluator_declared}, file has {actual_count}. "
                        "Champion/candidate comparisons must use the same frozen GT."
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

    from .discovery_trace_ledger import (
        TRACE_LEDGER_SCHEMA,
        DiscoveryTraceError,
        validate_trace_ledger,
    )

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
    try:
        return validate_trace_ledger(trace_ledger)
    except DiscoveryTraceError as exc:
        raise EvaluationContractError(
            f"trace_ledger contract invalid: {exc}"
        ) from exc


def _validated_formal_evaluation_scope(
    delivery_occurrences: list[dict[str, Any]],
    *,
    obligation_attempt_ledger: dict[str, Any] | None,
    run_id: str,
    target: EvaluationTarget,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Validate the evaluator's exact Gate-v2 + ledger input boundary."""

    if not isinstance(delivery_occurrences, list):
        raise EvaluationContractError("delivery_occurrences must be a list")
    if any(not isinstance(item, dict) for item in delivery_occurrences):
        raise EvaluationContractError(
            "delivery_occurrences must contain objects only"
        )
    if obligation_attempt_ledger is None:
        raise EvaluationContractError(
            "obligation_attempt_ledger_required_for_completed_evaluation"
        )

    validated_ledger: dict[str, Any] | None = None
    if obligation_attempt_ledger is not None:
        try:
            validated_ledger = validate_obligation_attempt_ledger(
                obligation_attempt_ledger
            )
        except ObligationAttemptLedgerError as exc:
            raise EvaluationContractError(
                f"obligation_attempt_ledger_invalid:{exc}"
            ) from exc
        if str(validated_ledger.get("run_id") or "").strip() != run_id:
            raise EvaluationContractError(
                "obligation_attempt_ledger_run_id_mismatch"
            )

    try:
        deliverable = formal_customer_deliverable_findings(
            delivery_occurrences,
            obligation_attempt_ledger=validated_ledger,
        )
        gate_ids = validated_delivery_gate_finding_ids(validated_ledger)
    except MainlineContractError as exc:
        raise EvaluationContractError(
            f"formal_delivery_scope_invalid:{exc}"
        ) from exc

    finding_ids = [
        str(
            item.get("finding_id")
            or item.get("id")
            or item.get("bug_id")
            or ""
        ).strip()
        for item in delivery_occurrences
    ]
    if not all(finding_ids):
        raise EvaluationContractError("formal_evaluation_finding_id_missing")
    if len(finding_ids) != len(set(finding_ids)):
        raise EvaluationContractError("formal_evaluation_finding_id_duplicate")
    if (
        sorted(finding_ids) != gate_ids
        or len(deliverable) != len(delivery_occurrences)
    ):
        raise EvaluationContractError(
            "evaluation_findings_not_exact_formal_gate_scope"
        )

    for finding in deliverable:
        identity = dict(
            dict(finding.get("delivery_gate_receipt") or {}).get("identity")
            or {}
        )
        expected = {
            "run_id": run_id,
            "target_id": target.target_id,
            "environment_id": target.environment_ref,
        }
        for field, value in expected.items():
            if str(identity.get(field) or "").strip() != value:
                raise EvaluationContractError(
                    f"formal_delivery_{field}_mismatch"
                )
    return deliverable, validated_ledger


def evaluate_completed_scan(
    manifest: EvaluationManifest,
    target_id: str,
    *,
    run_id: str,
    policy_id: str,
    evaluation_mode: str,
    findings: list[dict[str, Any]],
    delivery_occurrences: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None,
    pipeline_health: dict[str, Any],
    operational_metrics: dict[str, Any],
    fixture_governance: dict[str, Any] | None = None,
    trace_ledger: dict[str, Any] | None = None,
    obligation_attempt_ledger: dict[str, Any] | None = None,
    mainline_run: dict[str, Any] | None = None,
    formal_count_projection: dict[str, Any] | None = None,
    formal_delivery_authority: dict[str, Any] | None = None,
    canonical_defect_registry: dict[str, Any] | None = None,
    defect_identity_consistency: dict[str, Any] | None = None,
    evaluator_policy_identity: dict[str, Any] | None = None,
    process_boundary: dict[str, Any] | None = None,
    execution_attestation: dict[str, Any] | None = None,
    receipt_signing_key: str | bytes | bytearray | None = None,
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
    try:
        validated_mainline = validate_mainline_run_contract(
            dict(mainline_run or {})
        )
    except (MainlineContractError, TypeError, ValueError) as exc:
        raise EvaluationContractError(
            f"mainline_run_invalid:{exc}"
        ) from exc
    expected_mainline_identity = {
        "run_id": run_id,
        "target_id": target.target_id,
        "environment_id": target.environment_ref,
        "evaluation_mode": evaluation_mode,
    }
    for field, value in expected_mainline_identity.items():
        if str(validated_mainline.get(field) or "").strip() != value:
            raise EvaluationContractError(f"mainline_run_{field}_mismatch")
    if evaluator_policy_identity is None:
        raise EvaluationContractError("evaluator_policy_identity required")
    identity = _as_dict(
        evaluator_policy_identity, "evaluator_policy_identity"
    )
    if set(identity) != {
        "policy_id",
        "policy_version",
        "strategy_fingerprint",
    }:
        raise EvaluationContractError(
            "evaluator_policy_identity fields invalid"
        )
    if _required_text(
        identity.get("policy_id"), "evaluator_policy_identity.policy_id"
    ) != policy_id:
        raise EvaluationContractError(
            "evaluator_policy_identity policy_id mismatch"
        )
    if _required_text(
        identity.get("policy_version"),
        "evaluator_policy_identity.policy_version",
    ) != validated_mainline["policy_version"]:
        raise EvaluationContractError(
            "evaluator_policy_identity policy_version mismatch"
        )
    policy_strategy_fingerprint = _required_text(
        identity.get("strategy_fingerprint"),
        "evaluator_policy_identity.strategy_fingerprint",
    ).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", policy_strategy_fingerprint):
        raise EvaluationContractError(
            "evaluator_policy_identity strategy_fingerprint invalid"
        )
    validated_policy_identity = {
        "policy_id": policy_id,
        "policy_version": validated_mainline["policy_version"],
        "strategy_fingerprint": policy_strategy_fingerprint,
    }
    measurement_status, not_measured_reason = _pipeline_health_status(pipeline_health)
    fingerprints = manifest.target_fingerprints[target_id]
    validated_trace_ledger = _validate_trace_ledger(
        trace_ledger,
        target=target,
        run_id=run_id,
        policy_id=policy_id,
        evaluation_mode=evaluation_mode,
    )
    deliverable_occurrences, validated_attempt_ledger = (
        _validated_formal_evaluation_scope(
            delivery_occurrences if delivery_occurrences is not None else [],
            obligation_attempt_ledger=obligation_attempt_ledger,
            run_id=run_id,
            target=target,
        )
    )
    assert validated_attempt_ledger is not None
    campaign_terminal_status = derive_campaign_terminal_status(
        validated_attempt_ledger
    )
    observed_request_receipt_count = sum(
        1
        for raw_attempt in validated_attempt_ledger.get("attempts") or []
        if int(
            dict(raw_attempt.get("operational_receipt") or {}).get(
                "http_request_attempt_count"
            )
            or 0
        )
        > 0
    )
    if (
        measurement_status == "MEASURED"
        and campaign_terminal_status != "completed"
    ):
        measurement_status = "NOT_MEASURED"
        not_measured_reason = (
            f"obligation_campaign_{campaign_terminal_status}"
        )
    elif measurement_status == "MEASURED" and observed_request_receipt_count == 0:
        measurement_status = "NOT_MEASURED"
        not_measured_reason = "target_request_receipts_missing"
    validated_execution_attestation: dict[str, Any]
    if execution_attestation is None:
        validated_execution_attestation = {
            "schema_version": EXECUTION_ATTESTATION_SCHEMA,
            "status": "NOT_PROVIDED",
            "reason": "evaluator_owned_io_attestation_required",
        }
        if measurement_status == "MEASURED":
            measurement_status = "NOT_MEASURED"
            not_measured_reason = "evaluator_execution_attestation_missing"
    else:
        if not isinstance(process_boundary, dict):
            raise EvaluationContractError(
                "process_boundary_required_for_execution_attestation"
            )
        try:
            validated_execution_attestation = validate_execution_attestation(
                dict(execution_attestation),
                mainline_run=validated_mainline,
                obligation_attempt_ledger=validated_attempt_ledger,
                policy_identity=validated_policy_identity,
                fixture_governance=dict(fixture_governance or {}),
                process_boundary=dict(process_boundary),
                signing_key=receipt_signing_key,
            )
        except ExecutionAttestationError as exc:
            raise EvaluationContractError(
                f"execution_attestation_invalid:{exc}"
            ) from exc
    try:
        validated_formal_authority = build_formal_delivery_authority_receipt(
            mainline_run=validated_mainline,
            findings=deliverable_occurrences,
            obligation_attempt_ledger=validated_attempt_ledger,
        )
    except FormalDeliveryAuthorityError as exc:
        raise EvaluationContractError(
            f"formal_delivery_authority_invalid:{exc}"
        ) from exc
    try:
        submitted_authority = validate_formal_delivery_authority_receipt(
            dict(formal_delivery_authority or {})
        )
    except FormalDeliveryAuthorityError as exc:
        raise EvaluationContractError(
            f"submitted_formal_delivery_authority_invalid:{exc}"
        ) from exc
    if submitted_authority != validated_formal_authority:
        raise EvaluationContractError(
            "submitted_formal_delivery_authority_mismatch"
        )
    try:
        validated_canonical_registry = validate_canonical_defect_registry(
            dict(canonical_defect_registry or {}),
            mainline_run=validated_mainline,
            deliverable_occurrences=deliverable_occurrences,
            obligation_attempt_ledger=validated_attempt_ledger,
        )
        canonical_findings = canonical_representative_findings(
            validated_canonical_registry,
            deliverable_occurrences=deliverable_occurrences,
        )
    except CanonicalDefectRegistryError as exc:
        raise EvaluationContractError(
            f"canonical_defect_registry_invalid:{exc}"
        ) from exc
    if findings != canonical_findings:
        raise EvaluationContractError(
            "evaluation_findings_not_exact_canonical_scope"
        )
    projection = (
        formal_count_projection
        if isinstance(formal_count_projection, dict)
        else {}
    )
    if (
        projection.get("schema_version")
        != QUALITY_PROJECTION_SCHEMA
        or projection.get("authority_status") != "VERIFIED"
        or projection.get("canonical_defect_ids")
        != validated_canonical_registry["canonical_defect_ids"]
        or projection.get("delivery_occurrence_finding_ids")
        != validated_formal_authority["delivery_occurrence_finding_ids"]
        or int(projection.get("formal_customer_deliverable_count") or 0)
        != validated_canonical_registry["canonical_defect_count"]
        or int(projection.get("delivery_occurrence_count") or 0)
        != validated_formal_authority["delivery_occurrence_count"]
    ):
        raise EvaluationContractError(
            "formal_count_projection_mismatch"
        )
    allowed_occurrence_scopes = {
        "delivery_gate_ids",
        "formal_authority_occurrence_ids",
        "registry_occurrence_ids",
        "formal_projection_occurrence_ids",
        "product_projection_occurrence_ids",
        "evaluator_submission_occurrence_ids",
        "trace_ledger_occurrence_ids",
    }
    allowed_canonical_scopes = {
        "canonical_registry_ids",
        "formal_projection_ids",
        "product_projection_ids",
        "evaluator_projection_ids",
        "evaluator_submission_ids",
    }
    try:
        submitted_identity_consistency = validate_defect_identity_consistency(
            dict(defect_identity_consistency or {}),
            required_occurrence_scopes={
                "delivery_gate_ids",
                "registry_occurrence_ids",
                "formal_projection_occurrence_ids",
            },
            required_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
                "product_projection_ids",
            },
            allowed_occurrence_scopes=allowed_occurrence_scopes,
            allowed_canonical_scopes=allowed_canonical_scopes,
        )
    except CanonicalDefectRegistryError as exc:
        raise EvaluationContractError(
            f"defect_identity_consistency_invalid:{exc}"
        ) from exc
    occurrence_scope = submitted_identity_consistency["occurrence_scopes"]
    canonical_scope = submitted_identity_consistency["canonical_scopes"]
    if any(
        ids != validated_formal_authority["delivery_occurrence_finding_ids"]
        for ids in occurrence_scope.values()
    ):
        raise EvaluationContractError(
            "defect_identity_occurrence_scope_mismatch"
        )
    if any(
        ids != validated_canonical_registry["canonical_defect_ids"]
        for ids in canonical_scope.values()
    ):
        raise EvaluationContractError(
            "defect_identity_canonical_scope_mismatch"
        )
    evaluator_identity_consistency = build_defect_identity_consistency(
        occurrence_scopes={
            **{
                name: list(values)
                for name, values in occurrence_scope.items()
            },
            "formal_authority_occurrence_ids": list(
                validated_formal_authority["delivery_occurrence_finding_ids"]
            ),
            "evaluator_submission_occurrence_ids": list(
                validated_formal_authority["delivery_occurrence_finding_ids"]
            ),
        },
        canonical_scopes={
            **{
                name: list(values)
                for name, values in canonical_scope.items()
            },
            "evaluator_submission_ids": list(
                validated_canonical_registry["canonical_defect_ids"]
            ),
        },
    )
    try:
        validated_identity_consistency = validate_defect_identity_consistency(
            evaluator_identity_consistency,
            required_occurrence_scopes={
                "delivery_gate_ids",
                "formal_authority_occurrence_ids",
                "registry_occurrence_ids",
                "evaluator_submission_occurrence_ids",
            },
            required_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
                "evaluator_submission_ids",
            },
            allowed_occurrence_scopes=allowed_occurrence_scopes,
            allowed_canonical_scopes=allowed_canonical_scopes,
        )
    except CanonicalDefectRegistryError as exc:
        raise EvaluationContractError(
            f"evaluator_identity_consistency_invalid:{exc}"
        ) from exc
    if validated_trace_ledger is not None:
        trace_expected = {
            "campaign_id": validated_attempt_ledger["campaign_id"],
            "attempt_ledger_fingerprint": validated_attempt_ledger[
                "ledger_fingerprint"
            ],
        }
        for field, value in trace_expected.items():
            if str(validated_trace_ledger.get(field) or "").strip() != value:
                raise EvaluationContractError(
                    f"trace_ledger_{field}_mismatch"
                )
        trace_occurrence_ids = sorted(
            str(value or "").strip()
            for value in validated_trace_ledger.get(
                "delivery_occurrence_finding_ids"
            ) or []
            if str(value or "").strip()
        )
        if trace_occurrence_ids != validated_formal_authority[
            "delivery_occurrence_finding_ids"
        ]:
            raise EvaluationContractError(
                "trace_ledger_delivery_occurrence_ids_mismatch"
            )

    metrics: dict[str, Any] = {}
    if measurement_status == "MEASURED" and target.expectation == "seeded_defects":
        compute_benchmark, compute_stage_loss_matrix = _load_external_benchmark_compute()
        ground_truth_path = _resolve_ref(target.ground_truth_ref, manifest.manifest_path)
        metrics = compute_benchmark(
            target.project_id,
            canonical_findings,
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
        matched_bug_ids = list(metrics.get("matched_bug_ids") or [])
        metrics.pop("ground_truth_source", None)
        # Isolate hidden ground truth: receipts expose aggregate counts only.
        for _leak_key in (
            "matched_bugs",
            "missed_bug_ids",
            "bug_type_breakdown",
            "risk_family_breakdown",
            "false_positive_findings",
            "true_positive_findings",
        ):
            metrics.pop(_leak_key, None)
        metrics["ground_truth_fingerprint"] = fingerprints.get("ground_truth_fingerprint", "")
        metrics["ground_truth_bug_count"] = int(metrics.get("ground_truth_bug_count") or 0)
        metrics["canonical_defects_evaluated"] = len(canonical_findings)
        metrics["delivery_occurrences_verified"] = len(deliverable_occurrences)
        metrics["non_delivery_findings_excluded"] = 0
        if validated_trace_ledger is not None and metrics.get("benchmark_active") is True:
            metrics["stage_loss_diagnostics"] = compute_stage_loss_matrix(
                ground_truth_path=ground_truth_path,
                candidates=candidates or [],
                trace_ledger=validated_trace_ledger,
                delivered_bug_ids=matched_bug_ids,
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
        metrics["aggregate_only"] = True
    elif measurement_status == "MEASURED":
        high_value = [
            item
            for item in canonical_findings
            if str(item.get("severity") or "").strip().lower() in {"p0", "p1", "critical", "high"}
        ]
        metrics = {
            "benchmark_active": False,
            "ground_truth_available": False,
            "clean_evaluation_active": True,
            "customer_deliverable_false_positives": len(canonical_findings),
            "critical_high_false_positives": len(high_value),
        }

    trace_ledger_projection = (
        {
            "schema_version": validated_trace_ledger.get("schema_version"),
            "ledger_fingerprint": validated_trace_ledger.get("ledger_fingerprint"),
            "trace_count": int(validated_trace_ledger.get("trace_count") or 0),
            "attempt_count": int(validated_trace_ledger.get("attempt_count") or 0),
            "redaction_contract": dict(
                validated_trace_ledger.get("redaction_contract") or {}
            ),
        }
        if validated_trace_ledger is not None
        else {
            "schema_version": "qualibug.discovery-trace-ledger.v3",
            "status": "NOT_PROVIDED",
            "ledger_fingerprint": "",
            "trace_count": 0,
            "attempt_count": 0,
        }
    )
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
        "campaign_id": validated_mainline["campaign_id"],
        "policy_id": policy_id,
        "policy_identity": {
            "policy_id": policy_id,
            "policy_version": validated_mainline["policy_version"],
            "strategy_fingerprint": policy_strategy_fingerprint,
            "mainline_contract_fingerprint": validated_mainline[
                "contract_fingerprint"
            ],
        },
        "evaluation_mode": evaluation_mode,
        "measurement_status": measurement_status,
        "not_measured_reason": not_measured_reason,
        "pipeline_health": dict(pipeline_health),
        "metrics": metrics,
        "operational_metrics": dict(operational_metrics),
        "fixture_governance": dict(fixture_governance or {}),
        "execution_attestation": validated_execution_attestation,
        "trace_ledger_projection": trace_ledger_projection,
        "formal_delivery_authority": validated_formal_authority,
        "canonical_defect_registry": validated_canonical_registry,
        "defect_identity_consistency": validated_identity_consistency,
    }
    try:
        return seal_evaluator_artifact(
            receipt,
            signing_key=receipt_signing_key,
            domain=RECEIPT_SCHEMA,
            fingerprint_field=RECEIPT_FINGERPRINT_FIELD,
            authentication_field=RECEIPT_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise EvaluationContractError(
            f"evaluation receipt authentication failed: {exc}"
        ) from exc


def _assert_receipt_integrity(
    receipt: dict[str, Any],
    *,
    target_id: str,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> None:
    try:
        verify_evaluator_artifact(
            receipt,
            signing_key=receipt_signing_key,
            domain=RECEIPT_SCHEMA,
            fingerprint_field=RECEIPT_FINGERPRINT_FIELD,
            authentication_field=RECEIPT_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise EvaluationContractError(
            f"evaluation receipt authentication failed for target {target_id}: {exc}"
        ) from exc
    try:
        authority = validate_formal_delivery_authority_receipt(
            dict(receipt.get("formal_delivery_authority") or {})
        )
    except FormalDeliveryAuthorityError as exc:
        raise EvaluationContractError(
            f"formal delivery authority invalid for target {target_id}: {exc}"
        ) from exc
    expected_authority_identity = {
        "run_id": _required_text(receipt.get("run_id"), f"{target_id}.run_id"),
        "campaign_id": _required_text(
            receipt.get("campaign_id"),
            f"{target_id}.campaign_id",
        ),
        "target_id": target_id,
    }
    for field, value in expected_authority_identity.items():
        if authority.get(field) != value:
            raise EvaluationContractError(
                f"formal delivery authority {field} mismatch for target: {target_id}"
            )
    policy_identity = receipt.get("policy_identity")
    policy_identity_fields = {
        "policy_id",
        "policy_version",
        "strategy_fingerprint",
        "mainline_contract_fingerprint",
    }
    if not isinstance(policy_identity, dict) or set(policy_identity) != (
        policy_identity_fields
    ):
        raise EvaluationContractError(
            f"evaluation receipt policy identity invalid for target: {target_id}"
        )
    expected_policy_identity = {
        "policy_id": _required_text(
            receipt.get("policy_id"), f"{target_id}.policy_id"
        ),
        "policy_version": authority["policy_version"],
        "mainline_contract_fingerprint": authority[
            "mainline_contract_fingerprint"
        ],
    }
    strategy_fingerprint = str(
        policy_identity.get("strategy_fingerprint") or ""
    ).strip().lower()
    expected_policy_identity["strategy_fingerprint"] = strategy_fingerprint
    if policy_identity != expected_policy_identity:
        raise EvaluationContractError(
            f"evaluation receipt policy identity mismatch for target: {target_id}"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", strategy_fingerprint):
        raise EvaluationContractError(
            "evaluation receipt strategy fingerprint invalid for target: "
            f"{target_id}"
        )
    attestation = receipt.get("execution_attestation")
    if not isinstance(attestation, dict) or attestation.get(
        "schema_version"
    ) != EXECUTION_ATTESTATION_SCHEMA:
        raise EvaluationContractError(
            f"evaluation receipt execution attestation invalid: {target_id}"
        )
    attestation_status = str(attestation.get("status") or "").strip()
    if receipt.get("measurement_status") == "MEASURED" and (
        attestation_status != "VERIFIED"
    ):
        raise EvaluationContractError(
            f"measured receipt lacks verified execution attestation: {target_id}"
        )
    if attestation_status == "VERIFIED":
        try:
            verify_evaluator_artifact(
                attestation,
                signing_key=receipt_signing_key,
                domain=EXECUTION_ATTESTATION_SCHEMA,
                fingerprint_field=ATTESTATION_FINGERPRINT_FIELD,
                authentication_field=ATTESTATION_AUTHENTICATION_FIELD,
            )
        except EvaluatorReceiptAuthError as exc:
            raise EvaluationContractError(
                f"execution attestation authentication failed for target "
                f"{target_id}: {exc}"
            ) from exc
        expected_attestation_identity = {
            "run_id": expected_authority_identity["run_id"],
            "campaign_id": expected_authority_identity["campaign_id"],
            "target_id": target_id,
            "policy_identity": {
                "policy_id": expected_policy_identity["policy_id"],
                "policy_version": expected_policy_identity["policy_version"],
                "strategy_fingerprint": expected_policy_identity[
                    "strategy_fingerprint"
                ],
            },
            "mainline_contract_fingerprint": expected_policy_identity[
                "mainline_contract_fingerprint"
            ],
        }
        for field, value in expected_attestation_identity.items():
            if attestation.get(field) != value:
                raise EvaluationContractError(
                    f"execution attestation {field} mismatch for target: "
                    f"{target_id}"
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
    values_by_field: dict[str, list[float]] = {
        field: [] for field in _REQUIRED_OPERATIONAL_FIELDS
    }
    for receipt in receipts:
        raw = receipt.get("operational_metrics")
        raw = raw if isinstance(raw, dict) else {}
        missing_fields = [
            field
            for field in _REQUIRED_OPERATIONAL_FIELDS
            if field not in raw or raw[field] is None
        ]
        if missing_fields:
            missing.append({"target_id": receipt.get("target_id"), "fields": missing_fields})
        for field in _REQUIRED_OPERATIONAL_FIELDS:
            if field not in raw or raw[field] is None:
                continue
            parsed = _finite_number(
                raw[field],
                f"{receipt.get('target_id')}.operational_metrics.{field}",
            )
            if field in {
                "execution_success_rate",
                "engine_success_rate",
                "duplicate_rate",
            } and parsed > 1:
                raise EvaluationContractError(
                    f"{receipt.get('target_id')}.{field} must be between 0 and 1"
                )
            values_by_field[field].append(parsed)

    field_completeness = {
        field: bool(receipts) and len(values) == len(receipts)
        for field, values in values_by_field.items()
    }
    complete = bool(receipts) and all(field_completeness.values())
    seeded_true_positives = sum(
        int((item.get("metrics") or {}).get("true_positives") or 0)
        for item in receipts
        if item.get("expectation") == "seeded_defects" and item.get("measurement_status") == "MEASURED"
    )
    total_cost = (
        sum(values_by_field["estimated_cost_usd"])
        if field_completeness["estimated_cost_usd"]
        else None
    )
    def total(field: str) -> float | None:
        if not field_completeness[field]:
            return None
        return sum(values_by_field[field])

    def mean(field: str) -> float | None:
        if not field_completeness[field]:
            return None
        return _mean(values_by_field[field])

    return {
        "complete": complete,
        "missing_fields": missing,
        "field_completeness": field_completeness,
        "total_wall_clock_seconds": (
            round(float(total("wall_clock_seconds")), 4)
            if total("wall_clock_seconds") is not None
            else None
        ),
        "total_estimated_cost_usd": round(float(total_cost), 6) if total_cost is not None else None,
        "total_request_count": (
            int(total("request_count"))
            if total("request_count") is not None
            else None
        ),
        "production_http_requests": (
            int(total("production_http_requests"))
            if total("production_http_requests") is not None
            else None
        ),
        "cleanup_failures": (
            int(total("cleanup_failures"))
            if total("cleanup_failures") is not None
            else None
        ),
        "safety_incidents": (
            int(total("safety_incidents"))
            if total("safety_incidents") is not None
            else None
        ),
        "dirty_test_environments": (
            int(total("dirty_test_environments"))
            if total("dirty_test_environments") is not None
            else None
        ),
        "execution_success_rate": mean("execution_success_rate"),
        "engine_success_rate": mean("engine_success_rate"),
        "duplicate_rate": mean("duplicate_rate"),
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
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
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
    policy_identities: set[tuple[str, str, str]] = set()
    target_mainline_contract_fingerprints: dict[str, str] = {}
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA:
            raise EvaluationContractError("all evaluation receipts must use the current receipt schema")
        if receipt.get("dataset_id") != manifest.dataset_id or receipt.get("dataset_version") != manifest.dataset_version:
            raise EvaluationContractError("evaluation receipt dataset identity does not match manifest")
        if receipt.get("dataset_manifest_fingerprint") != manifest.manifest_fingerprint:
            raise EvaluationContractError("evaluation receipt manifest fingerprint does not match frozen manifest")
        target_id = _required_text(receipt.get("target_id"), "receipt.target_id")
        _assert_receipt_integrity(
            receipt,
            target_id=target_id,
            receipt_signing_key=receipt_signing_key,
        )
        policy_identity = dict(receipt.get("policy_identity") or {})
        policy_identities.add((
            _required_text(
                policy_identity.get("policy_id"),
                f"{target_id}.policy_identity.policy_id",
            ),
            _required_text(
                policy_identity.get("policy_version"),
                f"{target_id}.policy_identity.policy_version",
            ),
            str(
                policy_identity.get("strategy_fingerprint") or ""
            ).strip().lower(),
        ))
        target_mainline_contract_fingerprints[target_id] = _required_text(
            policy_identity.get("mainline_contract_fingerprint"),
            f"{target_id}.policy_identity.mainline_contract_fingerprint",
        )
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
        for field in ("project_id", "industry", "split", "expectation"):
            if receipt.get(field) != getattr(target, field):
                raise EvaluationContractError(
                    f"{field} drift for target: {target_id}"
                )
        by_target[target_id] = receipt

    if len(policy_identities) > 1:
        raise EvaluationContractError(
            "all evaluation receipts in one report must use one policy identity"
        )
    report_policy_identity = next(
        iter(policy_identities), ("", "", "")
    )

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
    report = {
        "schema_version": REPORT_SCHEMA,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "dataset_manifest_fingerprint": manifest.manifest_fingerprint,
        "policy_id": next(iter(policy_ids), ""),
        "policy_identity": {
            "policy_id": report_policy_identity[0],
            "policy_version": report_policy_identity[1],
            "strategy_fingerprint": report_policy_identity[2],
            "target_mainline_contract_fingerprints": dict(
                sorted(target_mainline_contract_fingerprints.items())
            ),
        },
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
    try:
        return seal_evaluator_artifact(
            report,
            signing_key=receipt_signing_key,
            domain=REPORT_SCHEMA,
            fingerprint_field=REPORT_FINGERPRINT_FIELD,
            authentication_field=REPORT_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise EvaluationContractError(
            f"evaluation report authentication failed: {exc}"
        ) from exc


def _assert_report_authentication(
    report: dict[str, Any],
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> None:
    if not isinstance(report, dict) or report.get("schema_version") != REPORT_SCHEMA:
        raise EvaluationContractError("evaluation report uses an unsupported schema")
    try:
        verify_evaluator_artifact(
            report,
            signing_key=receipt_signing_key,
            domain=REPORT_SCHEMA,
            fingerprint_field=REPORT_FINGERPRINT_FIELD,
            authentication_field=REPORT_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise EvaluationContractError(
            f"evaluation report authentication failed: {exc}"
        ) from exc


def _assert_report(
    manifest: EvaluationManifest,
    report: dict[str, Any],
    *,
    evaluation_mode: str,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> None:
    _assert_report_authentication(
        report,
        receipt_signing_key=receipt_signing_key,
    )
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
    receipts = report.get("target_receipts")
    if not isinstance(receipts, list):
        raise EvaluationContractError(
            f"{evaluation_mode} evaluation report target receipts missing"
        )
    rebuilt = aggregate_evaluation_receipts(
        manifest,
        receipts,
        receipt_signing_key=receipt_signing_key,
    )
    rebuilt_without_authentication = {
        key: value
        for key, value in rebuilt.items()
        if key != REPORT_AUTHENTICATION_FIELD
    }
    report_without_authentication = {
        key: value
        for key, value in report.items()
        if key != REPORT_AUTHENTICATION_FIELD
    }
    if rebuilt_without_authentication != report_without_authentication:
        raise EvaluationContractError(
            f"{evaluation_mode} evaluation report rebuild mismatch"
        )


def _report_target_fingerprints(
    report: dict[str, Any],
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, tuple[str, str, str, str]]:
    result: dict[str, tuple[str, str, str, str]] = {}
    for receipt in report.get("target_receipts") or []:
        if not isinstance(receipt, dict):
            raise EvaluationContractError("target receipt must be an object")
        target_id = _required_text(receipt.get("target_id"), "target_receipt.target_id")
        _assert_receipt_integrity(
            receipt,
            target_id=target_id,
            receipt_signing_key=receipt_signing_key,
        )
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
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Prove four real, dataset-identical runs before policy promotion."""

    reports = (
        (champion_replay, "replay"),
        (challenger_replay, "replay"),
        (champion_shadow, "shadow"),
        (challenger_shadow, "shadow"),
    )
    for report, mode in reports:
        _assert_report(
            manifest,
            report,
            evaluation_mode=mode,
            receipt_signing_key=receipt_signing_key,
        )
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
    champion_policy_versions = {
        str(dict(report.get("policy_identity") or {}).get("policy_version") or "").strip()
        for report in (champion_replay, champion_shadow)
    }
    challenger_policy_versions = {
        str(dict(report.get("policy_identity") or {}).get("policy_version") or "").strip()
        for report in (challenger_replay, challenger_shadow)
    }
    if len(champion_policy_versions) != 1 or "" in champion_policy_versions:
        raise EvaluationContractError(
            "champion replay and shadow reports must use one policy version"
        )
    if len(challenger_policy_versions) != 1 or "" in challenger_policy_versions:
        raise EvaluationContractError(
            "challenger replay and shadow reports must use one policy version"
        )
    champion_strategy_fingerprints = {
        str(
            dict(report.get("policy_identity") or {}).get(
                "strategy_fingerprint"
            )
            or ""
        ).strip()
        for report in (champion_replay, champion_shadow)
    }
    challenger_strategy_fingerprints = {
        str(
            dict(report.get("policy_identity") or {}).get(
                "strategy_fingerprint"
            )
            or ""
        ).strip()
        for report in (challenger_replay, challenger_shadow)
    }
    if (
        len(champion_strategy_fingerprints) != 1
        or "" in champion_strategy_fingerprints
    ):
        raise EvaluationContractError(
            "champion replay and shadow reports must bind one strategy"
        )
    if (
        len(challenger_strategy_fingerprints) != 1
        or "" in challenger_strategy_fingerprints
    ):
        raise EvaluationContractError(
            "challenger replay and shadow reports must bind one strategy"
        )

    fingerprints = [
        _report_target_fingerprints(
            report,
            receipt_signing_key=receipt_signing_key,
        )
        for report, _ in reports
    ]
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
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Flatten the SSOT reports into the strict policy-promotion metric schema."""

    _assert_report_authentication(
        replay_report,
        receipt_signing_key=receipt_signing_key,
    )
    _assert_report_authentication(
        shadow_report,
        receipt_signing_key=receipt_signing_key,
    )
    if replay_report.get("evaluation_mode") != "replay" or shadow_report.get("evaluation_mode") != "shadow":
        raise EvaluationContractError("policy metrics require one replay and one shadow report")
    if replay_report.get("policy_id") != shadow_report.get("policy_id"):
        raise EvaluationContractError("replay and shadow reports must belong to the same policy")
    replay_policy_identity = dict(replay_report.get("policy_identity") or {})
    shadow_policy_identity = dict(shadow_report.get("policy_identity") or {})
    for field in ("policy_id", "policy_version", "strategy_fingerprint"):
        if replay_policy_identity.get(field) != shadow_policy_identity.get(field):
            raise EvaluationContractError(
                f"replay and shadow reports must use the same policy {field}"
            )
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


def _authenticated_report_validation_manifest(
    comparison: dict[str, Any],
    report: dict[str, Any],
) -> EvaluationManifest:
    """Reconstruct only the GT-free facts needed to revalidate signed reports."""

    receipts = report.get("target_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise EvaluationContractError(
            "authenticated comparison report target receipts missing"
        )
    targets: list[EvaluationTarget] = []
    target_fingerprints: dict[str, dict[str, str]] = {}
    seen_target_ids: set[str] = set()
    for index, raw_receipt in enumerate(receipts):
        receipt = _as_dict(
            raw_receipt,
            f"comparison.target_receipts[{index}]",
        )
        target_id = _required_text(
            receipt.get("target_id"),
            f"comparison.target_receipts[{index}].target_id",
        )
        if target_id in seen_target_ids:
            raise EvaluationContractError(
                f"duplicate authenticated comparison target: {target_id}"
            )
        seen_target_ids.add(target_id)
        split = _required_text(
            receipt.get("split"), f"{target_id}.split"
        ).lower()
        expectation = _required_text(
            receipt.get("expectation"), f"{target_id}.expectation"
        ).lower()
        environment_type = _required_text(
            receipt.get("environment_type"),
            f"{target_id}.environment_type",
        ).lower()
        if split not in VALID_SPLITS:
            raise EvaluationContractError(
                f"authenticated comparison target split invalid: {target_id}"
            )
        if expectation not in VALID_EXPECTATIONS:
            raise EvaluationContractError(
                "authenticated comparison target expectation invalid: "
                f"{target_id}"
            )
        if environment_type not in NON_PRODUCTION_ENVIRONMENTS:
            raise EvaluationContractError(
                "authenticated comparison target is not explicitly "
                f"non-production: {target_id}"
            )
        targets.append(
            EvaluationTarget(
                target_id=target_id,
                project_id=_required_text(
                    receipt.get("project_id"), f"{target_id}.project_id"
                ),
                industry=_required_text(
                    receipt.get("industry"), f"{target_id}.industry"
                ),
                split=split,
                expectation=expectation,
                environment_ref=_required_text(
                    receipt.get("environment_ref"),
                    f"{target_id}.environment_ref",
                ),
                environment_type=environment_type,
                # Report validation never opens runtime or hidden-GT artifacts.
                input_bundle_ref="authenticated-report-only",
                fixture_snapshot_ref="authenticated-report-only",
                context_artifact_ref="authenticated-report-only",
                ground_truth_ref="",
            )
        )
        target_fingerprints[target_id] = {
            field: _required_text(receipt.get(field), f"{target_id}.{field}")
            for field in (
                "runtime_fingerprint",
                "input_fingerprint",
                "fixture_fingerprint",
                "context_fingerprint",
            )
        }
    return EvaluationManifest(
        dataset_id=_required_text(
            comparison.get("dataset_id"), "comparison.dataset_id"
        ),
        dataset_version=_required_text(
            comparison.get("dataset_version"),
            "comparison.dataset_version",
        ),
        targets=tuple(targets),
        manifest_path=Path("<authenticated-report-validation>"),
        manifest_fingerprint=_required_text(
            comparison.get("dataset_manifest_fingerprint"),
            "comparison.dataset_manifest_fingerprint",
        ),
        target_fingerprints=target_fingerprints,
    )


def _validated_comparison_policy_identity(
    raw: Any,
    *,
    role: str,
) -> dict[str, str]:
    identity = _as_dict(raw, f"comparison.{role}")
    expected_fields = {
        "policy_id",
        "policy_version",
        "parent_policy_version",
        "strategy_fingerprint",
    }
    if set(identity) != expected_fields:
        raise EvaluationContractError(
            f"comparison {role} policy identity fields invalid"
        )
    normalized = {
        "policy_id": _required_text(
            identity.get("policy_id"), f"comparison.{role}.policy_id"
        ),
        "policy_version": _required_text(
            identity.get("policy_version"),
            f"comparison.{role}.policy_version",
        ),
        "parent_policy_version": str(
            identity.get("parent_policy_version") or ""
        ).strip(),
        "strategy_fingerprint": _required_text(
            identity.get("strategy_fingerprint"),
            f"comparison.{role}.strategy_fingerprint",
        ).lower(),
    }
    if not re.fullmatch(r"[0-9a-f]{64}", normalized["strategy_fingerprint"]):
        raise EvaluationContractError(
            f"comparison {role} strategy fingerprint invalid"
        )
    return normalized


def _assert_canonical_rebuild(
    actual: Any,
    expected: Any,
    *,
    field: str,
) -> None:
    if _canonical_json(actual) != _canonical_json(expected):
        raise EvaluationContractError(f"comparison {field} rebuild mismatch")


def validate_authenticated_policy_comparison(
    comparison: dict[str, Any],
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
    expected_champion_identity: dict[str, Any] | None = None,
    expected_challenger_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Revalidate every signed input and recompute the promotion decision.

    This validator deliberately reconstructs a GT-free manifest projection
    from authenticated target receipts. Policy activation therefore never
    needs to open evaluator-private ground truth, while still refusing a
    comparison whose reports, metrics, evidence, or gate decision drifted.
    """

    if not isinstance(comparison, dict):
        raise EvaluationContractError("policy comparison must be an object")
    expected_fields = {
        "schema_version",
        "created_at_utc",
        "evaluation_id",
        "dataset_id",
        "dataset_version",
        "dataset_manifest_fingerprint",
        "champion",
        "challenger",
        "observed_execution",
        "estimated_metrics_used",
        "report_refs",
        "report_fingerprints",
        "evaluation_evidence",
        "champion_metrics",
        "challenger_metrics",
        "promotion_decision",
        "activation_performed",
        POLICY_COMPARISON_FINGERPRINT_FIELD,
        POLICY_COMPARISON_AUTHENTICATION_FIELD,
    }
    if comparison.get("schema_version") != POLICY_COMPARISON_SCHEMA:
        raise EvaluationContractError(
            "policy comparison uses an unsupported strict schema"
        )
    try:
        verify_evaluator_artifact(
            comparison,
            signing_key=receipt_signing_key,
            domain=POLICY_COMPARISON_SCHEMA,
            fingerprint_field=POLICY_COMPARISON_FINGERPRINT_FIELD,
            authentication_field=POLICY_COMPARISON_AUTHENTICATION_FIELD,
        )
    except EvaluatorReceiptAuthError as exc:
        raise EvaluationContractError(
            f"policy comparison authentication failed: {exc}"
        ) from exc
    if set(comparison) != expected_fields:
        raise EvaluationContractError(
            "policy comparison fields do not match the strict schema"
        )
    for field in (
        "created_at_utc",
        "evaluation_id",
        "dataset_id",
        "dataset_version",
        "dataset_manifest_fingerprint",
    ):
        _required_text(comparison.get(field), f"comparison.{field}")
    if comparison.get("observed_execution") is not True:
        raise EvaluationContractError(
            "policy comparison is not observed execution evidence"
        )
    if comparison.get("estimated_metrics_used") is not False:
        raise EvaluationContractError(
            "policy comparison contains estimated metrics"
        )
    if comparison.get("activation_performed") is not False:
        raise EvaluationContractError(
            "policy comparison must be validated before activation"
        )

    champion_identity = _validated_comparison_policy_identity(
        comparison.get("champion"), role="champion"
    )
    challenger_identity = _validated_comparison_policy_identity(
        comparison.get("challenger"), role="challenger"
    )
    if champion_identity["policy_id"] == challenger_identity["policy_id"]:
        raise EvaluationContractError(
            "comparison champion and challenger policy identities must differ"
        )
    if (
        challenger_identity["parent_policy_version"]
        != champion_identity["policy_version"]
    ):
        raise EvaluationContractError(
            "comparison challenger parent policy version mismatch"
        )
    for role, actual, expected in (
        ("champion", champion_identity, expected_champion_identity),
        ("challenger", challenger_identity, expected_challenger_identity),
    ):
        if expected is not None:
            _assert_canonical_rebuild(
                actual,
                _validated_comparison_policy_identity(expected, role=role),
                field=f"{role} policy identity",
            )

    report_refs = _as_dict(
        comparison.get("report_refs"), "comparison.report_refs"
    )
    report_fingerprints = _as_dict(
        comparison.get("report_fingerprints"),
        "comparison.report_fingerprints",
    )
    if (
        set(report_refs) != POLICY_COMPARISON_REPORT_KEYS
        or set(report_fingerprints) != POLICY_COMPARISON_REPORT_KEYS
    ):
        raise EvaluationContractError(
            "policy comparison requires all four authenticated reports"
        )

    reports: dict[str, dict[str, Any]] = {}
    resolved_report_paths: set[Path] = set()
    for name in sorted(POLICY_COMPARISON_REPORT_KEYS):
        report_path = Path(
            _required_text(
                report_refs.get(name), f"comparison.report_refs.{name}"
            )
        )
        if not report_path.is_absolute():
            raise EvaluationContractError(
                f"comparison report reference must be absolute: {name}"
            )
        report_path = report_path.resolve()
        if report_path in resolved_report_paths:
            raise EvaluationContractError(
                "comparison report references must identify four files"
            )
        resolved_report_paths.add(report_path)
        if not report_path.is_file():
            raise EvaluationContractError(
                f"comparison report reference missing: {name}"
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvaluationContractError(
                f"comparison report is invalid JSON: {name}"
            ) from exc
        if not isinstance(report, dict):
            raise EvaluationContractError(
                f"comparison report must be an object: {name}"
            )
        _assert_report_authentication(
            report,
            receipt_signing_key=receipt_signing_key,
        )
        actual_fingerprint = _sha256_bytes(_canonical_json(report))
        claimed_fingerprint = _required_text(
            report_fingerprints.get(name),
            f"comparison.report_fingerprints.{name}",
        ).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", claimed_fingerprint):
            raise EvaluationContractError(
                f"comparison report fingerprint invalid: {name}"
            )
        if actual_fingerprint != claimed_fingerprint:
            raise EvaluationContractError(
                f"comparison report fingerprint mismatch: {name}"
            )
        reports[name] = report

    validation_manifest = _authenticated_report_validation_manifest(
        comparison,
        reports["champion_replay"],
    )
    for name, report in reports.items():
        mode = "replay" if name.endswith("_replay") else "shadow"
        _assert_report(
            validation_manifest,
            report,
            evaluation_mode=mode,
            receipt_signing_key=receipt_signing_key,
        )
        role = "champion" if name.startswith("champion_") else "challenger"
        expected_identity = (
            champion_identity if role == "champion" else challenger_identity
        )
        report_identity = _as_dict(
            report.get("policy_identity"), f"{name}.policy_identity"
        )
        if report_identity.get("policy_id") != expected_identity["policy_id"]:
            raise EvaluationContractError(
                f"comparison {role} report policy identity mismatch"
            )
        if (
            report_identity.get("policy_version")
            != expected_identity["policy_version"]
        ):
            raise EvaluationContractError(
                f"comparison {role} report policy version mismatch"
            )
        if (
            report_identity.get("strategy_fingerprint")
            != expected_identity["strategy_fingerprint"]
        ):
            raise EvaluationContractError(
                f"comparison {role} report strategy fingerprint mismatch"
            )

    rebuilt_evidence = build_paired_evaluation_evidence(
        validation_manifest,
        champion_replay=reports["champion_replay"],
        challenger_replay=reports["challenger_replay"],
        champion_shadow=reports["champion_shadow"],
        challenger_shadow=reports["challenger_shadow"],
        receipt_signing_key=receipt_signing_key,
    )
    rebuilt_champion_metrics = policy_metrics_from_evaluation_reports(
        reports["champion_replay"],
        reports["champion_shadow"],
        receipt_signing_key=receipt_signing_key,
    )
    rebuilt_challenger_metrics = policy_metrics_from_evaluation_reports(
        reports["challenger_replay"],
        reports["challenger_shadow"],
        receipt_signing_key=receipt_signing_key,
    )
    rebuilt_decision = PolicyPromotionGate().evaluate(
        rebuilt_champion_metrics,
        rebuilt_challenger_metrics,
        rebuilt_evidence,
    )
    for field, expected in (
        ("evaluation_evidence", rebuilt_evidence),
        ("champion_metrics", rebuilt_champion_metrics),
        ("challenger_metrics", rebuilt_challenger_metrics),
        ("promotion_decision", rebuilt_decision),
    ):
        _assert_canonical_rebuild(
            comparison.get(field), expected, field=field
        )
    return dict(comparison)


def _atomic_write_json(destination: Path, payload: dict[str, Any]) -> None:
    """Atomically write JSON, including Windows MAX_PATH boundary paths."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".q-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary is None:
            raise RuntimeError("atomic JSON temporary path was not created")
        os.replace(_filesystem_path(temporary), _filesystem_path(destination))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _filesystem_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def persist_evaluation_receipt(
    receipt: dict[str, Any],
    output_root: Path | str,
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> Path:
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
        _assert_receipt_integrity(
            receipt,
            target_id=target,
            receipt_signing_key=receipt_signing_key,
        )
        return path
    _assert_receipt_integrity(
        receipt,
        target_id=target,
        receipt_signing_key=receipt_signing_key,
    )
    _atomic_write_json(path, receipt)
    return path


def persist_evaluation_report(
    report: dict[str, Any],
    path: Path | str,
    *,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> Path:
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
        _assert_report_authentication(
            report,
            receipt_signing_key=receipt_signing_key,
        )
        return destination
    _assert_report_authentication(
        report,
        receipt_signing_key=receipt_signing_key,
    )
    _atomic_write_json(destination, report)
    return destination


# ---------------------------------------------------------------------------
# Commercial Goal stage gates (SSOT thresholds from DISCOVERY_HARNESS_EVOLUTION_GOAL)
# ---------------------------------------------------------------------------

GOAL_GATE_STATUS_SCHEMA = "qualibug.discovery-goal-gate-status.v1"

# Frozen product ports — discovery/harness evolution must never retarget these.
from .version import DEFAULT_PRIVATE_PILOT_PORT as PRODUCT_BACKEND_PORT
PRODUCT_FRONTEND_PORT = 5174

# Absolute measured thresholds. Missing measurement never becomes a pass.
CAPABILITY_BREAKTHROUGH_THRESHOLDS: dict[str, float | int] = {
    "held_out_macro_industry_recall": 0.30,
    "held_out_micro_precision": 0.50,
    "reproduction_success_rate": 0.90,
    "held_out_min_industry_recall": 0.15,
    "clean_critical_high_false_positives": 0,
    "unit_cost_improvement_ratio": 0.40,
}

CONTROLLED_COMMERCIAL_PILOT_THRESHOLDS: dict[str, float | int] = {
    "held_out_macro_industry_recall": 0.50,
    "held_out_micro_precision": 0.70,
    "reproduction_success_rate": 0.95,
    "cleanup_failures": 0,
    "production_http_requests": 0,
    "clean_critical_high_false_positives": 0,
}

FULL_AUTONOMY_GA_THRESHOLDS: dict[str, float | int] = {
    "held_out_macro_industry_recall": 0.70,
    "held_out_micro_precision": 0.80,
    "held_out_min_industry_recall": 0.50,
    "reproduction_success_rate": 0.97,
    "execution_success_rate": 0.95,
    "engine_success_rate": 0.95,
    "evidence_completeness_rate": 1.0,
    "cleanup_failures": 0,
    "production_http_requests": 0,
    "safety_incidents": 0,
    "dirty_test_environments": 0,
    "clean_critical_high_false_positives": 0,
}


def assess_implementation_stage_gates() -> dict[str, Any]:
    """Gate A/B/C inventory: required contracts must exist and be importable.

    These gates certify engineering readiness, not commercial discovery quality.
    Quality claims remain NOT_MEASURED until a private measured report is supplied.
    """

    from .customer_delivery_gate_v2 import validate_customer_delivery_gate_bundle
    from .discovery_harness_proposer import (
        propose_harness_candidates,
        validate_strategy_guardrails,
    )
    from .discovery_policy_evaluation_runner import DiscoveryPolicyEvaluationRunner
    from .discovery_trace_ledger import build_discovery_trace_ledger
    from .discovery_weakness_miner import mine_discovery_weaknesses
    from .sandbox_write_executor import is_production_environment

    gate_a_checks = [
        {
            "name": "evaluation_manifest_loader",
            "passed": callable(load_evaluation_manifest),
            "detail": "evaluator-private manifest loader with non-production fail-closed",
        },
        {
            "name": "runtime_view_redaction",
            "passed": callable(build_runtime_view),
            "detail": "discovery receives runtime view only; ground truth stays evaluator-private",
        },
        {
            "name": "completed_scan_evaluator",
            "passed": callable(evaluate_completed_scan),
            "detail": "formal customer-deliverable findings only enter commercial scoring",
        },
        {
            "name": "paired_promotion_evidence",
            "passed": callable(build_paired_evaluation_evidence),
            "detail": "champion/challenger replay+shadow fingerprints must match before promotion",
        },
        {
            "name": "policy_promotion_gate",
            "passed": callable(PolicyPromotionGate),
            "detail": "non-regressive measured promotion with hard safety blockers",
        },
        {
            "name": "external_evaluation_cli",
            "passed": Path(__file__).resolve().parents[1].joinpath("tools", "discovery_evaluation.py").is_file(),
            "detail": "external CLI is the only layer that opens hidden ground truth",
        },
        {
            "name": "customer_delivery_gate",
            "passed": callable(validate_customer_delivery_gate_bundle),
            "detail": "formal delivery requires immutable Gate-v2 evidence plus the attempt ledger",
        },
        {
            "name": "sandbox_write_fail_closed",
            "passed": callable(is_production_environment),
            "detail": "production and unknown environments remain fail-closed for writes",
        },
    ]
    gate_b_checks = [
        {
            "name": "trace_ledger_builder",
            "passed": callable(build_discovery_trace_ledger),
            "detail": "cross-stage redacted identity from generation through formal accounting",
        },
        {
            "name": "weakness_miner",
            "passed": callable(mine_discovery_weaknesses),
            "detail": "verifier-grounded failure clustering with editable-surface proposals",
        },
    ]
    gate_c_checks = [
        {
            "name": "bounded_harness_proposer",
            "passed": callable(propose_harness_candidates),
            "detail": "minimal evidence-bound StrategyBundle edits; frozen surfaces rejected",
        },
        {
            "name": "strategy_guardrails",
            "passed": callable(validate_strategy_guardrails),
            "detail": "timeout/token floors, hypothesis/worker caps, and evidence thresholds stay frozen",
        },
        {
            "name": "observed_policy_evaluation_runner",
            "passed": callable(DiscoveryPolicyEvaluationRunner),
            "detail": "four real champion/challenger replay+shadow passes; no estimated metrics",
        },
        {
            "name": "product_ports_frozen",
            "passed": PRODUCT_FRONTEND_PORT == 5174 and PRODUCT_BACKEND_PORT == 8088,
            "detail": f"frontend={PRODUCT_FRONTEND_PORT}, backend={PRODUCT_BACKEND_PORT}",
        },
    ]

    def _gate(gate_id: str, title: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
        passed = all(bool(item.get("passed")) for item in checks)
        return {
            "gate_id": gate_id,
            "title": title,
            "status": "PASSED" if passed else "FAILED",
            "passed": passed,
            "measurement_status": "IMPLEMENTED",
            "checks": checks,
        }

    return {
        "gate_a_evaluation_integrity": _gate(
            "A", "Evaluation integrity", gate_a_checks
        ),
        "gate_b_trace_and_weakness_mining": _gate(
            "B", "Trace and weakness mining", gate_b_checks
        ),
        "gate_c_bounded_proposal_and_real_runner": _gate(
            "C", "Bounded proposal and real runner", gate_c_checks
        ),
    }


def _metric_check(
    name: str,
    *,
    actual: Any,
    threshold: float | int,
    comparator: str,
    detail: str,
) -> dict[str, Any]:
    if actual is None:
        return {
            "name": name,
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "actual": None,
            "threshold": threshold,
            "comparator": comparator,
            "detail": detail,
            "reason": "metric_missing",
        }
    try:
        value = float(actual)
    except (TypeError, ValueError):
        return {
            "name": name,
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "actual": actual,
            "threshold": threshold,
            "comparator": comparator,
            "detail": detail,
            "reason": "metric_not_numeric",
        }
    if comparator == ">=":
        passed = value + 1e-12 >= float(threshold)
    elif comparator == "<=":
        passed = value - 1e-12 <= float(threshold)
    elif comparator == "==":
        passed = abs(value - float(threshold)) <= 1e-12
    else:
        raise EvaluationContractError(f"unsupported goal-gate comparator: {comparator}")
    return {
        "name": name,
        "passed": passed,
        "measurement_status": "MEASURED",
        "actual": value,
        "threshold": threshold,
        "comparator": comparator,
        "detail": detail,
        "reason": "" if passed else "threshold_not_met",
    }


def assess_measured_capability_gate(
    evaluation_report: dict[str, Any] | None,
    *,
    gate_id: str,
    thresholds: dict[str, float | int],
    baseline_cost_per_true_positive_usd: float | None = None,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Score one absolute commercial gate from a MEASURED aggregate report.

    Incomplete, degraded, or missing reports produce NOT_MEASURED — never a
    fabricated zero-Bug or zero-FP commercial claim.
    """

    titles = {
        "D": "Capability breakthrough",
        "PILOT": "Controlled commercial pilot exit",
        "GA": "Full-autonomy GA",
    }
    title = titles.get(gate_id, gate_id)
    if not isinstance(evaluation_report, dict):
        return {
            "gate_id": gate_id,
            "title": title,
            "status": "NOT_MEASURED",
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "reason": "evaluation_report_missing",
            "checks": [],
            "thresholds": dict(thresholds),
        }
    _assert_report_authentication(
        evaluation_report,
        receipt_signing_key=receipt_signing_key,
    )
    if evaluation_report.get("claim_status") != "MEASURED":
        return {
            "gate_id": gate_id,
            "title": title,
            "status": "NOT_MEASURED",
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "reason": "evaluation_report_not_measured",
            "claim_status": evaluation_report.get("claim_status"),
            "not_measured_targets": list(evaluation_report.get("not_measured_targets") or []),
            "checks": [],
            "thresholds": dict(thresholds),
        }
    if evaluation_report.get("evaluation_complete") is not True:
        return {
            "gate_id": gate_id,
            "title": title,
            "status": "NOT_MEASURED",
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "reason": "evaluation_incomplete",
            "checks": [],
            "thresholds": dict(thresholds),
        }
    shape = _as_dict(evaluation_report.get("commercial_shape"), "commercial_shape")
    if shape.get("commercial_shape_ready") is not True:
        return {
            "gate_id": gate_id,
            "title": title,
            "status": "NOT_MEASURED",
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "reason": "commercial_dataset_shape_not_ready",
            "commercial_shape": shape,
            "checks": [],
            "thresholds": dict(thresholds),
        }

    held_out = _as_dict(evaluation_report.get("held_out"), "held_out")
    clean = _as_dict(evaluation_report.get("clean"), "clean")
    evidence = _as_dict(evaluation_report.get("evidence_quality"), "evidence_quality")
    operational = _as_dict(evaluation_report.get("operational"), "operational")
    if operational.get("complete") is not True:
        return {
            "gate_id": gate_id,
            "title": title,
            "status": "NOT_MEASURED",
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "reason": "operational_metrics_incomplete",
            "checks": [],
            "thresholds": dict(thresholds),
        }

    checks: list[dict[str, Any]] = []
    metric_sources: dict[str, Any] = {
        "held_out_macro_industry_recall": evaluation_report.get("held_out_macro_industry_recall"),
        "held_out_micro_precision": held_out.get("micro_precision"),
        "held_out_min_industry_recall": evaluation_report.get("held_out_min_industry_recall"),
        "reproduction_success_rate": evidence.get("reproduction_success_rate"),
        "evidence_completeness_rate": evidence.get("evidence_completeness_rate"),
        "clean_critical_high_false_positives": clean.get("critical_high_false_positives"),
        "cleanup_failures": operational.get("cleanup_failures"),
        "production_http_requests": operational.get("production_http_requests"),
        "safety_incidents": operational.get("safety_incidents"),
        "dirty_test_environments": operational.get("dirty_test_environments"),
        "execution_success_rate": operational.get("execution_success_rate"),
        "engine_success_rate": operational.get("engine_success_rate"),
    }

    for name, threshold in thresholds.items():
        if name == "unit_cost_improvement_ratio":
            actual_cost = operational.get("cost_per_true_positive_usd")
            if baseline_cost_per_true_positive_usd is None:
                checks.append(
                    {
                        "name": name,
                        "passed": False,
                        "measurement_status": "NOT_MEASURED",
                        "actual": actual_cost,
                        "threshold": threshold,
                        "comparator": ">=",
                        "detail": "unit cost must improve >=40% vs frozen baseline",
                        "reason": "baseline_cost_per_true_positive_usd_missing",
                    }
                )
                continue
            if actual_cost is None or float(baseline_cost_per_true_positive_usd) <= 0:
                checks.append(
                    {
                        "name": name,
                        "passed": False,
                        "measurement_status": "NOT_MEASURED",
                        "actual": actual_cost,
                        "threshold": threshold,
                        "comparator": ">=",
                        "detail": "unit cost must improve >=40% vs frozen baseline",
                        "reason": "cost_per_true_positive_not_measurable",
                    }
                )
                continue
            improvement = 1.0 - (float(actual_cost) / float(baseline_cost_per_true_positive_usd))
            checks.append(
                _metric_check(
                    name,
                    actual=improvement,
                    threshold=threshold,
                    comparator=">=",
                    detail="unit cost improvement vs frozen baseline",
                )
            )
            continue

        comparator = (
            "<="
            if name
            in {
                "cleanup_failures",
                "production_http_requests",
                "safety_incidents",
                "dirty_test_environments",
                "clean_critical_high_false_positives",
            }
            else ">="
        )
        checks.append(
            _metric_check(
                name,
                actual=metric_sources.get(name),
                threshold=threshold,
                comparator=comparator,
                detail=f"absolute Goal gate threshold for {name}",
            )
        )

    measured_complete = all(item.get("measurement_status") == "MEASURED" for item in checks)
    passed = measured_complete and all(bool(item.get("passed")) for item in checks)
    if not measured_complete:
        status = "NOT_MEASURED"
        reason = "one_or_more_metrics_not_measured"
    elif passed:
        status = "PASSED"
        reason = ""
    else:
        status = "FAILED"
        reason = "absolute_thresholds_not_met"
    return {
        "gate_id": gate_id,
        "title": title,
        "status": status,
        "passed": passed,
        "measurement_status": "MEASURED" if measured_complete else "NOT_MEASURED",
        "reason": reason,
        "checks": checks,
        "thresholds": dict(thresholds),
        "dataset_id": evaluation_report.get("dataset_id"),
        "dataset_version": evaluation_report.get("dataset_version"),
        "policy_id": evaluation_report.get("policy_id"),
        "evaluation_mode": evaluation_report.get("evaluation_mode"),
    }


def assess_discovery_goal_status(
    *,
    evaluation_report: dict[str, Any] | None = None,
    baseline_cost_per_true_positive_usd: float | None = None,
    consecutive_non_regressive_windows: int | None = None,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Single machine-checkable status for the commercial discovery Goal.

    Gate A/B/C report engineering readiness. Gate D / pilot / GA report absolute
    measured quality and remain NOT_MEASURED until a complete private evaluation
    report is supplied. Never invent commercial quality numbers.
    """

    implementation = assess_implementation_stage_gates()
    gate_d = assess_measured_capability_gate(
        evaluation_report,
        gate_id="D",
        thresholds=CAPABILITY_BREAKTHROUGH_THRESHOLDS,
        baseline_cost_per_true_positive_usd=baseline_cost_per_true_positive_usd,
        receipt_signing_key=receipt_signing_key,
    )
    gate_pilot = assess_measured_capability_gate(
        evaluation_report,
        gate_id="PILOT",
        thresholds=CONTROLLED_COMMERCIAL_PILOT_THRESHOLDS,
        baseline_cost_per_true_positive_usd=None,
        receipt_signing_key=receipt_signing_key,
    )
    gate_ga = assess_measured_capability_gate(
        evaluation_report,
        gate_id="GA",
        thresholds=FULL_AUTONOMY_GA_THRESHOLDS,
        baseline_cost_per_true_positive_usd=None,
        receipt_signing_key=receipt_signing_key,
    )
    if consecutive_non_regressive_windows is None:
        ga_window_check = {
            "name": "consecutive_non_regressive_windows",
            "passed": False,
            "measurement_status": "NOT_MEASURED",
            "actual": None,
            "threshold": 3,
            "comparator": ">=",
            "detail": "GA requires at least three consecutive frozen evaluation windows with no measured regression",
            "reason": "consecutive_windows_not_supplied",
        }
    else:
        ga_window_check = _metric_check(
            "consecutive_non_regressive_windows",
            actual=consecutive_non_regressive_windows,
            threshold=3,
            comparator=">=",
            detail="GA requires at least three consecutive frozen evaluation windows with no measured regression",
        )
    gate_ga = {
        **gate_ga,
        "checks": list(gate_ga.get("checks") or []) + [ga_window_check],
    }
    ga_measured = all(
        item.get("measurement_status") == "MEASURED" for item in (gate_ga.get("checks") or [])
    )
    ga_passed = ga_measured and all(bool(item.get("passed")) for item in (gate_ga.get("checks") or []))
    if not ga_measured:
        gate_ga["status"] = "NOT_MEASURED"
        gate_ga["passed"] = False
        gate_ga["measurement_status"] = "NOT_MEASURED"
        gate_ga["reason"] = "one_or_more_metrics_not_measured"
    elif ga_passed:
        gate_ga["status"] = "PASSED"
        gate_ga["passed"] = True
        gate_ga["measurement_status"] = "MEASURED"
        gate_ga["reason"] = ""
    else:
        gate_ga["status"] = "FAILED"
        gate_ga["passed"] = False
        gate_ga["measurement_status"] = "MEASURED"
        gate_ga["reason"] = "absolute_thresholds_not_met"

    implementation_ready = all(
        bool(implementation[key]["passed"])
        for key in (
            "gate_a_evaluation_integrity",
            "gate_b_trace_and_weakness_mining",
            "gate_c_bounded_proposal_and_real_runner",
        )
    )
    # Higher commercial claims require every lower absolute gate to pass. A
    # missing Gate D baseline must not silently unlock pilot/GA eligibility.
    commercial_claim_status = "NOT_MEASURED"
    if gate_d["passed"] and gate_pilot["passed"] and gate_ga["passed"]:
        commercial_claim_status = "FULL_AUTONOMY_GA_ELIGIBLE"
    elif gate_d["passed"] and gate_pilot["passed"]:
        commercial_claim_status = "CONTROLLED_PILOT_ELIGIBLE"
    elif gate_d["passed"]:
        commercial_claim_status = "CAPABILITY_BREAKTHROUGH_REACHED"
    elif evaluation_report is not None and gate_d.get("measurement_status") == "MEASURED":
        commercial_claim_status = "MEASURED_BELOW_GATE_D"

    blockers: list[str] = []
    if not implementation_ready:
        blockers.append("implementation_stage_gates_incomplete")
    if gate_d.get("measurement_status") != "MEASURED":
        blockers.append(str(gate_d.get("reason") or "gate_d_not_measured"))
    elif not gate_d.get("passed"):
        blockers.append("gate_d_thresholds_not_met")
    if gate_pilot.get("measurement_status") == "MEASURED" and not gate_pilot.get("passed"):
        blockers.append("controlled_pilot_thresholds_not_met")
    if gate_ga.get("measurement_status") == "MEASURED" and not gate_ga.get("passed"):
        blockers.append("full_autonomy_ga_thresholds_not_met")

    return {
        "schema_version": GOAL_GATE_STATUS_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "product_ports": {
            "frontend": PRODUCT_FRONTEND_PORT,
            "backend": PRODUCT_BACKEND_PORT,
        },
        "implementation_ready": implementation_ready,
        "commercial_claim_status": commercial_claim_status,
        "blockers": blockers,
        "gates": {
            **implementation,
            "gate_d_capability_breakthrough": gate_d,
            "controlled_commercial_pilot": gate_pilot,
            "full_autonomy_ga": gate_ga,
        },
        "north_star_metrics": [
            "held_out_macro_industry_recall",
            "held_out_micro_precision",
            "reproduction_success_rate",
            "clean_critical_high_false_positives",
            "cost_per_true_positive_usd",
        ],
        "notes": [
            "Internal candidate/confirmed/validated funnel counts are diagnostic only and never commercial claims.",
            "Missing ground truth, incomplete receipts, or degraded pipelines yield NOT_MEASURED.",
            "Harness proposals may edit StrategyBundle surfaces only; evaluator, evidence threshold, sandbox boundary, and ports stay frozen.",
        ],
    }
