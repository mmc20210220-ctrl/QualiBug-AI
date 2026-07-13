from __future__ import annotations

"""Evaluator boundary for executing the product scan without evaluator secrets."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Protocol

from .discovery_mainline_contract import (
    MAINLINE_AUTHORITIES,
    MainlineContractError,
    build_mainline_run_contract,
    validate_mainline_run_contract,
)
from .discovery_policy_evaluation_runner import (
    SCAN_RESULT_SCHEMA,
    PolicyEvaluationRunnerError,
    strategy_fingerprint,
)
from .observed_product_scan_protocol import (
    PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
    find_evaluator_private_context_paths,
    is_evaluator_secret_environment_name,
)
from .evaluator_execution_attestation import PROCESS_BOUNDARY_SCHEMA
from .policy_wiring import get_effective_policy_strategy


PRODUCT_SCAN_INPUT_SCHEMA = "qualibug.discovery-evaluation-input.v1"
PRODUCT_SCAN_CONTEXT_SCHEMA = "qualibug.discovery-evaluation-context.v1"
DEFAULT_PRODUCT_SCAN_TIMEOUT_SECONDS = 3600.0


def _canonical_fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _product_subprocess_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not is_evaluator_secret_environment_name(name)
    }


class OperationalMetricsCollector(Protocol):
    def __call__(
        self,
        *,
        scan_result: dict[str, Any],
        wall_clock_seconds: float,
        runtime_view: dict[str, Any],
        campaign_id: str,
        policy_id: str,
        evaluation_mode: str,
    ) -> dict[str, Any]: ...


def _load_json(path_value: Any, field: str) -> tuple[Path, dict[str, Any]]:
    path = Path(str(path_value or "")).resolve()
    if not path.is_file():
        raise PolicyEvaluationRunnerError(f"{field} not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyEvaluationRunnerError(f"{field} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PolicyEvaluationRunnerError(f"{field} must contain a JSON object")
    return path, payload


def _resolve_ref(ref: Any, owner_path: Path, field: str) -> Path:
    value = Path(str(ref or ""))
    path = value if value.is_absolute() else owner_path.parent / value
    path = path.resolve()
    if not path.is_file():
        raise PolicyEvaluationRunnerError(f"{field} not found: {path}")
    return path


def _redacted_process_diagnostic(value: Any) -> tuple[str, str]:
    raw = str(value or "").strip()
    reference = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    if len(raw) > 4000:
        raw = raw[-4000:]
    from .artifact_redactor import ArtifactSecretLeakError, redact_and_validate

    try:
        redacted, _ = redact_and_validate({"diagnostic": raw})
    except ArtifactSecretLeakError:
        return "<REDACTION_FAILED>", reference
    return str(redacted.get("diagnostic") or ""), reference


class ObservedProductScanExecutor:
    """Execute one product scan across a process-level evaluator trust boundary."""

    def __init__(
        self,
        *,
        workspace_root: Path | str,
        operational_metrics_collector: OperationalMetricsCollector,
        scan_callable: Callable[..., dict[str, Any]] | None = None,
        allow_in_process_test_scan: bool = False,
        subprocess_timeout_seconds: float = DEFAULT_PRODUCT_SCAN_TIMEOUT_SECONDS,
    ) -> None:
        if not callable(operational_metrics_collector):
            raise TypeError("operational_metrics_collector must be callable")
        if scan_callable is not None and not callable(scan_callable):
            raise TypeError("scan_callable must be callable")
        if scan_callable is not None and allow_in_process_test_scan is not True:
            raise ValueError(
                "in-process product scan is test-only and requires "
                "allow_in_process_test_scan=True"
            )
        if allow_in_process_test_scan is True and scan_callable is None:
            raise ValueError(
                "allow_in_process_test_scan requires an explicit scan_callable"
            )
        timeout = float(subprocess_timeout_seconds)
        if timeout <= 0:
            raise ValueError("subprocess_timeout_seconds must be positive")
        self.workspace_root = Path(workspace_root).resolve()
        self.operational_metrics_collector = operational_metrics_collector
        self._test_scan_callable = scan_callable
        self.subprocess_timeout_seconds = timeout

    def _invoke_scan(
        self,
        *,
        scan_kwargs: dict[str, Any],
        effective_strategy: Any,
    ) -> dict[str, Any]:
        if self._test_scan_callable is not None:
            result = self._test_scan_callable(**scan_kwargs)
            if not isinstance(result, dict):
                raise PolicyEvaluationRunnerError(
                    "in-process test scan did not return a result object"
                )
            return result

        request = {
            "schema_version": PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
            "strategy": asdict(effective_strategy),
            "strategy_fingerprint": strategy_fingerprint(effective_strategy),
            "scan_kwargs": scan_kwargs,
        }
        with tempfile.TemporaryDirectory(prefix="qualibug-observed-scan-") as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            result_path = Path(temp_dir) / "result.json"
            try:
                request_path.write_text(
                    json.dumps(request, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
            except (OSError, TypeError, ValueError) as exc:
                raise PolicyEvaluationRunnerError(
                    f"product scan worker request is not serializable: {exc}"
                ) from exc
            command = [
                sys.executable,
                "-m",
                "ai_test_asset_center.observed_product_scan_worker",
                str(request_path),
                str(result_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(self.workspace_root),
                    env=_product_subprocess_environment(),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.subprocess_timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise PolicyEvaluationRunnerError(
                    "isolated product scan process timed out after "
                    f"{self.subprocess_timeout_seconds:g} seconds"
                ) from exc
            if completed.returncode != 0:
                detail, diagnostic_ref = _redacted_process_diagnostic(
                    completed.stderr or completed.stdout
                )
                raise PolicyEvaluationRunnerError(
                    "isolated product scan process failed "
                    f"(diagnostic_ref=sha256:{diagnostic_ref})"
                    + (f": {detail}" if detail else "")
                )
            if not result_path.is_file():
                raise PolicyEvaluationRunnerError(
                    "isolated product scan process produced no result artifact"
                )
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PolicyEvaluationRunnerError(
                    f"isolated product scan result is invalid JSON: {exc}"
                ) from exc
            if not isinstance(result, dict):
                raise PolicyEvaluationRunnerError(
                    "isolated product scan did not return a result object"
                )
            return result

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        runtime_view = kwargs.get("runtime_view")
        if (
            not isinstance(runtime_view, dict)
            or find_evaluator_private_context_paths(runtime_view)
        ):
            raise PolicyEvaluationRunnerError("product scan runtime view is invalid or leaks evaluator data")
        mainline_authority = str(kwargs.get("mainline_authority") or "").strip()
        if mainline_authority not in MAINLINE_AUTHORITIES:
            reason = "missing" if not mainline_authority else f"invalid:{mainline_authority}"
            raise PolicyEvaluationRunnerError(f"mainline_authority {reason}")
        effective_strategy = get_effective_policy_strategy()
        if effective_strategy.execution.mainline_authority != mainline_authority:
            raise PolicyEvaluationRunnerError(
                "mainline_authority does not match the effective policy strategy"
            )
        target = runtime_view.get("target") if isinstance(runtime_view.get("target"), dict) else {}
        runtime = target.get("runtime") if isinstance(target.get("runtime"), dict) else {}
        input_path, input_bundle = _load_json(runtime.get("input_bundle_ref"), "input_bundle_ref")
        context_path, context_bundle = _load_json(runtime.get("context_artifact_ref"), "context_artifact_ref")
        if input_bundle.get("schema_version") != PRODUCT_SCAN_INPUT_SCHEMA:
            raise PolicyEvaluationRunnerError("product scan input bundle schema is unsupported")
        if context_bundle.get("schema_version") != PRODUCT_SCAN_CONTEXT_SCHEMA:
            raise PolicyEvaluationRunnerError("product scan context artifact schema is unsupported")
        if (
            find_evaluator_private_context_paths(input_bundle)
            or find_evaluator_private_context_paths(context_bundle)
        ):
            raise PolicyEvaluationRunnerError("product scan runtime artifacts contain evaluator-private fields")

        project_id = str(target.get("project_id") or "").strip()
        if input_bundle.get("project_id") != project_id:
            raise PolicyEvaluationRunnerError("product scan input project_id does not match runtime target")
        base_url = str(input_bundle.get("base_url") or "").strip().rstrip("/")
        environment_ref = str(runtime.get("environment_ref") or "").strip().rstrip("/")
        if not base_url or base_url != environment_ref:
            raise PolicyEvaluationRunnerError("product scan input base_url must match runtime environment_ref")
        api_doc_path = _resolve_ref(input_bundle.get("api_doc_ref"), input_path, "api_doc_ref")
        api_doc_text = api_doc_path.read_text(encoding="utf-8")
        source_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()
        prd_ref = str(input_bundle.get("prd_ref") or "").strip()
        prd_text = _resolve_ref(prd_ref, input_path, "prd_ref").read_text(encoding="utf-8") if prd_ref else ""
        context = context_bundle.get("campaign_context")
        if not isinstance(context, dict):
            raise PolicyEvaluationRunnerError("product scan context requires campaign_context")
        context = dict(context)
        campaign_id = str(kwargs.get("campaign_id") or "").strip()
        policy_id = str(kwargs.get("policy_id") or "").strip()
        policy_version = str(kwargs.get("policy_version") or "").strip()
        evaluation_mode = str(kwargs.get("evaluation_mode") or "").strip()
        target_id = str(target.get("target_id") or "").strip()
        evaluator_run_id = f"evaluation-scan-{uuid.uuid4().hex}"
        try:
            mainline_run = build_mainline_run_contract(
                mainline_authority=mainline_authority,
                run_id=evaluator_run_id,
                campaign_id=campaign_id,
                target_id=target_id,
                environment_id=environment_ref,
                policy_version=policy_version,
                evaluation_mode=evaluation_mode,
            )
        except MainlineContractError as exc:
            raise PolicyEvaluationRunnerError(
                f"product scan mainline contract invalid before execution: {exc}"
            ) from exc
        context["source_manifest"] = {
            "source_id": f"evaluation-source:{source_hash[:24]}",
            "source_hash": source_hash,
            "source_version_id": f"evaluation-{source_hash[:24]}",
            "source_origin": "evaluator_frozen_runtime_input",
        }
        test_accounts = context_bundle.get("test_accounts")
        if test_accounts is not None:
            if not isinstance(test_accounts, dict):
                raise PolicyEvaluationRunnerError("product scan context test_accounts must be an object")
            if test_accounts:
                raise PolicyEvaluationRunnerError(
                    "inline evaluator test_accounts forbidden; configure product-owned "
                    "secret references before the observed scan"
                )
        context.update({
            "campaign_id": campaign_id,
            "run_id": mainline_run["run_id"],
            "target_id": mainline_run["target_id"],
            "environment_id": mainline_run["environment_id"],
            "policy_version": mainline_run["policy_version"],
            "environment_ref": environment_ref,
            "target_environment": str(runtime.get("environment_type") or ""),
            "environment_kind": str(runtime.get("environment_type") or ""),
            "policy_id": policy_id,
            "mainline_authority": mainline_authority,
            "evaluation_mode": evaluation_mode,
            "mainline_run": dict(mainline_run),
            "fixture_audit_receipt_id": str(
                (kwargs.get("fixture_preparation_receipt") or {}).get("audit_receipt_id") or ""
            ),
        })

        scan_kwargs = {
            "project": project_id,
            "root": str(self.workspace_root),
            "prd_text": prd_text,
            "api_doc_path": str(api_doc_path),
            "base_url": base_url,
            "ci_gate": False,
            "multi_layer": bool(input_bundle.get("multi_layer", True)),
            "output_dir": str(
                self.workspace_root
                / "platform_outputs"
                / project_id
                / "evaluation_runs"
                / campaign_id
            ),
            "save_report": False,
            "campaign_context": context,
        }
        started = time.monotonic()
        scan_result = self._invoke_scan(
            scan_kwargs=scan_kwargs,
            effective_strategy=effective_strategy,
        )
        wall_clock_seconds = round(time.monotonic() - started, 6)
        if not isinstance(scan_result, dict):
            raise PolicyEvaluationRunnerError("product scan did not return a result object")
        process_boundary = {
            "schema_version": PROCESS_BOUNDARY_SCHEMA,
            "isolation": (
                "isolated_subprocess"
                if self._test_scan_callable is None
                else "in_process_test"
            ),
            "worker_protocol_schema": PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
            "evaluator_secrets_removed": self._test_scan_callable is None,
            "request_fingerprint": _canonical_fingerprint({
                "strategy": asdict(effective_strategy),
                "scan_kwargs": scan_kwargs,
            }),
            "result_fingerprint": _canonical_fingerprint(scan_result),
            "exit_code": 0,
        }
        operational = self.operational_metrics_collector(
            scan_result=scan_result,
            wall_clock_seconds=wall_clock_seconds,
            runtime_view=runtime_view,
            campaign_id=campaign_id,
            policy_id=policy_id,
            evaluation_mode=evaluation_mode,
        )
        if not isinstance(operational, dict):
            raise PolicyEvaluationRunnerError("operational metrics collector did not return an object")
        pipeline_health = scan_result.get("pipeline_health")
        if not isinstance(pipeline_health, dict) or not str(pipeline_health.get("status") or "").strip():
            pipeline_health = {
                "status": "NOT_MEASURED",
                "reason": "product_scan_pipeline_health_missing",
                "scan_success": scan_result.get("success") is True,
                "execution_status": str(scan_result.get("execution_status") or ""),
            }
        fingerprints = {
            "input_fingerprint": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "fixture_fingerprint": hashlib.sha256(
                Path(str(runtime.get("fixture_snapshot_ref") or "")).resolve().read_bytes()
            ).hexdigest(),
            "context_fingerprint": hashlib.sha256(context_path.read_bytes()).hexdigest(),
        }
        run_id = str(scan_result.get("scan_id") or "").strip()
        if not run_id:
            raise PolicyEvaluationRunnerError(
                "product scan must emit an explicit scan_id"
            )
        if run_id != mainline_run["run_id"]:
            raise PolicyEvaluationRunnerError(
                "product scan scan_id does not match evaluator-preallocated run_id"
            )
        scan_v12 = (
            scan_result.get("v12")
            if isinstance(scan_result.get("v12"), dict)
            else {}
        )
        raw_scan_contract = scan_result.get("mainline_run") or scan_v12.get(
            "mainline_run"
        )
        if raw_scan_contract is None:
            raise PolicyEvaluationRunnerError(
                "product scan must emit its frozen mainline_run contract"
            )
        try:
            observed_scan_contract = validate_mainline_run_contract(
                dict(raw_scan_contract)
            )
        except (TypeError, ValueError, MainlineContractError) as exc:
            raise PolicyEvaluationRunnerError(
                f"product scan embedded mainline contract invalid: {exc}"
            ) from exc
        if (
            observed_scan_contract["contract_fingerprint"]
            != mainline_run["contract_fingerprint"]
        ):
            raise PolicyEvaluationRunnerError(
                "scan mainline contract does not match evaluator run"
            )
        projection_input = dict(scan_result)
        projection_input["mainline_run"] = observed_scan_contract
        evaluator_projection: dict[str, Any] = {}
        if mainline_run["private_evaluator_observation_allowed"]:
            from .discovery_evaluator_projection import (
                build_evaluator_only_projection,
            )

            try:
                evaluator_projection = build_evaluator_only_projection(
                    projection_input
                )
            except MainlineContractError as exc:
                raise PolicyEvaluationRunnerError(
                    f"private evaluator projection invalid: {exc}"
                ) from exc
            evaluator_findings = list(evaluator_projection["findings"])
            evaluator_candidates = list(evaluator_projection["candidates"])
        else:
            evaluator_findings = list(scan_result.get("findings") or [])
            evaluator_candidates = list(
                scan_result.get("candidate_findings") or []
            )

        obligation_attempt_ledger = (
            evaluator_projection.get("obligation_attempt_ledger")
            if evaluator_projection
            else (
                projection_input.get("obligation_attempt_ledger")
                or scan_v12.get("obligation_attempt_ledger")
            )
        )
        formal_count_projection = (
            evaluator_projection.get("formal_count_projection")
            if evaluator_projection
            else (
                projection_input.get("formal_count_projection")
                or scan_v12.get("formal_count_projection")
            )
        )
        canonical_defect_registry = (
            evaluator_projection.get("canonical_defect_registry")
            if evaluator_projection
            else (
                projection_input.get("canonical_defect_registry")
                or scan_v12.get("canonical_defect_registry")
            )
        )
        defect_identity_consistency = (
            evaluator_projection.get("defect_identity_consistency")
            if evaluator_projection
            else (
                projection_input.get("defect_identity_consistency")
                or scan_v12.get("defect_identity_consistency")
            )
        )
        delivery_occurrences = (
            evaluator_projection.get("delivery_occurrences")
            if evaluator_projection
            else (
                projection_input.get("delivery_occurrences")
                or scan_v12.get("delivery_occurrences")
            )
        )
        formal_delivery_authority = (
            evaluator_projection.get("formal_delivery_authority")
            if evaluator_projection
            else (
                projection_input.get("formal_delivery_authority")
                or scan_v12.get("formal_delivery_authority")
            )
        )

        trace_ledger = scan_result.get("trace_ledger")
        v12 = scan_v12
        if trace_ledger is None:
            trace_ledger = v12.get("trace_ledger")
        trace_source = v12 or scan_result
        if (
            trace_ledger is None
            and isinstance(trace_source.get("obligation_attempt_ledger"), dict)
            and isinstance(trace_source.get("formal_count_projection"), dict)
        ):
            from .discovery_trace_ledger import build_discovery_trace_ledger_v2

            trace_ledger = build_discovery_trace_ledger_v2(
                trace_source,
                run_id=run_id,
                policy_id=policy_id,
                target_id=str(target.get("target_id") or ""),
                project_id=project_id,
                industry=str(target.get("industry") or "unclassified"),
                evaluation_mode=evaluation_mode,
            )
        return {
            "schema_version": SCAN_RESULT_SCHEMA,
            "run_id": run_id,
            "target_id": str(target.get("target_id") or ""),
            "campaign_id": campaign_id,
            "policy_id": policy_id,
            "policy_version": str(kwargs.get("policy_version") or ""),
            "evaluation_mode": evaluation_mode,
            "execution_kind": "observed",
            "estimated_metrics_used": False,
            "customer_outputs_published": mainline_run["customer_outputs_published"],
            "mainline_run": mainline_run,
            "effective_strategy_fingerprint": strategy_fingerprint(effective_strategy),
            "fixture_audit_receipt_id": str(
                (kwargs.get("fixture_preparation_receipt") or {}).get("audit_receipt_id") or ""
            ),
            "runtime_fingerprint": str(target.get("runtime_fingerprint") or ""),
            **fingerprints,
            "findings": evaluator_findings,
            "candidates": evaluator_candidates,
            "evaluator_projection": evaluator_projection,
            "delivery_occurrences": delivery_occurrences,
            "obligation_attempt_ledger": obligation_attempt_ledger,
            "canonical_defect_registry": canonical_defect_registry,
            "formal_count_projection": formal_count_projection,
            "defect_identity_consistency": defect_identity_consistency,
            "formal_delivery_authority": formal_delivery_authority,
            "trace_ledger": trace_ledger,
            "pipeline_health": pipeline_health,
            "operational_metrics": operational,
            "process_boundary": process_boundary,
        }

    def finalize_after_cleanup(
        self,
        *,
        scan_output: dict[str, Any],
        cleanup_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply the post-scan campaign reset before evaluator adjudication."""

        defects = [
            dict(item)
            for item in list(scan_output.get("findings") or [])
            if isinstance(item, dict)
        ]
        candidates = [
            dict(item)
            for item in list(scan_output.get("candidates") or [])
            if isinstance(item, dict)
        ]
        operational = dict(scan_output.get("operational_metrics") or {})
        scenario_cleanup_failures = int(operational.get("cleanup_failures") or 0)
        # Preserve the original per-scenario cleanup failure count. Global reset
        # may only record environment_restored — never erase cleanup failures.
        operational["scenario_cleanup_failures_before_campaign_reset"] = scenario_cleanup_failures
        operational["cleanup_failures"] = scenario_cleanup_failures
        environment_restored = bool(
            cleanup_receipt.get("status") in {"completed", "SUCCEEDED", "succeeded"}
            or cleanup_receipt.get("dirty_environment") is False
        )
        operational["environment_restored"] = environment_restored
        if environment_restored:
            operational["dirty_test_environments"] = 0 if scenario_cleanup_failures == 0 else 1
        from .discovery_funnel import reconcile_pipeline_health_after_campaign_cleanup

        pipeline_health = reconcile_pipeline_health_after_campaign_cleanup(
            scan_output.get("pipeline_health") if isinstance(scan_output.get("pipeline_health"), dict) else {},
            findings=defects,
            scenario_cleanup_failures_recovered=0,
            environment_restored=environment_restored,
            original_cleanup_failures=scenario_cleanup_failures,
        )
        return {
            **scan_output,
            "findings": defects,
            "candidates": candidates,
            "operational_metrics": operational,
            "pipeline_health": pipeline_health,
            "campaign_cleanup_finalization": {
                "status": "SUCCEEDED" if environment_restored else "FAILED",
                "environment_restored": environment_restored,
                "audit_receipt_id": str(cleanup_receipt.get("audit_receipt_id") or ""),
                "after_cleanup_observation_ref": str(
                    cleanup_receipt.get("after_cleanup_observation_ref") or ""
                ),
                "readjudicated_defect_count": 0,
                "residual_candidate_count": len(candidates),
                "original_cleanup_failures": scenario_cleanup_failures,
                "cleanup_failures_preserved": True,
            },
        }
