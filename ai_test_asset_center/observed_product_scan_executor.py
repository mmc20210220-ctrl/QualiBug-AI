from __future__ import annotations

"""In-process adapter from evaluator runtime artifacts to the real scan entrypoint."""

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .discovery_policy_evaluation_runner import (
    SCAN_RESULT_SCHEMA,
    PolicyEvaluationRunnerError,
    strategy_fingerprint,
)
from .policy_wiring import get_effective_policy_strategy
from .target_policy import normalize_base_url
from .evaluator_execution_attestation import PROCESS_BOUNDARY_SCHEMA
from .observed_product_scan_protocol import (
    PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
    find_evaluator_private_context_paths,
    is_evaluator_secret_environment_name,
)


PRODUCT_SCAN_INPUT_SCHEMA = "qualibug.discovery-evaluation-input.v1"
PRODUCT_SCAN_CONTEXT_SCHEMA = "qualibug.discovery-evaluation-context.v1"


_CREDENTIAL_VALUE_KEYS = frozenset({
    "access_token",
    "api_key",
    "apikey",
    "client_secret",
    "id_token",
    "jwt",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
})
_ACCOUNT_IDENTITY_KEYS = (
    "account_ref",
    "account_id",
    "principal_ref",
    "principal_id",
    "email",
    "username",
    "id",
)
_ACCOUNT_COLLECTION_KEYS = ("accounts", "actors", "users")


def _normalized_account_identity(value: Any) -> str:
    return str(value or "").strip().casefold()


def _account_identity_values(
    row: dict[str, Any], *, source_key: str = ""
) -> set[str]:
    values = {
        _normalized_account_identity(row.get(key))
        for key in _ACCOUNT_IDENTITY_KEYS
    }
    if source_key:
        values.add(_normalized_account_identity(source_key))
    return {value for value in values if value}


def _account_rows(payload: Any) -> list[tuple[dict[str, Any], str]]:
    if isinstance(payload, list):
        return [
            (dict(row), "")
            for row in payload
            if isinstance(row, dict)
        ]
    if not isinstance(payload, dict):
        return []
    for collection_key in _ACCOUNT_COLLECTION_KEYS:
        collection = payload.get(collection_key)
        if isinstance(collection, list):
            return [
                (dict(row), "")
                for row in collection
                if isinstance(row, dict)
            ]
    return [
        (dict(value), str(key))
        for key, value in payload.items()
        if isinstance(value, dict)
        and str(key) not in {"schema", "schema_version", "meta"}
    ]


def _is_credential_value_key(key: Any) -> bool:
    normalized = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])",
        "_",
        str(key or "").strip(),
    ).lower().replace("-", "_")
    if normalized in {"secret_ref", "credential_ref", "credential_secret_ref"}:
        return False
    return normalized in _CREDENTIAL_VALUE_KEYS or normalized.endswith(
        ("_token", "_password", "_secret", "_api_key", "_private_key")
    )


