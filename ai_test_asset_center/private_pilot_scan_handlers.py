"""Scan, preflight and regression-run HTTP authorities.

One project has one scan lease across manual, continuous and regression entry
points. A request executes at most once and may persist only report data whose
identity is explicitly bound to the returned scan id.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist
from .private_pilot_continuous import _update_continuous_state
from .private_pilot_debug_client import _dbg_fingerprint_payload, _dbg_report
from .private_pilot_json_io import _write_json_object_atomic
from .private_pilot_scan_coordinator import ScanLeaseBusy, project_scan_lease
from .private_pilot_scan_prep import (
    _is_local_private_service,
    _issue_runtime_approval_for_result,
    _prepare_v12_scan_body,
    _validate_scan_base_url,
)
from .scan_counter import increment_scan_counter
from .target_policy import build_target_policy_decision

_scan_logger = logging.getLogger("qualibug.scan")
_SCAN_ROLES = {"project_owner", "qa_lead", "testops_admin", "admin"}
_DESTRUCTIVE_REGRESSION_ROLES = {"project_owner", "testops_admin", "admin"}


def _finalization_event(
    scan_id: str,
    phase: str,
    *,
    elapsed_ms: int = 0,
    detail: Any = None,
) -> None:
    """Structured scan finalization lifecycle telemetry (one event per phase).

    Every phase between pipeline completion and the HTTP response is logged so
    a hung finalization can be attributed to the exact step that never
    completed. This is observability only; it never changes control flow.
    """
    import threading as _threading

    # WARNING level on purpose: the product logging root sits at WARNING, so
    # INFO records are dropped and the lifecycle timeline would be invisible
    # exactly when a finalization hangs. Lifecycle telemetry must survive.
    _scan_logger.warning(
        "scan.finalization.phase",
        extra={
            "context": {
                "event": "scan.finalization.phase",
                "scan_id": str(scan_id or "")[:64],
                "phase": str(phase),
                "thread_id": _threading.get_ident(),
                "process_id": os.getpid(),
                "elapsed_ms": int(elapsed_ms),
                "detail": detail,
            }
        },
    )


def _response_stall_watchdog(scan_id: str, stall_seconds: int = 120) -> Any:
    """Diagnostic-only watchdog: if the finalization response has not been
    written within stall_seconds, log where the thread is stuck.

    This never changes control flow and never masks the hang; it exists so the
    exact finalization phase can be attributed when the response fails to
    close. The daemon exits with the process, and the last observed phase is
    reported when the watchdog fires.
    """
    import threading as _threading

    last_phase: dict[str, str] = {}
    _written = _threading.Event()

    def _watch() -> None:
        if _written.wait(stall_seconds):
            return  # response written; nothing to report
        _scan_logger.warning(
            "scan.finalization.stalled",
            extra={
                "context": {
                    "event": "scan.finalization.stalled",
                    "scan_id": str(scan_id or "")[:64],
                    "stall_seconds": int(stall_seconds),
                    "thread_id": _threading.get_ident(),
                    "process_id": os.getpid(),
                    "last_phase": str(last_phase.get("phase") or "response_not_started"),
                }
            },
        )

    def _mark(phase: str) -> None:
        last_phase["phase"] = phase
        if phase == "response_written":
            _written.set()

    watchdog = _threading.Thread(
        target=_watch,
        name=f"scan-finalize-watchdog-{str(scan_id or '')[:12]}",
        daemon=True,
    )
    watchdog.start()
    return {"mark": _mark, "thread": watchdog}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _finding_key(finding: dict[str, Any]) -> tuple[str, str, str]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    title = _text(finding.get("title") or finding.get("description")).lower()[:240]
    method = _text(
        finding.get("_api_method") or finding.get("method") or evidence.get("method")
    ).upper()
    path = _text(
        finding.get("_api_path") or finding.get("path") or evidence.get("path")
    )
    return title, method, path


def _collect_findings(result: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for value in (
        report.get("real_findings"),
        report.get("bug_scores"),
        result.get("real_findings"),
        result.get("bug_scores"),
        result.get("db_findings"),
        result.get("e2e_findings"),
        result.get("deep_findings"),
        result.get("ui_findings"),
    ):
        if isinstance(value, list):
            candidates.extend(value)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        key = _finding_key(raw)
        if not any(key) or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "title": _text(raw.get("title") or raw.get("description"))[:500],
                "severity": _text(raw.get("severity")) or "P1",
                "category": _text(raw.get("category") or raw.get("risk_type")),
                "description": _text(raw.get("description"))[:500],
                "confidence_score": float(
                    raw.get("confidence_score") or raw.get("score") or 0
                ),
                "_api_path": _text(raw.get("_api_path") or raw.get("path")),
                "_api_method": _text(raw.get("_api_method") or raw.get("method")),
                "evidence": dict(raw.get("evidence"))
                if isinstance(raw.get("evidence"), dict)
                else {},
            }
        )
    return rows


def _report_scan_id(report: dict[str, Any]) -> str:
    for value in (
        report.get("scan_id"),
        (report.get("scan_meta") or {}).get("scan_id")
        if isinstance(report.get("scan_meta"), dict)
        else "",
        (report.get("metadata") or {}).get("scan_id")
        if isinstance(report.get("metadata"), dict)
        else "",
        (report.get("runtime_contract") or {}).get("scan_id")
        if isinstance(report.get("runtime_contract"), dict)
        else "",
    ):
        text = _text(value)
        if text:
            return text
    return ""


class ScanHandlersMixin:
    def _handle_scan_preflight(
        self,
        project: str,
        root: Path,
        body: dict[str, Any] | None = None,
    ) -> None:
        request = dict(body or {})
        reasons: list[dict[str, str]] = []
        service_config_path = (
            root / "platform_workspace" / project / "multi_service_config.json"
        )
        services: list[Any] = []
        if service_config_path.exists():
            try:
                service_config = _read_json_object(service_config_path)
                raw_services = service_config.get("services", [])
                if not isinstance(raw_services, list) or any(
                    not isinstance(item, dict) for item in raw_services
                ):
                    raise ValueError("services must be a list of objects")
                services = raw_services
            except Exception as exc:
                reasons.append(
                    {
                        "code": "SERVICE_CONFIG_INVALID",
                        "message": f"服务凭证配置无法解析: {exc}",
                    }
                )
        if not services:
            reasons.append(
                {
                    "code": "NO_CREDENTIALS",
                    "message": "尚未配置任何服务凭证，请先在设置页保存。",
                }
            )

        assets: list[dict[str, Any]] = []
        try:
            from .enterprise_source_registry import list_source_assets

            value = list_source_assets(project, root=root)
            if isinstance(value, list):
                assets = [item for item in value if isinstance(item, dict)]
        except Exception as exc:
            reasons.append(
                {
                    "code": "SOURCE_REGISTRY_UNAVAILABLE",
                    "message": f"企业资料注册表不可用: {exc}",
                }
            )
        if not assets:
            reasons.append(
                {
                    "code": "NO_SOURCE",
                    "message": "尚未入库任何企业资料，请先接入或上传资料。",
                }
            )
        else:
            source_types = {
                _text(asset.get("source_type")).lower() for asset in assets
            }
            if not source_types.intersection(
                {
                    "openapi",
                    "openapi3",
                    "swagger",
                    "postman",
                    "api_spec",
                    "markdown_api",
                }
            ):
                reasons.append(
                    {
                        "code": "NO_API_SPEC",
                        "message": "缺少可执行 API 契约，接口探针将无法正式执行。",
                    }
                )

        approved_url = _text(request.get("approved_base_url"))
        base_url = _text(
            request.get("target_url") or request.get("base_url") or approved_url
        )
        environment_type = _text(
            request.get("environment_type") or request.get("environment_kind")
        )
        environment_ref = _text(
            request.get("environment_ref") or request.get("target_id")
        )
        project_config_path = (
            root / "platform_inputs" / project / "real_project_config.json"
        )
        project_config: dict[str, Any] = {}
        if project_config_path.is_file():
            try:
                project_config = _read_json_object(project_config_path)
            except Exception as exc:
                reasons.append(
                    {
                        "code": "PROJECT_CONFIG_INVALID",
                        "message": f"项目运行配置无法解析: {exc}",
                    }
                )
            base_url = base_url or _text(project_config.get("base_url"))
            approved_url = approved_url or _text(
                project_config.get("approved_base_url")
            )
            approved_urls = project_config.get("approved_base_urls")
            if (
                not approved_url
                and isinstance(approved_urls, list)
                and len(approved_urls) == 1
            ):
                approved_url = _text(approved_urls[0])
            environment_type = environment_type or _text(
                project_config.get("environment_type")
                or project_config.get("environment_kind")
            )
            environment_ref = environment_ref or _text(
                project_config.get("environment_ref")
                or project_config.get("target_id")
            )
        if not base_url:
            reasons.append(
                {"code": "NO_TARGET", "message": "未配置被测目标 base_url。"}
            )

        read_only = request.get("read_only") is True
        target_policy = build_target_policy_decision(
            requested_base_url=base_url,
            approved_base_url=approved_url,
            environment_type=environment_type,
            environment_ref=environment_ref,
            execution_mode="safe_read_only"
            if read_only
            else "approved_sandbox_write",
            runtime_status="approved",
        )
        blocking_codes = list(target_policy.get("blocking_codes") or [])
        if read_only:
            blocking_codes = [
                code
                for code in blocking_codes
                if code
                not in {
                    "READ_ONLY_MODE",
                    "UNKNOWN_ENVIRONMENT",
                    "PRODUCTION_WRITE_BLOCKED",
                }
            ]
        for code in blocking_codes:
            reasons.append(
                {
                    "code": str(code),
                    "message": "补全明确环境、精确批准 URL 和运行授权后重试。",
                }
            )

        unique_reasons: list[dict[str, str]] = []
        seen_codes: set[str] = set()
        for reason in reasons:
            code = _text(reason.get("code"))
            if code and code not in seen_codes:
                seen_codes.add(code)
                unique_reasons.append(reason)
        return self._json(
            {
                "ok": True,
                "schema_version": "qualibug.environment-preflight.v1",
                "project_id": project,
                "ready": not unique_reasons,
                "blocking_codes": [row["code"] for row in unique_reasons],
                "reasons": unique_reasons,
                "target_policy_decision": target_policy,
                "input_checks": {
                    "credentials": {
                        "status": "passed" if services else "blocked",
                        "service_count": len(services),
                    },
                    "sources": {
                        "status": "passed" if assets else "blocked",
                        "source_count": len(assets),
                    },
                    "target": {
                        "status": "passed" if base_url else "blocked",
                        "target_url": base_url,
                        "approved_base_url": approved_url,
                    },
                    "environment": {
                        "status": "passed"
                        if environment_type and environment_ref
                        else "blocked",
                        "environment_type": environment_type,
                        "environment_ref": environment_ref,
                    },
                    "target_policy": {
                        "status": "passed"
                        if (
                            target_policy.get("read_allowed")
                            if read_only
                            else target_policy.get("write_allowed")
                        )
                        else "blocked"
                    },
                },
            }
        )

    def _bound_scan_report(
        self,
        project: str,
        root: Path,
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result_scan_id = _text(result.get("scan_id"))
        report_ref = _text(result.get("report_path"))
        if report_ref:
            candidate = Path(report_ref)
            report_path = (
                candidate.resolve()
                if candidate.is_absolute()
                else (root / candidate).resolve()
            )
        else:
            report_path = (
                root / "platform_outputs" / project / "intelligence_report.json"
            ).resolve()
        allowed_root = (root / "platform_outputs" / project).resolve()
        try:
            report_path.relative_to(allowed_root)
        except ValueError:
            return {}, {
                "status": "blocked",
                "reason": "report_path_outside_project_outputs",
                "report_path": str(report_path),
            }
        if not report_path.exists() or not report_path.is_file():
            return {}, {
                "status": "missing",
                "reason": "report_not_found",
                "report_path": str(report_path),
            }
        report = _read_json_object(report_path)
        report_scan_id = _report_scan_id(report)
        if not result_scan_id or report_scan_id != result_scan_id:
            return {}, {
                "status": "unbound",
                "reason": "report_scan_id_mismatch_or_missing",
                "result_scan_id": result_scan_id,
                "report_scan_id": report_scan_id,
                "report_path": str(report_path),
            }
        return report, {
            "status": "bound",
            "result_scan_id": result_scan_id,
            "report_scan_id": report_scan_id,
            "report_path": str(report_path),
        }

    def _persist_scan_result(
        self,
        project: str,
        root: Path,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        scan_id = _text(result.get("scan_id"))
        _step_started = time.perf_counter()

        def _step(phase: str, detail: Any = None) -> None:
            _finalization_event(
                scan_id,
                phase,
                elapsed_ms=int((time.perf_counter() - _step_started) * 1000),
                detail=detail,
            )

        _step("persist_started")
        report, report_binding = self._bound_scan_report(project, root, result)
        _step("persist_bound_report")
        findings = _collect_findings(result, report)
        _step("persist_collect_findings", {"count": len(findings)})
        tenant_id = self._request_tenant()
        enriched = dict(result)
        enriched["findings"] = findings
        enriched["report_binding"] = report_binding
        scan_record_id = db_persist.save_scan(
            root,
            tenant_id,
            project,
            enriched,
        )
        _step("persist_save_scan", {"scan_record_id": scan_record_id})
        cumulative = db_persist.merge_findings_cumulative(
            root,
            tenant_id,
            project,
            scan_record_id,
            findings,
        )
        _step("persist_merge_cumulative", dict(cumulative))
        projection_errors: list[dict[str, str]] = []
        try:
            increment_scan_counter(
                root / "platform_outputs" / project / "scan_counter.json"
            )
            _step("persist_scan_counter")
        except Exception as exc:
            projection_errors.append(
                {
                    "projection": "scan_counter",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
            )
        if result.get("spectrum"):
            try:
                spectrum_path = (
                    root
                    / "platform_outputs"
                    / project
                    / "spectrum"
                    / "spectrum_result.json"
                )
                _write_json_object_atomic(spectrum_path, dict(result["spectrum"]))
                _step("persist_spectrum")
            except Exception as exc:
                projection_errors.append(
                    {
                        "projection": "spectrum",
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                    }
                )
        try:
            _update_continuous_state(root, project, result)
            _step("persist_continuous_state")
        except Exception as exc:
            projection_errors.append(
                {
                    "projection": "continuous_state",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
            )
        _step("persist_done")
        return {
            "scan_record_id": scan_record_id,
            **cumulative,
            "report_binding": report_binding,
            "projection_status": "degraded" if projection_errors else "complete",
            "projection_errors": projection_errors,
        }

    def _handle_v12_scan(
        self,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> None:
        if not self._require_role(actor, _SCAN_ROLES, "scan execution"):
            return None
        tenant_id = self._request_tenant()
        principal = self._principal()
        if principal.get("auth_type") == "local_development":
            # Workspace-provisioned projects never touch the account registry;
            # register the tenant/project rows so scan persistence can own the
            # envelope. Loopback binding is enforced in _principal for this
            # auth type; credential-authenticated principals never auto-provision.
            db_persist.ensure_workspace_owned_project(root, tenant_id, project)
        try:
            with project_scan_lease(
                root,
                project,
                mode="manual_scan",
                tenant_id=tenant_id,
                actor=actor,
            ):
                return self._execute_v12_scan(project, root, actor, body)
        except ScanLeaseBusy as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "PROJECT_SCAN_ALREADY_RUNNING",
                    "message": "该项目已有检测任务运行中。",
                    "active_scan": exc.owner,
                },
                409,
            )
        finally:
            _finalization_event("", "lease_released", detail={"project": project})

    def _execute_v12_scan(
        self,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> None:
        try:
            from .__main__ import scan
            from .enterprise_source_registry import compose_project_source_manifest
            from .private_pilot_scan_context_contract import (
                SCAN_CAMPAIGN_CONTEXT,
                build_campaign_context_from_scan_body,
                prepare_scan_body_for_campaign,
            )

            trace_id = uuid.uuid4().hex
            request = dict(body)
            if request.get("read_only") is True:
                request["execution_mode"] = "safe_read_only"
            request = prepare_scan_body_for_campaign(project, root, request)
            manifest = (
                request.get("source_manifest")
                if isinstance(request.get("source_manifest"), dict)
                else {}
            )
            manifest_valid = bool(_text(manifest.get("source_id"))) and len(
                _text(manifest.get("source_hash"))
            ) == 64
            if not manifest_valid:
                composed = compose_project_source_manifest(
                    project,
                    root=root,
                    actor=actor,
                )
                source_id = _text(composed.get("source_id"))
                source_hash = _text(composed.get("source_hash")).lower()
                if source_id and len(source_hash) == 64:
                    request["source_manifest"] = {
                        "source_id": source_id,
                        "source_hash": source_hash,
                    }
                    request["source_composition"] = {
                        "part_count": composed.get("part_count") or 0,
                        "composed_from": composed.get("composed_from") or [],
                    }

            local_dev_mode = _is_local_private_service(self.server)
            prepared = _prepare_v12_scan_body(
                project,
                root,
                actor,
                request,
                local_dev_mode=local_dev_mode,
            )
            api_doc = _text(
                prepared.get("api_doc") or prepared.get("api_doc_text")
            )
            base_url = _text(prepared.get("base_url"))
            if base_url:
                _validate_scan_base_url(
                    base_url,
                    local_dev_mode=local_dev_mode,
                )
            if not api_doc:
                fallback = root / "platform_outputs" / project / "api_spec.md"
                if fallback.exists() and fallback.is_file():
                    api_doc = fallback.read_text(encoding="utf-8")
                    prepared["api_doc"] = api_doc
            campaign_context = build_campaign_context_from_scan_body(prepared)
            _dbg_report(
                hypothesis_id="SCAN_SINGLE_RUN",
                msg="[DEBUG] executing one prepared scan",
                data={
                    "actor": {
                        "name": _text(actor.get("name")),
                        "role": _text(actor.get("role")),
                    },
                    "prepared_body": _dbg_fingerprint_payload(prepared),
                    "campaign_context": _dbg_fingerprint_payload(campaign_context),
                },
                trace_id=trace_id,
            )
            context_token = SCAN_CAMPAIGN_CONTEXT.set(campaign_context or None)
            try:
                result = scan(
                    project=project,
                    root=root,
                    prd_text=_text(prepared.get("prd")),
                    api_doc_text=_text(
                        prepared.get("api_doc")
                        or prepared.get("api_doc_text")
                        or api_doc
                    ),
                    base_url=_text(prepared.get("base_url") or base_url),
                    multi_layer=bool(_text(prepared.get("base_url") or base_url)),
                    campaign_context=campaign_context,
                )
            finally:
                SCAN_CAMPAIGN_CONTEXT.reset(context_token)
            if not isinstance(result, dict):
                raise TypeError("scan result must be an object")

            future_approval_id = _issue_runtime_approval_for_result(
                project,
                root,
                actor,
                prepared,
                result,
                local_dev_mode=local_dev_mode,
            )
            scan_failed = (
                result.get("success") is False
                or bool(result.get("error"))
                or not _text(result.get("scan_id"))
            )
            if scan_failed:
                return self._json(
                    {
                        "ok": False,
                        "error": _text(result.get("error"))
                        or "SCAN_PRODUCED_NO_RESULT",
                        "message": _text(result.get("message"))
                        or "扫描未产出结果，请查看执行状态与失败阶段。",
                        "project": project,
                        "scan_id": _text(result.get("scan_id")),
                        "execution_status": _text(result.get("execution_status")),
                        "customer_output_status": _text(
                            result.get("customer_output_status")
                        ),
                        "failure_stage": _text(result.get("failure_stage")),
                        "future_execution_approval_id": future_approval_id or "",
                        "implicit_retry_performed": False,
                    },
                    500,
                )

            try:
                cumulative = self._persist_scan_result(project, root, result)
            except Exception as exc:
                _scan_logger.exception(
                    "scan result persistence failed",
                    extra={
                        "error_code": "QB-S005",
                        "context": {
                            "project": project,
                            "scan_id": _text(result.get("scan_id")),
                        },
                    },
                )
                return self._json(
                    {
                        "ok": False,
                        "error": "SCAN_PERSISTENCE_FAILED",
                        "message": str(exc)[:500],
                        "scan_id": _text(result.get("scan_id")),
                        "execution_status": _text(result.get("execution_status")),
                        "result_available_but_not_committed": True,
                        "future_execution_approval_id": future_approval_id or "",
                        "implicit_retry_performed": False,
                    },
                    500,
                )

            _finalization_event(
                _text(result.get("scan_id")),
                "response_building",
                detail={"cumulative": dict(cumulative)},
            )
            _response_started = time.perf_counter()
            _watchdog = _response_stall_watchdog(_text(result.get("scan_id")))
            response = self._json(
                {
                    "ok": True,
                    "scan_id": result.get("scan_id", ""),
                    "grade": result.get("grade", ""),
                    "score": result.get("score", 0),
                    "coverage": result.get("coverage", 0),
                    "total_findings": result.get("total_findings", 0),
                    "total_ms": result.get("total_ms", 0),
                    "layers": result.get("layers", {}),
                    "spectrum": result.get("spectrum", {}),
                    "auto_har": result.get("auto_har", {}),
                    "execution_status": result.get("execution_status", ""),
                    "campaign": result.get("campaign", {}),
                    "coverage_gaps": result.get("coverage_gaps", []),
                    "runtime_contract": result.get("runtime_contract", {}),
                    "test_data_plan": result.get("test_data_plan", {}),
                    "release_gate": result.get("release_gate", {}),
                    "execution_evidence_summary": result.get(
                        "execution_evidence_summary", {}
                    ),
                    "report_path": result.get("report_path", ""),
                    "benchmark_metrics": result.get("benchmark_metrics", {}),
                    "cumulative": cumulative,
                    "future_execution_approval_id": future_approval_id or "",
                    "implicit_retry_performed": False,
                }
            )
            _watchdog["mark"]("response_written")
            _finalization_event(
                _text(result.get("scan_id")),
                "response_written",
                elapsed_ms=int((time.perf_counter() - _response_started) * 1000),
            )
            return response
        except Exception as exc:
            _scan_logger.exception(
                "scan request failed",
                extra={"context": {"project": project}},
            )
            return self._json(
                {
                    "ok": False,
                    "error": "V12_SCAN_FAILED",
                    "message": str(exc)[:500],
                },
                500,
            )

    def _regression_target_policy(
        self,
        project: str,
        root: Path,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        config_path = root / "platform_inputs" / project / "real_project_config.json"
        config = _read_json_object(config_path) if config_path.exists() else {}
        return build_target_policy_decision(
            requested_base_url=body.get("base_url") or config.get("base_url"),
            approved_base_url=config.get("approved_base_url"),
            environment_type=config.get("environment_type")
            or config.get("environment_kind"),
            environment_ref=config.get("environment_ref")
            or config.get("target_id"),
            execution_mode="approved_sandbox_write"
            if body.get("allow_destructive_execution") is True
            else "safe_read_only",
            runtime_status=config.get("runtime_status") or "approved",
        )

    def _handle_regression_run(
        self,
        project: str,
        root: Path,
        body: dict[str, Any],
    ) -> None:
        if not self._require_known_project(project, root):
            return None
        actor = self._require_actor()
        if actor is None:
            return None
        if not self._require_role(actor, _SCAN_ROLES, "regression execution"):
            return None
        mode = _text(body.get("mode") or "release").lower() or "release"
        if mode not in {"smoke", "release", "full"}:
            return self._json(
                {
                    "ok": False,
                    "error": "BAD_REGRESSION_MODE",
                    "message": "回归模式仅支持 smoke、release 或 full。",
                },
                400,
            )
        allow_destructive = body.get("allow_destructive_execution") is True
        dry_run = body.get("dry_run") is True
        target_policy = self._regression_target_policy(project, root, body)
        if allow_destructive:
            if not self._require_role(
                actor,
                _DESTRUCTIVE_REGRESSION_ROLES,
                "destructive regression execution",
            ):
                return None
            approval_id = _text(body.get("execution_approval_id"))
            if not approval_id:
                return self._json(
                    {
                        "ok": False,
                        "error": "EXECUTION_APPROVAL_REQUIRED",
                        "message": "破坏性回归必须提供当前运行审批标识。",
                    },
                    403,
                )
            if target_policy.get("write_allowed") is not True:
                return self._json(
                    {
                        "ok": False,
                        "error": "TARGET_POLICY_BLOCKED",
                        "blocking_codes": target_policy.get("blocking_codes", []),
                        "target_policy_decision": target_policy,
                    },
                    403,
                )
        tenant_id = self._request_tenant()
        try:
            with project_scan_lease(
                root,
                project,
                mode="regression",
                tenant_id=tenant_id,
                actor=actor,
            ):
                from .regression_runner import run_regression_suite

                result = run_regression_suite(
                    project_id=project,
                    root=root,
                    options={
                        "mode": mode,
                        "allow_destructive_execution": allow_destructive,
                        "dry_run": dry_run,
                        "execution_approval_id": _text(
                            body.get("execution_approval_id")
                        ),
                        "target_policy_decision": target_policy,
                        "actor": actor,
                    },
                )
        except ScanLeaseBusy as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "PROJECT_SCAN_ALREADY_RUNNING",
                    "active_scan": exc.owner,
                },
                409,
            )
        except Exception as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "REGRESSION_RUN_FAILED",
                    "message": str(exc)[:400],
                },
                500,
            )
        if not isinstance(result, dict):
            return self._json(
                {"ok": False, "error": "REGRESSION_RESULT_INVALID"},
                500,
            )
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        ci_feedback = (
            result.get("ci_feedback")
            if isinstance(result.get("ci_feedback"), dict)
            else {}
        )
        failures = result.get("failures") if isinstance(result.get("failures"), list) else []
        return self._json(
            {
                "ok": result.get("ok") is True,
                "project_id": project,
                "mode": mode,
                "summary": summary,
                "ci_feedback": ci_feedback,
                "failures": failures[:20],
                "artifacts": {
                    "regression_suite_ref": _text(result.get("regression_suite_ref")),
                    "run_result_ref": (
                        f"platform_outputs/{project}/regression_run/"
                        "regression_run_result.json"
                    ),
                    "run_report_ref": (
                        f"platform_outputs/{project}/regression_run/"
                        "regression_failure_report.html"
                    ),
                },
                "governance": {
                    "dry_run": dry_run,
                    "allow_destructive_execution": allow_destructive,
                    "safe_by_default": not allow_destructive,
                    "target_policy_decision": target_policy,
                },
            }
        )
