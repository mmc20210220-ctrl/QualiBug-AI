"""V12 scan / preflight / regression-run handlers for PrivatePilotHandler."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from .private_pilot_debug_client import _dbg_fingerprint_payload, _dbg_report
from .private_pilot_continuous import _update_continuous_state
from .private_pilot_scan_prep import (
    _has_campaign_id_mismatch,
    _is_local_private_service,
    _issue_runtime_approval_for_result,
    _prepare_v12_scan_body,
    _validate_scan_base_url,
)
from .scan_counter import increment_scan_counter

class ScanHandlersMixin:
    def _handle_scan_preflight(self, project: str, root: Path, body: dict[str, Any] | None = None) -> None:
        """Customer-facing readiness check: surface actionable blockers BEFORE a scan
        is launched, instead of failing late with a 400/500 the UI can't explain."""
        body = dict(body or {})
        reasons: list[dict[str, str]] = []
        # 1) service credentials configured?
        _cfg = root / "platform_workspace" / project / "multi_service_config.json"
        _services: list = []
        if _cfg.exists():
            try:
                _services = json.loads(_cfg.read_text(encoding="utf-8")).get("services", [])
            except Exception:
                pass
        if not _services:
            reasons.append({"code": "NO_CREDENTIALS", "message": "尚未配置任何服务凭证，请先在「设置」页保存。"})
        # 2) source ingested — with type-awareness so the UI can tell the
        #    customer WHY the scan might still fail even with sources present.
        _assets: list[dict[str, Any]] = []
        try:
            from .enterprise_source_registry import list_source_assets
            _assets = list_source_assets(project, root=root)
        except Exception:
            _assets = []
        if not _assets:
            reasons.append({"code": "NO_SOURCE", "message": "尚未入库任何资料（PRD / OpenAPI 等），请先上传。"})
        else:
            # Classify source types so the Run Center can surface actionable hints:
            #   - has_api_spec: the scan CAN produce executable probes
            #   - has_prd_only: the scan will run but may produce zero API probes
            _source_types = {str(a.get("source_type") or "").strip().lower() for a in _assets}
            _has_openapi = bool(_source_types & {"openapi", "openapi3", "swagger", "postman", "api_spec"})
            _has_db = bool(_source_types & {"db_design", "database_schema", "sql", "db_schema"})
            _has_prd = bool(_source_types & {"prd", "requirement", "business_rules", "collaboration_document", "other_document"})
            if not _has_openapi:
                reasons.append({
                    "code": "NO_API_SPEC",
                    "message": (
                        "已入库 {} 份资料，但缺少 API 接口规范（OpenAPI / Swagger / Postman）。"
                        "扫描将无法生成可执行的 API 探针，只能产出基于 PRD 的候选线索。"
                        "请上传被测系统的接口文档后再运行。"
                    ).format(len(_assets)),
                })
            # 3) target base_url / connector endpoint?
        _approved_url = str(body.get("approved_base_url") or "").strip()
        _base_url = str(body.get("target_url") or body.get("base_url") or _approved_url or "").strip()
        _environment_type = str(body.get("environment_type") or body.get("environment_kind") or "").strip()
        _environment_ref = str(body.get("environment_ref") or body.get("target_id") or "").strip()
        _project_config = root / "platform_inputs" / project / "real_project_config.json"
        if _project_config.is_file():
            try:
                _project_values = json.loads(_project_config.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                reasons.append({"code": "PROJECT_CONFIG_INVALID", "message": f"项目运行配置无法解析: {exc}"})
                _project_values = {}
            if isinstance(_project_values, dict):
                _base_url = _base_url or str(_project_values.get("base_url") or "").strip()
                _approved_url = _approved_url or str(_project_values.get("approved_base_url") or "").strip()
                if not _approved_url:
                    _approved_urls = _project_values.get("approved_base_urls")
                    if isinstance(_approved_urls, list) and len(_approved_urls) == 1:
                        _approved_url = str(_approved_urls[0] or "").strip()
                _environment_type = _environment_type or str(_project_values.get("environment_type") or _project_values.get("environment_kind") or "").strip()
                _environment_ref = _environment_ref or str(_project_values.get("environment_ref") or _project_values.get("target_id") or "").strip()
        try:
            from .enterprise_pilot_runtime import load_connector_registry
            for _c in load_connector_registry(project, root).get("connectors", []):
                if _c.get("enabled"):
                    _ep = str(_c.get("endpoint_ref") or "")
                    if not _base_url and _ep.startswith(("http://", "https://")):
                        _base_url = _ep
                        break
        except Exception:
            pass
        if not _base_url:
            reasons.append({"code": "NO_TARGET", "message": "未配置被测目标 base_url 或启用连接器端点。"})

        from .target_policy import build_target_policy_decision

        _read_only = bool(body.get("read_only"))
        _target_policy = build_target_policy_decision(
            requested_base_url=_base_url,
            approved_base_url=_approved_url,
            environment_type=_environment_type,
            environment_ref=_environment_ref,
            execution_mode="safe_read_only" if _read_only else "approved_sandbox_write",
            runtime_status="approved",
        )
        _policy_blocking_codes = list(_target_policy.get("blocking_codes") or [])
        if _read_only:
            _policy_blocking_codes = [
                code for code in _policy_blocking_codes
                if code not in {"READ_ONLY_MODE", "UNKNOWN_ENVIRONMENT", "PRODUCTION_WRITE_BLOCKED"}
            ]
        for _code in _policy_blocking_codes:
            reasons.append({
                "code": str(_code),
                "message": "补全明确环境类型、环境标识和精确批准 URL 后重试；生产或未知环境写入不会放行。",
            })

        # Integrate with CapabilityGapResolver for gap detection and resolution tasks
        gap_summary = None
        if reasons:
            try:
                from .capability_gap_resolver import CapabilityGapResolver
                resolver = CapabilityGapResolver(project_id=project)
                detected_gaps = resolver.detect_from_scan_preflight(reasons)
                gap_summary = resolver.build_gap_report(detected_gaps)
            except Exception:
                pass

        _blocking_codes = list(dict.fromkeys(str(item.get("code") or "") for item in reasons if item.get("code")))
        response = {
            "ok": True,
            "schema_version": "qualibug.environment-preflight.v1",
            "project_id": project,
            "ready": len(_blocking_codes) == 0,
            "blocking_codes": _blocking_codes,
            "reasons": reasons,
            "target_policy_decision": _target_policy,
            "input_checks": {
                "credentials": {"status": "passed" if _services else "blocked", "service_count": len(_services)},
                "sources": {"status": "passed" if _assets else "blocked", "source_count": len(_assets)},
                "target": {"status": "passed" if _base_url else "blocked", "target_url": _base_url, "approved_base_url": _approved_url},
                "environment": {"status": "passed" if _environment_type and _environment_ref else "blocked", "environment_type": _environment_type, "environment_ref": _environment_ref},
                "target_policy": {"status": "passed" if (_target_policy.get("read_allowed") if _read_only else _target_policy.get("write_allowed")) else "blocked"},
            },
        }
        if gap_summary:
            response["gap_summary"] = gap_summary
        return self._json(response)

    def _handle_v12_scan(self, project: str, root: Path, actor: dict[str, str], body: dict[str, Any]) -> None:
        try:
            from .__main__ import scan
            from .private_pilot_scan_context_contract import build_campaign_context_from_scan_body
            trace_id = str(uuid.uuid4())

            # B3 (customer one-click run): when the scan body does not already carry a
            # usable source_manifest, auto-bind the project's most recently ingested
            # source so the scan has real scope instead of running source-less. Never
            # fabricate a manifest — only bind an existing registered source.
            _manifest = body.get("source_manifest") if isinstance(body.get("source_manifest"), dict) else {}
            _manifest_ok = bool(str(_manifest.get("source_id") or "").strip()) and len(str(_manifest.get("source_hash") or "").strip()) == 64
            if not _manifest_ok:
                try:
                    from .enterprise_source_registry import list_source_assets
                    for _asset in list_source_assets(project, root=root):
                        _sid = str(_asset.get("source_id") or "").strip()
                        _sh = str(_asset.get("latest_source_hash") or "").strip().lower()
                        if _sid and len(_sh) == 64:
                            body = dict(body)
                            body["source_manifest"] = {"source_id": _sid, "source_hash": _sh}
                            break
                except Exception as _bind_exc:
                    _dbg_report(hypothesis_id="B3", msg="[WARN] auto source bind skipped",
                                data={"error": str(_bind_exc)}, trace_id=trace_id)

            prepared_body = _prepare_v12_scan_body(
                project,
                root,
                actor,
                body,
                local_dev_mode=_is_local_private_service(self.server),
            )
            api_doc = str(prepared_body.get("api_doc") or prepared_body.get("api_doc_text") or "")
            base_url = str(prepared_body.get("base_url") or "").strip()
            # SSRF guard: validate user-supplied base_url before it reaches scan().
            if base_url:
                from .ssrf_guard import SsrfBlockedError
                try:
                    _validate_scan_base_url(base_url, local_dev_mode=_is_local_private_service(self.server))
                except SsrfBlockedError as exc:
                    return self._json({"ok": False, "error": "SSRF_BLOCKED", "message": str(exc)}, 400)
            if not api_doc:
                p = root / "platform_outputs" / project / "api_spec.md"
                if p.exists(): api_doc = p.read_text(encoding="utf-8")
            # Auto-build API doc + base_url from connectors
            if not api_doc or not base_url:
                from .enterprise_pilot_runtime import load_connector_registry
                reg = load_connector_registry(project, root)
                connectors = reg.get("connectors", [])
                enabled = [c for c in connectors if c.get("enabled")]
                if enabled:
                    if not base_url:
                        for c in enabled:
                            ep = c.get("endpoint_ref", "")
                            if ep and (ep.startswith("http://") or ep.startswith("https://")):
                                base_url = ep; break
                # A connector URL is target identity only. Endpoint contracts must
                # come from registered project sources; never synthesize routes.
            if api_doc and not prepared_body.get("api_doc"):
                prepared_body["api_doc"] = api_doc
            if base_url and not prepared_body.get("base_url"):
                prepared_body["base_url"] = base_url
            if not str(body.get("execution_mode") or "").strip():
                prepared_body.pop("execution_mode", None)
            prepared_body = _prepare_v12_scan_body(
                project,
                root,
                actor,
                prepared_body,
                local_dev_mode=_is_local_private_service(self.server),
            )
            campaign_context = build_campaign_context_from_scan_body(prepared_body)
            # #region debug-point A:prepared-http-scan-body
            _dbg_report(
                hypothesis_id="A",
                msg="[DEBUG] prepared http scan payload",
                data={
                    "actor": {"name": str(actor.get("name") or ""), "role": str(actor.get("role") or "")},
                    "local_dev_mode": _is_local_private_service(self.server),
                    "prepared_body": _dbg_fingerprint_payload(prepared_body),
                    "campaign_context": _dbg_fingerprint_payload(campaign_context),
                },
                trace_id=trace_id,
            )
            # #endregion

            result = scan(
                project=project,
                root=root,
                prd_text=str(prepared_body.get("prd", "")),
                api_doc_text=str(prepared_body.get("api_doc") or prepared_body.get("api_doc_text") or api_doc),
                base_url=str(prepared_body.get("base_url") or base_url).strip(),
                multi_layer=bool(str(prepared_body.get("base_url") or base_url).strip()),
                campaign_context=campaign_context,
            )
            # #region debug-point C:http-scan-result
            _dbg_report(
                hypothesis_id="C",
                msg="[DEBUG] http scan result campaign",
                data={
                    "campaign": result.get("campaign") if isinstance(result.get("campaign"), dict) else {},
                    "runtime_contract": result.get("runtime_contract") if isinstance(result.get("runtime_contract"), dict) else {},
                    "incremental_discovery": result.get("phases", {}).get("incremental_discovery") if isinstance(result.get("phases"), dict) else {},
                },
                trace_id=trace_id,
            )
            # #endregion
            retry_approval_id = _issue_runtime_approval_for_result(
                project,
                root,
                actor,
                prepared_body,
                result,
                local_dev_mode=_is_local_private_service(self.server),
            )
            if retry_approval_id and _has_campaign_id_mismatch(result):
                prepared_body["execution_approval_id"] = retry_approval_id
                campaign_context = build_campaign_context_from_scan_body(prepared_body)
                # #region debug-point D:retry-http-scan-body
                _dbg_report(
                    hypothesis_id="D",
                    msg="[DEBUG] retry http scan payload",
                    data={
                        "prepared_body": _dbg_fingerprint_payload(prepared_body),
                        "campaign_context": _dbg_fingerprint_payload(campaign_context),
                        "retry_approval_id": retry_approval_id,
                    },
                    trace_id=trace_id,
                )
                # #endregion
                result = scan(
                    project=project,
                    root=root,
                    prd_text=str(prepared_body.get("prd", "")),
                    api_doc_text=str(prepared_body.get("api_doc") or prepared_body.get("api_doc_text") or api_doc),
                    base_url=str(prepared_body.get("base_url") or base_url).strip(),
                    multi_layer=bool(str(prepared_body.get("base_url") or base_url).strip()),
                    campaign_context=campaign_context,
                )
                # #region debug-point E:retry-http-scan-result
                _dbg_report(
                    hypothesis_id="E",
                    msg="[DEBUG] retry http scan result campaign",
                    data={
                        "campaign": result.get("campaign") if isinstance(result.get("campaign"), dict) else {},
                        "runtime_contract": result.get("runtime_contract") if isinstance(result.get("runtime_contract"), dict) else {},
                    },
                    trace_id=trace_id,
                )
                # #endregion
            # Persist to DB — use cumulative merge so bugs accumulate across scans
            try:
                db_persist.init_db(root)
                # Extract findings from report
                report_path = root / "platform_outputs" / project / "intelligence_report.json"
                report_data = {}
                if report_path.exists():
                    import json as _jr
                    report_data = _jr.loads(report_path.read_text(encoding="utf-8"))
                findings_list = report_data.get("real_findings") or report_data.get("bug_scores") or []
                # Also include multi-source findings from scan result
                if isinstance(result.get("db_findings"), list):
                    findings_list = list(findings_list) + result["db_findings"]
                if isinstance(result.get("e2e_findings"), list):
                    findings_list = list(findings_list) + result["e2e_findings"]
                if isinstance(result.get("deep_findings"), list):
                    findings_list = list(findings_list) + result["deep_findings"]
                if isinstance(result.get("ui_findings"), list):
                    findings_list = list(findings_list) + result["ui_findings"]
                # Dedupe input list before merging
                seen_titles: set[str] = set()
                deduped_findings: list[dict] = []
                for f in (findings_list if isinstance(findings_list, list) else []):
                    if not isinstance(f, dict):
                        continue
                    t = str(f.get("title") or f.get("description", ""))[:160].lower()
                    if t in seen_titles:
                        continue
                    seen_titles.add(t)
                    deduped_findings.append(f)
                # Save scan record
                enriched = dict(result)
                enriched["findings"] = [
                    {"title": str(f.get("title") or f.get("description", ""))[:120],
                     "severity": str(f.get("severity", "P1")),
                     "category": str(f.get("category", "")),
                     "description": str(f.get("description", ""))[:500],
                     "confidence_score": float(f.get("confidence_score") or f.get("score") or 0),
                     "_api_path": str(f.get("_api_path") or f.get("path") or ""),
                     "_api_method": str(f.get("_api_method") or f.get("method") or ""),
                     "evidence": f.get("evidence") if isinstance(f.get("evidence"), dict) else {}}
                    for f in deduped_findings
                ]
                scan_id = db_persist.save_scan(root, self._request_tenant(), project, enriched)
                # Cumulative merge — bugs accumulate, never silently dropped
                merge_result = db_persist.merge_findings_cumulative(
                    root, self._request_tenant(), project, scan_id, enriched["findings"]
                )
            except Exception:
                merge_result = {}
            # Increment scan counter through the shared helper so CLI and HTTP
            # entrypoints project the same run metadata into command center.
            try:
                increment_scan_counter(root / "platform_outputs" / project / "scan_counter.json")
            except Exception:
                pass
            # Also write raw findings for frontend Dashboard/Findings compatibility
            try:
                # Re-read from evaluation result
                out_dir = root / "platform_outputs" / project
                eval_json = out_dir / "intelligence_report.json"
                if eval_json.exists():
                    import json as _j2
                    existing = _j2.loads(eval_json.read_text(encoding="utf-8"))
                    # Merge raw findings — only set raw_total once (first scan), never decrement
                    old_raw = existing.get("raw_total", 0)
                    new_raw = result.get("total_findings", 0)
                    existing["raw_total"] = max(old_raw, new_raw) if old_raw else new_raw
                    # Preserve real_findings across scans
                    if not existing.get("real_findings"):
                        existing["real_findings"] = existing.get("bug_scores", [])
                    existing["layers"] = result.get("layers", {})
                    # Merge DB verification findings
                    db_finds = result.get("db_findings")
                    if isinstance(db_finds, list) and db_finds:
                        existing["db_verification"] = {"findings": db_finds, "total": len(db_finds)}
                    e2e_finds = result.get("e2e_findings")
                    if isinstance(e2e_finds, list) and e2e_finds:
                        existing.setdefault("e2e_findings", []).extend(e2e_finds)
                    deep_finds = result.get("deep_findings")
                    if isinstance(deep_finds, list) and deep_finds:
                        existing.setdefault("deep_findings", []).extend(deep_finds)
                    ui_finds = result.get("ui_findings")
                    if isinstance(ui_finds, list) and ui_finds:
                        existing.setdefault("ui_findings", []).extend(ui_finds)
                    eval_json.write_text(_j2.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            # Save spectrum result to disk for Dashboard polling
            if result.get("spectrum"):
                spectrum_dir = root / "platform_outputs" / project / "spectrum"
                spectrum_dir.mkdir(parents=True, exist_ok=True)
                import json as _json_save
                _json_save.dump(result["spectrum"], (spectrum_dir / "spectrum_result.json").open("w", encoding="utf-8"),
                               ensure_ascii=False, default=str)

            # Update continuous state for manual scans too
            _update_continuous_state(root, project, result)

            return self._json({"ok": True, "scan_id": result.get("scan_id",""), "grade": result.get("grade",""),
                "score": result.get("score",0), "coverage": result.get("coverage",0),
                "total_findings": result.get("total_findings",0), "total_ms": result.get("total_ms",0),
                "layers": result.get("layers",{}),
                "spectrum": result.get("spectrum", {}),
                "auto_har": result.get("auto_har", {}),
                # Honest run-status fields the Run Center / Dashboard need to
                # distinguish executed / blocked / plan_only / partial_coverage
                # instead of misleading the customer. scan() already computes these;
                # they were previously dropped from the HTTP envelope so the frontend
                # always saw execution_status == undefined.
                "execution_status": result.get("execution_status", ""),
                "campaign": result.get("campaign", {}),
                "coverage_gaps": result.get("coverage_gaps", []),
                "runtime_contract": result.get("runtime_contract", {}),
                "test_data_plan": result.get("test_data_plan", {}),
                "release_gate": result.get("release_gate", {}),
                "execution_evidence_summary": result.get("execution_evidence_summary", {}),
                "report_path": result.get("report_path", ""),
                "benchmark_metrics": result.get("benchmark_metrics", {}),
                "cumulative": merge_result,})
        except Exception as e:
            return self._json({"ok": False, "error": "V12_SCAN_FAILED", "message": str(e)[:500]}, 500)

    def _handle_regression_run(self, project: str, root: Path, body: dict[str, Any]) -> None:
        if not self._require_known_project(project, root):
            return
        mode = str(body.get("mode") or "release").strip().lower() or "release"
        if mode not in {"smoke", "release", "full"}:
            return self._json(
                {
                    "ok": False,
                    "error": "BAD_REGRESSION_MODE",
                    "message": "回归模式仅支持 smoke、release 或 full。",
                },
                400,
            )
        allow_destructive = bool(body.get("allow_destructive_execution") is True)
        dry_run = bool(body.get("dry_run") is True)
        try:
            from .regression_runner import run_regression_suite

            result = run_regression_suite(
                project_id=project,
                root=root,
                options={
                    "mode": mode,
                    "allow_destructive_execution": allow_destructive,
                    "dry_run": dry_run,
                },
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

        regression_summary: dict[str, Any] = {}
        try:
            command_center = self._build_command_center(project, root)
            if isinstance(command_center, dict):
                regression_summary = command_center.get("data", {}).get("regression_summary", {})
        except Exception:
            regression_summary = {}

        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        ci_feedback = result.get("ci_feedback") if isinstance(result.get("ci_feedback"), dict) else {}
        failures = result.get("failures") if isinstance(result.get("failures"), list) else []
        return self._json(
            {
                "ok": True,
                "project_id": project,
                "mode": mode,
                "summary": summary,
                "ci_feedback": ci_feedback,
                "regression_summary": regression_summary,
                "failures": failures[:20],
                "artifacts": {
                    "regression_suite_ref": str(result.get("regression_suite_ref") or ""),
                    "run_result_ref": f"platform_outputs/{project}/regression_run/regression_run_result.json",
                    "run_report_ref": f"platform_outputs/{project}/regression_run/regression_failure_report.html",
                },
                "governance": {
                    "dry_run": dry_run,
                    "allow_destructive_execution": allow_destructive,
                    "safe_by_default": not allow_destructive,
                },
            }
        )
