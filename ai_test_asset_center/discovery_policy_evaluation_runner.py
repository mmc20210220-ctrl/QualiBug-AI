from __future__ import annotations

"""Observed champion/challenger replay and shadow execution.

This runner is evaluator-owned. Discovery receives only the redacted runtime
view, while a governed fixture controller owns setup and cleanup. No historical
metric estimation path exists here: every report is built from a scan executor
invocation and an immutable target receipt.
"""

import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from benchmark_evaluator.http_observation_gateway import (
    EvaluatorHttpObservationGateway,
)

from .discovery_evaluation_contract import (
    POLICY_COMPARISON_AUTHENTICATION_FIELD,
    POLICY_COMPARISON_FINGERPRINT_FIELD,
    POLICY_COMPARISON_SCHEMA,
    EvaluationContractError,
    EvaluationManifest,
    aggregate_evaluation_receipts,
    assess_commercial_dataset_shape,
    build_paired_evaluation_evidence,
    build_runtime_view,
    evaluate_completed_scan,
    load_evaluation_manifest,
    persist_evaluation_receipt,
    persist_evaluation_report,
    policy_metrics_from_evaluation_reports,
)
from .policy_evaluation_gate import PolicyPromotionGate
from .policy_registry import PolicyRecord, StrategyBundle
from .policy_wiring import policy_strategy_override
from .evaluator_execution_attestation import build_execution_attestation
from .evaluator_receipt_auth import (
    EvaluatorReceiptAuthError,
    seal_evaluator_artifact,
    verify_evaluator_artifact,
)


SCAN_RESULT_SCHEMA = "qualibug.discovery-evaluation-scan-result.v1"
FIXTURE_PREPARE_SCHEMA = "qualibug.governed-evaluation-fixture-prepare.v1"
FIXTURE_CLEANUP_SCHEMA = "qualibug.governed-evaluation-fixture-cleanup.v1"
COMPARISON_SCHEMA = POLICY_COMPARISON_SCHEMA
TRUSTED_OBSERVATION_PACK_SCHEMA = (
    "qualibug.evaluator-trusted-observation-pack.v1"
)
OBSERVATION_PACK_FINGERPRINT_FIELD = "observation_pack_fingerprint"
OBSERVATION_PACK_AUTHENTICATION_FIELD = "observation_pack_authentication"


class PolicyEvaluationRunnerError(RuntimeError):
    """Observed evaluation could not produce trustworthy promotion evidence."""


class TrustedObservationStore:
    """Read evaluator-owned request observations that product code cannot reach."""

    def __init__(
        self,
        root: Path | str,
        *,
        product_workspace_root: Path | str,
        verification_key: str | bytes | bytearray,
    ) -> None:
        self.root = Path(root).resolve()
        workspace = Path(product_workspace_root).resolve()
        if self.root == workspace or workspace in self.root.parents:
            raise PolicyEvaluationRunnerError(
                "trusted observation root must be outside product workspace"
            )
        if not self.root.is_dir():
            raise PolicyEvaluationRunnerError(
                f"trusted observation root not found: {self.root}"
            )
        self.verification_key = verification_key

    def load(
        self,
        *,
        run_id: str,
        campaign_id: str,
        target_id: str,
    ) -> list[dict[str, Any]]:
        resolved_run_id = _required_text(run_id, "trusted_observations.run_id")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", resolved_run_id):
            raise PolicyEvaluationRunnerError(
                "trusted observation run_id is not a safe filename identity"
            )
        path = (self.root / f"{resolved_run_id}.json").resolve()
        if path.parent != self.root or not path.is_file():
            raise PolicyEvaluationRunnerError(
                f"trusted observation pack not found: {path}"
            )
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyEvaluationRunnerError(
                f"trusted observation pack is invalid JSON: {path}: {exc}"
            ) from exc
        try:
            pack = verify_evaluator_artifact(
                pack,
                signing_key=self.verification_key,
                domain=TRUSTED_OBSERVATION_PACK_SCHEMA,
                fingerprint_field=OBSERVATION_PACK_FINGERPRINT_FIELD,
                authentication_field=OBSERVATION_PACK_AUTHENTICATION_FIELD,
            )
        except EvaluatorReceiptAuthError as exc:
            raise PolicyEvaluationRunnerError(
                f"trusted observation pack authentication failed: {exc}"
            ) from exc
        if not isinstance(pack, dict) or set(pack) != {
            "schema_version",
            "created_at_utc",
            "run_id",
            "campaign_id",
            "target_id",
            "observations",
            OBSERVATION_PACK_FINGERPRINT_FIELD,
            OBSERVATION_PACK_AUTHENTICATION_FIELD,
        }:
            raise PolicyEvaluationRunnerError(
                "trusted observation pack fields are invalid"
            )
        if pack.get("schema_version") != TRUSTED_OBSERVATION_PACK_SCHEMA:
            raise PolicyEvaluationRunnerError(
                "trusted observation pack schema is unsupported"
            )
        for field, expected in (
            ("run_id", resolved_run_id),
            ("campaign_id", _required_text(campaign_id, "campaign_id")),
            ("target_id", _required_text(target_id, "target_id")),
        ):
            if pack.get(field) != expected:
                raise PolicyEvaluationRunnerError(
                    f"trusted observation pack {field} mismatch"
                )
        observations = pack.get("observations")
        if not isinstance(observations, list) or not all(
            isinstance(row, dict) for row in observations
        ):
            raise PolicyEvaluationRunnerError(
                "trusted observation pack observations must be an object list"
            )
        return [dict(row) for row in observations]


