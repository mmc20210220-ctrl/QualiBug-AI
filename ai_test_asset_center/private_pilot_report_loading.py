"""Report / findings loading helpers for PrivatePilotHandler."""
from __future__ import annotations

import json
import logging
import threading
import os
import re
import time
from pathlib import Path
from typing import Any

from .private_pilot_campaign_projection import _report_finding_dedupe_key as _finding_dedupe_key
from .private_pilot_defect_summaries import (
    _claim_request_identity,
    _materialized_path_matches,
    _validate_api_path,
)
from .private_pilot_json_io import _read_json_artifact, _read_json_object, _read_json_safe
from .real_id_resolver import normalize_path_placeholders
from .real_project_onboarding import _safe_project_id

_LOGGER = logging.getLogger(__name__)

# 产物化证据 bundle 的报告视图缓存：内容寻址产物不可变，指针 mtime 即身份。
_EVIDENCE_BUNDLE_VIEW_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_EVIDENCE_BUNDLE_VIEW_CACHE_MAX = 32
_EVIDENCE_BUNDLE_VIEW_CACHE_LOCK = threading.Lock()

class ReportLoadingMixin:
    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any]:
        from .private_pilot_build_scope import scoped_read_json

        return scoped_read_json(path, lambda: _read_json_object(path))

    @staticmethod
    def _read_scan_report_payload(path: Path) -> dict[str, Any]:
        """Read one scan-report candidate with Dual-Read hydration.

        ``scan_result.json`` may be an artifactized/sharded store: its heavy
        rows persist as ``$qualibug_artifact_ref`` placeholders that hydrate
        ONLY through ``scan_result_store.load_scan_result``. A bare JSON read
        yields ref-placeholder rows with no identity and no authority
        fingerprint, which downstream fail-fast contracts then reject with
        ``finding_authority_fingerprint_missing`` — blocking the entire
        command-center view for the project. Hydration failures fall back to
        the raw view (store-disabled mode) with a visible warning; downstream
        contracts still surface any real inconsistency.

        Reads are memoized per build scope (see ``private_pilot_build_scope``):
        one command-center build re-enters this loader for the same artifact
        identity several times; hydration of a multi-hundred-MB shard store is
        far too expensive to repeat per consumer.
        """
        from .private_pilot_build_scope import scoped_read_json, scoped_scan_report

        if path.name != "scan_result.json":
            return scoped_read_json(path, lambda: _read_json_object(path))

        def _hydrate() -> dict[str, Any]:
            from .scan_result_store import (
                STORE_COMPLETE_MARKER,
                is_sharded_scan_result,
                load_scan_result,
            )

            parts_dir = path.parent / "scan_result.parts"
            marker_path = parts_dir / STORE_COMPLETE_MARKER
            try:
                if parts_dir.is_dir() and not marker_path.is_file():
                    # 撕裂 store：完成标记缺失，加载器必然拒绝。在这里立即
                    # 失败，避免每个轮询周期都白白解析数十 MB 的索引。
                    raise ValueError(
                        "scan_result store is torn/incomplete: completion "
                        f"marker missing ({marker_path}); refusing partial "
                        "hydration. Re-run the scan to regenerate a consistent "
                        "store."
                    )
                hydrated = load_scan_result(path)
                if hydrated:
                    return hydrated
            except Exception as exc:  # noqa: BLE001 - fallback keeps report load alive
                _LOGGER.warning(
                    "scan_result_dual_read_hydration_failed path=%s error_type=%s error=%s",
                    path,
                    type(exc).__name__,
                    str(exc)[:240],
                    exc_info=True,
                )
                if is_sharded_scan_result(path):
                    # 分片索引没有可用的裸视图：裸读会再次进入加载器并复现
                    # 同一个错误（回退死路）。跳过该候选，让候选循环尝试
                    # 下一个数据源（v12_report / intelligence_report /
                    # workspace 副本 / evidence bundle）。
                    return {}
            return _read_json_object(path)

        return scoped_scan_report(path, _hydrate)

    @staticmethod
    def _read_knowledge_asset_scoped(path: Path) -> dict[str, Any] | None:
        """Parse one knowledge-asset candidate within the active build scope.

        Legacy semantics preserved exactly: any parse failure returns ``None``
        so callers skip to the next candidate; the ``None`` result itself is
        cached so one build never retries the same failed identity.
        """

        def _parse() -> dict[str, Any] | None:
            try:
                payload = json.loads(path.read_text(encoding="utf-8") or "{}")
            except Exception:
                return None
            return payload if isinstance(payload, dict) else None

        from .private_pilot_build_scope import scoped_read_json

        return scoped_read_json(path, _parse)

    @staticmethod
    def _mtime_utc(path: Path) -> str:
        try:
            return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(path.stat().st_mtime))
        except Exception:
            return ""

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _normalize_severity(value: Any) -> str:
        text = str(value or "").strip().upper()
        return {"CRITICAL": "P0", "HIGH": "P1", "MEDIUM": "P2", "LOW": "P2"}.get(text, text if text in {"P0", "P1", "P2", "P3"} else "P1")

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _report_signal_count(payload: dict[str, Any]) -> int:
        def _count_list(value: Any) -> int:
            return len(value) if isinstance(value, list) else 0

        direct_counts = [
            payload.get("risk_total"),
            payload.get("risk_count"),
            payload.get("total_findings"),
            payload.get("total_bugs_found"),
            payload.get("total_found"),
            payload.get("raw_total"),
            (payload.get("executive_summary") or {}).get("total_findings") if isinstance(payload.get("executive_summary"), dict) else None,
            (payload.get("summary") or {}).get("total_findings") if isinstance(payload.get("summary"), dict) else None,
        ]
        materialized = max(
            _count_list(payload.get("real_findings")),
            _count_list(payload.get("findings")),
            _count_list(payload.get("bug_scores")),
            _count_list(payload.get("e2e_findings")),
            _count_list(payload.get("deep_findings")),
            _count_list(payload.get("ui_findings")),
            _count_list((payload.get("db_verification") or {}).get("findings") if isinstance(payload.get("db_verification"), dict) else None),
        )
        parsed_counts: list[int] = [materialized]
        for value in direct_counts:
            try:
                parsed_counts.append(int(float(value)))
            except Exception:
                continue
        return max(parsed_counts or [0])

    @staticmethod
    def _report_summary_number(report: dict[str, Any], *keys: str, fallback: int = 0) -> int:
        scopes: list[Any] = [report]
        for nested in ("executive_summary", "summary", "value_metrics", "scan_meta"):
            value = report.get(nested)
            if isinstance(value, dict):
                scopes.append(value)
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            for key in keys:
                if key not in scope:
                    continue
                try:
                    return int(float(scope.get(key)))
                except Exception:
                    continue
        return fallback

    @staticmethod
    def _discovery_current_scope_summary(payload: dict[str, Any]) -> dict[str, Any]:
        campaign_projection = payload.get("continuous_discovery_campaign") if isinstance(payload.get("continuous_discovery_campaign"), dict) else {}
        summary = campaign_projection.get("summary") if isinstance(campaign_projection.get("summary"), dict) else {}
        current_run = campaign_projection.get("current_run") if isinstance(campaign_projection.get("current_run"), dict) else {}
        campaign = campaign_projection.get("campaign") if isinstance(campaign_projection.get("campaign"), dict) else {}
        scopes = [current_run, summary, campaign_projection, campaign]

        def _pick_int(*keys: str) -> int | None:
            for scope in scopes:
                if not isinstance(scope, dict):
                    continue
                for key in keys:
                    if key not in scope:
                        continue
                    try:
                        return int(float(scope.get(key)))
                    except Exception:
                        continue
            return None

        total_findings = _pick_int(
            "current_campaign_bundle_finding_count_raw",
            "current_report_total_findings",
            "total_findings",
        )
        customer_ready_defects = _pick_int(
            "current_campaign_customer_ready_defect_count",
            "current_report_customer_ready_defect_count",
            "customer_ready_defects",
            "ready_bug_count",
        )
        confirmed_slice_count = _pick_int(
            "current_campaign_confirmed_slice_count",
            "confirmed_slice_count",
        )
        if total_findings is None and customer_ready_defects is None and confirmed_slice_count is None:
            return {}
        return {
            "total_findings": max(0, int(total_findings or 0)),
            "customer_ready_defects": max(0, int(customer_ready_defects or 0)),
            "confirmed_slice_count": max(0, int(confirmed_slice_count or 0)),
            "report_source_path": str(payload.get("report_source_path") or ""),
        }

    # 轻报告 = Phase-6 产物化运行报告（finding 摘要 + artifact_refs，MB 级）。
    # 重候选 = scan_result 分片 store / v12 全量报告——分片 store 的全量水合
    # 实测分钟级（590MB 组装），只有轻源全部缺失时才允许回退。
    _HEAVY_REPORT_RELPATHS = (
        ("platform_outputs", "{p}", "v12_report.json"),
        ("platform_outputs", "{p}", "scan_result.json"),
        ("platform_workspace", "{p}", "v12_report.json"),
        ("platform_workspace", "{p}", "scan_result.json"),
    )

    def _light_report_candidates(self, project: str, root: Path) -> list[Path]:
        return [
            root / "platform_outputs" / project / "intelligence_report.json",
            root / "platform_workspace" / project / "intelligence_report.json",
            root / "benchmark_outputs" / project / "intelligence_report.json",
        ]

    def _load_v12_report(self, project_id: str, root: Path) -> dict[str, Any]:
        project = _safe_project_id(project_id)

        # Do not return the first/newest JSON blindly. Real backend runs can write
        # summary numbers to one report while evidence files are written under
        # platform_workspace. Pick the strongest source-of-truth by materialized
        # finding signal, then by mtime. This prevents the React page from reading
        # an older/empty scan_result while the backend report shows newer totals.
        #
        # 轻报告优先：产物化运行报告是同一 run 的正式摘要（finding 摘要 +
        # artifact_refs），命令中心视图所需的全部业务行都在其中；重型
        # scan_result 分片组装只在轻源缺失时作为回退，绝不让一次页面轮询
        # 隐式支付 590MB 的全量水合。
        candidate_payloads: list[tuple[int, float, dict[str, Any]]] = []

        def _consider(path: Path) -> None:
            if not path.exists():
                return
            payload = self._read_scan_report_payload(path)
            if not payload:
                return
            payload.setdefault("report_source_path", path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path))
            candidate_payloads.append((self._report_signal_count(payload), path.stat().st_mtime, payload))

        for path in self._light_report_candidates(project, root):
            _consider(path)
        if not candidate_payloads:
            for rel in self._HEAVY_REPORT_RELPATHS:
                _consider(root.joinpath(*[part.format(p=project) for part in rel]))

        workspace_report = self._load_workspace_report(project, root)
        if workspace_report:
            workspace_path = root / "platform_workspace" / project
            candidate_payloads.append((
                self._report_signal_count(workspace_report),
                workspace_path.stat().st_mtime if workspace_path.exists() else time.time(),
                workspace_report,
            ))

        # When the latest scan_result only contains the current scan delta, a
        # completed campaign can legitimately produce 0 new findings while
        # historical confirmed defects still exist in a related evidence bundle.
        # Recover the strongest same-snapshot/lineage evidence bundle so the
        # command center reflects real customer-visible defects instead of a
        # misleading empty list.
        anchor_candidate = max(candidate_payloads, key=lambda item: (item[1], item[0])) if candidate_payloads else None
        anchor_report = anchor_candidate[2] if anchor_candidate else {}
        related_bundle_candidates = self._load_evidence_bundle_report_candidates(project, root, anchor_report)
        candidate_payloads.extend(related_bundle_candidates)
        aggregate_candidate = self._aggregate_related_report_candidate(anchor_candidate, related_bundle_candidates)
        if aggregate_candidate is not None:
            candidate_payloads.append(aggregate_candidate)

        if candidate_payloads:
            candidate_payloads.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidate_payloads[0][2]

        # Last-resort benchmark aggregate: useful when a benchmark run was written
        # outside platform_outputs but the frontend still asks for that project.
        batch_path = root / "benchmark_outputs" / "batch_report.json"
        batch = self._read_json_dict(batch_path)
        results = batch.get("results")
        if isinstance(results, dict):
            matched = results.get(project) or results.get(project_id)
            if isinstance(matched, dict):
                return {
                    "project_id": project,
                    "project_name": project_id,
                    "generated_at_utc": self._mtime_utc(batch_path),
                    "system_grade": str(matched.get("grade") or matched.get("system_grade") or ""),
                    "overall_score": self._coerce_float(matched.get("score") or matched.get("overall_score"), 0),
                    "total_findings": int(matched.get("total_found") or matched.get("total_findings") or 0),
                    "real_findings": [],
                    "summary": "benchmark aggregate only",
                    "report_source_path": "benchmark_outputs/batch_report.json",
                }
        return {}

    def _load_current_scan_report(self, project_id: str, root: Path) -> dict[str, Any]:
        project = _safe_project_id(project_id)
        # 轻报告优先（与 _load_v12_report 同一分层）：mtime 权威选择只在
        # 轻源内部进行；轻源全部缺失才回退重型候选。
        candidates = self._light_report_candidates(project, root)
        if not any(path.exists() for path in candidates):
            candidates = [
                root / "platform_outputs" / project / "v12_report.json",
                root / "platform_outputs" / project / "scan_result.json",
                root / "platform_workspace" / project / "v12_report.json",
                root / "platform_workspace" / project / "scan_result.json",
            ]
        chosen: tuple[float, int, dict[str, Any]] | None = None
        for path in candidates:
            if not path.exists():
                continue
            payload = self._read_scan_report_payload(path)
            if not payload:
                continue
            payload.setdefault("report_source_path", path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path))
            score = path.stat().st_mtime
            signal_count = self._report_signal_count(payload)
            if chosen is None or (score, signal_count) > (chosen[0], chosen[1]):
                chosen = (score, signal_count, payload)
        return chosen[2] if chosen else {}

    def _load_evidence_bundle_report_candidates(
        self,
        project_id: str,
        root: Path,
        anchor_report: dict[str, Any] | None = None,
    ) -> list[tuple[int, float, dict[str, Any]]]:
        project = _safe_project_id(project_id)
        bundle_root = root / "platform_workspace" / project / "evidence_bundles"
        if not bundle_root.exists():
            return []
        anchor = anchor_report if isinstance(anchor_report, dict) else {}
        anchor_campaign = anchor.get("campaign") if isinstance(anchor.get("campaign"), dict) else {}
        anchor_campaign_id = self._first_text(anchor_campaign.get("campaign_id"), anchor.get("campaign_id"))
        anchor_lineage_id = self._first_text(anchor_campaign.get("lineage_campaign_id"))
        anchor_snapshot = self._first_text(anchor_campaign.get("source_snapshot_hash"), (anchor.get("behavior_slice_ledger") or {}).get("source_snapshot_hash") if isinstance(anchor.get("behavior_slice_ledger"), dict) else "")
        anchor_source_hash = self._first_text(anchor_campaign.get("source_hash"), (anchor.get("runtime_contract") or {}).get("source_manifest", {}).get("source_hash") if isinstance((anchor.get("runtime_contract") or {}).get("source_manifest"), dict) else "")
        anchor_scope_id = self._first_text(anchor_campaign.get("scope_id"), anchor.get("scope_id"))
        anchor_environment_ref = self._first_text(anchor_campaign.get("environment_ref"), anchor.get("environment_ref"))
        anchored = bool(anchor_campaign_id or anchor_snapshot or anchor_source_hash)
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        for bundle_dir in sorted(bundle_root.glob("evb_*")):
            if not bundle_dir.is_dir():
                continue
            manifest_path = bundle_dir / "manifest.json"
            pointer_path = bundle_dir / "manifest.pointer.json"
            if pointer_path.is_file():
                # ── P0-4 Dual Read: artifactized bundles hydrate through the
                # content-addressed store; no evidence is copied back to disk. ──
                # Bundle 视图按 (project, bundle, pointer-mtime) 模块级缓存：
                # 内容寻址产物不可变，指针未变即视图未变。一次冷构建会为每个
                # bundle 读取 manifest/campaign/findings（实测 14 包 ~47s），
                # 缓存让后续构建直接复用。
                try:
                    from .evidence_artifactization import load_evidence_bundle_report_view

                    view = None
                    try:
                        _pointer_key = (project, bundle_dir.name, pointer_path.stat().st_mtime_ns)
                    except OSError:
                        _pointer_key = None
                    if _pointer_key is not None:
                        with _EVIDENCE_BUNDLE_VIEW_CACHE_LOCK:
                            view = _EVIDENCE_BUNDLE_VIEW_CACHE.get(_pointer_key)
                    if view is None:
                        view = load_evidence_bundle_report_view(project, bundle_dir.name, root=root)
                        if view and _pointer_key is not None:
                            with _EVIDENCE_BUNDLE_VIEW_CACHE_LOCK:
                                if len(_EVIDENCE_BUNDLE_VIEW_CACHE) >= _EVIDENCE_BUNDLE_VIEW_CACHE_MAX:
                                    _EVIDENCE_BUNDLE_VIEW_CACHE.clear()
                                _EVIDENCE_BUNDLE_VIEW_CACHE[_pointer_key] = view
                except Exception:
                    continue
                if not view:
                    continue
                manifest = view.get("manifest") if isinstance(view.get("manifest"), dict) else {}
                campaign = view.get("campaign") if isinstance(view.get("campaign"), dict) else {}
                findings = view.get("findings") if isinstance(view.get("findings"), list) else []
                source_file = pointer_path
                findings_path = pointer_path
            else:
                if not manifest_path.is_file():
                    continue
                manifest = self._read_json_dict(manifest_path)
                if not manifest or self._first_text(manifest.get("project_id")) != project:
                    continue
                campaign = self._read_json_dict(bundle_dir / "campaign.json")
                findings_path = bundle_dir / "findings.json"
                if not findings_path.exists():
                    continue
                try:
                    raw_findings = json.loads(findings_path.read_text(encoding="utf-8") or "[]")
                except Exception:
                    continue
                if not isinstance(raw_findings, list):
                    continue
                findings = [item for item in raw_findings if isinstance(item, dict)]
                source_file = manifest_path
            if not manifest or self._first_text(manifest.get("project_id")) != project:
                continue
            if not findings:
                continue
            bundle_campaign_id = self._first_text(campaign.get("campaign_id"), manifest.get("campaign_id"))
            bundle_lineage_id = self._first_text(campaign.get("lineage_campaign_id"))
            bundle_snapshot = self._first_text(campaign.get("source_snapshot_hash"))
            bundle_source_hash = self._first_text(campaign.get("source_hash"), (manifest.get("source_manifest") or {}).get("source_hash") if isinstance(manifest.get("source_manifest"), dict) else "")
            bundle_scope_id = self._first_text(campaign.get("scope_id"), manifest.get("scope_id"))
            bundle_environment_ref = self._first_text(campaign.get("environment_ref"), manifest.get("environment_ref"))
            family_ids = {value for value in (anchor_campaign_id, anchor_lineage_id) if value}
            bundle_family_ids = {value for value in (bundle_campaign_id, bundle_lineage_id) if value}
            family_match = bool(family_ids and bundle_family_ids.intersection(family_ids))
            scope_match = not (anchor_scope_id and bundle_scope_id) or anchor_scope_id == bundle_scope_id
            environment_match = not (anchor_environment_ref and bundle_environment_ref) or anchor_environment_ref == bundle_environment_ref
            source_match = bool(
                (anchor_snapshot and bundle_snapshot == anchor_snapshot)
                or (anchor_source_hash and bundle_source_hash == anchor_source_hash)
            )
            if anchored:
                if family_ids:
                    if not (family_match and scope_match and environment_match):
                        continue
                elif not (source_match and scope_match and environment_match):
                    continue
            created_at = self._first_text(manifest.get("created_at_utc"), self._mtime_utc(findings_path))
            payload = {
                "project_id": project,
                "project_name": project_id,
                "generated_at_utc": created_at,
                "system_grade": self._first_text(anchor.get("system_grade"), anchor.get("grade")),
                "overall_score": self._coerce_float(anchor.get("overall_score"), self._coerce_float(anchor.get("score"), 0.0)),
                "total_findings": len(findings),
                "raw_total": len(findings),
                "real_findings": findings,
                "bug_scores": findings,
                "summary": f"从 evidence bundle 回填 {len(findings)} 条历史确认结果。",
                "report_source_path": findings_path.relative_to(root).as_posix() if findings_path.is_relative_to(root) else str(findings_path),
                "campaign": campaign,
                "evidence_bundle": {
                    "bundle_id": self._first_text(manifest.get("bundle_id"), bundle_dir.name),
                    "manifest_ref": str(source_file.relative_to(root)) if source_file.is_relative_to(root) else str(source_file),
                    "evidence_level": self._first_text(manifest.get("evidence_level")),
                },
            }
            candidates.append((self._report_signal_count(payload), findings_path.stat().st_mtime, payload))
        return candidates

    @staticmethod
    def _report_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("real_findings", "findings", "bug_scores"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _report_finding_dedupe_key(item: dict[str, Any]) -> str:
        return _finding_dedupe_key(item)

    def _aggregate_related_report_candidate(
        self,
        anchor_candidate: tuple[int, float, dict[str, Any]] | None,
        related_candidates: list[tuple[int, float, dict[str, Any]]],
    ) -> tuple[int, float, dict[str, Any]] | None:
        if anchor_candidate is None:
            return None
        source_candidates = [item for item in [*related_candidates, anchor_candidate] if isinstance(item[2], dict)]
        if len(source_candidates) < 2:
            return None

        strongest_signal = max(item[0] for item in source_candidates)
        merged_findings: dict[str, dict[str, Any]] = {}
        for _signal, _mtime, payload in sorted(source_candidates, key=lambda item: item[1]):
            for finding in self._report_findings(payload):
                key = self._report_finding_dedupe_key(finding)
                if not key:
                    continue
                merged_findings[key] = dict(finding)

        merged_total = len(merged_findings)
        if merged_total <= strongest_signal:
            return None

        latest_signal, latest_mtime, latest_payload = max(source_candidates, key=lambda item: (item[1], item[0]))
        anchor_payload = anchor_candidate[2]
        merged_payload = dict(anchor_payload)
        merged_list = list(merged_findings.values())
        source_refs: list[str] = []
        for _signal, _mtime, payload in sorted(source_candidates, key=lambda item: item[1]):
            ref = str(payload.get("report_source_path") or "").strip()
            if ref and ref not in source_refs:
                source_refs.append(ref)
        merged_payload.update({
            "project_id": self._first_text(anchor_payload.get("project_id"), latest_payload.get("project_id")),
            "project_name": self._first_text(anchor_payload.get("project_name"), latest_payload.get("project_name")),
            "generated_at_utc": self._first_text(latest_payload.get("generated_at_utc"), anchor_payload.get("generated_at_utc")),
            "total_findings": merged_total,
            "raw_total": merged_total,
            "real_findings": merged_list,
            "bug_scores": merged_list,
            "summary": f"聚合当前报告与 {max(0, len(source_refs) - 1)} 个关联 evidence bundle，回填 {merged_total} 条历史确认结果。",
            "report_source_path": f"aggregated:{source_refs[0]}" if source_refs else "aggregated:evidence_bundle_union",
            "report_source_paths": source_refs,
        })
        if not isinstance(merged_payload.get("campaign"), dict) and isinstance(latest_payload.get("campaign"), dict):
            merged_payload["campaign"] = latest_payload.get("campaign")
        if isinstance(latest_payload.get("evidence_bundle"), dict) or source_refs:
            merged_payload["evidence_bundle"] = {
                **(latest_payload.get("evidence_bundle") if isinstance(latest_payload.get("evidence_bundle"), dict) else {}),
                "aggregate": True,
                "source_count": len(source_refs),
            }
        return (merged_total, max(latest_mtime, anchor_candidate[1]), merged_payload)

    def _load_workspace_report(self, project_id: str, root: Path) -> dict[str, Any]:
        workspace = root / "platform_workspace" / project_id
        if not workspace.exists():
            return {}
        findings: list[dict[str, Any]] = []
        sources: list[str] = []
        defect_dir = workspace / "defect_discovery"
        if defect_dir.exists():
            for path in sorted(defect_dir.glob("*_run.json")):
                payload = self._read_json_dict(path)
                if not payload:
                    continue
                for key in ("findings", "counterexample_findings", "readiness_findings", "structure_findings"):
                    raw_items = payload.get(key)
                    if not isinstance(raw_items, list):
                        continue
                    for index, item in enumerate(raw_items):
                        if isinstance(item, dict):
                            normalized = self._normalize_workspace_finding(item, payload, path, index)
                            if normalized:
                                findings.append(normalized)
                                sources.append(path.name)

        # Real HTTP probe execution can produce direct runtime evidence. Keep only
        # suspicious or failed probes so the frontend does not label normal probes as bugs.
        probe_result = workspace / "real_project" / "probe_execution_result.json"
        payload = self._read_json_dict(probe_result)
        items = payload.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                if not item.get("suspicious") and not item.get("error"):
                    continue
                normalized = self._normalize_probe_execution_finding(item, probe_result, index)
                if normalized:
                    findings.append(normalized)
                    sources.append(probe_result.name)

        findings = self._dedupe_risks(findings)
        if not findings:
            return {}
        p0 = sum(1 for item in findings if self._normalize_severity(item.get("severity")) == "P0")
        p1 = sum(1 for item in findings if self._normalize_severity(item.get("severity")) == "P1")
        score = 97.0 if p0 + p1 else 80.0
        grade = "A+" if score >= 95 else "A" if score >= 85 else "B" if score >= 70 else "C"
        latest = max((path.stat().st_mtime for path in defect_dir.glob("*.json") if path.exists()), default=workspace.stat().st_mtime if workspace.exists() else time.time())
        generated = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(latest))
        return {
            "project_id": project_id,
            "project_name": project_id,
            "generated_at_utc": generated,
            "system_grade": grade,
            "overall_score": score,
            "total_findings": len(findings),
            "raw_total": len(findings),
            "real_findings": findings,
            "bug_scores": findings,
            "summary": f"从 platform_workspace 聚合 {len(findings)} 条真实检测结果 / 覆盖缺口。",
            "report_source_path": f"platform_workspace/{project_id}",
            "workspace_sources": sorted(set(sources)),
        }

    def _normalize_workspace_finding(self, item: dict[str, Any], payload: dict[str, Any], source_path: Path, index: int) -> dict[str, Any]:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        method = self._first_text(item.get("method"), evidence.get("method"), (item.get("probe") or {}).get("method") if isinstance(item.get("probe"), dict) else "").upper()
        path = self._first_text(item.get("path"), item.get("path_template"), evidence.get("path"), evidence.get("path_template"), (item.get("probe") or {}).get("path") if isinstance(item.get("probe"), dict) else "")
        title = self._first_text(item.get("title"), item.get("technical_title"), item.get("detail"), item.get("description"), f"{source_path.stem} finding {index + 1}")
        risk_type = self._first_text(item.get("risk_type"), item.get("category"), item.get("business_assurance_type"), source_path.stem)
        status = self._first_text(item.get("status"), item.get("verdict"), "needs_human_review")
        confidence = self._coerce_float(item.get("confidence_score"), self._coerce_float(item.get("confidence"), self._coerce_float(item.get("score"), 0.75)))
        quality_gap = (
            "coverage_gap" in risk_type
            or "assurance" in risk_type
            or status in {"needs_human_review", "candidate", "pending"}
            or bool(item.get("claim_guard"))
        )
        expected = self._first_text(item.get("expected"), item.get("expected_behavior"), evidence.get("expected"), item.get("test_oracle"))
        actual = self._first_text(item.get("actual"), item.get("actual_behavior"), item.get("bug_signal"), item.get("summary"), item.get("detail"), item.get("description"))
        steps = item.get("reproduction_steps") if isinstance(item.get("reproduction_steps"), list) else []
        if not steps:
            steps = [
                f"定位检测来源：{source_path.name}",
                f"回放业务动作：{method or '业务操作'} {path or title}",
                "对比预期规则、真实返回、日志与 DB 快照，确认是否可复现。",
            ]
        return {
            "risk_id": self._first_text(item.get("risk_id"), item.get("finding_id"), item.get("issue_id"), item.get("bug_id"), f"{source_path.stem}_{index}"),
            "title": title,
            "technical_title": f"{method} {path} · {title}" if method or path else title,
            "severity": self._normalize_severity(item.get("severity")),
            "status": "pending" if quality_gap else ("confirmed" if status in {"confirmed", "validated", "reproduced"} else status),
            "risk_type": risk_type,
            "defect_family": self._first_text(item.get("defect_family"), "scenario_flow" if quality_gap else risk_type),
            "summary": actual or title,
            "business_impact": actual or title,
            "suggested_action": expected or "补齐真实请求、响应、日志与 DB 快照后再进入缺陷交付。",
            "expected": expected,
            "actual": actual,
            "confidence_score": confidence,
            "reproducibility_score": confidence if not quality_gap else min(confidence, 0.45),
            "affected_business_flow": {"name": self._first_text(item.get("flow"), item.get("contract_id"), risk_type)},
            "affected_modules": [self._extract_module(title, actual)],
            "affected_roles": [],
            "first_seen_at": self._first_text(item.get("first_seen_at"), payload.get("generated_at_utc"), self._mtime_utc(source_path)),
            "last_verified_at": self._first_text(item.get("last_verified_at"), payload.get("generated_at_utc"), self._mtime_utc(source_path)),
            "reproduction_steps": steps,
            "quality_assurance_gap": quality_gap,
            "evidence_hint": f"来源文件：{source_path.name}；执行策略：{self._first_text(item.get('execution_policy'), evidence.get('execution_policy'), 'unknown')}",
            "evidence": {**evidence, "method": method, "path": path, "source_file": source_path.name, "expected": expected, "actual": actual},
            "_api_path": path,
            "_api_method": method,
        }

    def _normalize_probe_execution_finding(self, item: dict[str, Any], source_path: Path, index: int) -> dict[str, Any]:
        probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
        method = self._first_text(probe.get("method"), item.get("method")).upper()
        path = self._first_text(probe.get("path"), item.get("path"))
        status_code = item.get("response_status")
        error = self._first_text(item.get("error"), item.get("reason"))
        title = self._first_text(probe.get("title"), f"运行时探针异常：{method} {path}")
        return {
            "risk_id": self._first_text(item.get("probe_id"), probe.get("probe_id"), f"probe_exec_{index}"),
            "title": title,
            "technical_title": f"{method} {path} · {title}",
            "severity": self._normalize_severity(probe.get("severity") or "P1"),
            "status": "confirmed" if item.get("suspicious") else "pending",
            "risk_type": self._first_text(probe.get("risk_type"), "runtime_probe"),
            "defect_family": self._first_text(probe.get("defect_family"), "runtime_probe"),
            "summary": error or title,
            "business_impact": error or title,
            "suggested_action": self._first_text(probe.get("expected"), "对照响应码、日志与 DB 结果确认是否为可复现缺陷。"),
            "expected": self._first_text(probe.get("expected")),
            "actual": error or f"response_status={status_code}",
            "confidence_score": self._coerce_float(item.get("confidence"), 0.70),
            "reproducibility_score": self._coerce_float(item.get("confidence"), 0.70),
            "affected_business_flow": {"name": self._first_text(probe.get("risk_type"), "runtime_probe")},
            "affected_modules": [self._extract_module(title, error)],
            "affected_roles": [],
            "first_seen_at": self._mtime_utc(source_path),
            "last_verified_at": self._mtime_utc(source_path),
            "reproduction_steps": [f"执行 {method} {path}", "记录响应状态码、响应体、请求时间与 traceId", "核对业务数据是否出现不符合预期的副作用"],
            "quality_assurance_gap": not bool(item.get("suspicious")),
            "evidence_hint": f"来源文件：{source_path.name}；response_status={status_code}",
            "evidence": {"method": method, "path": path, "status_code": status_code, "error": error, "source_file": source_path.name},
            "_api_path": path,
            "_api_method": method,
        }

    def _v12_findings(self, report: dict[str, Any], enterprise_docs: list[dict] | None = None) -> list[dict[str, Any]]:
        raw_items = report.get("real_findings") or report.get("findings") or report.get("bug_scores") or []
        if not isinstance(raw_items, list):
            return []
        docs = enterprise_docs or []
        findings: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            title = self._first_text(item.get("title"), item.get("bug_title"), item.get("technical_title"), item.get("description"), f"V12 finding {index + 1}")
            description = self._first_text(item.get("description"), item.get("summary"), item.get("actual"), item.get("actual_behavior"), title)
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
            raw_evidence = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
            request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
            reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
            har_evidence = reproduction.get("har_evidence") if isinstance(reproduction.get("har_evidence"), dict) else {}
            expected = self._first_text(item.get("expected_behavior"), item.get("expected"), evidence.get("expected"), evidence.get("assertion"), item.get("suggested_action"))
            actual = self._first_text(item.get("actual_behavior"), item.get("actual"), evidence.get("actual"), item.get("business_impact"), description)
            steps = item.get("reproduction_steps") if isinstance(item.get("reproduction_steps"), list) else []
            if not steps:
                steps = evidence.get("reproduction_steps") if isinstance(evidence.get("reproduction_steps"), list) else []
            if not steps:
                steps = reproduction.get("steps") if isinstance(reproduction.get("steps"), list) else []
            claim_method, claim_path = _claim_request_identity(
                title,
                description,
                expected,
                actual,
                self._first_text(evidence.get("assertion")),
                self._first_text(item.get("suggested_action")),
                [str(step) for step in steps if str(step or "").strip()],
            )

            import re
            text_for_route = " ".join([title, description, str(evidence), str(probe)])
            # 提取 API 路径——用 ASCII-only 正则避免中文/日文等被误匹配为路径
            # \w 在 Python re 默认包含 Unicode，会把路径后的中文叙述一并匹配
            path_match = re.search(r'(/api/[a-zA-Z0-9_/{}.-]+|/[a-zA-Z0-9{}.-]+/[a-zA-Z0-9_/{}.-]+)', text_for_route)
            api_path = self._first_text(
                item.get("_api_path"),
                item.get("path"),
                item.get("path_template"),
                claim_path,
                evidence.get("path"),
                evidence.get("path_template"),
                probe.get("path"),
                request_raw.get("path"),
                har_evidence.get("path"),
                reproduction.get("path"),
                path_match.group(1) if path_match else "",
            )
            # 验证提取的路径是合法 API 端点（防止描述文本被误判为路径）
            if api_path:
                api_path = _validate_api_path(api_path)
            if api_path and "{" in api_path:
                for candidate_path in (request_raw.get("path"), har_evidence.get("path"), reproduction.get("path")):
                    concrete_path = _validate_api_path(str(candidate_path or "").strip())
                    if concrete_path and _materialized_path_matches(api_path, concrete_path):
                        api_path = concrete_path
                        break
            method_match = re.search(r'\b(POST|GET|PUT|DELETE|PATCH)\b', text_for_route, re.IGNORECASE)
            api_method = self._first_text(
                item.get("_api_method"),
                item.get("method"),
                claim_method,
                evidence.get("method"),
                probe.get("method"),
                request_raw.get("method"),
                har_evidence.get("method"),
                reproduction.get("method"),
                method_match.group(1) if method_match else "",
            ).upper()

            matched = item.get("_doc_refs") if isinstance(item.get("_doc_refs"), list) else (self._match_docs_for_finding(title, docs) if docs else [])
            severity = self._normalize_severity(item.get("severity"))
            risk_type = self._first_text(item.get("category"), item.get("risk_type"), item.get("business_assurance_type"), "业务规则验证")
            confirmation_status = self._first_text(item.get("confirmation_status"), item.get("status"), item.get("verdict"), item.get("bug_confirmation"), "candidate").lower()
            gate_passed = bool(item.get("gate_passed"))
            bug_status = self._first_text(item.get("bug_status"), "reproduced" if gate_passed else "risk_clue")
            evidence_status = item.get("evidence_status") if isinstance(item.get("evidence_status"), dict) else {}
            evidence_quality = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
            semantic_verdict = self._first_text(item.get("semantic_verdict"), evidence_status.get("semantic_verdict"))
            business_evidence_status = self._first_text(item.get("business_evidence_status"), evidence_status.get("business_evidence_status")).upper()
            final_review_status = self._first_text(item.get("final_review_status"), evidence_status.get("final_review_status"))
            missing_requirements = item.get("missing_requirements") if isinstance(item.get("missing_requirements"), list) else (
                evidence_status.get("missing_requirements") if isinstance(evidence_status.get("missing_requirements"), list) else []
            )
            status = "confirmed" if confirmation_status in {"confirmed", "validated", "validated_candidate"} else ("pending" if confirmation_status in {"candidate", "pending"} else confirmation_status)
            quality_gap = (
                bool(item.get("quality_assurance_gap"))
                or "coverage_gap" in risk_type
                or confirmation_status in {"candidate", "pending", "needs_human_review"}
                or not gate_passed
                or bug_status != "reproduced"
                or business_evidence_status not in {"VALIDATED", "READY"}
            )
            timestamp = self._first_text(
                item.get("timestamp"),
                evidence.get("timestamp"),
                raw_evidence.get("timestamp"),
                item.get("last_verified_at"),
                report.get("generated_at_utc"),
            )
            reproducibility = item.get("reproducibility") if isinstance(item.get("reproducibility"), dict) else {}
            if not reproducibility:
                har_evidence = reproduction.get("har_evidence") if isinstance(reproduction.get("har_evidence"), dict) else {}
                reproducibility = {
                    "reproducible": bool(gate_passed and (steps or raw_evidence.get("has_real_evidence") or har_evidence)),
                    "reproduction_confidence": self._coerce_float(
                        item.get("reproducibility_score"),
                        0.95 if gate_passed else (0.7 if confirmation_status in {"confirmed", "validated", "validated_candidate"} else 0.45),
                    ),
                }
            failed_assertions = item.get("failed_assertions") if isinstance(item.get("failed_assertions"), list) else []
            if not failed_assertions and expected and actual and expected != actual:
                failed_assertions = [{
                    "type": "semantic_violation",
                    "rule": self._first_text(evidence.get("assertion"), item.get("description"), title),
                    "expected": expected,
                    "actual": actual,
                }]
            if har_evidence:
                har_evidence = {
                    **har_evidence,
                    "method": self._first_text(har_evidence.get("method"), api_method, raw_evidence.get("request_raw", {}).get("method")),
                    "path": self._first_text(har_evidence.get("path"), api_path, raw_evidence.get("request_raw", {}).get("path")),
                    "actor": self._first_text(har_evidence.get("actor"), raw_evidence.get("request_raw", {}).get("actor"), evidence.get("actor")),
                    "duration_ms": har_evidence.get("duration_ms") if har_evidence.get("duration_ms") is not None else (
                        raw_evidence.get("response_raw", {}).get("duration_ms") if isinstance(raw_evidence.get("response_raw"), dict) else 0
                    ),
                }
            request_path = _validate_api_path(self._first_text(request_raw.get("path")))
            request_method = self._first_text(request_raw.get("method")).upper()
            if (
                request_path
                and api_path
                and isinstance(raw_evidence.get("response_raw"), dict)
                and raw_evidence.get("response_raw")
                and (
                    normalize_path_placeholders(request_path) != normalize_path_placeholders(api_path)
                    or (request_method and api_method and request_method != api_method)
                )
            ):
                raw_evidence = {
                    **raw_evidence,
                    "response_raw": {},
                }
            # 不伪造复现步骤——如果没有真实步骤，留空列表，由 formatter 生成标记为 [指引] 的建议

            finding_evidence = dict(evidence)
            finding_evidence.update({
                "path": api_path,
                "method": api_method,
                "summary": self._first_text(evidence.get("summary"), item.get("summary"), description),
                "expected": expected,
                "actual": actual,
            })
            if item.get("evidence_hint") and not finding_evidence.get("source_file"):
                finding_evidence["source_file"] = str(item.get("evidence_hint"))

            findings.append({
                "candidate_id": self._first_text(item.get("candidate_id")),
                "slice_id": self._first_text(item.get("slice_id")),
                "obligation_id": self._first_text(item.get("obligation_id")),
                "experiment_id": self._first_text(item.get("experiment_id")),
                "execution_id": self._first_text(item.get("execution_id")),
                "evidence_id": self._first_text(item.get("evidence_id")),
                "finding_id": self._first_text(item.get("finding_id")),
                "risk_id": self._first_text(item.get("risk_id"), item.get("bug_id"), item.get("evidence_id"), item.get("finding_id"), f"v12_{index}"),
                "id": self._first_text(item.get("risk_id"), item.get("bug_id"), item.get("evidence_id"), item.get("finding_id"), f"v12_{index}"),
                "title": title,
                "technical_title": f"{api_method} {api_path} · {title}" if api_method or api_path else title,
                "severity": severity,
                "status": "pending" if quality_gap and status != "confirmed" else status,
                "risk_type": risk_type,
                "defect_family": self._first_text(item.get("defect_family"), "scenario_flow" if quality_gap else risk_type),
                "summary": actual or title,
                "business_impact": self._first_text(item.get("business_impact"), actual, title),
                "suggested_action": expected or "补齐真实复现证据后进入缺陷闭环。",
                "expected": expected,
                "actual": actual,
                "confidence_score": self._coerce_float(item.get("confidence_score"), self._coerce_float(item.get("score"), self._coerce_float(item.get("confidence"), 0.75))),
                "reproducibility_score": self._coerce_float(item.get("reproducibility_score"), 0.85 if status in {"confirmed", "validated", "reproduced"} else 0.45 if quality_gap else 0.70),
                "affected_business_flow": {"name": self._first_text(item.get("flow"), item.get("category"), risk_type, "system")},
                "affected_modules": [self._first_text(item.get("module"), item.get("category"), (api_path.split("/")[1] if api_path.startswith("/") and len(api_path.split("/")) > 1 else ""), self._extract_module(title, description))],
                "affected_roles": item.get("affected_roles") if isinstance(item.get("affected_roles"), list) else [],
                "first_seen_at": self._first_text(item.get("first_seen_at"), report.get("generated_at_utc")),
                "last_verified_at": self._first_text(item.get("last_verified_at"), report.get("generated_at_utc")),
                "reproduction_steps": steps,
                "quality_assurance_gap": quality_gap,
                "evidence_hint": self._first_text(item.get("evidence_hint"), finding_evidence.get("source_file")),
                "timestamp": timestamp,
                "bug_status": bug_status,
                "gate_passed": gate_passed,
                "execution_status": self._first_text(item.get("execution_status"), "executed" if gate_passed else "planned"),
                "confirmation_status": confirmation_status,
                "semantic_verdict": semantic_verdict,
                "business_evidence_status": business_evidence_status,
                "final_review_status": final_review_status,
                "missing_requirements": missing_requirements,
                "evidence_quality": evidence_quality,
                "evidence_status": evidence_status,
                "raw_evidence": raw_evidence,
                "reproduction": reproduction,
                "reproducibility": reproducibility,
                "failed_assertions": failed_assertions,
                "har_evidence": har_evidence,
                "customer_delivery_status": self._first_text(item.get("customer_delivery_status"), "defect" if gate_passed and bug_status == "reproduced" else "clue"),
                "_doc_refs": matched,
                "doc_refs": matched,
                "evidence": finding_evidence,
                "_api_path": api_path,
                "_api_method": api_method,
            })
        return findings

    def _scan_report_load_diagnostics(self, project_id: str, root: Path) -> dict[str, Any] | None:
        """Honest visibility when the strongest scan-report candidate is unreadable.

        Called by the command-center builder only when no report candidate
        yielded a payload. A torn sharded store is the one failure mode that
        silently zeroes every candidate; it must surface as a named state in
        the payload (and the logs), never as a fake clean empty view.
        """
        from .scan_result_store import STORE_COMPLETE_MARKER, is_sharded_scan_result

        project = _safe_project_id(project_id)
        for base in ("platform_outputs", "platform_workspace"):
            path = root / base / project / "scan_result.json"
            try:
                if not path.exists() or not is_sharded_scan_result(path):
                    continue
            except OSError:
                continue
            marker_path = path.parent / "scan_result.parts" / STORE_COMPLETE_MARKER
            if not marker_path.is_file():
                return {
                    "status": "SCAN_RESULT_STORE_TORN",
                    "message": (
                        "扫描结果分片存储不完整（缺少完成标记），本轮结果无法读取。"
                        "请对该项目重跑一次扫描以重建一致的存储。"
                    ),
                    "report_source_path": f"{base}/{project}/scan_result.json",
                }
        return None

    def _load_enterprise_docs(self, project_id: str, root: Path) -> list[dict]:
        """Load enterprise knowledge documents for evidence association.

        来源优先级（文件系统优先）：
        1. JSON 文件 — 上传走 ingest_enterprise_knowledge_documents，写入
           source_registry.json + enterprise_knowledge_center/sources/，
           不写 knowledge_docs 表，因此文件系统是文档的实际存储位置。
        2. SQLite 数据库 knowledge_docs 表 — 补充源，用请求上下文中的真实
           tenant_id 查询（不再硬编码 "default"，避免租户隔离失效）。

        所有路径均按 project_id 严格隔离，绝不跨项目/跨客户读取文档。
        """
        rows: list[dict[str, Any]] = []

        # ── 1. 从 JSON 文件加载（上传文档的实际存储位置，优先）──
        candidates = [
            root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "source_registry.json",
            root / "platform_workspace" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
            root / "platform_outputs" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
        ]
        for doc_path in candidates:
            if not doc_path.exists():
                continue
            asset = self._read_knowledge_asset_scoped(doc_path)
            if asset is None:
                continue
            sources = asset.get("source_inventory") or asset.get("sources") or asset.get("items") or []
            if isinstance(sources, dict):
                sources = list(sources.values())
            actor_loader = getattr(self, "_principal", None)
            actor = actor_loader() if callable(actor_loader) else None
            if isinstance(sources, list) and actor is not None:
                from .connector_acl_authority import filter_connector_sources_for_actor

                sources, _acl_projection = filter_connector_sources_for_actor(
                    project_id,
                    [row for row in sources if isinstance(row, dict)],
                    actor={**actor, "project_id": project_id},
                    root=root,
                )
            for s in sources if isinstance(sources, list) else []:
                if not isinstance(s, dict):
                    continue
                source_id = self._first_text(s.get("source_id"), s.get("id"), s.get("stored_path"), s.get("filename"))
                label = self._first_text(s.get("display_name"), s.get("filename"), s.get("original_name"), s.get("name"), source_id)
                if source_id or label:
                    rows.append({
                        "source_id": source_id or label,
                        "display_name": label,
                        "type": self._first_text(s.get("type"), s.get("source_type"), "文档"),
                        "excerpt": self._first_text(s.get("summary"), s.get("excerpt"), s.get("content"))[:260],
                    })

        # ── 2. 从数据库加载（补充源，用真实 tenant_id 保证租户隔离）──
        try:
            from . import db_persistence as dbp
            tenant_id = self._request_tenant()
            db_docs = dbp.get_knowledge_docs(root, tenant_id, project_id)
            for d in db_docs:
                content = ""
                try:
                    content = dbp.get_knowledge_doc_content(root, d.get("source_id", ""))
                except Exception:
                    pass
                rows.append({
                    "source_id": d.get("source_id", ""),
                    "display_name": d.get("display_name", ""),
                    "type": d.get("type", "文档"),
                    "excerpt": content[:260] if content else "",
                })
        except Exception:
            pass

        return self._dedupe_docs(rows)

    def _load_knowledge_summary(self, project_id: str, root: Path) -> dict[str, Any]:
        """Load a compact business-facing summary for the dashboard.

        Morning backend runs often write into platform_workspace before a
        formal report is materialized under platform_outputs.  The UI must
        read both locations, otherwise dashboard numbers look unrelated to the
        backend result.
        """
        candidates = [
            root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
            root / "platform_outputs" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
            root / "platform_workspace" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "source_registry.json",
        ]
        for doc_path in candidates:
            if not doc_path.exists():
                continue
            asset = self._read_knowledge_asset_scoped(doc_path)
            if asset is None:
                continue
            summary = asset.get("summary") if isinstance(asset, dict) else None
            if isinstance(summary, dict):
                visible_source_count = int(
                    summary.get("active_source_count")
                    or summary.get("source_count")
                    or 0
                )
                actor_loader = getattr(self, "_principal", None)
                actor = actor_loader() if callable(actor_loader) else None
                if actor is not None and isinstance(asset, dict):
                    from .connector_acl_authority import filter_connector_sources_for_actor

                    # 只对来源清单做可见性投影（与 docs 加载器同一来源级权威）。
                    # 整资产 ACL 投影会深拷贝并递归走 100MB+ 树（实测 143s），
                    # 而这里需要的只是可见来源计数。
                    raw_inventory = (
                        asset.get("source_inventory")
                        or asset.get("sources")
                        or []
                    )
                    if isinstance(raw_inventory, dict):
                        raw_inventory = list(raw_inventory.values())
                    visible_inventory, _acl_summary = filter_connector_sources_for_actor(
                        project_id,
                        [row for row in raw_inventory if isinstance(row, dict)],
                        actor={**actor, "project_id": project_id},
                        root=root,
                    )
                    visible_source_count = len(visible_inventory)
                return {
                    "active_source_count": visible_source_count,
                    "rule_count": int(summary.get("rule_count") or 0),
                    "risk_domain_count": int(summary.get("risk_domain_count") or 0),
                    "oracle_count": int(summary.get("oracle_count") or 0),
                    "business_object_count": int(summary.get("business_object_count") or 0),
                    "state_machine_count": int(summary.get("state_machine_count") or 0),
                    "knowledge_ready": bool(summary.get("knowledge_ready") or summary.get("ready")),
                }
            # Fallback for registry-style files: surface source count instead of zero.
            sources = []
            if isinstance(asset, dict):
                raw_sources = asset.get("source_inventory") or asset.get("sources") or asset.get("items") or []
                if isinstance(raw_sources, dict):
                    sources = list(raw_sources.values())
                elif isinstance(raw_sources, list):
                    sources = raw_sources
            if sources:
                return {
                    "active_source_count": len(sources),
                    "rule_count": 0,
                    "risk_domain_count": 0,
                    "oracle_count": 0,
                    "business_object_count": 0,
                    "state_machine_count": 0,
                    "knowledge_ready": True,
                }
        return {}

    @staticmethod
    def _load_db_findings(root: Path, project_id: str, report: dict[str, Any] | None = None) -> list[dict]:
        # 轻报告时代：db_verification 若已在已加载报告中，直接使用；
        # 仅当报告缺失该段且 scan_result 是旧单文件（非分片）时才回退读盘，
        # 绝不为遗留段落隐式支付分片全量水合。
        data: dict[str, Any] | None = None
        if isinstance(report, dict) and "db_verification" in report:
            data = report
        else:
            scan_file = root / "platform_outputs" / project_id / "scan_result.json"
            if not scan_file.exists():
                return []
            from .scan_result_store import is_sharded_scan_result

            if is_sharded_scan_result(scan_file):
                return []
            data = ReportLoadingMixin._read_scan_report_payload(scan_file)
        if not data:
            return []
        db_verification = data.get("db_verification")
        if db_verification is None:
            return []
        if not isinstance(db_verification, dict):
            raise ValueError(f"db_verification must be an object: {project_id}")
        db_findings = db_verification.get("findings", [])
        if not isinstance(db_findings, list):
            raise ValueError(f"db_verification.findings must be a list: {project_id}")
        for index, finding in enumerate(db_findings):
            if not isinstance(finding, dict):
                raise ValueError(f"db_verification.findings[{index}] must be an object: {project_id}")
            finding.setdefault("risk_type", "db_snapshot")
            finding.setdefault("defect_family", "data_integrity")
            ev_row = finding.get("evidence", {}).get("db_row") if isinstance(finding.get("evidence"), dict) else None
            if isinstance(ev_row, dict) and ev_row:
                for value in ev_row.values():
                    if value is not None and str(value).strip():
                        finding.setdefault("source_value", str(value))
                        break
            description = str(finding.get("description") or "").strip()
            if description:
                finding.setdefault("actual_behavior", description)
        return db_findings

    @staticmethod
    def _load_perf_regressions(root: Path, project_id: str) -> list[dict]:
        perf_file = root / "platform_outputs" / project_id / "performance" / "baseline.json"
        if not perf_file.exists():
            return []
        history = _read_json_artifact(perf_file)
        if not isinstance(history, list):
            raise ValueError(f"performance baseline must be a list: {perf_file}")
        if len(history) < 2:
            return []
        latest = history[-1]
        if not isinstance(latest, dict):
            raise ValueError(f"latest performance baseline must be an object: {perf_file}")
        regressions = latest.get("regressions", [])
        if not isinstance(regressions, list):
            raise ValueError(f"performance regressions must be a list: {perf_file}")
        findings: list[dict[str, Any]] = []
        for index, regression in enumerate(regressions):
            if not isinstance(regression, dict):
                raise ValueError(f"performance regressions[{index}] must be an object: {perf_file}")
            finding = {
                "risk_id": "perf_reg_" + str(regression.get("metric") or "unknown"),
                "title": regression.get("detail", ""),
                "severity": regression.get("severity", "P2"),
                "risk_type": "performance_regression",
                "defect_family": "performance",
                "source": "performance_baseline",
            }
            if regression.get("confidence") is not None:
                finding["confidence_score"] = float(regression["confidence"])
            findings.append(finding)
        return findings

    @staticmethod
    def _load_spectrum_findings(root: Path, project_id: str) -> list[dict]:
        spectrum = root / "platform_outputs" / project_id / "spectrum" / "spectrum_result.json"
        if not spectrum.exists():
            return []
        data = _read_json_object(spectrum)
        capabilities = data.get("capabilities", [])
        if not isinstance(capabilities, list):
            raise ValueError(f"spectrum capabilities must be a list: {spectrum}")
        findings: list[dict[str, Any]] = []
        for cap_index, capability in enumerate(capabilities):
            if not isinstance(capability, dict):
                raise ValueError(f"spectrum capabilities[{cap_index}] must be an object: {spectrum}")
            if capability.get("id") == "test_gen":
                continue
            capability_findings = capability.get("findings", [])
            if not isinstance(capability_findings, list):
                raise ValueError(f"spectrum capability findings must be a list: {spectrum}")
            for finding_index, source_finding in enumerate(capability_findings):
                if not isinstance(source_finding, dict):
                    raise ValueError(f"spectrum finding {cap_index}:{finding_index} must be an object: {spectrum}")
                if not source_finding.get("bug_id"):
                    continue
                finding = {
                    "risk_id": source_finding.get("bug_id", ""),
                    "title": f"[全频谱] {source_finding.get('title', '')}",
                    "severity": source_finding.get("severity", "P2"),
                    "risk_type": f"spectrum_{capability.get('id', 'unknown')}",
                    "defect_family": "spectrum",
                    "summary": str(source_finding.get("description", source_finding.get("title", ""))),
                    "source": "full_spectrum",
                }
                if source_finding.get("confidence") is not None:
                    finding["confidence_score"] = float(source_finding["confidence"])
                findings.append(finding)
        return findings

    @staticmethod
    def _load_spectrum_status_payload(root: Path, project_id: str) -> dict:
        result_file = root / "platform_outputs" / project_id / "spectrum" / "spectrum_result.json"
        ts_file = root / "platform_outputs" / project_id / "spectrum" / "spectrum_timestamp.txt"
        if not result_file.exists():
            return {"status": "not_run", "message": "尚未运行全频谱检测", "summary": {"total_findings": 0}, "capabilities": []}
        result = _read_json_object(result_file)
        capabilities = result.get("capabilities")
        summary = result.get("summary")
        if capabilities is not None and not isinstance(capabilities, list):
            raise ValueError(f"spectrum capabilities must be a list: {result_file}")
        if summary is not None and not isinstance(summary, dict):
            raise ValueError(f"spectrum summary must be an object: {result_file}")
        return {
            "status": "completed",
            "last_run": ts_file.read_text(encoding="utf-8").strip() if ts_file.exists() else "",
            "summary": summary or {"total_findings": 0},
            "capabilities": capabilities or [],
        }

    @staticmethod
    def _load_multi_layer_findings(root: Path, project_id: str, report: dict[str, Any] | None = None) -> list[dict]:
        # 轻报告时代：layers 若已在已加载报告中直接使用；分片 store 不为
        # 遗留段落做全量水合（与 _load_db_findings 同一守卫）。
        data: dict[str, Any] | None = None
        if isinstance(report, dict) and "layers" in report:
            data = report
        else:
            scan_file = root / "platform_outputs" / project_id / "scan_result.json"
            if not scan_file.exists():
                return []
            from .scan_result_store import is_sharded_scan_result

            if is_sharded_scan_result(scan_file):
                return []
            data = ReportLoadingMixin._read_scan_report_payload(scan_file)
        if not data:
            return []
        layers = data.get("layers", {})
        if not isinstance(layers, dict):
            raise ValueError(f"scan layers must be an object: {project_id}")
        findings: list[dict[str, Any]] = []
        for layer_name, layer_data in layers.items():
            if not isinstance(layer_data, dict):
                raise ValueError(f"scan layer {layer_name} must be an object: {scan_file}")
            details = layer_data.get("findings_details", [])
            if not isinstance(details, list):
                raise ValueError(f"scan layer {layer_name}.findings_details must be a list: {scan_file}")
            for index, source_finding in enumerate(details):
                if not isinstance(source_finding, dict):
                    raise ValueError(f"scan layer {layer_name} finding {index} must be an object: {scan_file}")
                finding = {
                    "risk_id": f"layer_{layer_name}_{index}",
                    "title": f"[{str(layer_name).upper()}] {source_finding.get('title', '')}",
                    "severity": source_finding.get("severity", "P2"),
                    "risk_type": f"multi_layer_{layer_name}",
                    "defect_family": "multi_layer",
                    "summary": source_finding.get("description", ""),
                    "source": f"multi_layer_{layer_name}",
                }
                if source_finding.get("confidence") is not None:
                    finding["confidence_score"] = float(source_finding["confidence"])
                findings.append(finding)
        return findings

    def _auto_discovery_payload(self, project_id: str, root: Path, report: dict[str, Any]) -> dict:
        """Auto-generate continuous discovery payload — tracks convergence across rounds."""
        import time
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        real_findings = report.get("real_findings") or report.get("bug_scores") or []
        if isinstance(real_findings, list):
            real_findings = [item for item in real_findings if isinstance(item, dict)]
        else:
            real_findings = []
        total_findings = len(real_findings)
        raw_total = int(report.get("raw_total") or report.get("total_findings") or total_findings)

        # Track convergence: compare with previous scan findings
        prev_titles = self._previous_finding_titles(project_id, root)
        # Build current titles from report — match the DB storage format
        current_titles = set()
        for f in real_findings:
            t = str(f.get("title") or f.get("description", ""))[:120]
            if t: current_titles.add(t.lower())  # Normalize for matching
        new_count = len(current_titles - prev_titles)
        confirmed_count = len(current_titles & prev_titles)
        resolved_count = max(0, raw_total - total_findings)

        verified = sum(1 for f in real_findings if max(float(f.get("confidence_score", 0)), float(f.get("score", 0)), float(f.get("confidence", 0))) > 0.5)
        blocked = sum(1 for f in real_findings if str(f.get("severity", "")).upper() in ("P0", "CRITICAL"))

        scan_counter = self._scan_counter(project_id, root)
        current_run = scan_counter.get("count", total_findings // 3 or 1)
        total_discovered = len(prev_titles | current_titles)  # All unique findings ever
        pending = max(0, raw_total - total_discovered)

        return {
            "project_id": project_id,
            "generated_at_utc": now,
            "continuous_discovery_campaign": {
                "summary": {
                    "campaign_state": "in_progress",
                    "run_count": current_run,
                    "coverage_ledger_entry_count": raw_total,
                    "validated_frontier_count": confirmed_count + new_count,
                    "remaining_actionable_frontier_count": pending,
                    "blocked_frontier_count": blocked,
                    "revalidation_queue_size": max(0, total_findings - verified),
                    "can_stop_now": pending == 0 and new_count == 0,
                    "frontier_burn_down_count": confirmed_count,
                    "frontier_burn_down_rate": round(confirmed_count / max(1, total_discovered), 2),
                    "current_run_validated_yield": new_count,
                    "marginal_validated_yield_threshold": max(1, raw_total // 5),
                    "new_this_round": new_count,
                    "confirmed": confirmed_count,
                    "total_discovered": total_discovered,
                },
                "coverage_ledger": {
                    "entries": [
                        {"last_status": "validated" if str(f.get("title",""))[:80] in prev_titles else "new",
                         "frontier": {"title": str(f.get("title", f.get("description", "")))[:60]},
                         "last_blocker_reason": ""}
                        for f in real_findings[:10]
                    ],
                    "status_counts": {"validated": confirmed_count, "new": new_count, "blocked": blocked, "pending": pending},
                },
                "recommended_frontier": [
                    {"title": str(f.get("title", f.get("description", "")))[:60],
                     "value_score": int(float(f.get("confidence_score", f.get("score", 1))) * 10)}
                    for f in real_findings[:5]
                ],
            },
            "metrics": {
                "continuous_discovery_coverage": round(total_discovered / max(1, raw_total) * 100, 1),
                "continuous_discovery_total": raw_total,
                "continuous_discovery_verified": total_discovered,
                "continuous_discovery_new": new_count,
                "continuous_discovery_confirmed": confirmed_count,
                "continuous_discovery_blocked": blocked,
                "doc_completeness": self._doc_completeness_score(project_id, root),
            },
        }

    def _doc_completeness_score(self, project_id: str, root: Path) -> int:
        """Score 0-100 based on uploaded enterprise documents knowledge richness."""
        try:
            candidates = [
                root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
                root / "platform_outputs" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
                root / "platform_workspace" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json",
                root / "platform_workspace" / project_id / "defect_discovery" / "enterprise_business_knowledge_asset.json",
            ]
            for kc_path in candidates:
                if not kc_path.exists():
                    continue
                kc = self._read_knowledge_asset_scoped(kc_path)
                if kc is None:
                    continue
                raw_sources = kc.get("source_inventory") or kc.get("sources") or kc.get("items") or []
                sources = len(raw_sources) if isinstance(raw_sources, list) else len(raw_sources.keys()) if isinstance(raw_sources, dict) else 0
                rules = len(kc.get("rule_library") or [])
                states = len(kc.get("state_machines") or [])
                roles = len(kc.get("roles") or [])
                interfaces = len(kc.get("interfaces") or [])
                score = min(100, sources * 15 + rules * 8 + states * 5 + roles * 3 + interfaces * 3)
                return max(0, score)
            return 0
        except Exception as e:
            print(f"ERROR in _doc_completeness_score: {e}")
            return 0
