"""Command-center payload builder mixin for PrivatePilotHandler.

Extracted from ``private_pilot_service`` to keep the HTTP handler class thinner.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db_persistence as db_persist
from .customer_delivery_gate import split_customer_delivery_tracks as _partition_delivery_tracks
from .private_pilot_command_center_helpers import (
    _annotate_ui_risk_item,
    _build_internal_clue_contract,
    _defect_intake_stats,
    _first_text,
    _normalize_execution_evidence_summary,
    _rebuild_customer_display_contract,
    _select_commercial_assets,
    _ui_verification_stats,
)
from .private_pilot_continuous import _get_continuous_state
from .private_pilot_defect_summaries import (
    _build_defect_delivery_cards,
    _build_defect_grouped_summary,
    _build_defect_priority_summary,
    _build_defect_repro_summary,
)
from .private_pilot_regression_projection import (
    _build_regression_release_guidance,
    _load_regression_projection,
)


def _svc():
    from . import private_pilot_service as service

    return service


class CommandCenterBuilderMixin:
    def _build_command_center(self, project_id: str, root: Path) -> dict:
        report = self._load_v12_report(project_id, root)
        current_scan_report = self._load_current_scan_report(project_id, root)
        real_discovery_payload = _svc()._load_real_project_discovery_payload(root, project_id)
        current_report_source = current_scan_report if current_scan_report else report
        discovery_current_scope = self._discovery_current_scope_summary(real_discovery_payload or {})
        enterprise_docs = self._load_enterprise_docs(project_id, root)
        knowledge_summary = self._load_knowledge_summary(project_id, root)
        discovery_payload = real_discovery_payload or self._auto_discovery_payload(project_id, root, report)
        risks = self._v12_findings(report, enterprise_docs)
        risks.extend(self._load_db_findings(root, project_id))
        risks.extend(self._load_perf_regressions(root, project_id))
        risks.extend(self._load_spectrum_findings(root, project_id))
        risks.extend(self._load_multi_layer_findings(root, project_id))
        # Load DB verification findings
        db_verify = report.get("db_verification", {})
        if isinstance(db_verify.get("findings"), list):
            for f in db_verify["findings"]:
                f.setdefault("risk_type", "db_verification")
                f.setdefault("defect_family", "data_integrity")
            risks.extend(db_verify["findings"])
        # E2E flow findings
        e2e = report.get("e2e_findings", [])
        if isinstance(e2e, list):
            for f in e2e:
                f.setdefault("risk_type", "e2e_flow")
                f.setdefault("defect_family", "business_flow")
            risks.extend(e2e)
        # Deep verifier findings
        deep = report.get("deep_findings", [])
        if isinstance(deep, list):
            for f in deep:
                f.setdefault("risk_type", "深度验证")
                f.setdefault("defect_family", "deep_test")
            risks.extend(deep)
        # Frontend UI findings
        ui = report.get("ui_findings", [])
        if isinstance(ui, list):
            for f in ui:
                f.setdefault("risk_type", "frontend_ui")
                f.setdefault("defect_family", "ui")
                risks.append(_annotate_ui_risk_item(dict(f)))
        # ── 累积 findings：从 DB 加载跨扫描累积的未修复 bug ──
        # 这是"bug 货架"模型的核心：只要 bug 没修复（status='open'），
        # 就一直保留在列表里，即使本次扫描没触发也要展示。
        # 注意：只加载不在当前 report findings 中的（避免双重计算）
        tenant_id = self._request_tenant()
        cumulative = db_persist.get_cumulative_findings(root, tenant_id, project_id)
        if cumulative:
            import re as _re2
            current_keys: set[str] = set()
            for r in risks:
                t = str(r.get("title") or "")[:200].strip().lower()
                t = _re2.sub(r'^(\[[^\]]*\]\s*)+', '', t)
                t = _re2.sub(r'\s+', ' ', t).strip()
                m = str(r.get("_api_method") or (r.get("evidence") or {}).get("method") or "").upper()
                p = str(r.get("_api_path") or (r.get("evidence") or {}).get("path") or "").strip()
                current_keys.add(f"{t}|{m}|{p}")
            for f in cumulative:
                t = str(f.get("title") or "")[:200].strip().lower()
                t = _re2.sub(r'^(\[[^\]]*\]\s*)+', '', t)
                t = _re2.sub(r'\s+', ' ', t).strip()
                m = str(f.get("_api_method") or (f.get("evidence") or {}).get("method") or "").upper()
                p = str(f.get("_api_path") or (f.get("evidence") or {}).get("path") or "").strip()
                key = f"{t}|{m}|{p}"
                if key not in current_keys:
                    f.setdefault("risk_type", f.get("category") or "累积发现")
                    f.setdefault("defect_family", "cumulative")
                    f.setdefault("_cumulative", True)
                    risks.append(f)
                    current_keys.add(key)
        risks = self._dedupe_risks([item for item in risks if isinstance(item, dict)])

        # ── 为所有未关联文档的 finding 补充文档匹配（通用，非 v12 finding 也需要）──
        if enterprise_docs:
            for item in risks:
                if not item.get("_doc_refs"):
                    title = str(item.get("title") or "")
                    matched = self._match_docs_for_finding(title, enterprise_docs)
                    if matched:
                        item["_doc_refs"] = matched

        # ── Generic: convert code identifiers to customer-facing labels ──
        import re
        # Rule: any snake_case or lowercase_underscore identifier is internal;
        # infer a readable label from the finding's actual category/title instead.
        _INTERNAL_PATTERNS = [
            (re.compile(r".*_verifier$"), "验证引擎"),
            (re.compile(r".*_discovery$|.*_scanner$"), "检测引擎"),
            (re.compile(r".*_engine$|.*_oracle$"), "规则引擎"),
            (re.compile(r".*_pipeline$|.*_pilot$"), "分析引擎"),
            (re.compile(r".*_command_center$|.*_center$"), "分析引擎"),
            (re.compile(r".*_bridge$|.*_enricher$"), "证据引擎"),
        ]

        def _is_internal_name(s: str) -> bool:
            """A string looks internal if it's snake_case with no Chinese chars."""
            if not s or not isinstance(s, str):
                return False
            # Has underscores and no Chinese characters
            return "_" in s and not any("\u4e00" <= c <= "\u9fff" for c in s)

        def _to_customer_label(s: str, title: str = "") -> str:
            """Convert internal identifier to customer label using title hints."""
            if not _is_internal_name(s):
                return s
            # Try pattern match first
            for pat, label in _INTERNAL_PATTERNS:
                if pat.match(s):
                    return label
            # Infer from title keywords
            t = title.lower()
            if "权限" in t or "auth" in t:
                return "权限检测"
            if "状态" in t or "state" in t or "禁止路径" in t:
                return "状态机分析"
            if "库存" in t or "inventory" in t:
                return "数据完整性检测"
            if "并发" in t or "concurrent" in t:
                return "并发检测"
            if "幂等" in t or "idempotent" in t:
                return "幂等检测"
            if "数据" in t or "泄露" in t or "data" in t:
                return "数据安全检测"
            # Generic fallback: just say "业务规则验证"
            return "业务规则验证"

        for r in risks:
            for field in ("source", "risk_type", "defect_family"):
                val = r.get(field, "")
                if val and _is_internal_name(str(val)):
                    new_val = _to_customer_label(str(val), str(r.get("title", "")))
                    r[field] = new_val
        # Debug: verify cleanup worked
        _remaining = sum(1 for r in risks for f in ("source","risk_type","defect_family") if r.get(f) and _is_internal_name(str(r.get(f,""))))
        if _remaining:
            print(f"[CLEANUP] WARNING: {_remaining} internal name fields remain after cleanup", flush=True)

        # ── HAR Bridge: enrich findings with real HTTP call evidence ──
        from .har_bridge import enrich_findings_batch_with_har, load_har_entries
        scan_result_path = root / "platform_outputs" / project_id / "scan_result.json"
        if scan_result_path.exists():
            scan_result = self._read_json_dict(scan_result_path)
            har_entries = load_har_entries(scan_result) if scan_result else []
            if har_entries:
                risks = enrich_findings_batch_with_har(risks, har_entries)
        if isinstance(report, dict):
            har_entries_rpt = load_har_entries(report)
            if har_entries_rpt:
                risks = enrich_findings_batch_with_har(risks, har_entries_rpt)

        # ── V3 Evidence Enrichment: three-perspective evidence chain ──
        from .evidence_enricher_v3 import enrich_findings_batch, load_enterprise_context
        enterprise_ctx = load_enterprise_context(project_id, root)
        risks = enrich_findings_batch(risks, enterprise_ctx)

        # ── Display-Ready Formatting: unify all findings into display-ready JSON ──
        from .display_ready_formatter import (
            _build_display_contract,
            _compute_commercial_value,
            _compute_scores,
            format_findings_display_ready,
            sanitize_customer_evidence_payload,
        )
        raw_display_risks, _display_metrics = format_findings_display_ready(risks, enterprise_ctx, report)
        sanitized_display_risks = sanitize_customer_evidence_payload(raw_display_risks)
        display_risks = sanitized_display_risks if isinstance(sanitized_display_risks, list) else []
        display_metrics = {
            "scores": _compute_scores(display_risks, report),
            "commercial_value": _compute_commercial_value(display_risks, report),
            "display_contract": _build_display_contract(display_risks, report),
        }

        all_display_risks = [
            item for item in display_risks
            if isinstance(item, dict) and not bool(item.get("_summary_only"))
        ]
        delivery_defects, internal_clues = _partition_delivery_tracks(all_display_risks)
        display_risks = delivery_defects
        overall_display_contract = display_metrics.get("display_contract") if isinstance(display_metrics.get("display_contract"), dict) else {}
        if isinstance(display_metrics.get("display_contract"), dict):
            display_metrics["display_contract"] = _rebuild_customer_display_contract(
                overall_display_contract,
                display_risks,
            )
        clue_contract = _build_internal_clue_contract(
            overall_display_contract,
            internal_clues,
        )

        materialized_total = len(display_risks)
        ui_stats = _ui_verification_stats(
            report.get("ui_candidate_findings")
            if isinstance(report.get("ui_candidate_findings"), list)
            else report.get("ui_findings")
        )
        intake_stats = _defect_intake_stats(all_display_risks)
        scores = display_metrics.get("scores") or {}
        commercial_value = display_metrics.get("commercial_value") or {}
        display_contract = display_metrics.get("display_contract") if isinstance(display_metrics.get("display_contract"), dict) else {}
        if not display_contract:
            status_counts = {}
            severity_counts = {}
            for item in display_risks:
                status = str(item.get("bug_status") or "risk_clue")
                severity = str(item.get("severity") or "P2")
                status_counts[status] = status_counts.get(status, 0) + 1
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
            display_contract = {
                "schema_version": "display-ready-fallback",
                "source_of_truth": "backend.private_pilot_service._build_command_center",
                "display_key": "risks",
                "materialized_risk_count": materialized_total,
                "raw_candidate_risk_count": max(materialized_total, self._report_summary_number(report, "risk_total", "risk_count", "total_findings", "total_bugs_found", "total_found", "raw_total", fallback=0)),
                "ready_bug_count": sum(1 for item in display_risks if item.get("bug_status") == "reproduced" and bool(item.get("gate_passed"))),
                "needs_validation_count": status_counts.get("suspected", 0) + status_counts.get("risk_clue", 0),
                "not_reproduced_count": status_counts.get("not_reproduced", 0),
                "status_counts": status_counts,
                "severity_counts": severity_counts,
            }

        regression_summary = _load_regression_projection(root, project_id, delivery_defects)

        defect_grouped_summary = _build_defect_grouped_summary(delivery_defects)
        defect_priority_summary = _build_defect_priority_summary(delivery_defects)
        defect_repro_summary = _build_defect_repro_summary(delivery_defects)
        defect_delivery_cards = _build_defect_delivery_cards(delivery_defects)
        current_report_findings = self._report_findings(current_report_source)
        current_report_category_counts: dict[str, int] = {}
        for item in current_report_findings:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or item.get("risk_type") or "uncategorized").strip() or "uncategorized"
            current_report_category_counts[category] = current_report_category_counts.get(category, 0) + 1
        current_report_path = str(current_report_source.get("report_source_path") or current_report_source.get("report_path") or "")
        if discovery_current_scope.get("report_source_path"):
            current_report_path = str(discovery_current_scope.get("report_source_path") or current_report_path)
        current_report_breakdown = {
            "total_findings": len(current_report_findings),
            "category_counts": current_report_category_counts,
            "report_source_path": current_report_path,
        }
        current_campaign_bundle_stats = {"raw": 0, "deduped": 0}
        if real_discovery_payload and isinstance(discovery_payload.get("continuous_discovery_campaign"), dict):
            current_campaign_bundle_stats = _svc()._current_campaign_bundle_finding_stats(
                project_id,
                root,
                discovery_payload["continuous_discovery_campaign"],
            )
        discovery_total_findings = int(discovery_current_scope.get("total_findings") or 0)
        current_scope_total_findings = max(
            0,
            int(current_report_breakdown.get("total_findings") or 0),
            discovery_total_findings,
            int(current_campaign_bundle_stats.get("raw") or 0),
        )
        current_scope_raw_candidate_findings = max(
            current_scope_total_findings,
            int(current_report_source.get("raw_total") or current_report_source.get("total_findings") or current_scope_total_findings or 0),
        )
        has_campaign_scope = bool(discovery_current_scope) or bool(
            int(current_campaign_bundle_stats.get("deduped") or 0)
            or int(current_campaign_bundle_stats.get("raw") or 0)
        )
        if has_campaign_scope:
            # Real campaign scope present: customer-ready defects is the deduped /
            # validated campaign count, NEVER the raw bundle total. Using the raw
            # total (current_scope_total_findings) here conflated "本轮候选总数"
            # with "本轮 customer-ready 缺陷数".
            current_scope_customer_ready_defect_count = max(
                0,
                int(discovery_current_scope.get("customer_ready_defects") or 0),
                int(current_campaign_bundle_stats.get("deduped") or 0),
            )
            if current_scope_total_findings:
                current_scope_customer_ready_defect_count = min(
                    current_scope_customer_ready_defect_count,
                    current_scope_total_findings,
                )
        else:
            current_scope_customer_ready_defect_count = max(
                0,
                int(self._report_summary_number(
                    current_report_source,
                    "customer_ready_defects",
                    "ready_bug_count",
                    "total_bugs_found",
                    fallback=current_scope_total_findings,
                ) or current_scope_total_findings),
            )
            current_scope_customer_ready_defect_count = min(
                current_scope_customer_ready_defect_count or current_scope_total_findings,
                current_scope_total_findings or len(delivery_defects),
            )
        current_scope_materialized_findings = max(
            current_scope_customer_ready_defect_count,
            current_scope_total_findings,
        )
        family_customer_ready_defect_count = len(delivery_defects)
        campaign_scope = _svc()._current_campaign_scope_summary(
            discovery_payload.get("continuous_discovery_campaign")
            if isinstance(discovery_payload.get("continuous_discovery_campaign"), dict)
            else {}
        )

        canonical_total = materialized_total
        raw_candidate_total = int(display_contract.get("raw_candidate_risk_count") or canonical_total)
        ready_bug_count = current_scope_customer_ready_defect_count or int(display_contract.get("ready_bug_count") or 0)
        needs_validation_count = int(display_contract.get("needs_validation_count") or 0)
        not_reproduced_count = int(display_contract.get("not_reproduced_count") or 0)
        status_counts = display_contract.get("status_counts") if isinstance(display_contract.get("status_counts"), dict) else {}
        severity_counts = display_contract.get("severity_counts") if isinstance(display_contract.get("severity_counts"), dict) else {}
        canonical_p0 = int(severity_counts.get("P0") or sum(1 for item in display_risks if item.get("severity") == "P0"))
        canonical_p1 = int(severity_counts.get("P1") or sum(1 for item in display_risks if item.get("severity") == "P1"))
        canonical_p2 = max(0, canonical_total - canonical_p0 - canonical_p1)
        ready_p0 = sum(1 for item in display_risks if item.get("bug_status") == "reproduced" and bool(item.get("gate_passed")) and item.get("severity") == "P0")
        ready_p1 = sum(1 for item in display_risks if item.get("bug_status") == "reproduced" and bool(item.get("gate_passed")) and item.get("severity") == "P1")
        evidence_trust = self._evidence_trust_score(risks)
        if scores:
            evidence_trust = scores.get("evidence_trust_score", evidence_trust)
        try:
            evidence_trust = max(0.0, min(100.0, float(evidence_trust or 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid evidence_trust_score in command-center projection") from exc
        try:
            ai_points = max(int(report.get("raw_total") or 0), int(report.get("total_findings") or 0), raw_candidate_total, canonical_total)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid finding count in command-center report") from exc
        if canonical_total <= 0:
            evidence_grade = "C"
        elif evidence_trust >= 85:
            evidence_grade = "A"
        elif evidence_trust >= 70:
            evidence_grade = "B"
        elif evidence_trust >= 50:
            evidence_grade = "C"
        else:
            evidence_grade = "D"
        scan_counter = self._scan_counter(project_id, root)
        execution_evidence_summary = _normalize_execution_evidence_summary(
            report.get("execution_evidence_summary"),
            report.get("ui_execution_summary"),
            report.get("ui_execution"),
        )
        commercial_assets = _select_commercial_assets(report.get("external_commercial_assets"), report.get("commercial_assets"))
        if regression_summary:
            regression_summary.update(_build_regression_release_guidance(regression_summary, commercial_assets))
        validation_summary = regression_summary.get("validation_summary") if isinstance(regression_summary.get("validation_summary"), dict) else {}

        scan_meta = {
            "scan_id": str(current_report_source.get("scan_id") or report.get("scan_id") or ""),
            "run_count": int(scan_counter.get("count") or 0),
            "first_scan_at": str(scan_counter.get("first_scan_at") or ""),
            "last_scan_at": str(scan_counter.get("last_scan_at") or report.get("generated_at_utc") or ""),
            "total_ms": int(current_report_source.get("total_ms") or report.get("total_ms") or 0),
            "total_findings": current_scope_total_findings,
            "materialized_findings": current_scope_materialized_findings,
            "raw_candidate_findings": current_scope_raw_candidate_findings,
            "ready_bug_count": ready_bug_count,
            "needs_validation_findings": needs_validation_count,
            "not_reproduced_findings": not_reproduced_count,
            "customer_ready_defects": current_scope_customer_ready_defect_count,
            "current_report_total_findings": current_scope_total_findings,
            "current_report_materialized_findings": current_scope_materialized_findings,
            "current_report_customer_ready_defect_count": current_scope_customer_ready_defect_count,
            "current_campaign_bundle_finding_count_raw": int(current_campaign_bundle_stats.get("raw") or 0),
            "family_customer_ready_defect_count": family_customer_ready_defect_count,
            "family_materialized_findings": materialized_total,
            "internal_clue_count": len(internal_clues),
            "ui_candidate_findings": ui_stats["ui_candidate_total"],
            "ui_verified_candidates": ui_stats["ui_verified_candidate_total"],
            "ui_high_confidence_candidates": ui_stats["ui_high_confidence_candidate_total"],
            "defect_intake_recommended": intake_stats["defect_intake_recommended_total"],
            "grade": evidence_grade,
            "score": evidence_trust,
            "regression_last_run_at": _first_text((regression_summary.get("latest_run") or {}).get("generated_at")),
            "regression_last_run_mode": _first_text((regression_summary.get("latest_run") or {}).get("suite_mode")),
            "regression_gate_status": _first_text((regression_summary.get("latest_run") or {}).get("gate_status")),
            "regression_failed_defect_count": int(regression_summary.get("failed_defect_count") or 0),
            "regression_pending_defect_count": int(regression_summary.get("pending_defect_count") or 0),
            "regression_covered_defect_count": int(regression_summary.get("covered_defect_count") or 0),
            "regression_history_run_count": int(regression_summary.get("history_run_count") or 0),
            "regression_trend_direction": _first_text(regression_summary.get("trend_direction")),
            "release_recommendation": _first_text(regression_summary.get("release_recommendation")),
            "customer_delivery_readiness": _first_text(regression_summary.get("customer_delivery_readiness")),
            "regression_double_run_verified": 1 if validation_summary.get("double_run_verified") else 0,
            "regression_repeated_failure_defect_count": int(validation_summary.get("repeated_failure_defect_count") or 0),
            "report_path": current_report_path or str(current_report_source.get("report_source_path") or current_report_source.get("report_path") or report.get("report_source_path") or report.get("report_path") or ""),
            "current_report_breakdown": current_report_breakdown,
        }
        # ── P6: Benchmark metrics (only when ground truth exists) ──
        benchmark_data: dict[str, Any] = {}
        _bm_path = root / "platform_outputs" / project_id / "benchmark" / "benchmark_metrics.json"
        if _bm_path.exists():
            benchmark_data = _svc()._read_json_object(_bm_path)
        if benchmark_data:
            scan_meta["benchmark_metrics"] = benchmark_data
        if campaign_scope:
            scan_meta["current_campaign_scope"] = campaign_scope
        data = {
            "project_id": project_id,
            "project_name": str(report.get("project_name") or project_id),
            "industry": str(report.get("industry") or "multi_layer"),
            "updated_at": scan_meta["last_scan_at"] or str(report.get("generated_at_utc") or ""),
            "live_map": {"status": "completed" if report else "idle"},
            "scan_meta": scan_meta,
            "execution_evidence_summary": execution_evidence_summary,
            "regression_summary": regression_summary,
            "defects": delivery_defects,
            "clues": internal_clues,
            "risks": display_risks,
            "defect_grouped_summary": defect_grouped_summary,
            "defect_priority_summary": defect_priority_summary,
            "defect_repro_summary": defect_repro_summary,
            "defect_delivery_cards": defect_delivery_cards,
            "value_metrics": {
                "evidence_trust_score": evidence_trust,
                "ai_equivalent_test_points": ai_points,
                "canonical_risk_count": current_scope_total_findings,
                "materialized_risk_count": current_scope_materialized_findings,
                "raw_candidate_risk_count": current_scope_raw_candidate_findings,
                "ready_bug_count": ready_bug_count,
                "needs_validation_count": needs_validation_count,
                "not_reproduced_count": not_reproduced_count,
                "defect_count": family_customer_ready_defect_count,
                "clue_count": len(internal_clues),
                "current_report_total_findings": current_scope_total_findings,
                "current_report_customer_ready_defect_count": current_scope_customer_ready_defect_count,
                "family_customer_ready_defect_count": family_customer_ready_defect_count,
                "p0_count": canonical_p0,
                "p1_count": canonical_p1,
                "p2_count": canonical_p2,
                "ready_p0_count": ready_p0,
                "ready_p1_count": ready_p1,
                "status_counts": status_counts,
                "ui_total": ui_stats["ui_total"],
                "ui_candidate_total": ui_stats["ui_candidate_total"],
                "ui_verified_candidate_total": ui_stats["ui_verified_candidate_total"],
                "ui_unverified_candidate_total": ui_stats["ui_unverified_candidate_total"],
                "ui_high_confidence_candidate_total": ui_stats["ui_high_confidence_candidate_total"],
                "defect_intake_recommended_total": intake_stats["defect_intake_recommended_total"],
                "customer_defect_intake_total": intake_stats["customer_defect_intake_total"],
                "internal_defect_intake_total": intake_stats["internal_defect_intake_total"],
                "regression_covered_defect_count": int(regression_summary.get("covered_defect_count") or 0),
                "regression_passed_defect_count": int(regression_summary.get("passed_defect_count") or 0),
                "regression_failed_defect_count": int(regression_summary.get("failed_defect_count") or 0),
                "regression_needs_review_defect_count": int(regression_summary.get("needs_review_defect_count") or 0),
                "regression_pending_defect_count": int(regression_summary.get("pending_defect_count") or 0),
                "regression_not_covered_defect_count": int(regression_summary.get("not_covered_defect_count") or 0),
                "regression_gate_status": _first_text((regression_summary.get("latest_run") or {}).get("gate_status")),
                "regression_last_run_mode": _first_text((regression_summary.get("latest_run") or {}).get("suite_mode")),
                "regression_history_run_count": int(regression_summary.get("history_run_count") or 0),
                "regression_trend_direction": _first_text(regression_summary.get("trend_direction")),
                "release_recommendation": _first_text(regression_summary.get("release_recommendation")),
                "customer_delivery_readiness": _first_text(regression_summary.get("customer_delivery_readiness")),
                "regression_double_run_verified": 1 if validation_summary.get("double_run_verified") else 0,
                "regression_repeated_failure_defect_count": int(validation_summary.get("repeated_failure_defect_count") or 0),
                "scores": scores,
                "commercial_value": commercial_value,
                "current_report_breakdown": current_report_breakdown,
                "defect_grouped_summary": defect_grouped_summary,
                "defect_priority_summary": defect_priority_summary,
                "defect_repro_summary": defect_repro_summary,
                "defect_delivery_cards": defect_delivery_cards,
                "regression_summary": regression_summary,
            },
            "data_contract": {
                **display_contract,
                "endpoint": f"/api/v1/projects/{project_id}/command-center",
                "frontend_entry": "frontend/src/api/client.ts:getFindings",
                "backend_builder": "ai_test_asset_center/private_pilot_service.py:_build_command_center",
                "formatter": "ai_test_asset_center/display_ready_formatter.py:format_findings_display_ready",
                "display_key": "defects",
                "compatibility_alias": "risks",
                "contract_rule": "客户页优先渲染 data.defects；data.risks 仅为兼容别名。内部待验证线索统一放在 data.clues。",
                "current_report_breakdown": current_report_breakdown,
                "defect_grouped_summary": defect_grouped_summary,
                "defect_priority_summary": defect_priority_summary,
                "defect_repro_summary": defect_repro_summary,
                "defect_delivery_cards": defect_delivery_cards,
                "regression_summary": regression_summary,
            },
            "delivery_tracks": {
                "defects": {
                    **display_contract,
                    "display_key": "defects",
                    "compatibility_alias": "risks",
                },
                "clues": clue_contract,
            },
            "business_flow_summary": {"total": ai_points},
            "executive_summary": {
                "total_findings": current_scope_total_findings,
                "total_bugs_found": ready_bug_count,
                "ready_bugs": ready_bug_count,
                "customer_ready_defects": current_scope_customer_ready_defect_count,
                "family_customer_ready_defects": family_customer_ready_defect_count,
                "internal_clues": len(internal_clues),
                "needs_validation_findings": needs_validation_count,
                "not_reproduced_findings": not_reproduced_count,
                "raw_candidate_findings": current_scope_raw_candidate_findings,
                "critical_bugs": ready_p0,
                "high_priority_bugs": ready_p1,
                "materialized_findings": current_scope_materialized_findings,
                "ui_candidate_findings": ui_stats["ui_candidate_total"],
                "ui_verified_candidates": ui_stats["ui_verified_candidate_total"],
                "ui_high_confidence_candidates": ui_stats["ui_high_confidence_candidate_total"],
                "defect_intake_recommended": intake_stats["defect_intake_recommended_total"],
                "regression_gate_status": _first_text((regression_summary.get("latest_run") or {}).get("gate_status")),
                "regression_failed_defects": int(regression_summary.get("failed_defect_count") or 0),
                "regression_pending_defects": int(regression_summary.get("pending_defect_count") or 0),
                "regression_covered_defects": int(regression_summary.get("covered_defect_count") or 0),
                "regression_history_run_count": int(regression_summary.get("history_run_count") or 0),
                "regression_trend_direction": _first_text(regression_summary.get("trend_direction")),
                "release_recommendation": _first_text(regression_summary.get("release_recommendation")),
                "release_recommendation_label": _first_text(regression_summary.get("release_recommendation_label")),
                "customer_delivery_readiness": _first_text(regression_summary.get("customer_delivery_readiness")),
                "customer_delivery_readiness_label": _first_text(regression_summary.get("customer_delivery_readiness_label")),
                "regression_double_run_verified": 1 if validation_summary.get("double_run_verified") else 0,
                "regression_repeated_failure_defects": int(validation_summary.get("repeated_failure_defect_count") or 0),
                "llm_powered_analyses": ai_points,
                "system_grade": scan_meta["grade"],
                "overall_score": scan_meta["score"],
                "current_report_breakdown": current_report_breakdown,
                "defect_grouped_summary": defect_grouped_summary,
                "defect_priority_summary": defect_priority_summary,
                "defect_repro_summary": defect_repro_summary,
                "defect_delivery_cards": defect_delivery_cards,
                "regression_summary": regression_summary,
            },
        }
        if campaign_scope:
            data["current_campaign_scope"] = campaign_scope
            data["value_metrics"]["current_campaign_scope"] = campaign_scope
            data["executive_summary"]["current_campaign_scope"] = campaign_scope
        # Discovery funnel observability (five-stage + blockers). Prefer the
        # freshest scan report; never invent numbers when absent.
        discovery_funnel = None
        discovery_funnel_report = None
        for source in (current_scan_report, report, real_discovery_payload):
            if not isinstance(source, dict):
                continue
            candidate_report = source.get("discovery_funnel_report")
            if isinstance(candidate_report, dict):
                discovery_funnel_report = candidate_report
                break
            nested = source.get("v12")
            if isinstance(nested, dict) and isinstance(
                nested.get("discovery_funnel_report"), dict
            ):
                discovery_funnel_report = nested["discovery_funnel_report"]
                break
        for source in (current_scan_report, report, real_discovery_payload):
            if isinstance(source, dict) and isinstance(source.get("discovery_funnel"), dict):
                discovery_funnel = source.get("discovery_funnel")
                break
            if isinstance(source, dict) and isinstance(source.get("v12"), dict):
                nested = source["v12"].get("discovery_funnel")
                if isinstance(nested, dict):
                    discovery_funnel = nested
                    break
        if isinstance(discovery_funnel, dict):
            data["discovery_funnel"] = discovery_funnel
            data["scan_meta"]["discovery_funnel"] = discovery_funnel
            pipeline_health = discovery_funnel.get("pipeline_health") if isinstance(discovery_funnel.get("pipeline_health"), dict) else None
            if not pipeline_health:
                for source in (current_scan_report, report, real_discovery_payload):
                    if isinstance(source, dict) and isinstance(source.get("pipeline_health"), dict):
                        pipeline_health = source.get("pipeline_health")
                        break
                    if isinstance(source, dict) and isinstance(source.get("v12"), dict):
                        nested_funnel = source["v12"].get("discovery_funnel")
                        if isinstance(nested_funnel, dict) and isinstance(nested_funnel.get("pipeline_health"), dict):
                            pipeline_health = nested_funnel.get("pipeline_health")
                            break
            if isinstance(pipeline_health, dict):
                data["pipeline_health"] = pipeline_health
                data["scan_meta"]["pipeline_health"] = pipeline_health
                data["scan_meta"]["pipeline_health_status"] = str(pipeline_health.get("status") or "")
                data["scan_meta"]["empty_findings_means_no_bugs"] = bool(pipeline_health.get("empty_findings_means_no_bugs"))
        if isinstance(discovery_funnel_report, dict):
            data["discovery_funnel_report"] = discovery_funnel_report
            data["scan_meta"]["discovery_funnel_report"] = discovery_funnel_report
        if knowledge_summary:
            data["knowledge_summary"] = knowledge_summary
        if real_discovery_payload and isinstance(discovery_payload.get("continuous_discovery_campaign"), dict):
            data["continuous_discovery_campaign"] = _svc()._augment_continuous_discovery_campaign(
                discovery_payload["continuous_discovery_campaign"],
                current_report_breakdown=current_report_breakdown,
                delivery_defects=delivery_defects,
                current_campaign_customer_ready_defect_count=int(current_campaign_bundle_stats.get("deduped") or 0),
                current_campaign_bundle_finding_count_raw=int(current_campaign_bundle_stats.get("raw") or 0),
            )
        if real_discovery_payload and isinstance(discovery_payload.get("metrics"), dict):
            data["continuous_discovery_metrics"] = {
                key: value
                for key, value in discovery_payload["metrics"].items()
                if str(key).startswith("continuous_discovery_") or str(key) == "doc_completeness"
            }
        spectrum = self._load_spectrum_status_payload(root, project_id)
        if spectrum:
            data["spectrum"] = spectrum
        # ── 累积 findings 统计 + continuous 状态 ──
        tenant_id = self._request_tenant()
        data["cumulative_stats"] = db_persist.get_finding_stats(root, tenant_id, project_id)
        if commercial_assets:
            data["commercial_assets"] = commercial_assets
            data["scan_meta"]["commercial_handoff_status"] = _first_text((commercial_assets.get("commercial_handoff") or {}).get("status"))
            data["scan_meta"]["external_tracker_sync_payload_status"] = _first_text((commercial_assets.get("tracker_sync") or {}).get("payload_status"))
            data["scan_meta"]["delivery_package_status"] = _first_text((commercial_assets.get("delivery_package") or {}).get("status"))
            data["value_metrics"]["commercial_asset_materialized"] = 1 if commercial_assets.get("status") == "materialized" else 0
            data["value_metrics"]["commercial_delivery_package_created"] = 1 if _first_text((commercial_assets.get("delivery_package") or {}).get("status")) == "created" else 0
            data["executive_summary"]["commercial_handoff_status"] = _first_text((commercial_assets.get("commercial_handoff") or {}).get("status"))
            data["executive_summary"]["delivery_package_status"] = _first_text((commercial_assets.get("delivery_package") or {}).get("status"))
        # ── Rounds Summary (Round 1-4 data for dashboard) ──
        from .rounds_summary import build_rounds_summary
        data["rounds_summary"] = build_rounds_summary(project_id, root)

        # ── 主链 8: 测试任务看板 ──
        # Surface the per-task lifecycle status board (主链 4), the production-data
        # safety-boundary block count (主链 5/6), and the persisted evidence-chain
        # count (主链 7) so the frontend can render the full main-chain closure.
        # All fields come straight from the v12 report; no transformation.
        _test_task_board = self._build_test_task_board(report)
        if _test_task_board:
            data["test_task_board"] = _test_task_board
        # ── 主链 4/5 覆盖诚实性: surface unexecuted high-value slices + any grade
        # downgrade so the frontend never shows a clean completion while
        # authorization/isolation/money/concurrency checks were silently skipped. ──
        _coverage_honesty = current_scan_report.get("coverage_honesty") if isinstance(current_scan_report, dict) else None
        if isinstance(_coverage_honesty, dict):
            data["coverage_honesty"] = _coverage_honesty
            if _test_task_board:
                data["test_task_board"]["coverage_honesty"] = _coverage_honesty
        data["continuous_state"] = _get_continuous_state(root, project_id)
        # External evaluation / commercial quality projection (Phase 0 SSOT).
        # NOT_MEASURED must never surface as a quality score of 100 or 0.
        try:
            from .discovery_quality_projection import (
                attach_quality_projection_to_scan_result,
                suppress_benchmark_quality_when_not_measured,
            )
            from .discovery_mainline_contract import MainlineContractError

            _scan_for_quality: dict[str, Any] = {}
            for source in (current_scan_report, report, real_discovery_payload):
                if isinstance(source, dict) and source:
                    _scan_for_quality = dict(source)
                    break
            _current_findings_declared = False
            if isinstance(current_scan_report, dict):
                for _finding_key in ("findings", "real_findings"):
                    if isinstance(current_scan_report.get(_finding_key), list):
                        _scan_for_quality["findings"] = list(current_scan_report.get(_finding_key) or [])
                        _current_findings_declared = True
                        break
                if isinstance(current_scan_report.get("candidate_findings"), list):
                    _scan_for_quality["candidate_findings"] = list(current_scan_report.get("candidate_findings") or [])
            if not _current_findings_declared and not _scan_for_quality.get("findings"):
                _scan_for_quality["findings"] = list(delivery_defects or [])
            if isinstance(discovery_funnel, dict):
                _scan_for_quality["discovery_funnel"] = discovery_funnel
            # Prefer v12 nested obligation/experiment fields when present on report.
            if isinstance(current_scan_report, dict):
                for _key in (
                    "test_obligations",
                    "experiment_compile",
                    "obligation_plan",
                    "execution_adapters",
                    "behavior_ir",
                    "phases",
                    "pipeline_health",
                    "campaign",
                    "behavior_slice_ledger",
                    "v12",
                ):
                    if _key not in _scan_for_quality and current_scan_report.get(_key) is not None:
                        _scan_for_quality[_key] = current_scan_report.get(_key)
            if isinstance(report, dict):
                for _key in (
                    "test_obligations",
                    "experiment_compile",
                    "obligation_plan",
                    "execution_adapters",
                    "behavior_ir",
                    "phases",
                    "v12",
                ):
                    if _key not in _scan_for_quality and report.get(_key) is not None:
                        _scan_for_quality[_key] = report.get(_key)
            _projected = attach_quality_projection_to_scan_result(_scan_for_quality)
            _external = _projected.get("external_evaluation") if isinstance(_projected.get("external_evaluation"), dict) else {}
            _counts = _projected.get("formal_count_projection") if isinstance(_projected.get("formal_count_projection"), dict) else {}
            _classification = (
                _projected.get("finding_classification")
                if isinstance(_projected.get("finding_classification"), dict)
                else {}
            )
            _scope_counts = (
                _projected.get("scope_counts")
                if isinstance(_projected.get("scope_counts"), dict)
                else {}
            )
            _obl = (
                _projected.get("obligation_execution_projection")
                if isinstance(_projected.get("obligation_execution_projection"), dict)
                else {}
            )
            _run_delivery = (
                _projected.get("run_delivery_readiness")
                if isinstance(_projected.get("run_delivery_readiness"), dict)
                else {}
            )
            _commercial_readiness = (
                _projected.get("commercial_readiness")
                if isinstance(_projected.get("commercial_readiness"), dict)
                else {}
            )
            data["external_evaluation"] = _external
            data["commercial_readiness"] = _commercial_readiness
            data["run_delivery_readiness"] = _run_delivery
            data["release_gate"] = _projected.get("release_gate") or {}
            data["formal_count_projection"] = _counts
            data["finding_classification"] = _classification
            data["scope_counts"] = _scope_counts
            data["obligation_execution_projection"] = _obl
            data["quality_claim_status"] = _projected.get("quality_claim_status") or "NOT_MEASURED"
            data["commercial_quality_score"] = _projected.get("commercial_quality_score")
            data["score_semantics"] = _projected.get("score_semantics") or {}
            data["scan_meta"]["external_evaluation"] = _external
            data["scan_meta"]["quality_claim_status"] = data["quality_claim_status"]
            data["scan_meta"]["commercial_quality_score"] = data["commercial_quality_score"]
            data["scan_meta"]["formal_customer_deliverable_count"] = _counts.get("formal_customer_deliverable_count")
            data["scan_meta"]["published_formal_deliverable_count"] = _run_delivery.get(
                "published_formal_deliverable_count"
            )
            data["scan_meta"]["run_delivery_readiness"] = _run_delivery
            data["scan_meta"]["commercial_readiness"] = _commercial_readiness
            data["scan_meta"]["obligation_execution_projection"] = _obl
            # The formal delivery gate is the only source for customer-facing
            # defect lists and counts.  Legacy readiness/campaign counters are
            # retained only as diagnostics below, never as commercial defects.
            _eligible_formal_deliverables = list(_classification.get("deliverable") or [])
            _formal_deliverables = (
                _eligible_formal_deliverables
                if _run_delivery.get("release_ready") is True
                else []
            )
            _candidate_findings = list(_classification.get("candidate") or [])
            _rejected_findings = list(_classification.get("rejected") or [])
            _eligible_formal_count = int(
                _counts.get("formal_customer_deliverable_count") or 0
            )
            _formal_count = int(
                _run_delivery.get("published_formal_deliverable_count") or 0
            )
            _legacy_count_diagnostics = {
                "current_report_readiness_count": data["scan_meta"].get("current_report_customer_ready_defect_count"),
                "current_campaign_readiness_count": data["scan_meta"].get("current_campaign_customer_ready_defect_count"),
                "project_family_readiness_count": data["scan_meta"].get("family_customer_ready_defect_count"),
                "semantics": "diagnostic_only_not_formal_customer_deliverables",
            }
            data["deliverable_findings"] = _formal_deliverables
            data["eligible_formal_deliverable_findings"] = _eligible_formal_deliverables
            data["candidate_findings"] = _candidate_findings
            data["rejected_findings"] = _rejected_findings
            data["defects"] = _formal_deliverables
            data["risks"] = _formal_deliverables
            data["clues"] = _candidate_findings
            data["legacy_count_diagnostics"] = _legacy_count_diagnostics
            data["project_history"] = {
                "measurement_status": "NOT_MEASURED",
                "formal_customer_deliverable_count": len(delivery_defects or []),
                "deliverable_findings": list(delivery_defects or []),
                "note": "Historical shelf is a separate scope and is never merged into current-run formal counts.",
            }
            data["scope_counts"] = {
                **_scope_counts,
                "current_run_formal_deliverable": _eligible_formal_count,
                "current_campaign_formal_deliverable": _eligible_formal_count,
                "current_run_published_formal_deliverable": _formal_count,
                "project_open_formal_deliverable": None,
                "project_open_measurement_status": "NOT_MEASURED",
            }
            for _surface in (data["scan_meta"], data["executive_summary"], data["value_metrics"]):
                _surface["formal_customer_deliverable_count"] = _formal_count
                _surface["current_report_customer_ready_defect_count"] = _formal_count
                _surface["customer_ready_defects"] = _formal_count
                _surface["current_campaign_customer_ready_defect_count"] = _formal_count
                _surface["legacy_count_diagnostics"] = _legacy_count_diagnostics
            data["executive_summary"]["total_bugs_found"] = _formal_count
            data["executive_summary"]["ready_bugs"] = _formal_count
            data["value_metrics"]["defect_count"] = _formal_count
            data["value_metrics"]["ready_bug_count"] = _formal_count
            data["scan_meta"]["ready_bug_count"] = _formal_count
            if _test_task_board and _obl:
                data["test_task_board"]["obligation_execution_projection"] = _obl
            if str(_external.get("measurement_status") or "").upper() != "MEASURED":
                data["executive_summary"]["overall_score"] = None
                data["executive_summary"]["commercial_quality_suppressed"] = True
                data["executive_summary"]["quality_label"] = "尚未完成外部质量评测"
                data["value_metrics"]["commercial_quality_score"] = None
                data["value_metrics"]["quality_claim_status"] = "NOT_MEASURED"
            if isinstance(data.get("scan_meta"), dict) and isinstance(data["scan_meta"].get("benchmark_metrics"), dict):
                data["scan_meta"]["benchmark_metrics"] = suppress_benchmark_quality_when_not_measured(
                    data["scan_meta"]["benchmark_metrics"],
                    _external,
                )
        except MainlineContractError:
            raise
        except Exception as _quality_exc:
            data["external_evaluation"] = {
                "measurement_status": "NOT_MEASURED",
                "reason": f"quality_projection_failed:{type(_quality_exc).__name__}",
                "display": {
                    "quality_label": "尚未完成外部质量评测",
                    "suppress_quality_score": True,
                    "suppress_recall_precision": True,
                },
            }
            data["quality_claim_status"] = "NOT_MEASURED"
            data["commercial_quality_score"] = None
            data["executive_summary"]["overall_score"] = None
            data["executive_summary"]["quality_label"] = "尚未完成外部质量评测"
        return {
            "ok": True,
            "data": data,
        }