class GovernedFixtureController(Protocol):
    def prepare(
        self,
        *,
        runtime_view: dict[str, Any],
        campaign_id: str,
        policy_id: str,
        evaluation_mode: str,
        expected_fixture_fingerprint: str,
    ) -> dict[str, Any]: ...

    def cleanup(
        self,
        *,
        runtime_view: dict[str, Any],
        campaign_id: str,
        policy_id: str,
        evaluation_mode: str,
        preparation_receipt: dict[str, Any],
        scan_output: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


class ObservedScanExecutor(Protocol):
    def __call__(
        self,
        *,
        runtime_view: dict[str, Any],
        campaign_id: str,
        policy_id: str,
        policy_version: str,
        evaluation_mode: str,
        fixture_preparation_receipt: dict[str, Any],
    ) -> dict[str, Any]: ...


def strategy_fingerprint(strategy: StrategyBundle) -> str:
    return hashlib.sha256(
        json.dumps(asdict(strategy), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _canonical_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PolicyEvaluationRunnerError(f"missing required observed-evaluation field: {field}")
    return text


def _runtime_interface_request_attempts(
    scan_output: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project read-only surface probes into evaluator attestation inputs.

    Runtime interface discovery intentionally stays outside the business
    obligation ledger and budget, but its governed GET requests still cross
    the evaluator gateway. Attestation must therefore bind those requests in
    a separate, explicit attempt collection rather than silently dropping
    them from expected request coverage.
    """

    runtime_discovery = scan_output.get("runtime_interface_discovery")
    if not isinstance(runtime_discovery, dict):
        return []
    execution = runtime_discovery.get("execution")
    if not isinstance(execution, dict):
        return []
    selected_count = int(execution.get("selected_count") or 0)
    if selected_count == 0:
        return []
    execution_results = execution.get("execution_results")
    if not isinstance(execution_results, dict):
        raise PolicyEvaluationRunnerError(
            "runtime interface discovery execution results missing"
        )
    if len(execution_results) != selected_count:
        raise PolicyEvaluationRunnerError(
            "runtime interface discovery execution coverage mismatch"
        )
    attempts: list[dict[str, Any]] = []
    for obligation_id, raw in sorted(execution_results.items()):
        if not isinstance(raw, dict):
            raise PolicyEvaluationRunnerError(
                "runtime interface discovery execution result invalid"
            )
        attempts.append({
            "obligation_id": str(obligation_id or "").strip(),
            "execution_id": str(raw.get("execution_id") or "").strip(),
            "terminal_stage": "surface_discovery",
            "terminal_status": str(raw.get("status") or "").strip(),
            "operational_receipt": (
                dict(raw.get("operational_receipt"))
                if isinstance(raw.get("operational_receipt"), dict)
                else {}
            ),
        })
    return attempts


def _require_non_negative_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise PolicyEvaluationRunnerError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise PolicyEvaluationRunnerError(f"{field} must be numeric") from exc
    if parsed < 0:
        raise PolicyEvaluationRunnerError(f"{field} must be non-negative")
    return parsed


def _contains_private_evaluator_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in {
                "ground_truth",
                "ground_truth_ref",
                "ground_truth_fingerprint",
                "expected_defects",
                "expected_bug_ids",
            }:
                return True
            if _contains_private_evaluator_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_private_evaluator_key(item) for item in value)
    return False


class DiscoveryPolicyEvaluationRunner:
    """Run four real policy passes without mutating the active registry."""

    def __init__(
        self,
        manifest_path: Path | str,
        *,
        output_root: Path | str,
        fixture_controller: GovernedFixtureController,
        scan_executor: ObservedScanExecutor,
        trusted_observation_gateway: EvaluatorHttpObservationGateway,
        trusted_observation_store: TrustedObservationStore,
        receipt_signing_key: str | bytes | bytearray,
        require_commercial_shape: bool = True,
    ) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.output_root = Path(output_root).resolve()
        self.fixture_controller = fixture_controller
        self.scan_executor = scan_executor
        if not isinstance(
            trusted_observation_gateway,
            EvaluatorHttpObservationGateway,
        ):
            raise TypeError(
                "trusted_observation_gateway must be EvaluatorHttpObservationGateway"
            )
        self.trusted_observation_gateway = trusted_observation_gateway
        if not isinstance(trusted_observation_store, TrustedObservationStore):
            raise TypeError(
                "trusted_observation_store must be TrustedObservationStore"
            )
        self.trusted_observation_store = trusted_observation_store
        self.receipt_signing_key = receipt_signing_key
        self.manifest = load_evaluation_manifest(self.manifest_path)
        self._commercial_shape = assess_commercial_dataset_shape(self.manifest)
        if require_commercial_shape:
            self._assert_commercial_shape_ready("policy evaluation")

    def _assert_commercial_shape_ready(self, usage: str) -> None:
        if self._commercial_shape.get("commercial_shape_ready") is True:
            return
        failed = [
            item.get("name")
            for item in self._commercial_shape.get("checks") or []
            if not item.get("passed")
        ]
        raise PolicyEvaluationRunnerError(
            f"{usage} requires a commercial-shape dataset: {failed}"
        )

    def run_target_diagnostic(
        self,
        *,
        policy: PolicyRecord,
        target_id: str,
        evaluation_mode: str = "replay",
        evaluation_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one authenticated target without making a promotion claim."""

        resolved_target = _required_text(target_id, "diagnostic.target_id")
        if resolved_target not in {
            target.target_id for target in self.manifest.targets
        }:
            raise PolicyEvaluationRunnerError(
                f"diagnostic target is not in the frozen manifest: {resolved_target}"
            )
        if evaluation_mode not in {"replay", "shadow"}:
            raise PolicyEvaluationRunnerError(
                "diagnostic evaluation_mode must be replay or shadow"
            )
        resolved_evaluation_id = str(evaluation_id or "").strip()
        if not resolved_evaluation_id:
            material = f"{time.time_ns()}:{policy.policy_id}:{resolved_target}"
            resolved_evaluation_id = (
                "diagnostic-"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", resolved_evaluation_id):
            raise PolicyEvaluationRunnerError(
                "evaluation_id must be 1-120 safe filename characters"
            )
        return self._run_policy_mode(
            policy=policy,
            role="diagnostic",
            evaluation_mode=evaluation_mode,
            evaluation_id=resolved_evaluation_id,
            target_ids={resolved_target},
        )

    def run(
        self,
        *,
        champion: PolicyRecord,
        challenger: PolicyRecord,
        evaluation_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_commercial_shape_ready("promotion evaluation")
        safe_evaluation_id = self._evaluation_id(evaluation_id, champion, challenger)
        try:
            return self._run_observed(
                champion=champion,
                challenger=challenger,
                evaluation_id=safe_evaluation_id,
            )
        except Exception as exc:
            failure_ref = self._persist_failure(
                evaluation_id=safe_evaluation_id,
                champion=champion,
                challenger=challenger,
                error=exc,
            )
            raise PolicyEvaluationRunnerError(
                f"observed policy evaluation failed: {exc}; failure_ref={failure_ref}"
            ) from exc

    def _run_observed(
        self,
        *,
        champion: PolicyRecord,
        challenger: PolicyRecord,
        evaluation_id: str,
    ) -> dict[str, Any]:
        self._validate_policy_pair(champion, challenger)
        reports: dict[str, dict[str, Any]] = {}
        all_run_ids: set[str] = set()

        for mode, role, policy in (
            ("replay", "champion", champion),
            ("replay", "challenger", challenger),
            ("shadow", "champion", champion),
            ("shadow", "challenger", challenger),
        ):
            report = self._run_policy_mode(
                policy=policy,
                role=role,
                evaluation_mode=mode,
                evaluation_id=evaluation_id,
            )
            overlap = all_run_ids.intersection(str(item) for item in report.get("run_ids") or [])
            if overlap:
                raise PolicyEvaluationRunnerError(f"run_id values must be globally unique: {sorted(overlap)}")
            all_run_ids.update(str(item) for item in report.get("run_ids") or [])
            reports[f"{role}_{mode}"] = report

        evidence = build_paired_evaluation_evidence(
            self.manifest,
            champion_replay=reports["champion_replay"],
            challenger_replay=reports["challenger_replay"],
            champion_shadow=reports["champion_shadow"],
            challenger_shadow=reports["challenger_shadow"],
            receipt_signing_key=self.receipt_signing_key,
        )
        champion_metrics = policy_metrics_from_evaluation_reports(
            reports["champion_replay"],
            reports["champion_shadow"],
            receipt_signing_key=self.receipt_signing_key,
        )
        challenger_metrics = policy_metrics_from_evaluation_reports(
            reports["challenger_replay"],
            reports["challenger_shadow"],
            receipt_signing_key=self.receipt_signing_key,
        )
        decision = PolicyPromotionGate().evaluate(champion_metrics, challenger_metrics, evidence)
        report_refs = {
            name: str(self._report_path(evaluation_id, report))
            for name, report in reports.items()
        }
        comparison_payload = {
            "schema_version": COMPARISON_SCHEMA,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "evaluation_id": evaluation_id,
            "dataset_id": self.manifest.dataset_id,
            "dataset_version": self.manifest.dataset_version,
            "dataset_manifest_fingerprint": self.manifest.manifest_fingerprint,
            "champion": self._policy_identity(champion),
            "challenger": self._policy_identity(challenger),
            "observed_execution": True,
            "estimated_metrics_used": False,
            "report_refs": report_refs,
            "report_fingerprints": {
                name: _canonical_fingerprint(report) for name, report in reports.items()
            },
            "evaluation_evidence": evidence,
            "champion_metrics": champion_metrics,
            "challenger_metrics": challenger_metrics,
            "promotion_decision": decision,
            "activation_performed": False,
        }
        comparison = seal_evaluator_artifact(
            comparison_payload,
            signing_key=self.receipt_signing_key,
            domain=COMPARISON_SCHEMA,
            fingerprint_field=POLICY_COMPARISON_FINGERPRINT_FIELD,
            authentication_field=POLICY_COMPARISON_AUTHENTICATION_FIELD,
        )
        comparison_path = self._persist_comparison(comparison)
        return {**comparison, "comparison_ref": str(comparison_path)}

    def _run_policy_mode(
        self,
        *,
        policy: PolicyRecord,
        role: str,
        evaluation_mode: str,
        evaluation_id: str,
        target_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        receipts: list[dict[str, Any]] = []
        for target in self.manifest.targets:
            if target_ids is not None and target.target_id not in target_ids:
                continue
            self._assert_manifest_frozen()
            campaign_id = f"{evaluation_id}:{evaluation_mode}:{role}:{target.target_id}"
            runtime_view = build_runtime_view(self.manifest, target.target_id)
            if _contains_private_evaluator_key(runtime_view):
                raise PolicyEvaluationRunnerError("runtime view leaked evaluator-private fields")
            expected = self.manifest.target_fingerprints[target.target_id]
            preparation = self.fixture_controller.prepare(
                runtime_view=runtime_view,
                campaign_id=campaign_id,
                policy_id=policy.policy_id,
                evaluation_mode=evaluation_mode,
                expected_fixture_fingerprint=expected["fixture_fingerprint"],
            )
            try:
                self._validate_fixture_prepare(
                    preparation,
                    target_id=target.target_id,
                    campaign_id=campaign_id,
                    environment_ref=target.environment_ref,
                    environment_type=target.environment_type,
                    fixture_fingerprint=expected["fixture_fingerprint"],
                )
            except Exception as preparation_error:
                try:
                    cleanup_after_prepare_failure = self.fixture_controller.cleanup(
                        runtime_view=runtime_view,
                        campaign_id=campaign_id,
                        policy_id=policy.policy_id,
                        evaluation_mode=evaluation_mode,
                        preparation_receipt=preparation if isinstance(preparation, dict) else {},
                        scan_output=None,
                    )
                    self._validate_fixture_cleanup(
                        cleanup_after_prepare_failure,
                        target_id=target.target_id,
                        campaign_id=campaign_id,
                        environment_ref=target.environment_ref,
                        environment_type=target.environment_type,
                        fixture_fingerprint=expected["fixture_fingerprint"],
                    )
                except Exception as cleanup_error:
                    raise PolicyEvaluationRunnerError(
                        f"fixture prepare and compensating cleanup failed for {target.target_id}: "
                        f"prepare={preparation_error}; cleanup={cleanup_error}"
                    ) from cleanup_error
                raise PolicyEvaluationRunnerError(
                    f"fixture prepare failed for {target.target_id}: {preparation_error}; "
                    "compensating cleanup succeeded"
                ) from preparation_error

            scan_output: dict[str, Any] | None = None
            scan_error: Exception | None = None
            cleanup_error: Exception | None = None
            cleanup: dict[str, Any] | None = None
            try:
                with self.trusted_observation_gateway.observe(
                    upstream_base_url=target.environment_ref,
                    campaign_id=campaign_id,
                    target_id=target.target_id,
                    environment_type=target.environment_type,
                ) as observation_proxy_base_url:
                    with policy_strategy_override(policy.strategy):
                        scan_output = self.scan_executor(
                            runtime_view=runtime_view,
                            campaign_id=campaign_id,
                            policy_id=policy.policy_id,
                            policy_version=policy.policy_version,
                            evaluation_mode=evaluation_mode,
                            fixture_preparation_receipt=preparation,
                            observation_proxy_base_url=(
                                observation_proxy_base_url
                            ),
                            agent_semantic_linking_enabled=True,
                        )
            except Exception as exc:
                scan_error = exc
            finally:
                try:
                    cleanup = self.fixture_controller.cleanup(
                        runtime_view=runtime_view,
                        campaign_id=campaign_id,
                        policy_id=policy.policy_id,
                        evaluation_mode=evaluation_mode,
                        preparation_receipt=preparation,
                        scan_output=scan_output,
                    )
                    self._validate_fixture_cleanup(
                        cleanup,
                        target_id=target.target_id,
                        campaign_id=campaign_id,
                        environment_ref=target.environment_ref,
                        environment_type=target.environment_type,
                        fixture_fingerprint=expected["fixture_fingerprint"],
                    )
                except Exception as exc:
                    cleanup_error = exc

            if cleanup_error is not None:
                detail = f"; scan also failed: {scan_error}" if scan_error is not None else ""
                raise PolicyEvaluationRunnerError(
                    f"governed fixture cleanup failed for {target.target_id}: {cleanup_error}{detail}"
                ) from cleanup_error
            if scan_error is not None:
                raise PolicyEvaluationRunnerError(
                    f"observed scan failed for {target.target_id}: {scan_error}"
                ) from scan_error
            assert scan_output is not None and cleanup is not None
            finalize_after_cleanup = getattr(self.scan_executor, "finalize_after_cleanup", None)
            if callable(finalize_after_cleanup):
                finalized = finalize_after_cleanup(
                    scan_output=scan_output,
                    cleanup_receipt=cleanup,
                )
                if not isinstance(finalized, dict):
                    raise PolicyEvaluationRunnerError(
                        f"scan cleanup finalizer returned invalid output for {target.target_id}"
                    )
                scan_output = finalized
            self._validate_scan_output(
                scan_output,
                policy=policy,
                target_id=target.target_id,
                campaign_id=campaign_id,
                evaluation_mode=evaluation_mode,
                expected_fingerprints=expected,
                fixture_audit_receipt_id=str(preparation["audit_receipt_id"]),
            )
            governance = {
                "campaign_id": campaign_id,
                "prepare_audit_receipt_id": str(preparation["audit_receipt_id"]),
                "cleanup_audit_receipt_id": str(cleanup["audit_receipt_id"]),
                "before_observation_ref": str(preparation["before_observation_ref"]),
                "after_observation_ref": str(preparation["after_observation_ref"]),
                "after_cleanup_observation_ref": str(cleanup["after_cleanup_observation_ref"]),
                "prepare_receipt_fingerprint": _canonical_fingerprint(preparation),
                "cleanup_receipt_fingerprint": _canonical_fingerprint(cleanup),
                "cleanup_status": "SUCCEEDED",
                "dirty_environment": False,
            }
            execution_policy_identity = {
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
                "strategy_fingerprint": strategy_fingerprint(policy.strategy),
            }
            additional_request_attempts = _runtime_interface_request_attempts(
                scan_output
            )
            process_boundary = scan_output.get("process_boundary")
            if not isinstance(process_boundary, dict):
                raise PolicyEvaluationRunnerError(
                    f"isolated process boundary missing for {target.target_id}"
                )
            trusted_observations = self.trusted_observation_store.load(
                run_id=str(scan_output["run_id"]),
                campaign_id=str(
                    dict(scan_output["mainline_run"]).get("campaign_id") or ""
                ),
                target_id=target.target_id,
            )
            execution_attestation = build_execution_attestation(
                mainline_run=dict(scan_output["mainline_run"]),
                obligation_attempt_ledger=dict(
                    scan_output["obligation_attempt_ledger"]
                ),
                policy_identity=execution_policy_identity,
                fixture_governance=governance,
                process_boundary=process_boundary,
                trusted_observations=trusted_observations,
                additional_request_attempts=additional_request_attempts,
                signing_key=self.receipt_signing_key,
            )
            receipt = evaluate_completed_scan(
                self.manifest,
                target.target_id,
                run_id=str(scan_output["run_id"]),
                policy_id=policy.policy_id,
                evaluation_mode=evaluation_mode,
                findings=list(scan_output["findings"]),
                delivery_occurrences=list(scan_output["delivery_occurrences"]),
                candidates=list(scan_output["candidates"]),
                pipeline_health=dict(scan_output["pipeline_health"]),
                operational_metrics=dict(scan_output["operational_metrics"]),
                fixture_governance=governance,
                trace_ledger=(
                    dict(scan_output["trace_ledger"])
                    if isinstance(scan_output.get("trace_ledger"), dict)
                    else None
                ),
                obligation_attempt_ledger=dict(
                    scan_output["obligation_attempt_ledger"]
                ),
                mainline_run=dict(scan_output["mainline_run"]),
                formal_count_projection=dict(
                    scan_output["formal_count_projection"]
                ),
                formal_delivery_authority=dict(
                    scan_output["formal_delivery_authority"]
                ),
                canonical_defect_registry=dict(
                    scan_output["canonical_defect_registry"]
                ),
                defect_identity_consistency=dict(
                    scan_output["defect_identity_consistency"]
                ),
                evaluator_policy_identity=execution_policy_identity,
                process_boundary=process_boundary,
                execution_attestation=execution_attestation,
                additional_request_attempts=additional_request_attempts,
                receipt_signing_key=self.receipt_signing_key,
            )
            persist_evaluation_receipt(
                receipt,
                self.output_root / evaluation_id / "receipts",
                receipt_signing_key=self.receipt_signing_key,
            )
            receipts.append(receipt)

        report = aggregate_evaluation_receipts(
            self.manifest,
            receipts,
            receipt_signing_key=self.receipt_signing_key,
        )
        path = self._report_path(evaluation_id, report)
        persist_evaluation_report(
            report,
            path,
            receipt_signing_key=self.receipt_signing_key,
        )
        return report

    def _validate_policy_pair(self, champion: PolicyRecord, challenger: PolicyRecord) -> None:
        if not isinstance(champion, PolicyRecord) or not isinstance(challenger, PolicyRecord):
            raise PolicyEvaluationRunnerError("champion and challenger must be PolicyRecord instances")
        if champion.policy_id == challenger.policy_id:
            raise PolicyEvaluationRunnerError("champion and challenger policy_id values must differ")
        if strategy_fingerprint(champion.strategy) == strategy_fingerprint(challenger.strategy):
            raise PolicyEvaluationRunnerError("challenger strategy must differ from champion strategy")
        if challenger.parent_policy_version != champion.policy_version:
            raise PolicyEvaluationRunnerError("challenger parent_policy_version must identify the champion version")

    def _assert_manifest_frozen(self) -> None:
        current = load_evaluation_manifest(self.manifest_path)
        if current.manifest_fingerprint != self.manifest.manifest_fingerprint:
            raise PolicyEvaluationRunnerError("evaluation manifest or referenced artifact drifted during execution")

    @staticmethod
    def _validate_fixture_prepare(
        receipt: dict[str, Any],
        *,
        target_id: str,
        campaign_id: str,
        environment_ref: str,
        environment_type: str,
        fixture_fingerprint: str,
    ) -> None:
        if not isinstance(receipt, dict) or receipt.get("schema_version") != FIXTURE_PREPARE_SCHEMA:
            raise PolicyEvaluationRunnerError("fixture prepare must return the governed prepare receipt schema")
        expected = {
            "target_id": target_id,
            "campaign_id": campaign_id,
            "environment_ref": environment_ref,
            "environment_type": environment_type,
            "fixture_fingerprint": fixture_fingerprint,
        }
        for field, value in expected.items():
            if receipt.get(field) != value:
                raise PolicyEvaluationRunnerError(f"fixture prepare {field} mismatch for {target_id}")
        if receipt.get("status") != "READY" or receipt.get("governed_sandbox_executor") is not True:
            raise PolicyEvaluationRunnerError(f"fixture prepare was not governed and ready for {target_id}")
        for field in ("audit_receipt_id", "before_observation_ref", "after_observation_ref"):
            _required_text(receipt.get(field), f"fixture_prepare.{field}")
        if _require_non_negative_number(
            receipt.get("production_http_requests"), "fixture_prepare.production_http_requests"
        ) != 0:
            raise PolicyEvaluationRunnerError("fixture prepare attempted a production HTTP request")

    @staticmethod
    def _validate_fixture_cleanup(
        receipt: dict[str, Any],
        *,
        target_id: str,
        campaign_id: str,
        environment_ref: str,
        environment_type: str,
        fixture_fingerprint: str,
    ) -> None:
        if not isinstance(receipt, dict) or receipt.get("schema_version") != FIXTURE_CLEANUP_SCHEMA:
            raise PolicyEvaluationRunnerError("fixture cleanup must return the governed cleanup receipt schema")
        expected = {
            "target_id": target_id,
            "campaign_id": campaign_id,
            "environment_ref": environment_ref,
            "environment_type": environment_type,
            "fixture_fingerprint": fixture_fingerprint,
        }
        for field, value in expected.items():
            if receipt.get(field) != value:
                raise PolicyEvaluationRunnerError(f"fixture cleanup {field} mismatch for {target_id}")
        if receipt.get("status") != "SUCCEEDED" or receipt.get("dirty_environment") is not False:
            raise PolicyEvaluationRunnerError(f"fixture cleanup did not restore {target_id}")
        for field in ("audit_receipt_id", "after_cleanup_observation_ref"):
            _required_text(receipt.get(field), f"fixture_cleanup.{field}")
        if _require_non_negative_number(
            receipt.get("production_http_requests"), "fixture_cleanup.production_http_requests"
        ) != 0:
            raise PolicyEvaluationRunnerError("fixture cleanup attempted a production HTTP request")

    @staticmethod
    def _validate_scan_output(
        output: dict[str, Any],
        *,
        policy: PolicyRecord,
        target_id: str,
        campaign_id: str,
        evaluation_mode: str,
        expected_fingerprints: dict[str, str],
        fixture_audit_receipt_id: str,
    ) -> None:
        if not isinstance(output, dict) or output.get("schema_version") != SCAN_RESULT_SCHEMA:
            raise PolicyEvaluationRunnerError("scan executor must return the observed scan-result schema")
        if _contains_private_evaluator_key(output):
            raise PolicyEvaluationRunnerError("scan output contains evaluator-private fields")
        expected = {
            "target_id": target_id,
            "campaign_id": campaign_id,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "evaluation_mode": evaluation_mode,
            "effective_strategy_fingerprint": strategy_fingerprint(policy.strategy),
            "fixture_audit_receipt_id": fixture_audit_receipt_id,
            **{field: expected_fingerprints[field] for field in (
                "runtime_fingerprint",
                "input_fingerprint",
                "fixture_fingerprint",
                "context_fingerprint",
            )},
        }
        for field, value in expected.items():
            if output.get(field) != value:
                raise PolicyEvaluationRunnerError(f"scan output {field} mismatch for {target_id}")
        _required_text(output.get("run_id"), "scan_output.run_id")
        if output.get("execution_kind") != "observed" or output.get("estimated_metrics_used") is not False:
            raise PolicyEvaluationRunnerError("estimated or non-observed scan output cannot be promotion evidence")
        if evaluation_mode == "shadow" and output.get("customer_outputs_published") is not False:
            raise PolicyEvaluationRunnerError("shadow evaluation must not publish customer outputs")
        for field in ("findings", "candidates"):
            if not isinstance(output.get(field), list):
                raise PolicyEvaluationRunnerError(f"scan output {field} must be a list")
        if not isinstance(output.get("delivery_occurrences"), list):
            raise PolicyEvaluationRunnerError(
                "scan output delivery_occurrences must be a list"
            )
        for field in (
            "pipeline_health",
            "operational_metrics",
            "mainline_run",
            "obligation_attempt_ledger",
            "canonical_defect_registry",
            "formal_delivery_authority",
            "formal_count_projection",
            "defect_identity_consistency",
            "process_boundary",
        ):
            if not isinstance(output.get(field), dict):
                raise PolicyEvaluationRunnerError(f"scan output {field} must be an object")
        mainline = dict(output["mainline_run"])
        for field, value in (
            ("run_id", output["run_id"]),
            ("target_id", target_id),
            ("policy_version", policy.policy_version),
            ("evaluation_mode", evaluation_mode),
        ):
            if mainline.get(field) != value:
                raise PolicyEvaluationRunnerError(
                    f"scan output mainline {field} mismatch for {target_id}"
                )
        _required_text(
            mainline.get("campaign_id"),
            "scan_output.mainline_run.campaign_id",
        )

    @staticmethod
    def _policy_identity(policy: PolicyRecord) -> dict[str, str]:
        return {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "parent_policy_version": policy.parent_policy_version,
            "strategy_fingerprint": strategy_fingerprint(policy.strategy),
        }

    @staticmethod
    def _evaluation_id(
        supplied: str | None,
        champion: PolicyRecord,
        challenger: PolicyRecord,
    ) -> str:
        value = str(supplied or "").strip()
        if not value:
            material = f"{time.time_ns()}:{champion.policy_id}:{challenger.policy_id}"
            value = f"eval-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", value):
            raise PolicyEvaluationRunnerError("evaluation_id must be 1-120 safe filename characters")
        return value

    def _report_path(self, evaluation_id: str, report: dict[str, Any]) -> Path:
        policy = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(report.get("policy_id") or ""))
        mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(report.get("evaluation_mode") or ""))
        return self.output_root / evaluation_id / "reports" / f"{policy}.{mode}.json"

    def _persist_comparison(self, comparison: dict[str, Any]) -> Path:
        if comparison.get("schema_version") != COMPARISON_SCHEMA:
            raise PolicyEvaluationRunnerError("cannot persist unsupported policy comparison schema")
        evaluation_id = _required_text(comparison.get("evaluation_id"), "comparison.evaluation_id")
        path = self.output_root / evaluation_id / "comparison.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(comparison, ensure_ascii=False, indent=2)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if _canonical_fingerprint(existing) != _canonical_fingerprint(comparison):
                raise PolicyEvaluationRunnerError(
                    f"immutable policy comparison already exists with different content: {path}"
                )
            return path
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
        return path

    def _persist_failure(
        self,
        *,
        evaluation_id: str,
        champion: PolicyRecord,
        challenger: PolicyRecord,
        error: Exception,
    ) -> Path:
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload = {
            "schema_version": "qualibug.discovery-policy-evaluation-failure.v1",
            "created_at_utc": created_at,
            "evaluation_id": evaluation_id,
            "dataset_id": self.manifest.dataset_id,
            "dataset_version": self.manifest.dataset_version,
            "dataset_manifest_fingerprint": self.manifest.manifest_fingerprint,
            "champion": self._policy_identity(champion),
            "challenger": self._policy_identity(challenger),
            "status": "FAILED_SAFE",
            "activation_performed": False,
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
        }
        suffix = hashlib.sha256(
            f"{time.time_ns()}:{type(error).__name__}:{error}".encode("utf-8")
        ).hexdigest()[:16]
        path = self.output_root / evaluation_id / "failures" / f"failure-{suffix}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path


def run_observed_policy_evaluation(
    manifest_path: Path | str,
    *,
    output_root: Path | str,
    fixture_controller: GovernedFixtureController,
    scan_executor: ObservedScanExecutor,
    trusted_observation_gateway: EvaluatorHttpObservationGateway,
    trusted_observation_store: TrustedObservationStore,
    receipt_signing_key: str | bytes | bytearray,
    champion: PolicyRecord,
    challenger: PolicyRecord,
    evaluation_id: str | None = None,
) -> dict[str, Any]:
    return DiscoveryPolicyEvaluationRunner(
        manifest_path,
        output_root=output_root,
        fixture_controller=fixture_controller,
        scan_executor=scan_executor,
        trusted_observation_gateway=trusted_observation_gateway,
        trusted_observation_store=trusted_observation_store,
        receipt_signing_key=receipt_signing_key,
    ).run(champion=champion, challenger=challenger, evaluation_id=evaluation_id)
