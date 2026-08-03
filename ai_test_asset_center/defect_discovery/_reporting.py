"""High-value profiling, enterprise reporting, triage, capability assessment."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_test_asset_center.adaptive_probe_optimizer import build_learned_probe_policy

from ._common import *  # noqa: F401,F403
from ._model import *  # noqa: F401,F403
from ._scenarios import *  # noqa: F401,F403
from ._probes import *  # noqa: F401,F403
from ._runner import *  # noqa: F401,F403
from ._model import DiscoveryConfig  # noqa: F401
from ._probes import ORACLE_REQUIRED_RISKS, step  # noqa: F401


def high_value_profile(item: dict) -> dict:
    p = item["probe"]
    risk = str(p.get("risk_type") or "")
    source = str(p.get("source") or "")
    context = item.get("business_context") or p.get("business_context") or {}
    has_steps = bool(item.get("journey_steps") or p.get("steps"))
    step_count = len(item.get("journey_steps") or p.get("steps") or [])
    actual = item.get("actual") if isinstance(item.get("actual"), dict) else {}
    has_cross_step_oracle = bool(actual.get("cross_step_oracle") or p.get("cross_step_oracle"))
    evidence_strength = 18 if has_steps else 10
    if item.get("response", {}).get("body") is not None:
        evidence_strength += 4
    if context:
        evidence_strength += 5
    if step_count >= 3:
        evidence_strength += 3
    if has_cross_step_oracle:
        evidence_strength += 4
    business_impact = {
        "permission_bypass": 23,
        "auth_bypass": 22,
        "idor": 24,
        "tenant_isolation": 25,
        "money_consistency": 25,
        "stock_consistency": 23,
        "state_flow": 22,
        "state_consistency": 22,
        "coupon_abuse": 20,
        "refund_abuse": 21,
        "payment_callback": 20,
        "idempotency": 18,
        "boundary_validation": 15,
    }.get(risk, 12)
    source_weight = {
        "business_knowledge": 16,
        "business_adaptation_layer": 16,
        "risk_learning_profile": 16,
        "high_value_attack_plan": 16,
        "capability_gap": 16,
        "oracle_gap": 15,
        "high_value_memory": 15,
        "pattern_library": 13,
        "feedback_learning": 12,
        "feedback_adjusted": 12,
        "adaptive_policy": 11,
        "rag_enhanced": 10,
        "journey_auto": 9,
        "generic_auto": 6,
    }.get(source, 5)
    severity_weight = {"P0": 18, "P1": 12, "P2": 6}.get(str(p.get("severity") or "P2"), 4)
    reproducibility = 12 if item.get("assertion_result") == "failed" else 4
    if step_count >= 2:
        reproducibility += 3
    confidence_weight = int(float(item.get("confidence") or 0) * 10)
    security_financial_bonus = 6 if risk in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation", "money_consistency", "refund_abuse", "payment_callback"} else 0
    total = min(100, business_impact + evidence_strength + source_weight + severity_weight + reproducibility + confidence_weight + security_financial_bonus)
    reasons = []
    if risk in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation"}:
        reasons.append("权限/数据隔离风险")
    if risk in {"money_consistency", "refund_abuse", "payment_callback", "coupon_abuse"}:
        reasons.append("资金/权益损失风险")
    if has_steps:
        reasons.append(f"多步骤链路证据({step_count}步)")
    if has_cross_step_oracle:
        reasons.append("跨步骤业务断言命中")
    if context:
        reasons.append("命中企业业务知识")
    if source == "business_knowledge":
        reasons.append("业务知识驱动探针")
    if source == "business_adaptation_layer":
        reasons.append("业务适配画像驱动探针")
    if source == "risk_learning_profile":
        reasons.append("风险学习画像驱动探针")
    if source == "high_value_attack_plan":
        reasons.append("高价值攻击计划驱动探针")
    if source == "capability_gap":
        reasons.append("能力短板诊断驱动探针")
    if source == "oracle_gap":
        reasons.append("跨步骤 Oracle 覆盖缺口补齐")
    if source == "high_value_memory":
        reasons.append("历史高价值缺陷模式复测")
    return {
        "total_score": total,
        "tier": value_tier(total),
        "business_impact": min(30, business_impact + security_financial_bonus),
        "evidence_strength": min(30, evidence_strength),
        "source_weight": source_weight,
        "severity_weight": severity_weight,
        "reproducibility": min(20, reproducibility),
        "confidence_weight": confidence_weight,
        "step_count": step_count,
        "has_cross_step_oracle": has_cross_step_oracle,
        "has_business_context": bool(context),
        "reasons": reasons or ["接口行为违反预期"],
    }


def value_tier(score: int) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    return "C"


def build_high_value_summary(bugs: list[dict]) -> dict:
    tier_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for bug in bugs:
        tier_counts[bug.get("value_tier", "C")] = tier_counts.get(bug.get("value_tier", "C"), 0) + 1
        risk_counts[bug.get("risk_type", "unknown")] = risk_counts.get(bug.get("risk_type", "unknown"), 0) + 1
        source_counts[bug.get("probe_source", "unknown")] = source_counts.get(bug.get("probe_source", "unknown"), 0) + 1
    business_context_hits = sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("has_business_context"))
    journey_evidence_hits = sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("step_count", 0) >= 2)
    cross_step_oracle_hits = sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("has_cross_step_oracle"))
    return {
        "total_findings": len(bugs),
        "s_tier_findings": tier_counts.get("S", 0),
        "a_tier_findings": tier_counts.get("A", 0),
        "s_or_a_tier_findings": tier_counts.get("S", 0) + tier_counts.get("A", 0),
        "business_context_findings": business_context_hits,
        "journey_evidence_findings": journey_evidence_hits,
        "cross_step_oracle_findings": cross_step_oracle_hits,
        "tier_distribution": dict(sorted(tier_counts.items())),
        "risk_distribution": dict(sorted(risk_counts.items(), key=lambda item: item[1], reverse=True)),
        "source_distribution": dict(sorted(source_counts.items(), key=lambda item: item[1], reverse=True)),
        "top_findings": [
            {
                "title": bug.get("title"),
                "risk_type": bug.get("risk_type"),
                "value_tier": bug.get("value_tier"),
                "bug_value_score": bug.get("bug_value_score"),
                "reasons": bug.get("high_value_profile", {}).get("reasons", []),
                "probe_source": bug.get("probe_source"),
            }
            for bug in sorted(bugs, key=lambda item: item.get("bug_value_score", 0), reverse=True)[:10]
        ],
    }


def build_high_value_issue_clusters(bugs: list[dict]) -> dict:
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for bug in bugs:
        risk = str(bug.get("risk_type") or "unknown")
        template = str(bug.get("predicted_template_id") or risk or "unknown")
        business_object = str(bug.get("business_object") or "unknown")
        buckets.setdefault((risk, template, business_object), []).append(bug)

    severity_rank = {"P0": 3, "P1": 2, "P2": 1}
    source_distribution: dict[str, int] = {}
    risk_distribution: dict[str, int] = {}
    fix_labels = {
        "permission_bypass": "统一收敛服务端权限校验，补齐角色、资源、动作三元校验，并加入越权回归链路。",
        "auth_bypass": "所有受保护接口强制登录态校验，覆盖匿名、过期 token、锁定账号和降权后的访问。",
        "idor": "对象级权限必须在服务端按当前用户/租户二次校验，禁止只依赖前端传入 ID。",
        "tenant_isolation": "租户字段必须来自可信上下文，查询、写入、缓存和导出链路都要做租户隔离断言。",
        "money_consistency": "建立金额守恒校验，覆盖下单、优惠、支付、退款、流水、余额与舍入误差。",
        "stock_consistency": "建立库存/额度守恒校验，覆盖扣减、回滚、重复提交、并发边界和失败补偿。",
        "state_flow": "用状态机白名单约束流转，阻断终态重开、取消后支付、回调乱序等非法路径。",
        "state_consistency": "动作完成后必须做查询一致性校验，覆盖异步同步、审批、支付和退款结果。",
        "coupon_abuse": "权益核销要校验归属、门槛、有效期、叠加、重复使用和金额上限。",
        "refund_abuse": "退款必须绑定原订单、原支付和可退余额，防止重复退、超额退和跨单退。",
        "payment_callback": "支付回调必须验签、验金额、验订单状态并保证幂等。",
        "idempotency": "关键写操作补齐幂等键和重放保护，覆盖下单、支付、退款、回调和审批。",
    }

    clusters = []
    for index, ((risk, template, business_object), items) in enumerate(buckets.items(), start=1):
        ranked = sorted(items, key=lambda bug: (int(bug.get("bug_value_score") or 0), float(bug.get("confidence") or 0)), reverse=True)
        representative = ranked[0]
        affected_apis = sorted({str(api) for bug in items for api in (bug.get("related_apis") or [bug.get("affected_api") or "unknown"]) if api})
        source_counts: dict[str, int] = {}
        for bug in items:
            source = str(bug.get("probe_source") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
        for source in source_counts:
            source_distribution[source] = source_distribution.get(source, 0) + 1
        risk_distribution[risk] = risk_distribution.get(risk, 0) + 1
        max_score = max(int(bug.get("bug_value_score") or 0) for bug in items)
        max_confidence = max(float(bug.get("confidence") or 0) for bug in items)
        max_severity = max((str(bug.get("severity") or "P2") for bug in items), key=lambda item: severity_rank.get(item, 0))
        s_or_a_count = sum(1 for bug in items if bug.get("value_tier") in {"S", "A"})
        has_cross_step_oracle = any(bug.get("high_value_profile", {}).get("has_cross_step_oracle") for bug in items)
        has_business_context = any(bug.get("high_value_profile", {}).get("has_business_context") or bug.get("business_context") for bug in items)
        max_step_count = max(int(bug.get("high_value_profile", {}).get("step_count") or 0) for bug in items)
        evidence_strength = max(int(bug.get("high_value_profile", {}).get("evidence_strength") or 0) for bug in items)
        release_gate = "block" if (max_severity == "P0" or (max_score >= 90 and has_cross_step_oracle)) else "review" if max_score >= 80 else "monitor"
        cluster_id = f"HVIC_{safe_pattern_token(risk)}_{safe_pattern_token(template)}_{index:03d}"
        clusters.append({
            "cluster_id": cluster_id,
            "risk_type": risk,
            "predicted_template_id": template,
            "business_object": business_object,
            "finding_count": len(items),
            "s_or_a_count": s_or_a_count,
            "max_value_score": max_score,
            "max_confidence": round(max_confidence, 3),
            "value_tier": value_tier(max_score),
            "severity": max_severity,
            "affected_apis": affected_apis[:12],
            "affected_api_count": len(affected_apis),
            "probe_sources": dict(sorted(source_counts.items(), key=lambda item: item[1], reverse=True)),
            "has_cross_step_oracle": has_cross_step_oracle,
            "has_business_context": has_business_context,
            "max_step_count": max_step_count,
            "evidence_strength": evidence_strength,
            "representative_title": representative.get("title"),
            "representative_bug_id": representative.get("discovered_bug_id"),
            "representative_probe_id": representative.get("probe_id"),
            "recommended_owner": business_object if business_object != "unknown" else risk,
            "recommended_fix": fix_labels.get(risk, "根据 PRD/OpenAPI 补齐业务不变量、异常状态和跨步骤一致性校验。"),
            "release_gate": release_gate,
        })

    gate_rank = {"block": 3, "review": 2, "monitor": 1}
    clusters.sort(key=lambda item: (gate_rank.get(item["release_gate"], 0), item["max_value_score"], item["finding_count"], item["s_or_a_count"]), reverse=True)
    total = len(bugs)
    return {
        "version": "high_value_issue_clusters_v1",
        "total_findings": total,
        "cluster_count": len(clusters),
        "blocker_cluster_count": sum(1 for item in clusters if item["release_gate"] == "block"),
        "review_cluster_count": sum(1 for item in clusters if item["release_gate"] == "review"),
        "monitor_cluster_count": sum(1 for item in clusters if item["release_gate"] == "monitor"),
        "compression_rate": round(1 - (len(clusters) / total), 3) if total else 0,
        "risk_cluster_distribution": dict(sorted(risk_distribution.items(), key=lambda item: item[1], reverse=True)),
        "source_cluster_distribution": dict(sorted(source_distribution.items(), key=lambda item: item[1], reverse=True)),
        "top_clusters": clusters[:20],
    }


def business_risk_domain_for(risk_type: str) -> tuple[str, str]:
    risk = str(risk_type or "unknown")
    mapping = {
        "permission_bypass": ("access_isolation", "权限与访问隔离"),
        "auth_bypass": ("access_isolation", "权限与访问隔离"),
        "idor": ("access_isolation", "权限与访问隔离"),
        "tenant_isolation": ("access_isolation", "权限与访问隔离"),
        "privilege_escalation": ("access_isolation", "权限与访问隔离"),
        "locked_account_bypass": ("access_isolation", "权限与访问隔离"),
        "money_consistency": ("financial_integrity", "资金与金额一致性"),
        "refund_abuse": ("financial_integrity", "资金与金额一致性"),
        "payment_callback": ("financial_integrity", "资金与金额一致性"),
        "callback_trust": ("financial_integrity", "资金与金额一致性"),
        "stock_consistency": ("capacity_inventory", "库存容量与额度"),
        "quantity_consistency": ("capacity_inventory", "库存容量与额度"),
        "coupon_abuse": ("benefit_abuse", "权益优惠与滥用"),
        "state_flow": ("workflow_state", "状态流转与业务流程"),
        "state_consistency": ("workflow_state", "状态流转与业务流程"),
        "eventual_consistency": ("workflow_state", "状态流转与业务流程"),
        "message_ordering": ("workflow_state", "状态流转与业务流程"),
        "idempotency": ("concurrency_idempotency", "并发幂等与重复提交"),
        "race_condition": ("concurrency_idempotency", "并发幂等与重复提交"),
        "concurrent_update_lost": ("concurrency_idempotency", "并发幂等与重复提交"),
        "webhook_replay": ("concurrency_idempotency", "并发幂等与重复提交"),
        "audit_log_missing": ("audit_compliance", "审计合规与可追溯"),
        "audit_compliance": ("audit_compliance", "审计合规与可追溯"),
        "boundary_validation": ("boundary_validation", "边界校验与输入约束"),
    }
    return mapping.get(risk, ("domain_specific", "行业特定业务风险"))


def build_business_risk_radar(summary: dict, issue_clusters: dict, oracle_coverage: dict) -> dict:
    domain_rows: dict[str, dict] = {}
    for risk, count in (summary.get("risk_distribution") or {}).items():
        domain, label = business_risk_domain_for(str(risk))
        row = domain_rows.setdefault(
            domain,
            {
                "domain": domain,
                "label": label,
                "finding_count": 0,
                "cluster_count": 0,
                "blocker_cluster_count": 0,
                "s_or_a_estimated": 0,
                "risk_types": {},
                "oracle_required_risks": 0,
                "oracle_covered_risks": 0,
                "min_oracle_coverage_rate": None,
            },
        )
        row["finding_count"] += int(count or 0)
        row["risk_types"][str(risk)] = int(count or 0)
    for cluster in issue_clusters.get("top_clusters") or []:
        domain, label = business_risk_domain_for(str(cluster.get("risk_type") or ""))
        row = domain_rows.setdefault(
            domain,
            {
                "domain": domain,
                "label": label,
                "finding_count": 0,
                "cluster_count": 0,
                "blocker_cluster_count": 0,
                "s_or_a_estimated": 0,
                "risk_types": {},
                "oracle_required_risks": 0,
                "oracle_covered_risks": 0,
                "min_oracle_coverage_rate": None,
            },
        )
        row["cluster_count"] += 1
        row["s_or_a_estimated"] += int(cluster.get("s_or_a_count") or 0)
        if cluster.get("release_gate") == "block":
            row["blocker_cluster_count"] += 1
    for coverage in oracle_coverage.get("risk_coverage") or []:
        risk = str(coverage.get("risk_type") or "")
        domain, label = business_risk_domain_for(risk)
        row = domain_rows.setdefault(
            domain,
            {
                "domain": domain,
                "label": label,
                "finding_count": 0,
                "cluster_count": 0,
                "blocker_cluster_count": 0,
                "s_or_a_estimated": 0,
                "risk_types": {},
                "oracle_required_risks": 0,
                "oracle_covered_risks": 0,
                "min_oracle_coverage_rate": None,
            },
        )
        row["oracle_required_risks"] += 1
        if int(coverage.get("oracle_probe_count") or 0) > 0:
            row["oracle_covered_risks"] += 1
        rate = float(coverage.get("oracle_coverage_rate") or 0)
        if row["min_oracle_coverage_rate"] is None or rate < row["min_oracle_coverage_rate"]:
            row["min_oracle_coverage_rate"] = rate
    rows = []
    for row in domain_rows.values():
        oracle_rate = row["min_oracle_coverage_rate"]
        if oracle_rate is None:
            oracle_rate = 1.0 if not row["finding_count"] else 0.0
        row["min_oracle_coverage_rate"] = round(float(oracle_rate), 3)
        exposure = min(100, row["finding_count"] * 2 + row["blocker_cluster_count"] * 10 + row["cluster_count"] * 3)
        confidence = min(100, int(row["min_oracle_coverage_rate"] * 70) + min(30, row["cluster_count"] * 3))
        row["exposure_score"] = int(exposure)
        row["confidence_score"] = int(confidence)
        row["radar_score"] = max(0, min(100, int(exposure * 0.65 + (100 - confidence) * 0.35)))
        if row["blocker_cluster_count"] > 0 or row["radar_score"] >= 70:
            row["priority"] = "P0"
        elif row["cluster_count"] > 0 or row["radar_score"] >= 40:
            row["priority"] = "P1"
        else:
            row["priority"] = "P2"
        row["risk_types"] = dict(sorted(row["risk_types"].items(), key=lambda item: item[1], reverse=True))
        rows.append(row)
    rows.sort(key=lambda item: (item["priority"] == "P0", item["radar_score"], item["finding_count"]), reverse=True)
    return {
        "version": "business_risk_radar_v1",
        "domain_count": len(rows),
        "p0_domain_count": sum(1 for item in rows if item["priority"] == "P0"),
        "p1_domain_count": sum(1 for item in rows if item["priority"] == "P1"),
        "top_domains": rows[:10],
        "radar_summary": [
            f"{item['label']}：{item['finding_count']} 个发现，{item['blocker_cluster_count']} 个阻断问题簇"
            for item in rows[:5]
        ],
    }


def build_enterprise_release_gate_decision(summary: dict, issue_clusters: dict, oracle_coverage: dict, attack_plan: dict) -> dict:
    clusters = list(issue_clusters.get("top_clusters") or [])
    blocker_count = int(issue_clusters.get("blocker_cluster_count") or 0)
    review_count = int(issue_clusters.get("review_cluster_count") or 0)
    total_findings = int(summary.get("total_findings") or issue_clusters.get("total_findings") or 0)
    s_or_a = int(summary.get("s_or_a_tier_findings") or 0)
    oracle_rate = float(oracle_coverage.get("oracle_coverage_rate") or 0)
    compression_rate = float(issue_clusters.get("compression_rate") or 0)
    risk_score = min(
        100,
        blocker_count * 6
        + review_count * 2
        + min(30, s_or_a // 8)
        + (15 if oracle_rate < 0.8 else 8 if oracle_rate < 0.9 else 0),
    )
    if blocker_count > 0:
        gate = "block_release"
        decision = "不建议发布"
    elif review_count > 0 or oracle_rate < 0.9:
        gate = "hold_for_review"
        decision = "人工复核后发布"
    else:
        gate = "allow_release"
        decision = "允许发布"

    release_blockers = [
        {
            "cluster_id": item.get("cluster_id"),
            "risk_type": item.get("risk_type"),
            "business_object": item.get("business_object"),
            "finding_count": item.get("finding_count"),
            "max_value_score": item.get("max_value_score"),
            "affected_apis": item.get("affected_apis", [])[:5],
            "recommended_owner": item.get("recommended_owner"),
            "recommended_fix": item.get("recommended_fix"),
            "representative_bug_id": item.get("representative_bug_id"),
        }
        for item in clusters
        if item.get("release_gate") == "block"
    ][:10]
    fix_queue = []
    for index, item in enumerate(clusters[:15], start=1):
        gate_level = item.get("release_gate") or "review"
        fix_queue.append({
            "rank": index,
            "cluster_id": item.get("cluster_id"),
            "priority": "P0" if gate_level == "block" else "P1" if gate_level == "review" else "P2",
            "release_gate": gate_level,
            "risk_type": item.get("risk_type"),
            "business_object": item.get("business_object"),
            "recommended_owner": item.get("recommended_owner"),
            "finding_count": item.get("finding_count"),
            "affected_api_count": item.get("affected_api_count"),
            "recommended_fix": item.get("recommended_fix"),
            "acceptance_criteria": [
                "修复后同一问题簇的代表探针必须全部通过",
                "相关接口的跨步骤业务 Oracle 必须通过",
                "新增回归用例进入日常迭代套件",
            ],
        })

    blocking_reasons = []
    if blocker_count:
        blocking_reasons.append(f"存在 {blocker_count} 个阻断发布问题簇")
    if s_or_a:
        blocking_reasons.append(f"存在 {s_or_a} 个 S/A 高价值发现")
    if oracle_rate < 0.9:
        blocking_reasons.append(f"跨步骤业务 Oracle 覆盖率 {round(oracle_rate * 100)}%，低于 90% 目标")
    if not blocking_reasons:
        blocking_reasons.append("未发现阻断发布问题簇")

    return {
        "version": "enterprise_release_gate_decision_v1",
        "gate": gate,
        "decision": decision,
        "release_risk_score": risk_score,
        "total_findings": total_findings,
        "s_or_a_tier_findings": s_or_a,
        "issue_cluster_count": int(issue_clusters.get("cluster_count") or 0),
        "blocker_cluster_count": blocker_count,
        "review_cluster_count": review_count,
        "compression_rate": compression_rate,
        "oracle_coverage_rate": oracle_rate,
        "blocking_reasons": blocking_reasons,
        "release_blockers": release_blockers,
        "fix_queue": fix_queue,
        "next_run_probe_budget": attack_plan.get("next_run_probe_budget"),
        "quality_bar": {
            "block_when_blocker_cluster_exists": True,
            "target_oracle_coverage_rate": 0.9,
            "require_regression_for_fixed_clusters": True,
        },
    }


def split_api_signature(api: str) -> tuple[str, str]:
    parts = str(api or "").strip().split(" ", 1)
    if len(parts) == 2 and parts[0].isalpha():
        return parts[0].upper(), parts[1].strip() or "/"
    return "GET", str(api or "/").strip() or "/"


def build_cluster_fix_verification_plan(release_gate_decision: dict, issue_clusters: dict) -> dict:
    cluster_by_id = {str(item.get("cluster_id")): item for item in issue_clusters.get("top_clusters") or [] if item.get("cluster_id")}
    plan_items = []
    regression_probes = []
    for queue_item in release_gate_decision.get("fix_queue") or []:
        cluster_id = str(queue_item.get("cluster_id") or "")
        cluster = cluster_by_id.get(cluster_id, {})
        affected_apis = list(cluster.get("affected_apis") or [])
        if not affected_apis and queue_item.get("affected_apis"):
            affected_apis = list(queue_item.get("affected_apis") or [])
        if not affected_apis:
            affected_apis = ["GET /"]
        priority = str(queue_item.get("priority") or "P1")
        risk_type = str(queue_item.get("risk_type") or cluster.get("risk_type") or "business_risk")
        owner = str(queue_item.get("recommended_owner") or cluster.get("recommended_owner") or queue_item.get("business_object") or "unknown")
        fix = str(queue_item.get("recommended_fix") or cluster.get("recommended_fix") or "修复后必须确认原缺陷信号不再复现。")
        criteria = list(queue_item.get("acceptance_criteria") or [])
        representative_bug_id = str(cluster.get("representative_bug_id") or "")
        verification_id = f"FIX_CLUSTER_{safe_pattern_token(cluster_id)}"
        api_checks = []
        for api_index, api in enumerate(affected_apis[:5], start=1):
            method, path = split_api_signature(api)
            probe_id = f"REG_{safe_pattern_token(cluster_id)}_{api_index:02d}"
            expected = "；".join(criteria) if criteria else "修复后原缺陷信号不应复现，业务规则保持正确。"
            api_checks.append({
                "method": method,
                "path": path,
                "expected_after_fix": expected,
            })
            regression_probes.append({
                "regression_probe_id": probe_id,
                "issue_id": cluster_id,
                "title": f"{risk_type} 修复回归：{path}",
                "risk_type": risk_type,
                "severity": priority,
                "method": method,
                "path": path,
                "actor": "normal_user",
                "expected": expected,
                "source": "high_value_issue_cluster_gate",
                "cluster_id": cluster_id,
                "representative_bug_id": representative_bug_id,
            })
        plan_items.append({
            "verification_id": verification_id,
            "cluster_id": cluster_id,
            "representative_bug_id": representative_bug_id,
            "priority": priority,
            "release_gate": queue_item.get("release_gate"),
            "risk_type": risk_type,
            "business_object": queue_item.get("business_object") or cluster.get("business_object"),
            "recommended_owner": owner,
            "finding_count": queue_item.get("finding_count"),
            "recommended_fix": fix,
            "acceptance_criteria": criteria,
            "api_checks": api_checks,
            "regression_probe_ids": [item["regression_probe_id"] for item in regression_probes if item.get("cluster_id") == cluster_id],
        })

    p0_count = sum(1 for item in plan_items if item.get("priority") == "P0")
    p1_count = sum(1 for item in plan_items if item.get("priority") == "P1")
    return {
        "version": "cluster_fix_verification_plan_v1",
        "source_gate": release_gate_decision.get("gate"),
        "verification_count": len(plan_items),
        "p0_verification_count": p0_count,
        "p1_verification_count": p1_count,
        "regression_probe_count": len(regression_probes),
        "items": plan_items,
        "regression_probes": {
            "version": "fix_regression_probes_from_issue_clusters_v1",
            "items": regression_probes,
        },
    }


def evidence_reproduction_steps(evidence: dict, fallback_api: str) -> list[dict]:
    steps = []
    journey_steps = evidence.get("journey_steps") or []
    if journey_steps:
        for index, step in enumerate(journey_steps[:8], start=1):
            request = step.get("request") if isinstance(step.get("request"), dict) else {}
            response = step.get("response") if isinstance(step.get("response"), dict) else {}
            steps.append({
                "step_no": index,
                "name": step.get("step") or f"step_{index}",
                "method": request.get("method"),
                "url": request.get("url") or request.get("path"),
                "request": request,
                "response_status": response.get("status_code"),
                "response_excerpt": response.get("body_excerpt"),
            })
        return steps
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
    response = evidence.get("response") if isinstance(evidence.get("response"), dict) else {}
    steps.append({
        "step_no": 1,
        "name": "execute_representative_probe",
        "method": request.get("method"),
        "url": request.get("url") or request.get("path") or fallback_api,
        "request": request,
        "response_status": response.get("status_code"),
        "response_excerpt": response.get("body_excerpt"),
    })
    return steps


def build_high_value_repro_evidence_pack(discovered: list[dict], issue_clusters: dict, business_risk_radar: dict, fix_plan: dict, evidence_bundle: list[dict]) -> dict:
    bug_by_id = {str(item.get("discovered_bug_id")): item for item in discovered if item.get("discovered_bug_id")}
    bugs_by_probe = {str(item.get("probe_id")): item for item in discovered if item.get("probe_id")}
    evidence_by_probe = {str(item.get("probe_id")): item for item in evidence_bundle if item.get("probe_id")}
    fix_by_cluster = {str(item.get("cluster_id")): item for item in fix_plan.get("items") or [] if item.get("cluster_id")}
    domain_rows = {str(item.get("domain")): item for item in business_risk_radar.get("top_domains") or [] if item.get("domain")}
    items = []
    domain_summary: dict[str, dict] = {}
    gate_rank = {"block": 3, "review": 2, "monitor": 1}
    clusters = sorted(
        list(issue_clusters.get("top_clusters") or []),
        key=lambda item: (gate_rank.get(str(item.get("release_gate") or ""), 0), int(item.get("max_value_score") or 0), int(item.get("finding_count") or 0)),
        reverse=True,
    )
    for cluster in clusters[:20]:
        cluster_id = str(cluster.get("cluster_id") or "")
        representative_bug = bug_by_id.get(str(cluster.get("representative_bug_id") or ""))
        if not representative_bug and cluster.get("representative_probe_id"):
            representative_bug = bugs_by_probe.get(str(cluster.get("representative_probe_id")))
        if not representative_bug:
            for bug in discovered:
                if str(bug.get("risk_type") or "") == str(cluster.get("risk_type") or "") and str(bug.get("business_object") or "") == str(cluster.get("business_object") or ""):
                    representative_bug = bug
                    break
        representative_bug = representative_bug or {}
        probe_id = str(representative_bug.get("probe_id") or cluster.get("representative_probe_id") or "")
        evidence = evidence_by_probe.get(probe_id, {})
        fix_item = fix_by_cluster.get(cluster_id, {})
        risk_type = str(cluster.get("risk_type") or representative_bug.get("risk_type") or "business_risk")
        risk_domain, risk_domain_label = business_risk_domain_for(risk_type)
        domain_row = domain_rows.get(risk_domain, {})
        affected_apis = list(cluster.get("affected_apis") or representative_bug.get("related_apis") or [])
        if not affected_apis and representative_bug.get("affected_api"):
            affected_apis = [representative_bug.get("affected_api")]
        fallback_api = affected_apis[0] if affected_apis else "GET /"
        release_gate = str(cluster.get("release_gate") or fix_item.get("release_gate") or "review")
        priority = str(fix_item.get("priority") or ("P0" if release_gate == "block" else "P1" if release_gate == "review" else "P2"))
        reproduction_steps = evidence_reproduction_steps(evidence, fallback_api)
        request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
        response = evidence.get("response") if isinstance(evidence.get("response"), dict) else {}
        item = {
            "pack_id": f"REPRO_{safe_pattern_token(cluster_id or risk_type)}",
            "cluster_id": cluster_id,
            "priority": priority,
            "release_gate": release_gate,
            "risk_domain": risk_domain,
            "risk_domain_label": risk_domain_label,
            "risk_domain_priority": domain_row.get("priority"),
            "risk_type": risk_type,
            "business_object": cluster.get("business_object") or representative_bug.get("business_object"),
            "representative_bug_id": representative_bug.get("discovered_bug_id") or cluster.get("representative_bug_id"),
            "representative_probe_id": probe_id,
            "representative_title": cluster.get("representative_title") or representative_bug.get("title"),
            "affected_apis": affected_apis[:10],
            "finding_count": int(cluster.get("finding_count") or 0),
            "max_value_score": int(cluster.get("max_value_score") or representative_bug.get("bug_value_score") or 0),
            "value_tier": cluster.get("value_tier") or representative_bug.get("value_tier"),
            "has_cross_step_oracle": bool(cluster.get("has_cross_step_oracle") or representative_bug.get("high_value_profile", {}).get("has_cross_step_oracle")),
            "has_business_context": bool(cluster.get("has_business_context") or representative_bug.get("business_context")),
            "evidence_strength": int(cluster.get("evidence_strength") or representative_bug.get("high_value_profile", {}).get("evidence_strength") or 0),
            "reproduction_steps": reproduction_steps,
            "request_excerpt": {
                "method": request.get("method"),
                "url": request.get("url") or request.get("path"),
                "body": request.get("body"),
                "headers": request.get("headers"),
            },
            "response_excerpt": {
                "status_code": response.get("status_code"),
                "body_excerpt": response.get("body_excerpt"),
            },
            "expected": evidence.get("expected") or representative_bug.get("expected"),
            "actual": evidence.get("actual") or representative_bug.get("actual"),
            "bug_signal": evidence.get("bug_signal") or representative_bug.get("bug_signal"),
            "recommended_fix": fix_item.get("recommended_fix") or cluster.get("recommended_fix"),
            "acceptance_criteria": list(fix_item.get("acceptance_criteria") or []),
            "expected_after_fix": [check.get("expected_after_fix") for check in (fix_item.get("api_checks") or []) if check.get("expected_after_fix")][:5],
            "regression_probe_ids": list(fix_item.get("regression_probe_ids") or []),
            "recommended_owner": fix_item.get("recommended_owner") or cluster.get("recommended_owner"),
        }
        items.append(item)
        summary = domain_summary.setdefault(
            risk_domain,
            {
                "domain": risk_domain,
                "label": risk_domain_label,
                "pack_count": 0,
                "p0_pack_count": 0,
                "cross_step_pack_count": 0,
            },
        )
        summary["pack_count"] += 1
        if priority == "P0":
            summary["p0_pack_count"] += 1
        if item["has_cross_step_oracle"]:
            summary["cross_step_pack_count"] += 1
    domain_items = sorted(domain_summary.values(), key=lambda item: (item["p0_pack_count"], item["pack_count"]), reverse=True)
    return {
        "version": "high_value_repro_evidence_pack_v1",
        "pack_count": len(items),
        "p0_pack_count": sum(1 for item in items if item.get("priority") == "P0"),
        "cross_step_pack_count": sum(1 for item in items if item.get("has_cross_step_oracle")),
        "business_context_pack_count": sum(1 for item in items if item.get("has_business_context")),
        "domain_count": len(domain_items),
        "domain_summary": domain_items,
        "items": items,
    }


def root_cause_for_risk(risk_type: str) -> tuple[str, str, str]:
    risk = str(risk_type or "unknown")
    mapping = {
        "permission_bypass": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "auth_bypass": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "idor": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "tenant_isolation": ("tenant_boundary_gap", "租户隔离缺口", "租户边界没有贯穿查询、写入、导出、缓存或异步链路。"),
        "privilege_escalation": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "locked_account_bypass": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "money_consistency": ("conservation_oracle_gap", "金额守恒缺口", "金额、流水、支付、优惠或退款链路缺少端到端守恒校验。"),
        "refund_abuse": ("conservation_oracle_gap", "金额守恒缺口", "金额、流水、支付、优惠或退款链路缺少端到端守恒校验。"),
        "payment_callback": ("callback_trust_gap", "回调可信校验缺口", "外部回调缺少签名、订单状态、金额和幂等校验。"),
        "callback_trust": ("callback_trust_gap", "回调可信校验缺口", "外部回调缺少签名、订单状态、金额和幂等校验。"),
        "stock_consistency": ("inventory_conservation_gap", "库存容量守恒缺口", "库存、容量或额度链路缺少扣减、回滚、并发和失败补偿校验。"),
        "quantity_consistency": ("inventory_conservation_gap", "库存容量守恒缺口", "库存、容量或额度链路缺少扣减、回滚、并发和失败补偿校验。"),
        "coupon_abuse": ("benefit_policy_gap", "权益策略校验缺口", "优惠、权益或额度缺少归属、门槛、有效期、叠加和上限校验。"),
        "state_flow": ("state_machine_guard_gap", "状态机防护缺口", "业务状态流转没有用白名单和终态保护约束非法路径。"),
        "state_consistency": ("state_machine_guard_gap", "状态机防护缺口", "动作后查询、异步同步和跨系统状态缺少一致性校验。"),
        "eventual_consistency": ("state_machine_guard_gap", "状态机防护缺口", "动作后查询、异步同步和跨系统状态缺少一致性校验。"),
        "message_ordering": ("state_machine_guard_gap", "状态机防护缺口", "动作后查询、异步同步和跨系统状态缺少一致性校验。"),
        "idempotency": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "race_condition": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "concurrent_update_lost": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "webhook_replay": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "audit_log_missing": ("audit_traceability_gap", "审计追溯缺口", "关键操作缺少可追溯审计、操作者、对象和变更前后状态。"),
        "audit_compliance": ("audit_traceability_gap", "审计追溯缺口", "关键操作缺少可追溯审计、操作者、对象和变更前后状态。"),
        "boundary_validation": ("input_contract_gap", "输入契约校验缺口", "接口缺少边界、枚举、格式、范围或组合约束校验。"),
    }
    return mapping.get(risk, ("business_rule_gap", "业务规则缺口", "PRD/OpenAPI 中的业务约束没有沉淀为可执行校验。"))


def build_enterprise_bug_triage_matrix(issue_clusters: dict, business_risk_radar: dict, release_gate_decision: dict, repro_pack: dict) -> dict:
    pack_by_cluster = {str(item.get("cluster_id")): item for item in repro_pack.get("items") or [] if item.get("cluster_id")}
    domain_by_key = {str(item.get("domain")): item for item in business_risk_radar.get("top_domains") or [] if item.get("domain")}
    queue = []
    root_summary: dict[str, dict] = {}
    owner_summary: dict[str, dict] = {}
    gate_rank = {"block": 3, "review": 2, "monitor": 1}
    for index, cluster in enumerate(issue_clusters.get("top_clusters") or [], start=1):
        cluster_id = str(cluster.get("cluster_id") or "")
        pack = pack_by_cluster.get(cluster_id, {})
        risk_type = str(cluster.get("risk_type") or pack.get("risk_type") or "business_risk")
        root_key, root_label, root_reason = root_cause_for_risk(risk_type)
        domain_key = str(pack.get("risk_domain") or business_risk_domain_for(risk_type)[0])
        domain_label = str(pack.get("risk_domain_label") or business_risk_domain_for(risk_type)[1])
        domain = domain_by_key.get(domain_key, {})
        release_gate = str(cluster.get("release_gate") or pack.get("release_gate") or "review")
        priority = str(pack.get("priority") or ("P0" if release_gate == "block" else "P1" if release_gate == "review" else "P2"))
        owner = str(pack.get("recommended_owner") or cluster.get("recommended_owner") or cluster.get("business_object") or root_key)
        has_repro = bool(pack.get("reproduction_steps"))
        has_acceptance = bool(pack.get("acceptance_criteria") or pack.get("expected_after_fix"))
        max_score = int(cluster.get("max_value_score") or pack.get("max_value_score") or 0)
        finding_count = int(cluster.get("finding_count") or pack.get("finding_count") or 0)
        blocker_bonus = 35 if release_gate == "block" else 18 if release_gate == "review" else 0
        domain_bonus = 15 if domain.get("priority") == "P0" else 8 if domain.get("priority") == "P1" else 0
        evidence_bonus = 10 if has_repro and has_acceptance else 5 if has_repro else 0
        triage_score = min(100, int(max_score * 0.45 + min(25, finding_count) + blocker_bonus + domain_bonus + evidence_bonus))
        if priority == "P0" or triage_score >= 90:
            sla = "24h"
        elif priority == "P1" or triage_score >= 75:
            sla = "72h"
        else:
            sla = "next_iteration"
        row = {
            "rank": index,
            "cluster_id": cluster_id,
            "priority": priority,
            "sla": sla,
            "triage_score": triage_score,
            "release_gate": release_gate,
            "risk_type": risk_type,
            "risk_domain": domain_key,
            "risk_domain_label": domain_label,
            "root_cause_type": root_key,
            "root_cause_label": root_label,
            "root_cause_hypothesis": root_reason,
            "recommended_owner": owner,
            "business_object": cluster.get("business_object") or pack.get("business_object"),
            "finding_count": finding_count,
            "max_value_score": max_score,
            "affected_apis": list(cluster.get("affected_apis") or pack.get("affected_apis") or [])[:8],
            "representative_bug_id": cluster.get("representative_bug_id") or pack.get("representative_bug_id"),
            "representative_title": cluster.get("representative_title") or pack.get("representative_title"),
            "repro_pack_id": pack.get("pack_id"),
            "ready_for_fix": bool(has_repro and has_acceptance),
            "recommended_fix": pack.get("recommended_fix") or cluster.get("recommended_fix"),
            "acceptance_criteria": list(pack.get("acceptance_criteria") or [])[:5],
            "regression_probe_ids": list(pack.get("regression_probe_ids") or [])[:8],
        }
        queue.append(row)
        root = root_summary.setdefault(
            root_key,
            {
                "root_cause_type": root_key,
                "root_cause_label": root_label,
                "cluster_count": 0,
                "p0_count": 0,
                "ready_for_fix_count": 0,
                "affected_domains": {},
                "recommended_owners": {},
                "max_triage_score": 0,
            },
        )
        root["cluster_count"] += 1
        root["p0_count"] += 1 if priority == "P0" else 0
        root["ready_for_fix_count"] += 1 if row["ready_for_fix"] else 0
        root["affected_domains"][domain_key] = root["affected_domains"].get(domain_key, 0) + 1
        root["recommended_owners"][owner] = root["recommended_owners"].get(owner, 0) + 1
        root["max_triage_score"] = max(root["max_triage_score"], triage_score)
        owner_row = owner_summary.setdefault(owner, {"owner": owner, "cluster_count": 0, "p0_count": 0, "ready_for_fix_count": 0, "max_triage_score": 0})
        owner_row["cluster_count"] += 1
        owner_row["p0_count"] += 1 if priority == "P0" else 0
        owner_row["ready_for_fix_count"] += 1 if row["ready_for_fix"] else 0
        owner_row["max_triage_score"] = max(owner_row["max_triage_score"], triage_score)
    queue.sort(key=lambda item: (item["priority"] == "P0", gate_rank.get(item["release_gate"], 0), item["triage_score"], item["finding_count"]), reverse=True)
    for idx, row in enumerate(queue, start=1):
        row["rank"] = idx
    root_causes = []
    for row in root_summary.values():
        row["affected_domains"] = dict(sorted(row["affected_domains"].items(), key=lambda item: item[1], reverse=True))
        row["recommended_owners"] = dict(sorted(row["recommended_owners"].items(), key=lambda item: item[1], reverse=True))
        root_causes.append(row)
    root_causes.sort(key=lambda item: (item["p0_count"], item["max_triage_score"], item["cluster_count"]), reverse=True)
    owners = sorted(owner_summary.values(), key=lambda item: (item["p0_count"], item["max_triage_score"], item["cluster_count"]), reverse=True)
    return {
        "version": "enterprise_bug_triage_matrix_v1",
        "source_gate": release_gate_decision.get("gate"),
        "triage_count": len(queue),
        "p0_count": sum(1 for item in queue if item.get("priority") == "P0"),
        "ready_for_fix_count": sum(1 for item in queue if item.get("ready_for_fix")),
        "root_cause_count": len(root_causes),
        "owner_count": len(owners),
        "top_root_causes": root_causes[:10],
        "owner_workload": owners[:15],
        "queue": queue[:30],
    }


def build_high_value_capability_assessment(summary: dict, oracle_coverage: dict, issue_clusters: dict, release_gate_decision: dict, fix_plan: dict, strategy: dict) -> dict:
    total = max(1, int(summary.get("total_findings") or 0))
    s_or_a = int(summary.get("s_or_a_tier_findings") or 0)
    business_hits = int(summary.get("business_context_findings") or 0)
    journey_hits = int(summary.get("journey_evidence_findings") or 0)
    cross_step_hits = int(summary.get("cross_step_oracle_findings") or 0)
    cluster_count = int(issue_clusters.get("cluster_count") or 0)
    blocker_count = int(issue_clusters.get("blocker_cluster_count") or 0)
    compression_rate = float(issue_clusters.get("compression_rate") or 0)
    oracle_rate = float(oracle_coverage.get("oracle_coverage_rate") or 0)
    verification_count = int(fix_plan.get("verification_count") or 0)
    regression_count = int(fix_plan.get("regression_probe_count") or 0)
    probe_count = max(1, int(strategy.get("probe_count") or 0))
    high_value_ratio = s_or_a / total
    business_ratio = business_hits / total
    journey_ratio = journey_hits / total
    oracle_hit_ratio = cross_step_hits / total
    source_count = len([name for name, count in (summary.get("source_distribution") or {}).items() if int(count or 0) > 0])
    risk_count = len([name for name, count in (summary.get("risk_distribution") or {}).items() if int(count or 0) > 0])
    probe_efficiency = min(1.0, s_or_a / probe_count)
    dimensions = {
        "high_value_yield": min(100, int(high_value_ratio * 100)),
        "business_understanding": min(100, int(business_ratio * 70 + min(30, risk_count * 2))),
        "oracle_depth": min(100, int(oracle_rate * 70 + oracle_hit_ratio * 30)),
        "actionability": min(100, int(compression_rate * 45 + min(35, blocker_count * 2) + (20 if cluster_count else 0))),
        "closed_loop_asset": min(100, int(min(60, verification_count * 3) + min(40, regression_count * 2))),
        "learning_diversity": min(100, int(min(45, source_count * 7) + min(35, risk_count * 2) + probe_efficiency * 20)),
    }
    overall_score = int(round(
        dimensions["high_value_yield"] * 0.18
        + dimensions["business_understanding"] * 0.16
        + dimensions["oracle_depth"] * 0.20
        + dimensions["actionability"] * 0.18
        + dimensions["closed_loop_asset"] * 0.14
        + dimensions["learning_diversity"] * 0.14
    ))
    if overall_score >= 90:
        maturity = "enterprise_ready"
        maturity_label = "企业级可落地"
    elif overall_score >= 75:
        maturity = "pilot_ready"
        maturity_label = "试点可落地"
    elif overall_score >= 60:
        maturity = "needs_hardening"
        maturity_label = "需要增强后试点"
    else:
        maturity = "early_stage"
        maturity_label = "早期能力"

    gaps = []
    for item in oracle_coverage.get("risk_coverage") or []:
        rate = float(item.get("oracle_coverage_rate") or 0)
        if rate < 0.9:
            gaps.append({
                "gap_type": "oracle_coverage_gap",
                "risk_type": item.get("risk_type"),
                "current": rate,
                "target": 0.9,
                "recommendation": "补齐跨步骤业务 Oracle，优先覆盖状态、金额、库存、幂等等高损失链路。",
            })
    if business_ratio < 0.5:
        gaps.append({
            "gap_type": "business_context_gap",
            "current": round(business_ratio, 3),
            "target": 0.5,
            "recommendation": "继续从 PRD/OpenAPI 中抽取业务对象、角色、状态机和数据守恒规则，提高业务命中率。",
        })
    if verification_count < max(1, min(10, blocker_count)):
        gaps.append({
            "gap_type": "closure_asset_gap",
            "current": verification_count,
            "target": min(10, blocker_count),
            "recommendation": "把阻断发布问题簇转成修复验证和回归探针，避免一次性报告无法沉淀。",
        })

    next_actions = [
        {
            "action": "raise_oracle_coverage",
            "priority": "P0" if oracle_rate < 0.9 else "P1",
            "detail": "下一轮优先补齐 Oracle 覆盖率低于 90% 的风险类型。",
        },
        {
            "action": "expand_business_context",
            "priority": "P1" if business_ratio < 0.6 else "P2",
            "detail": "继续基于需求文档和接口语义扩展业务规则、角色边界、数据依赖和状态机。",
        },
        {
            "action": "stabilize_fix_regression_assets",
            "priority": "P0" if blocker_count and regression_count == 0 else "P1",
            "detail": "把 P0/P1 问题簇固化到修复验证、回归套件和 CI 发布门禁。",
        },
    ]
    return {
        "version": "high_value_capability_assessment_v1",
        "overall_score": overall_score,
        "maturity": maturity,
        "maturity_label": maturity_label,
        "dimensions": dimensions,
        "evidence": {
            "total_findings": int(summary.get("total_findings") or 0),
            "s_or_a_tier_findings": s_or_a,
            "business_context_findings": business_hits,
            "journey_evidence_findings": journey_hits,
            "cross_step_oracle_findings": cross_step_hits,
            "oracle_coverage_rate": oracle_rate,
            "issue_cluster_count": cluster_count,
            "blocker_cluster_count": blocker_count,
            "compression_rate": compression_rate,
            "verification_count": verification_count,
            "regression_probe_count": regression_count,
            "probe_count": int(strategy.get("probe_count") or 0),
            "source_count": source_count,
            "risk_count": risk_count,
            "release_gate": release_gate_decision.get("gate"),
        },
        "capability_gaps": gaps[:10],
        "next_actions": next_actions,
    }


def build_high_value_self_improvement_report(previous_assessment: dict, current_assessment: dict, summary: dict, strategy: dict) -> dict:
    previous = previous_assessment if isinstance(previous_assessment, dict) else {}
    current = current_assessment if isinstance(current_assessment, dict) else {}
    previous_evidence = previous.get("evidence") or {}
    current_evidence = current.get("evidence") or {}
    previous_dimensions = previous.get("dimensions") or {}
    current_dimensions = current.get("dimensions") or {}
    source_distribution = summary.get("source_distribution") or {}
    capability_findings = int(source_distribution.get("capability_gap") or 0)
    capability_probe_count = int(strategy.get("capability_gap_probe_count") or 0)

    def delta(now: float | int | None, old: float | int | None) -> float:
        return round(float(now or 0) - float(old or 0), 3)

    improvements = {
        "overall_score_delta": delta(current.get("overall_score"), previous.get("overall_score")),
        "oracle_coverage_delta": delta(current_evidence.get("oracle_coverage_rate"), previous_evidence.get("oracle_coverage_rate")),
        "oracle_depth_delta": delta(current_dimensions.get("oracle_depth"), previous_dimensions.get("oracle_depth")),
        "total_findings_delta": delta(current_evidence.get("total_findings"), previous_evidence.get("total_findings")),
        "s_or_a_delta": delta(current_evidence.get("s_or_a_tier_findings"), previous_evidence.get("s_or_a_tier_findings")),
        "cross_step_oracle_findings_delta": delta(current_evidence.get("cross_step_oracle_findings"), previous_evidence.get("cross_step_oracle_findings")),
        "business_context_findings_delta": delta(current_evidence.get("business_context_findings"), previous_evidence.get("business_context_findings")),
    }
    closed_loop_active = capability_probe_count > 0 and capability_findings > 0
    if improvements["overall_score_delta"] > 0 or improvements["oracle_coverage_delta"] > 0:
        verdict = "improving"
        verdict_label = "自优化有效"
    elif closed_loop_active:
        verdict = "learning_active"
        verdict_label = "已启动学习闭环"
    else:
        verdict = "needs_more_signal"
        verdict_label = "需要更多有效反馈"
    return {
        "version": "high_value_self_improvement_report_v1",
        "mode": "capability_gap_closed_loop",
        "verdict": verdict,
        "verdict_label": verdict_label,
        "previous_score": previous.get("overall_score"),
        "current_score": current.get("overall_score"),
        "previous_maturity": previous.get("maturity"),
        "current_maturity": current.get("maturity"),
        "improvements": improvements,
        "capability_gap_contribution": {
            "enabled": bool(strategy.get("high_value_capability_assessment_enabled")),
            "input_gap_count": int(strategy.get("high_value_capability_gap_count") or 0),
            "probe_count": capability_probe_count,
            "finding_count": capability_findings,
            "s_or_a_estimated": capability_findings,
            "finding_rate": round(capability_findings / capability_probe_count, 3) if capability_probe_count else 0,
        },
        "remaining_gaps": current.get("capability_gaps") or [],
        "next_actions": current.get("next_actions") or [],
    }


def build_high_value_capability_trend(previous_trend: dict, assessment: dict, self_improvement: dict, strategy: dict) -> dict:
    history = list((previous_trend or {}).get("history") or [])
    evidence = assessment.get("evidence") or {}
    contribution = self_improvement.get("capability_gap_contribution") or {}
    run_index = int((history[-1].get("run_index") if history else 0) or 0) + 1
    snapshot = {
        "run_index": run_index,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall_score": assessment.get("overall_score"),
        "maturity": assessment.get("maturity"),
        "maturity_label": assessment.get("maturity_label"),
        "total_findings": evidence.get("total_findings"),
        "s_or_a_tier_findings": evidence.get("s_or_a_tier_findings"),
        "cross_step_oracle_findings": evidence.get("cross_step_oracle_findings"),
        "oracle_coverage_rate": evidence.get("oracle_coverage_rate"),
        "oracle_depth": (assessment.get("dimensions") or {}).get("oracle_depth"),
        "business_context_findings": evidence.get("business_context_findings"),
        "issue_cluster_count": evidence.get("issue_cluster_count"),
        "blocker_cluster_count": evidence.get("blocker_cluster_count"),
        "capability_gap_probe_count": contribution.get("probe_count") or strategy.get("capability_gap_probe_count"),
        "capability_gap_finding_count": contribution.get("finding_count"),
        "self_improvement_verdict": self_improvement.get("verdict"),
    }
    history.append(snapshot)
    history = history[-30:]

    def trend_delta(key: str, window: int) -> float:
        if len(history) < 2:
            return 0.0
        start = history[max(0, len(history) - window)].get(key)
        end = history[-1].get(key)
        try:
            return round(float(end or 0) - float(start or 0), 3)
        except Exception:
            return 0.0

    score_delta_last = trend_delta("overall_score", 2)
    oracle_delta_last = trend_delta("oracle_coverage_rate", 2)
    score_delta_5 = trend_delta("overall_score", 5)
    oracle_delta_5 = trend_delta("oracle_coverage_rate", 5)
    if score_delta_last > 0 or oracle_delta_last > 0:
        trend = "up"
        trend_label = "能力上升"
    elif score_delta_last < 0 or oracle_delta_last < 0:
        trend = "down"
        trend_label = "能力下降"
    else:
        trend = "stable"
        trend_label = "能力稳定"
    return {
        "version": "high_value_capability_trend_v1",
        "history_count": len(history),
        "latest": snapshot,
        "trend": trend,
        "trend_label": trend_label,
        "deltas": {
            "last_run_score_delta": score_delta_last,
            "last_run_oracle_coverage_delta": oracle_delta_last,
            "last_5_runs_score_delta": score_delta_5,
            "last_5_runs_oracle_coverage_delta": oracle_delta_5,
        },
        "history": history,
    }


def build_oracle_coverage_summary(probes: list[dict], bugs: list[dict]) -> dict:
    required = [
        p
        for p in probes
        if p.get("risk_type") in ORACLE_REQUIRED_RISKS and (p.get("steps") or p.get("source") in {"business_knowledge", "business_adaptation_layer", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "high_value_memory"})
    ]
    covered = [p for p in required if p.get("cross_step_oracle")]
    gap_probes = [p for p in probes if p.get("source") == "oracle_gap"]
    by_risk: dict[str, dict] = {}
    for p in required:
        risk = str(p.get("risk_type") or "unknown")
        item = by_risk.setdefault(risk, {"risk_type": risk, "required_probe_count": 0, "oracle_probe_count": 0, "gap_probe_count": 0, "finding_count": 0})
        item["required_probe_count"] += 1
        if p.get("cross_step_oracle"):
            item["oracle_probe_count"] += 1
        if p.get("source") == "oracle_gap":
            item["gap_probe_count"] += 1
    for bug in bugs:
        risk = str(bug.get("risk_type") or "unknown")
        if risk not in by_risk:
            continue
        if bug.get("high_value_profile", {}).get("has_cross_step_oracle"):
            by_risk[risk]["finding_count"] += 1
    for item in by_risk.values():
        total = max(1, int(item["required_probe_count"]))
        item["oracle_coverage_rate"] = round(item["oracle_probe_count"] / total, 3)
    total_required = len(required)
    total_covered = len(covered)
    return {
        "version": "oracle_coverage_summary_v1",
        "required_probe_count": total_required,
        "oracle_probe_count": total_covered,
        "oracle_coverage_rate": round(total_covered / total_required, 3) if total_required else 0,
        "oracle_gap_probe_count": len(gap_probes),
        "cross_step_oracle_findings": sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("has_cross_step_oracle")),
        "covered_risk_count": sum(1 for item in by_risk.values() if item.get("oracle_probe_count", 0) > 0),
        "risk_coverage": sorted(by_risk.values(), key=lambda item: (item["oracle_coverage_rate"], -item["required_probe_count"], item["risk_type"])),
        "top_gaps": [
            {
                "risk_type": item["risk_type"],
                "required_probe_count": item["required_probe_count"],
                "oracle_probe_count": item["oracle_probe_count"],
                "oracle_coverage_rate": item["oracle_coverage_rate"],
            }
            for item in sorted(by_risk.values(), key=lambda item: (item["oracle_coverage_rate"], -item["required_probe_count"]))[:8]
            if item["oracle_coverage_rate"] < 1
        ],
    }


def build_high_value_attack_plan(bugs: list[dict], summary: dict, oracle_coverage: dict, risk_profile: dict) -> dict:
    risk_rows: dict[str, dict] = {}
    for risk, count in (summary.get("risk_distribution") or {}).items():
        risk_rows.setdefault(str(risk), {"risk_type": str(risk), "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
        risk_rows[str(risk)]["finding_count"] = int(count or 0)
    for bug in bugs:
        risk = str(bug.get("risk_type") or "unknown")
        row = risk_rows.setdefault(risk, {"risk_type": risk, "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
        if bug.get("value_tier") in {"S", "A"}:
            row["s_or_a_count"] += 1
        row["max_score"] = max(int(row.get("max_score") or 0), int(bug.get("bug_value_score") or 0))
    for item in risk_profile.get("priority_risks") or []:
        risk = str(item.get("name") or "")
        if risk:
            risk_rows.setdefault(risk, {"risk_type": risk, "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
            risk_rows[risk]["learned_weight"] = int(item.get("weight") or 0)
    for item in oracle_coverage.get("risk_coverage") or []:
        risk = str(item.get("risk_type") or "")
        if not risk:
            continue
        row = risk_rows.setdefault(risk, {"risk_type": risk, "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
        rate = float(item.get("oracle_coverage_rate") or 0)
        required = int(item.get("required_probe_count") or 0)
        covered = int(item.get("oracle_probe_count") or 0)
        row["oracle_coverage_rate"] = rate
        row["oracle_gap_count"] = max(0, required - covered)

    action_labels = {
        "permission_bypass": "扩展横向/纵向权限绕过链路，覆盖普通用户、匿名用户、降权后缓存权限。",
        "auth_bypass": "补未登录和锁定账号链路，验证所有受保护资源的登录态门禁。",
        "idor": "扩展跨用户资源 ID 替换和状态变更链路，覆盖读、改、取消、导出。",
        "tenant_isolation": "扩展跨租户读写链路，验证查询参数、请求体和缓存隔离。",
        "money_consistency": "增强金额守恒 Oracle，覆盖支付、优惠、退款、流水、余额和舍入误差。",
        "stock_consistency": "增强库存/容量守恒 Oracle，覆盖扣减、回滚、重复提交和并发边界。",
        "state_flow": "增强状态机非法流转探针，覆盖取消后支付、终态重开、回调乱序。",
        "state_consistency": "增强动作后查询一致性探针，覆盖支付、退款、审批、异步状态同步。",
        "coupon_abuse": "增强权益滥用探针，覆盖重复抵扣、归属、门槛、过期和超额优惠。",
        "idempotency": "增强幂等重放探针，覆盖重复下单、重复回调、重复退款和幂等键冲突。",
    }

    focus_items = []
    for row in risk_rows.values():
        gap_factor = int((1.0 - float(row.get("oracle_coverage_rate", 1.0))) * 40)
        score = int(row.get("s_or_a_count") or 0) * 8 + int(row.get("finding_count") or 0) * 2 + int(row.get("learned_weight") or 0) + int(row.get("max_score") or 0) // 10 + gap_factor + int(row.get("oracle_gap_count") or 0)
        if score <= 0:
            continue
        risk = row["risk_type"]
        gap_types = []
        if float(row.get("oracle_coverage_rate", 1.0)) < 0.8:
            gap_types.append("oracle_coverage_gap")
        if int(row.get("s_or_a_count") or 0) >= 3:
            gap_types.append("high_value_cluster")
        if int(row.get("learned_weight") or 0) >= 10:
            gap_types.append("learning_weight_hotspot")
        if int(row.get("oracle_gap_count") or 0) > 0:
            gap_types.append("probe_assertion_gap")
        focus_items.append({
            **row,
            "priority_score": score,
            "priority": "P0" if score >= 80 else "P1" if score >= 45 else "P2",
            "gap_types": gap_types or ["continuous_regression_focus"],
            "recommended_next_probe_count": max(2, min(12, 2 + int(row.get("oracle_gap_count") or 0) + int(row.get("s_or_a_count") or 0) // 4)),
            "recommended_action": action_labels.get(risk, "基于当前 PRD/OpenAPI 继续扩展业务链路 Oracle 和异常边界探针。"),
        })
    focus_items.sort(key=lambda item: (item["priority_score"], item["s_or_a_count"], item["oracle_gap_count"]), reverse=True)
    return {
        "version": "high_value_attack_plan_v1",
        "mode": "closed_loop_gap_to_probe_strategy",
        "total_focus_risks": len(focus_items),
        "top_focus": focus_items[:10],
        "next_run_probe_budget": sum(int(item["recommended_next_probe_count"]) for item in focus_items[:6]),
        "quality_bar": {
            "target_oracle_coverage_rate": 0.9,
            "target_cross_step_oracle_findings": max(int((summary or {}).get("cross_step_oracle_findings") or 0), 1),
            "block_release_when_p0_oracle_fails": True,
        },
        "executive_summary": [
            f"下一轮优先攻击 {item['risk_type']}：{item['recommended_action']}"
            for item in focus_items[:5]
        ],
    }


def safe_pattern_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").upper())
    while "__" in token:
        token = token.replace("__", "_")
    return token.strip("_")[:80] or "UNKNOWN"


def build_high_value_pattern_memory(bugs: list[dict]) -> dict:
    high_value = [bug for bug in bugs if bug.get("value_tier") in {"S", "A"}]
    risk_weights: dict[str, int] = {}
    api_weights: dict[str, int] = {}
    source_weights: dict[str, int] = {}
    for bug in high_value:
        risk = str(bug.get("risk_type") or "unknown")
        api = str(bug.get("affected_api") or (bug.get("related_apis") or ["unknown"])[0])
        source = str(bug.get("probe_source") or "unknown")
        score = int(bug.get("bug_value_score") or 0)
        weight = 3 if bug.get("value_tier") == "S" else 2
        if score >= 95:
            weight += 1
        risk_weights[risk] = risk_weights.get(risk, 0) + weight
        api_weights[api] = api_weights.get(api, 0) + weight
        source_weights[source] = source_weights.get(source, 0) + weight

    top_patterns = []
    for bug in sorted(high_value, key=lambda item: item.get("bug_value_score", 0), reverse=True)[:36]:
        api = str(bug.get("affected_api") or (bug.get("related_apis") or [""])[0])
        risk = str(bug.get("risk_type") or "unknown")
        top_patterns.append(
            {
                "pattern_id": f"MEM_{safe_pattern_token(risk)}_{safe_pattern_token(api)}",
                "risk_type": risk,
                "api_template": api,
                "affected_api": api,
                "title": bug.get("title"),
                "value_tier": bug.get("value_tier"),
                "bug_value_score": bug.get("bug_value_score"),
                "probe_source": bug.get("probe_source"),
                "business_object": bug.get("business_object"),
                "reasons": bug.get("high_value_profile", {}).get("reasons", []),
                "recommended_probe_variants": 3 if bug.get("value_tier") == "S" else 2,
            }
        )
    recommendations = [
        f"下一轮优先覆盖 {risk} 风险，当前历史权重 {weight}"
        for risk, weight in sorted(risk_weights.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    return {
        "version": "high_value_pattern_memory_v1",
        "source": "discovered_high_value_findings",
        "pattern_count": len(top_patterns),
        "risk_weights": dict(sorted(risk_weights.items(), key=lambda item: item[1], reverse=True)),
        "api_weights": dict(sorted(api_weights.items(), key=lambda item: item[1], reverse=True)),
        "source_weights": dict(sorted(source_weights.items(), key=lambda item: item[1], reverse=True)),
        "top_patterns": top_patterns,
        "next_run_recommendations": recommendations,
    }


def build_risk_learning_profile(bugs: list[dict], summary: dict, strategy: dict, memory: dict) -> dict:
    high_value = [bug for bug in bugs if bug.get("value_tier") in {"S", "A"}]
    risk_weights: dict[str, int] = dict(memory.get("risk_weights") or {})
    api_weights: dict[str, int] = dict(memory.get("api_weights") or {})
    module_weights: dict[str, int] = {}
    oracle_weights: dict[str, int] = {}
    for bug in high_value:
        tier = bug.get("value_tier")
        score = int(bug.get("bug_value_score") or 0)
        weight = 5 if tier == "S" else 3
        if score >= 95:
            weight += 2
        risk = str(bug.get("risk_type") or "unknown")
        api = str(bug.get("affected_api") or (bug.get("related_apis") or ["unknown"])[0])
        module = str(bug.get("business_object") or "通用业务对象")
        risk_weights[risk] = risk_weights.get(risk, 0) + weight
        api_weights[api] = api_weights.get(api, 0) + weight
        module_weights[module] = module_weights.get(module, 0) + weight
        if bug.get("high_value_profile", {}).get("has_cross_step_oracle"):
            oracle_weights[risk] = oracle_weights.get(risk, 0) + weight

    def top_items(items: dict[str, int], limit: int = 8) -> list[dict]:
        return [{"name": name, "weight": weight} for name, weight in sorted(items.items(), key=lambda item: item[1], reverse=True)[:limit]]

    priority_risks = top_items(risk_weights)
    priority_modules = top_items(module_weights)
    recommendations = [f"下一轮优先攻击 {item['name']}，历史高价值权重 {item['weight']}" for item in priority_risks[:5]]
    recommendations.extend(f"重点覆盖业务对象/模块：{item['name']}，优先组合权限、数据一致性和状态流转探针" for item in priority_modules[:3])
    if oracle_weights:
        recommendations.append("保留跨步骤业务 Oracle：这些风险已出现链路级证据，优先作为发布门禁阻断条件")
    return {
        "version": "enterprise_high_value_risk_learning_v1",
        "mode": "closed_loop_probe_generation",
        "learned_from_findings": len(high_value),
        "memory_pattern_count": int(memory.get("pattern_count") or 0),
        "probe_count": strategy.get("probe_count") or 0,
        "memory_probe_count": strategy.get("high_value_memory_probe_count") or 0,
        "semantic_expansion_probe_count": strategy.get("high_value_memory_expansion_probe_count") or 0,
        "cross_step_oracle_findings": (summary or {}).get("cross_step_oracle_findings", 0),
        "priority_risks": priority_risks,
        "priority_apis": top_items(api_weights),
        "priority_modules": priority_modules,
        "oracle_priority_risks": top_items(oracle_weights, 5),
        "next_run_recommendations": recommendations[:10],
    }


def value_score(severity: str, confidence: float, item: dict) -> int:
    base = {"P0": 88, "P1": 76, "P2": 58}.get(severity, 45)
    completeness = 8 if item["response"]["body"] is not None else 3
    return min(100, int(base + completeness + confidence * 4))


def roi_metrics(probes: int, bugs: int) -> dict:
    return {"estimated_test_design_hours_saved": round(probes * 0.45, 1), "estimated_bug_report_hours_saved": round(bugs * 0.5, 1), "manual_probe_hours_avoided": round(probes * 0.7, 1)}


def build_bug_drafts(bugs: list[dict]) -> str:
    lines = ["# 缺陷草稿", ""]
    for bug in bugs:
        lines += [f"## {bug['title']}", f"- Severity: {bug['severity']}", f"- Score: {bug['bug_value_score']}", f"- API: {', '.join(bug['related_apis'])}", f"- Expected: {bug['expected']}", f"- Actual: {bug['actual']}", ""]
    return "\n".join(lines)


def build_report(data: dict) -> str:
    bugs = data["discovered_bugs"]
    mode = data.get("discovery_mode", "blind")
    model = data.get("business_model", {})
    rows = "\n".join(
        f"<tr><td>{b.get('value_tier','-')}</td><td>{b['severity']}</td><td>{b['title']}</td><td>{b['risk_type']}</td><td>{b['bug_value_score']}</td><td>{', '.join((b.get('high_value_profile') or {}).get('reasons', [])[:3])}</td><td>{b['confidence']}</td></tr>"
        for b in bugs
    )
    coverage = data.get("scenario_coverage", {})
    hv = data.get("high_value_summary", {})
    knowledge_note = f"业务知识增强：{'已启用' if model.get('business_knowledge_enabled') else '未启用'}，模块 {model.get('business_knowledge_modules', 0)}，规则 {model.get('business_knowledge_rules', 0)}，风险 {model.get('business_knowledge_risks', 0)}，业务知识探针 {data.get('business_knowledge_probe_count', 0)}。"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>缺陷发现报告</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}.card{{border:1px solid #d8dee9;padding:14px;border-radius:8px;background:#fff}}table{{border-collapse:collapse;width:100%;margin-top:18px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left;vertical-align:top}}.note{{background:#f8fafc;border:1px solid #d8dee9;padding:14px;border-radius:8px;margin:18px 0}}</style></head><body><h1>AI 高价值缺陷发现报告</h1><div class="note"><b>Discovery Mode：</b>{mode}。运行时探针仅来自当前项目资料与策略数据。</div><div class="note"><b>单输入自动理解：</b>系统从公开需求文档、接口文档和测试账号自动识别行业、业务对象、业务场景、操作、不变量、语义图、状态机和数据血缘；无需人工维护行业知识包。识别行业：{model.get('industry','-')}，业务场景：{model.get('business_scenarios','-')}，业务对象：{model.get('objects','-')}，操作：{model.get('operations','-')}，自动不变量：{model.get('auto_invariants','-')}，语义边：{model.get('semantic_graph_edges','-')}，状态机：{model.get('state_machines','-')}，数据血缘：{model.get('data_lineage','-')}。</div><div class="note"><b>{knowledge_note}</b></div><div class="kpi"><div class="card">场景覆盖率<br><b>{coverage.get('coverage_rate','-')}</b></div><div class="card">场景可执行率<br><b>{coverage.get('executable_rate','-')}</b></div><div class="card">阻塞场景<br><b>{coverage.get('blocked_scenarios','-')}</b></div><div class="card">缺陷探针<br><b>{data['probes']}</b></div><div class="card">业务知识探针<br><b>{data.get('business_knowledge_probe_count', 0)}</b></div><div class="card">发现缺陷<br><b>{len(bugs)}</b></div><div class="card">业务知识命中<br><b>{hv.get('business_context_findings', 0)}</b></div><div class="card">链路证据缺陷<br><b>{hv.get('journey_evidence_findings', 0)}</b></div><div class="card">S/A 级缺陷<br><b>{hv.get('s_or_a_tier_findings', 0)}</b></div><div class="card">节省工时<br><b>{data['roi_metrics']['estimated_test_design_hours_saved']}</b></div></div><h2>发现列表</h2><table><tr><th>价值层级</th><th>严重级别</th><th>标题</th><th>风险</th><th>价值分</th><th>评分原因</th><th>置信度</th></tr>{rows}</table></body></html>"""


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from ._runner import DefectDiscoveryRunner  # lazy: avoid circular import
    import argparse
    parser = argparse.ArgumentParser(description="Run AI defect discovery")
    parser.add_argument("--project", default=os.environ.get("PROJECT", "enterprise_shop"))
    parser.add_argument("--public-artifacts", default=os.environ.get("PUBLIC_ARTIFACTS", "enterprise_bug_factory/public_artifacts"))
    parser.add_argument("--discovery-mode", default=os.environ.get("DEFECT_DISCOVERY_MODE", "blind"))
    args = parser.parse_args(argv)
    runner = DefectDiscoveryRunner(DiscoveryConfig(project=args.project, public_artifacts=Path(args.public_artifacts), discovery_mode=args.discovery_mode))
    data = runner.run()
    print(json.dumps({"project": data.get("project"), "discovery_mode": data.get("discovery_mode"), "probes": data.get("probes"), "discovered_bugs": len(data.get("discovered_bugs", []))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
