# -*- coding: utf-8 -*-
"""Unified defect view — 产品发现与研发刻度（GT）的统一缺陷清单。

产品交付物是一套缺陷清单（交付门验证的 canonical defects），研发刻度
（benchmark GT）只是可选标注：有 GT 对应的条目标注 gt_id + 匹配分，无 GT
对应的条目是同样有效的真实缺陷（运行时发现，不依赖基准）。与来源标注
哲学一致：有则标注、绝不编造。

用法（评估侧后验）:
    view = build_unified_defect_view(
        canonical_registry=reg,       # 产品输出 canonical_defect_registry
        matched_bugs=matched_bugs,    # compute_benchmark 的 matched_bugs
    )
    # view["defects"]: 统一清单（每条含 canonical_defect_id / evidence /
    #   reproduction / source_refs + 可选 benchmark_gt_id / match_score）
"""
from __future__ import annotations

import json
from typing import Any


def build_unified_defect_view(
    *,
    canonical_registry: dict[str, Any],
    matched_bugs: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge the product's canonical defect list with benchmark GT matches.

    Every canonical defect gets at most one optional ``benchmark_gt_id`` /
    ``match_score`` (from the evaluator's matched_bugs). The evaluator's
    matched entries are keyed by ``finding_title``; the canonical registry
    keys by ``representative_finding_id``, so a finding id → title map is
    built from the run's findings when supplied. Defects without a match
    carry no benchmark fields — they are runtime-discovered real defects,
    not a separate class.
    """
    gt_by_id: dict[str, dict[str, Any]] = {}
    for bug in ground_truth or []:
        bug_id = str(bug.get("bug_id") or "")
        if bug_id:
            gt_by_id[bug_id] = dict(bug)

    title_by_finding_id: dict[str, str] = {}
    for finding in findings or []:
        fid = str(finding.get("finding_id") or "")
        title = str(finding.get("title") or "")
        if fid and title:
            title_by_finding_id[fid] = title

    match_by_title: dict[str, dict[str, Any]] = {}
    for match in matched_bugs or []:
        title = str(
            match.get("finding_title") or match.get("finding_id") or ""
        )
        if title:
            match_by_title[title] = dict(match)

    defects: list[dict[str, Any]] = []
    for defect in (canonical_registry.get("canonical_defects") or []):
        row = dict(defect)
        finding_ids = row.get("occurrence_finding_ids") or []
        rep_id = str(row.get("representative_finding_id") or "")
        match = None
        for fid in [rep_id] + list(finding_ids):
            if not fid:
                continue
            candidate = match_by_title.get(title_by_finding_id.get(fid, ""))
            if candidate:
                match = candidate
                break
        if match:
            gt_id = str(match.get("gt_bug_id") or match.get("bug_id") or "")
            if gt_id:
                row["benchmark_gt_id"] = gt_id
                row["match_score"] = match.get("match_score")
                gt = gt_by_id.get(gt_id)
                if gt:
                    row["benchmark_gt_title"] = gt.get("title")
        defects.append(row)

    matched_count = sum(1 for d in defects if d.get("benchmark_gt_id"))
    return {
        "schema_version": "qualibug.unified-defect-view.v1",
        "total_defects": len(defects),
        "benchmark_marked": matched_count,
        "runtime_discovered_unmarked": len(defects) - matched_count,
        "defects": defects,
    }


def write_unified_defect_view(
    *,
    path: Any,
    canonical_registry: dict[str, Any],
    matched_bugs: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    view = build_unified_defect_view(
        canonical_registry=canonical_registry,
        matched_bugs=matched_bugs,
        ground_truth=ground_truth,
    )
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(view, handle, ensure_ascii=False, indent=2, default=str)
    return view