def _merge_context_test_accounts_with_existing_credentials(
    accounts_path: Path,
    context_accounts: dict[str, Any],
) -> dict[str, Any]:
    """Keep fresh fixture credentials when context carries a stale snapshot.

    The evaluator fixture refreshes the product workspace account file immediately
    before the scan.  A context artifact may also carry an older token snapshot
    for identity binding.  Replacing the refreshed file with that snapshot makes
    every account appear unresolved after the runtime drops expired tokens.  Join
    rows only on explicit account coordinates and copy credential values from the
    existing product file; role/name similarity is intentionally not a join key.
    """
    if not isinstance(context_accounts, dict):
        raise PolicyEvaluationRunnerError(
            "product scan context test_accounts must be an object"
        )
    if not accounts_path.is_file():
        return dict(context_accounts)
    try:
        existing = json.loads(accounts_path.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyEvaluationRunnerError(
            f"existing runtime test account file is invalid: {accounts_path}"
        ) from exc
    existing_rows = _account_rows(existing)
    if not existing_rows:
        return dict(context_accounts)

    by_identity: dict[str, list[dict[str, Any]]] = {}
    for row, source_key in existing_rows:
        for identity in _account_identity_values(row, source_key=source_key):
            by_identity.setdefault(identity, []).append(row)

    def merge_row(row: dict[str, Any], source_key: str = "") -> dict[str, Any]:
        candidates: dict[int, dict[str, Any]] = {}
        for identity in _account_identity_values(row, source_key=source_key):
            for candidate in by_identity.get(identity, []):
                candidates[id(candidate)] = candidate
        if len(candidates) > 1:
            raise PolicyEvaluationRunnerError(
                "runtime test account credential identity is ambiguous"
            )
        if not candidates:
            return dict(row)
        merged = dict(row)
        current = next(iter(candidates.values()))
        for key, value in current.items():
            if _is_credential_value_key(key) and value not in (None, ""):
                merged[key] = value
        return merged

    merged_payload = dict(context_accounts)
    for collection_key in _ACCOUNT_COLLECTION_KEYS:
        collection = context_accounts.get(collection_key)
        if isinstance(collection, list):
            merged_payload[collection_key] = [
                merge_row(row)
                if isinstance(row, dict)
                else row
                for row in collection
            ]
            return merged_payload
    for key, value in context_accounts.items():
        if isinstance(value, dict):
            merged_payload[key] = merge_row(value, str(key))
    return merged_payload


def _sanitized_worker_environment(
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Remove evaluator-owned authority before starting product code."""

    source = os.environ if environment is None else environment
    return {
        str(name): str(value)
        for name, value in source.items()
        if not is_evaluator_secret_environment_name(str(name))
    }


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_isolated_product_worker(
    request: dict[str, Any],
    *,
    workspace_root: Path | str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute product scanning without evaluator secrets or in-process state."""

    if request.get("schema_version") != PRODUCT_SCAN_WORKER_REQUEST_SCHEMA:
        raise PolicyEvaluationRunnerError("product scan worker request schema is invalid")
    private_paths = find_evaluator_private_context_paths(request)
    if private_paths:
        raise PolicyEvaluationRunnerError(
            "product scan worker request contains evaluator-private fields: "
            + ",".join(private_paths)
        )
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        raise PolicyEvaluationRunnerError(f"product workspace not found: {root}")
    timeout = int(timeout_seconds)
    if timeout <= 0:
        raise PolicyEvaluationRunnerError("product scan worker timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="qualibug-observed-scan-") as directory:
        request_path = Path(directory) / "request.json"
        output_path = Path(directory) / "result.json"
        stdout_path = Path(directory) / "worker.stdout.log"
        stderr_path = Path(directory) / "worker.stderr.log"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "ai_test_asset_center.observed_product_scan_worker",
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ]
        # Redirect to files instead of capture_output=True. Long discovery scans
        # can emit large stdout/stderr; buffering them in the parent process has
        # OOM-killed observed diagnostics after ~10–16 minutes on Windows.
        with stdout_path.open("w", encoding="utf-8") as stdout_fh, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_fh:
            completed = subprocess.run(
                command,
                cwd=str(root),
                env=_sanitized_worker_environment(),
                stdout=stdout_fh,
                stderr=stderr_fh,
                text=True,
                timeout=timeout,
                check=False,
            )
        if completed.returncode != 0:
            detail_parts: list[str] = []
            for path in (stderr_path, stdout_path):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    text = ""
                if text:
                    detail_parts.append(text[-2000:])
            detail = "\n".join(detail_parts)
            raise PolicyEvaluationRunnerError(
                f"isolated product scan worker failed with exit code "
                f"{completed.returncode}: {detail}"
            )
        if not output_path.is_file():
            raise PolicyEvaluationRunnerError(
                "isolated product scan worker did not persist a result"
            )
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyEvaluationRunnerError(
                f"isolated product scan worker result is invalid: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise PolicyEvaluationRunnerError(
                "isolated product scan worker result must be an object"
            )
    boundary = {
        "schema_version": PROCESS_BOUNDARY_SCHEMA,
        "isolation": "isolated_subprocess",
        "worker_protocol_schema": PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
        "evaluator_secrets_removed": True,
        "request_fingerprint": _fingerprint(request),
        "result_fingerprint": _fingerprint(result),
        "exit_code": 0,
    }
    return result, boundary


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
        # utf-8-sig tolerates evaluator-exported bundles that carry a BOM.
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
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
        worker_timeout_seconds: int = 7200,
    ) -> None:
        if not callable(operational_metrics_collector):
            raise TypeError("operational_metrics_collector must be callable")
        self.workspace_root = Path(workspace_root).resolve()
        self.operational_metrics_collector = operational_metrics_collector
        self.worker_timeout_seconds = int(worker_timeout_seconds)
        if self.worker_timeout_seconds <= 0:
            raise ValueError("worker_timeout_seconds must be positive")

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        collector_module = str(
            getattr(self.operational_metrics_collector, "__module__", "") or ""
        ).strip()
        collector_name = str(
            getattr(self.operational_metrics_collector, "__qualname__", "") or ""
        ).strip()
        if not collector_module or not collector_name or "<locals>" in collector_name:
            raise PolicyEvaluationRunnerError(
                "operational metrics collector must be an importable module callable"
            )
        request = {
            "schema_version": PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
            "workspace_root": str(self.workspace_root),
            "operational_metrics_collector": {
                "module": collector_module,
                "qualname": collector_name,
            },
            "strategy": asdict(get_effective_policy_strategy()),
            "invocation": kwargs,
        }
        result, boundary = _run_isolated_product_worker(
            request,
            workspace_root=self.workspace_root,
            timeout_seconds=self.worker_timeout_seconds,
        )
        return {**result, "process_boundary": boundary}

    def _execute_in_process(self, **kwargs: Any) -> dict[str, Any]:
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
        transport_base_url = base_url
        proxy_value = str(kwargs.get("observation_proxy_base_url") or "").strip()
        if proxy_value:
            normalized_proxy = normalize_base_url(proxy_value)
            parsed_proxy = urlsplit(normalized_proxy)
            try:
                loopback = ipaddress.ip_address(
                    str(parsed_proxy.hostname or "")
                ).is_loopback
            except ValueError:
                loopback = False
            if parsed_proxy.scheme != "http" or not loopback:
                raise PolicyEvaluationRunnerError(
                    "observation proxy must be a loopback HTTP endpoint"
                )
            transport_base_url = normalized_proxy
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
        evaluation_campaign_id = str(kwargs.get("campaign_id") or "").strip()
        policy_id = str(kwargs.get("policy_id") or "").strip()
        policy_version = str(kwargs.get("policy_version") or "").strip()
        evaluation_mode = str(kwargs.get("evaluation_mode") or "").strip()
        agent_semantic_linking_enabled = kwargs.get(
            "agent_semantic_linking_enabled",
            False,
        )
        if not isinstance(agent_semantic_linking_enabled, bool):
            raise PolicyEvaluationRunnerError(
                "agent_semantic_linking_enabled must be boolean"
            )
        target_id = str(target.get("target_id") or "").strip()
        run_id = "RUN_" + hashlib.sha256(
            (
                f"{evaluation_campaign_id}:{target_id}:"
                f"{policy_id}:{evaluation_mode}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        strategy = get_effective_policy_strategy()
        context.update({
            "run_id": run_id,
            # The evaluator fixture campaign and product campaign are distinct
            # authorities. EnterpriseCampaign derives the latter from frozen
            # source/scope/environment identity; injecting the evaluator id as
            # campaign_id would corrupt that identity and fail the mainline.
            "evaluation_campaign_id": evaluation_campaign_id,
            "target_id": target_id,
            "environment_id": str(runtime.get("environment_ref") or ""),
            "environment_ref": str(runtime.get("environment_ref") or ""),
            "environment_type": str(runtime.get("environment_type") or ""),
            "target_environment": str(runtime.get("environment_type") or ""),
            "environment_kind": str(runtime.get("environment_type") or ""),
            "policy_id": policy_id,
            "policy_version": policy_version,
            "evaluation_mode": evaluation_mode,
            "mainline_authority": str(
                getattr(getattr(strategy, "execution", None), "mainline_authority", "")
                or ""
            ),
            "agent_semantic_linking_enabled": (
                agent_semantic_linking_enabled
            ),
            "fixture_audit_receipt_id": str(
                (kwargs.get("fixture_preparation_receipt") or {}).get("audit_receipt_id") or ""
            ),
        })

        test_accounts = context_bundle.get("test_accounts")
        accounts_path: Path | None = None
        previous_accounts: bytes | None = None
        accounts_existed = False
        if test_accounts is not None:
            if not isinstance(test_accounts, dict):
                raise PolicyEvaluationRunnerError("product scan context test_accounts must be an object")
            accounts_path = self.workspace_root / "platform_inputs" / project_id / "test_accounts.json"
            accounts_path.parent.mkdir(parents=True, exist_ok=True)
            accounts_existed = accounts_path.is_file()
            previous_accounts = accounts_path.read_bytes() if accounts_existed else None
            test_accounts = _merge_context_test_accounts_with_existing_credentials(
                accounts_path,
                test_accounts,
            )
            temporary = accounts_path.with_suffix(accounts_path.suffix + f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(test_accounts, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, accounts_path)

        from .__main__ import scan

        started = time.monotonic()
        try:
            scan_result = scan(
                project=project_id,
                root=self.workspace_root,
                prd_text=prd_text,
                api_doc_path=str(api_doc_path),
                base_url=transport_base_url,
                ci_gate=False,
                multi_layer=bool(input_bundle.get("multi_layer", True)),
                output_dir=(
                    self.workspace_root
                    / "platform_outputs"
                    / project_id
                    / "evaluation_runs"
                    / evaluation_campaign_id
                ),
                save_report=False,
                campaign_context=context,
            )
        finally:
            if accounts_path is not None:
                if accounts_existed:
                    restore = accounts_path.with_suffix(
                        accounts_path.suffix + f".{os.getpid()}.restore"
                    )
                    restore.write_bytes(previous_accounts or b"")
                    os.replace(restore, accounts_path)
                elif accounts_path.exists():
                    accounts_path.unlink()
        wall_clock_seconds = round(time.monotonic() - started, 6)
        if not isinstance(scan_result, dict):
            raise PolicyEvaluationRunnerError("product scan did not return a result object")
        if scan_result.get("success") is not True:
            failure_stage = str(scan_result.get("failure_stage") or "scan").strip()
            failure_reason = str(
                scan_result.get("error")
                or scan_result.get("reason")
                or "product_scan_unsuccessful"
            ).strip()
            raise PolicyEvaluationRunnerError(
                "product scan failed before evaluator projection: "
                f"stage={failure_stage or 'scan'} reason={failure_reason[:1000]}"
            )
        operational = self.operational_metrics_collector(
            scan_result=scan_result,
            wall_clock_seconds=wall_clock_seconds,
            runtime_view=runtime_view,
            campaign_id=evaluation_campaign_id,
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
        v12 = scan_result.get("v12") if isinstance(scan_result.get("v12"), dict) else {}
        mainline = scan_result.get("mainline_run")
        if not isinstance(mainline, dict):
            mainline = v12.get("mainline_run") if isinstance(v12.get("mainline_run"), dict) else {}
        required_authority: dict[str, Any] = {}
        for field in (
            "obligation_attempt_ledger",
            "canonical_defect_registry",
            "formal_delivery_authority",
            "formal_count_projection",
            "defect_identity_consistency",
        ):
            value = scan_result.get(field)
            if not isinstance(value, dict):
                value = v12.get(field)
            if not isinstance(value, dict):
                raise PolicyEvaluationRunnerError(
                    f"product scan did not emit required evaluation authority: {field}"
                )
            required_authority[field] = dict(value)
        delivery_occurrences = scan_result.get("delivery_occurrences")
        if not isinstance(delivery_occurrences, list):
            delivery_occurrences = v12.get("delivery_occurrences")
        if not isinstance(delivery_occurrences, list):
            raise PolicyEvaluationRunnerError(
                "product scan did not emit required evaluation authority: delivery_occurrences"
            )
        evaluator_findings = v12.get("evaluator_canonical_findings")
        findings = (
            evaluator_findings
            if isinstance(evaluator_findings, list)
            else scan_result.get("findings")
        )
        if not isinstance(findings, list):
            raise PolicyEvaluationRunnerError("product scan findings must be a list")
        runtime_interface_discovery = scan_result.get(
            "runtime_interface_discovery"
        )
        if not isinstance(runtime_interface_discovery, dict):
            runtime_interface_discovery = v12.get("runtime_interface_discovery")
        if not isinstance(runtime_interface_discovery, dict):
            runtime_interface_discovery = {}
        if (
            context.get("runtime_interface_discovery_enabled") is True
            and not runtime_interface_discovery
        ):
            raise PolicyEvaluationRunnerError(
                "product scan omitted runtime interface discovery execution"
            )
        resolved_run_id = str(mainline.get("run_id") or "").strip()
        if not resolved_run_id:
            raise PolicyEvaluationRunnerError("product scan mainline run_id is missing")
        return {
            "schema_version": SCAN_RESULT_SCHEMA,
            "run_id": resolved_run_id,
            "target_id": target_id,
            "campaign_id": evaluation_campaign_id,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "evaluation_mode": evaluation_mode,
            "execution_kind": "observed",
            "estimated_metrics_used": False,
            "customer_outputs_published": bool(
                mainline.get("customer_outputs_published")
            ),
            "effective_strategy_fingerprint": strategy_fingerprint(strategy),
            "fixture_audit_receipt_id": str(
                (kwargs.get("fixture_preparation_receipt") or {}).get("audit_receipt_id") or ""
            ),
            "runtime_fingerprint": str(target.get("runtime_fingerprint") or ""),
            **fingerprints,
            "mainline_run": dict(mainline),
            **required_authority,
            "delivery_occurrences": [
                dict(row) for row in delivery_occurrences if isinstance(row, dict)
            ],
            "trace_ledger": (
                dict(scan_result.get("trace_ledger"))
                if isinstance(scan_result.get("trace_ledger"), dict)
                else None
            ),
            "findings": [dict(row) for row in findings if isinstance(row, dict)],
            "candidates": list(scan_result.get("candidate_findings") or []),
            "runtime_interface_discovery": runtime_interface_discovery,
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

        delivery_occurrences = list(scan_output.get("delivery_occurrences") or [])
        validated_occurrences, cleanup_residual = apply_governed_campaign_cleanup(
            delivery_occurrences,
            cleanup_receipt,
        )
        if cleanup_residual or validated_occurrences != delivery_occurrences:
            raise PolicyEvaluationRunnerError(
                "campaign cleanup cannot rewrite the immutable formal delivery scope"
            )
        defects = [
            dict(item)
            for item in list(scan_output.get("findings") or [])
            if isinstance(item, dict)
        ]
        candidates = [
            item for item in list(scan_output.get("candidates") or [])
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
