from __future__ import annotations

"""In-process adapter from evaluator runtime artifacts to the real scan entrypoint."""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

from .discovery_policy_evaluation_runner import (
    SCAN_RESULT_SCHEMA,
    PolicyEvaluationRunnerError,
    strategy_fingerprint,
)
from .policy_wiring import get_effective_policy_strategy


PRODUCT_SCAN_INPUT_SCHEMA = "qualibug.discovery-evaluation-input.v1"
PRODUCT_SCAN_CONTEXT_SCHEMA = "qualibug.discovery-evaluation-context.v1"


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


def _has_private_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in {
                "ground_truth",
                "ground_truth_ref",
                "ground_truth_fingerprint",
                "expected_defects",
                "expected_bug_ids",
            }:
                return True
            if _has_private_key(item):
                return True
    elif isinstance(value, list):
        return any(_has_private_key(item) for item in value)
    return False


class ObservedProductScanExecutor:
    """Call ``ai_test_asset_center.__main__.scan`` with no evaluator oracle data."""

    def __init__(
        self,
        *,
        workspace_root: Path | str,
        operational_metrics_collector: OperationalMetricsCollector,
    ) -> None:
        if not callable(operational_metrics_collector):
            raise TypeError("operational_metrics_collector must be callable")
        self.workspace_root = Path(workspace_root).resolve()
        self.operational_metrics_collector = operational_metrics_collector

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        runtime_view = kwargs.get("runtime_view")
        if not isinstance(runtime_view, dict) or _has_private_key(runtime_view):
            raise PolicyEvaluationRunnerError("product scan runtime view is invalid or leaks evaluator data")
        target = runtime_view.get("target") if isinstance(runtime_view.get("target"), dict) else {}
        runtime = target.get("runtime") if isinstance(target.get("runtime"), dict) else {}
        input_path, input_bundle = _load_json(runtime.get("input_bundle_ref"), "input_bundle_ref")
        context_path, context_bundle = _load_json(runtime.get("context_artifact_ref"), "context_artifact_ref")
        if input_bundle.get("schema_version") != PRODUCT_SCAN_INPUT_SCHEMA:
            raise PolicyEvaluationRunnerError("product scan input bundle schema is unsupported")
        if context_bundle.get("schema_version") != PRODUCT_SCAN_CONTEXT_SCHEMA:
            raise PolicyEvaluationRunnerError("product scan context artifact schema is unsupported")
        if _has_private_key(input_bundle) or _has_private_key(context_bundle):
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
            accounts_path = self.workspace_root / "platform_inputs" / project_id / "test_accounts.json"
            accounts_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = accounts_path.with_suffix(accounts_path.suffix + f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(test_accounts, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, accounts_path)
        campaign_id = str(kwargs.get("campaign_id") or "").strip()
        policy_id = str(kwargs.get("policy_id") or "").strip()
        evaluation_mode = str(kwargs.get("evaluation_mode") or "").strip()
        context.update({
            "campaign_id": campaign_id,
            "environment_ref": str(runtime.get("environment_ref") or ""),
            "target_environment": str(runtime.get("environment_type") or ""),
            "environment_kind": str(runtime.get("environment_type") or ""),
            "policy_id": policy_id,
            "evaluation_mode": evaluation_mode,
            "fixture_audit_receipt_id": str(
                (kwargs.get("fixture_preparation_receipt") or {}).get("audit_receipt_id") or ""
            ),
        })

        from .__main__ import scan

        started = time.monotonic()
        scan_result = scan(
            project=project_id,
            root=self.workspace_root,
            prd_text=prd_text,
            api_doc_path=str(api_doc_path),
            base_url=base_url,
            ci_gate=False,
            multi_layer=bool(input_bundle.get("multi_layer", True)),
            output_dir=self.workspace_root / "platform_outputs" / project_id / "evaluation_runs" / campaign_id,
            save_report=False,
            campaign_context=context,
        )
        wall_clock_seconds = round(time.monotonic() - started, 6)
        if not isinstance(scan_result, dict):
            raise PolicyEvaluationRunnerError("product scan did not return a result object")
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
        return {
            "schema_version": SCAN_RESULT_SCHEMA,
            "run_id": str(scan_result.get("scan_id") or campaign_id),
            "target_id": str(target.get("target_id") or ""),
            "campaign_id": campaign_id,
            "policy_id": policy_id,
            "policy_version": str(kwargs.get("policy_version") or ""),
            "evaluation_mode": evaluation_mode,
            "execution_kind": "observed",
            "estimated_metrics_used": False,
            "customer_outputs_published": False,
            "effective_strategy_fingerprint": strategy_fingerprint(get_effective_policy_strategy()),
            "fixture_audit_receipt_id": str(
                (kwargs.get("fixture_preparation_receipt") or {}).get("audit_receipt_id") or ""
            ),
            "runtime_fingerprint": str(target.get("runtime_fingerprint") or ""),
            **fingerprints,
            "findings": list(scan_result.get("findings") or []),
            "candidates": list(scan_result.get("candidate_findings") or []),
            "pipeline_health": pipeline_health,
            "operational_metrics": operational,
        }

    def finalize_after_cleanup(
        self,
        *,
        scan_output: dict[str, Any],
        cleanup_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply the post-scan campaign reset before evaluator adjudication."""

        from .customer_delivery_gate import apply_governed_campaign_cleanup

        defects, cleanup_residual = apply_governed_campaign_cleanup(
            list(scan_output.get("findings") or []),
            cleanup_receipt,
        )
        candidates = [
            item for item in list(scan_output.get("candidates") or []) + cleanup_residual
            if isinstance(item, dict)
        ]
        deduped_candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in candidates:
            reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
            key = (
                str(item.get("title") or ""),
                str(reproduction.get("method") or item.get("repro_method") or ""),
                str(reproduction.get("path") or item.get("repro_path") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped_candidates.append(item)
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
            "candidates": deduped_candidates,
            "operational_metrics": operational,
            "pipeline_health": pipeline_health,
            "campaign_cleanup_finalization": {
                "status": "SUCCEEDED" if environment_restored else "FAILED",
                "environment_restored": environment_restored,
                "audit_receipt_id": str(cleanup_receipt.get("audit_receipt_id") or ""),
                "after_cleanup_observation_ref": str(
                    cleanup_receipt.get("after_cleanup_observation_ref") or ""
                ),
                "readjudicated_defect_count": len(defects),
                "residual_candidate_count": len(deduped_candidates),
                "original_cleanup_failures": scenario_cleanup_failures,
                "cleanup_failures_preserved": True,
            },
        }
