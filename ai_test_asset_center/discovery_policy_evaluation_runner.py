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

from .discovery_evaluation_contract import (
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
from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from .policy_evaluation_gate import PolicyPromotionGate
from .policy_registry import PolicyRecord, StrategyBundle
from .policy_wiring import policy_strategy_override


SCAN_RESULT_SCHEMA = "qualibug.discovery-evaluation-scan-result.v1"
FIXTURE_PREPARE_SCHEMA = "qualibug.governed-evaluation-fixture-prepare.v1"
FIXTURE_CLEANUP_SCHEMA = "qualibug.governed-evaluation-fixture-cleanup.v1"
COMPARISON_SCHEMA = "qualibug.discovery-policy-comparison.v1"


class PolicyEvaluationRunnerError(RuntimeError):
    """Observed evaluation could not produce trustworthy promotion evidence."""


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
        mainline_authority: str,
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
    ) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.output_root = Path(output_root).resolve()
        self.fixture_controller = fixture_controller
        self.scan_executor = scan_executor
        self.manifest = load_evaluation_manifest(self.manifest_path)
        shape = assess_commercial_dataset_shape(self.manifest)
        if shape.get("commercial_shape_ready") is not True:
            failed = [item.get("name") for item in shape.get("checks") or [] if not item.get("passed")]
            raise PolicyEvaluationRunnerError(f"evaluation dataset is not commercial-shape ready: {failed}")

    def run(
        self,
        *,
        champion: PolicyRecord,
        challenger: PolicyRecord,
        evaluation_id: str | None = None,
    ) -> dict[str, Any]:
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
        )
        champion_metrics = policy_metrics_from_evaluation_reports(
            reports["champion_replay"], reports["champion_shadow"]
        )
        challenger_metrics = policy_metrics_from_evaluation_reports(
            reports["challenger_replay"], reports["challenger_shadow"]
        )
        decision = PolicyPromotionGate().evaluate(champion_metrics, challenger_metrics, evidence)
        report_refs = {
            name: str(self._report_path(evaluation_id, report))
            for name, report in reports.items()
        }
        comparison = {
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
        comparison_path = self._persist_comparison(comparison)
        return {**comparison, "comparison_ref": str(comparison_path)}

    def _run_policy_mode(
        self,
        *,
        policy: PolicyRecord,
        role: str,
        evaluation_mode: str,
        evaluation_id: str,
    ) -> dict[str, Any]:
        receipts: list[dict[str, Any]] = []
        for target in self.manifest.targets:
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
                with policy_strategy_override(policy.strategy):
                    scan_output = self.scan_executor(
                        runtime_view=runtime_view,
                        campaign_id=campaign_id,
                        policy_id=policy.policy_id,
                        policy_version=policy.policy_version,
                        mainline_authority=policy.strategy.execution.mainline_authority,
                        evaluation_mode=evaluation_mode,
                        fixture_preparation_receipt=preparation,
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
                environment_id=target.environment_ref,
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
            receipt = evaluate_completed_scan(
                self.manifest,
                target.target_id,
                run_id=str(scan_output["run_id"]),
                policy_id=policy.policy_id,
                evaluation_mode=evaluation_mode,
                findings=list(scan_output["findings"]),
                candidates=list(scan_output["candidates"]),
                pipeline_health=dict(scan_output["pipeline_health"]),
                operational_metrics=dict(scan_output["operational_metrics"]),
                fixture_governance=governance,
            )
            persist_evaluation_receipt(
                receipt,
                self.output_root / evaluation_id / "receipts",
            )
            receipts.append(receipt)

        report = aggregate_evaluation_receipts(self.manifest, receipts)
        path = self._report_path(evaluation_id, report)
        persist_evaluation_report(report, path)
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
        environment_id: str,
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
        run_id = _required_text(output.get("run_id"), "scan_output.run_id")
        try:
            mainline_run = validate_mainline_run_contract(dict(output.get("mainline_run") or {}))
        except MainlineContractError as exc:
            raise PolicyEvaluationRunnerError(
                f"scan output mainline contract invalid for {target_id}: {exc}"
            ) from exc
        contract_expected = {
            "mainline_authority": policy.strategy.execution.mainline_authority,
            "run_id": run_id,
            "campaign_id": campaign_id,
            "target_id": target_id,
            "environment_id": environment_id,
            "policy_version": policy.policy_version,
            "evaluation_mode": evaluation_mode,
        }
        for field, value in contract_expected.items():
            if mainline_run[field] != value:
                if field == "mainline_authority":
                    raise PolicyEvaluationRunnerError(
                        f"scan output mainline authority mismatch for {target_id}"
                    )
                raise PolicyEvaluationRunnerError(
                    f"scan output mainline contract {field} mismatch for {target_id}"
                )
        if output.get("execution_kind") != "observed" or output.get("estimated_metrics_used") is not False:
            raise PolicyEvaluationRunnerError("estimated or non-observed scan output cannot be promotion evidence")
        if output.get("customer_outputs_published") is not mainline_run["customer_outputs_published"]:
            if evaluation_mode == "shadow":
                raise PolicyEvaluationRunnerError("shadow evaluation must not publish customer outputs")
            raise PolicyEvaluationRunnerError(
                f"scan output customer publication scope mismatch for {target_id}"
            )
        for field in ("findings", "candidates"):
            if not isinstance(output.get(field), list):
                raise PolicyEvaluationRunnerError(f"scan output {field} must be a list")
        for field in ("pipeline_health", "operational_metrics"):
            if not isinstance(output.get(field), dict):
                raise PolicyEvaluationRunnerError(f"scan output {field} must be an object")

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
    champion: PolicyRecord,
    challenger: PolicyRecord,
    evaluation_id: str | None = None,
) -> dict[str, Any]:
    return DiscoveryPolicyEvaluationRunner(
        manifest_path,
        output_root=output_root,
        fixture_controller=fixture_controller,
        scan_executor=scan_executor,
    ).run(champion=champion, challenger=challenger, evaluation_id=evaluation_id)
