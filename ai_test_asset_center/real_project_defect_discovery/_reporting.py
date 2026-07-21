"""Reporting: bug drafts, report rendering, CLI."""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403
from ._helpers import *  # noqa: F401,F403
from ._runner import *  # noqa: F401,F403



def _impact_for_risk(risk: str) -> str:
    return {
        "permission_bypass": "可能造成后台数据或敏感操作越权。",
        "idor": "可能造成用户数据泄露或越权操作。",
        "tenant_isolation": "可能造成跨租户数据泄露。",
        "coupon_abuse": "可能造成营销资金损失。",
        "stock_consistency": "可能造成超卖或库存账实不一致。",
        "payment": "可能造成资损、重复入账或订单状态不一致。",
        "refund": "可能造成重复退款、超额退款或账务不一致。",
        "idempotency": "可能造成重复订单、重复扣款或重复库存扣减。",
        "cross_system": "可能造成订单、库存、客户、财务等跨系统数据不一致。",
        "cross_system_oracle": "可能造成跨系统同步或对账错误，影响履约和经营决策。",
        "page_api_oracle": "可能造成页面展示与真实业务数据不一致。",
        "exception_path": "可能造成非法输入被静默忽略，掩盖业务风险或产生错误结果。",
        "historical_data_path": "可能造成存量数据丢失、迁移损坏或历史记录不可读取。",
        "async_result_consistency": "可能造成异步任务显示成功但没有结果、文件或业务产物，影响导入导出、同步和批处理。",
        "async_idempotency": "可能造成重复回调、重复入账、重复文件或重复同步结果。",
        "read_model_consistency": "可能造成缓存、索引、搜索、看板或读模型与事实源不一致。",
        "read_model_staleness": "可能造成关键业务变更长期未在缓存、索引或页面生效。",
        "read_stability": "可能造成无业务变更下页面或查询结果短时间内漂移。",
        "business_cohort_limit": "可能造成额度、预算、库存、名额或次数被拆分绕过，带来资损或资源超配。",
        "business_interval_overlap": "可能造成房间、人员、库存、车辆或预约资源被重复占用。",
        "business_composite_duplicate": "可能造成同一业务事实以不同主键重复写入，引发重复扣费、重复履约或重复报表。",
        "business_batch_integrity": "可能造成批量导入、同步、迁移或任务显示成功但实际漏处理、重复处理或统计失真。",
        "business_approval_threshold": "可能造成高风险、高金额业务绕过审批门禁，带来内控与资金风险。",
    }.get(risk, "可能影响核心业务质量，需要 QA 结合证据确认。")


def _fix_for_risk(risk: str) -> str:
    return {
        "permission_bypass": "增加 RBAC / ABAC 权限校验，并在后端强制执行。",
        "idor": "校验资源归属，避免仅依赖前端隐藏入口。",
        "tenant_isolation": "所有查询和写操作增加 tenant_id 隔离条件。",
        "coupon_abuse": "校验优惠券归属、有效期、门槛和单次使用限制。",
        "stock_consistency": "下单链路增加库存锁定、扣减、回滚和并发保护。",
        "payment": "校验支付金额一致性和回调幂等键。",
        "refund": "校验退款状态机、退款上限和幂等处理。",
        "idempotency": "使用幂等键保护订单、支付、退款和库存变更。",
        "cross_system_oracle": "统一跨系统主键、同步幂等与对账口径，并将 Oracle 纳入持续回归。",
        "page_api_oracle": "统一页面指标与 API 绑定口径，避免缓存/聚合和展示来源漂移。",
        "exception_path": "对非法参数返回明确 4xx 错误，禁止静默回退到未过滤查询。",
        "historical_data_path": "建立迁移映射和历史兼容回归，确保关键历史字段与记录可读取。",
        "async_result_consistency": "统一任务成功终态与结果产物写入，补充结果完整性校验、失败诊断和回归 Oracle。",
        "async_idempotency": "为回调、消息消费和任务完成引入幂等键、去重表和唯一约束，并在沙箱做并发回归。",
        "read_model_consistency": "修复消息投递、投影更新和缓存失效流程，并持续对账事实源与读模型。",
        "read_model_staleness": "设置最终一致性 SLA、延迟监控和超时补偿，避免关键变更长期滞后。",
        "read_stability": "梳理缓存键、失效条件与多副本读路径，避免同一查询无故返回不同业务字段。",
        "business_cohort_limit": "在数据库与服务层同时执行按主体/窗口的聚合限额，配合锁、幂等键和审批门禁防止拆分绕过。",
        "business_interval_overlap": "引入区间排他约束或资源占用锁，并在提交时按资源与时间区间原子校验容量。",
        "business_composite_duplicate": "为真实业务键建立复合唯一索引，并在服务层使用幂等键与重复写入冲突处理。",
        "business_batch_integrity": "将 total、processed、success、failed 置于同一可验证状态机，终态前强制做算术闭合和明细对账。",
        "business_approval_threshold": "在后端金额门禁、状态机与审批凭据校验中强制执行阈值规则，不能只依赖前端流程。",
    }.get(risk, "补充后端业务规则校验，并增加回归探针。")


def _render_bug_drafts(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "# 缺陷草稿\n\n本次未生成疑似缺陷。\n"
    parts = ["# 真实项目疑似缺陷草稿\n"]
    for issue in issues:
        parts.append(f"## {issue.get('severity')} {issue.get('title')}\n")
        parts.append(f"- 风险类型：{issue.get('risk_type')}\n- 置信度：{issue.get('confidence')}\n- 期望：{issue.get('expected')}\n- 实际：{issue.get('actual')}\n- 影响：{issue.get('business_impact')}\n- 建议：{issue.get('suggested_fix')}\n")
    return "\n".join(parts)


def render_real_project_report(data: dict[str, Any]) -> str:
    metrics = data.get("metrics", {})
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in metrics.items())
    issues = data.get("issues", [])
    rows = []
    for i in issues[:80]:
        rows.append(f"<tr><td>{_html_escape(i.get('severity'))}</td><td>{_html_escape(i.get('title'))}</td><td>{_html_escape(i.get('risk_type'))}</td><td>{_html_escape(i.get('confidence'))}</td><td>{_html_escape(i.get('actual'))}</td></tr>")
    risk = "".join(f"<li>{_html_escape(k)}：{_html_escape(v)}</li>" for k, v in (data.get("risk_distribution") or {}).items())
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>真实项目缺陷发现报告</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:22px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfeff;color:#155e75}}</style></head><body>
<section class='hero'><span class='badge'>Real Project Discovery</span><h1>{_html_escape(data.get('project_name'))}</h1><p>真实项目模式不使用 ground truth，不展示 recall / precision。本报告展示疑似高价值 Bug、证据完整度、风险分布和 QA 待确认项。</p><p>发现模式：<b>{_html_escape(data.get('mode'))}</b> · 生成时间：{_html_escape(data.get('generated_at'))}</p></section>
<section class='panel'><h2>质量概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>风险分布</h2><ul>{risk or '<li>暂无风险</li>'}</ul></section>
<section class='panel'><h2>业务适配与链路图谱</h2><p>业务域：{_html_escape(', '.join([str(x.get('domain')) for x in ((data.get('business_adaptation_profile') or {}).get('selected_domains') or [])]) or 'auto')} · 自适应探针数：{_html_escape(data.get('business_adaptive_probe_count', 0))} · 企业知识库探针数：{_html_escape(data.get('enterprise_knowledge_probe_count', 0))} · 通用规格行为探针数：{_html_escape(data.get('universal_defect_probe_count', 0))} · 业务结果审计探针数：{_html_escape(data.get('business_outcome_probe_count', 0))} · 业务结果问题：{_html_escape(data.get('business_outcome_finding_count', 0))} · 业务对账探针数：{_html_escape(data.get('business_reconciliation_probe_count', 0))} · 业务对账问题：{_html_escape(data.get('business_reconciliation_finding_count', 0))} · 业务不变量探针数：{_html_escape(data.get('business_invariant_probe_count', 0))} · 业务不变量问题：{_html_escape(data.get('business_invariant_finding_count', 0))} · 多源推理契约：{_html_escape(data.get('multi_source_reasoning_contract_count', 0))} · 多源推理问题：{_html_escape(data.get('multi_source_reasoning_finding_count', 0))} · 生命周期契约：{_html_escape(data.get('business_lifecycle_contract_count', 0))} · 生命周期问题：{_html_escape(data.get('business_lifecycle_finding_count', 0))} · 生命周期沙箱候选：{_html_escape(data.get('business_lifecycle_sandbox_candidate_count', 0))} · 一致性/隔离契约：{_html_escape(data.get('consistency_isolation_contract_count', 0))} · 角色权限契约：{_html_escape(data.get('consistency_isolation_role_access_contract_count', 0))} · 一致性/隔离问题：{_html_escape(data.get('consistency_isolation_finding_count', 0))} · 一致性沙箱候选：{_html_escape(data.get('consistency_isolation_sandbox_candidate_count', 0))} · 变形差分契约：{_html_escape(data.get('metamorphic_differential_contract_count', 0))} · 变形差分问题：{_html_escape(data.get('metamorphic_differential_finding_count', 0))} · 时间数据回归契约：{_html_escape(data.get('temporal_data_regression_contract_count', 0))} · 时间数据回归问题：{_html_escape(data.get('temporal_data_regression_finding_count', 0))} · 业务因果契约：{_html_escape(data.get('business_causality_contract_count', 0))} · 账务双分录契约：{_html_escape(data.get('business_causality_journal_balance_contract_count', 0))} · 账期滚动契约：{_html_escape(data.get('business_causality_period_rollforward_contract_count', 0))} · 库存预占契约：{_html_escape(data.get('business_causality_inventory_reservation_contract_count', 0))} · 业务因果问题：{_html_escape(data.get('business_causality_finding_count', 0))} · 群体业务契约：{_html_escape(data.get('business_population_contract_count', 0))} · 群体业务问题：{_html_escape(data.get('business_population_finding_count', 0))} · 事件链契约：{_html_escape(data.get('business_event_chain_contract_count', 0))} · 事件链问题：{_html_escape(data.get('business_event_chain_finding_count', 0))} · Saga补偿契约：{_html_escape(data.get('business_saga_compensation_contract_count', 0))} · Saga补偿问题：{_html_escape(data.get('business_saga_compensation_finding_count', 0))} · 已确认缺陷记忆：{_html_escape(data.get('confirmed_bug_memory_count', 0))} · 多接口链路探针数：{_html_escape(data.get('business_flow_scenario_probe_count', 0))} · 链路断言候选问题：{_html_escape(data.get('business_flow_execution_candidate_issue_count', 0))} · Phase40证据包：{_html_escape(data.get('replay_evidence_packet_count', 0))}</p></section>
<section class='panel'><h2>企业业务知识中心</h2><p>接入资料：{_html_escape(data.get('enterprise_business_knowledge_source_count', 0))} · 规则库：{_html_escape(data.get('enterprise_business_knowledge_rule_count', 0))} · Oracle：{_html_escape(data.get('enterprise_business_knowledge_oracle_count', 0))} · 资料到验证关联：{_html_escape(data.get('enterprise_business_knowledge_relationship_count', 0))} · 知识探针：{_html_escape(data.get('enterprise_business_knowledge_probe_count', 0))}</p><p>验证链路保留 PRD/MRD/接口/表结构/权限矩阵/历史缺陷的版本与来源指纹；原始资料不嵌入缺陷报告和证据摘要。</p></section>
<section class='panel'><h2>企业 TestOps 控制平面</h2><p>目标环境可测：{_html_escape((data.get('enterprise_testops_control_plane') or {}).get('preflight', {}).get('environment_testable'))} · 自动数据准备比例：{_html_escape((data.get('enterprise_testops_control_plane') or {}).get('preflight', {}).get('automatic_data_preparation_ratio'))} · 跨系统 Journey：{_html_escape((data.get('enterprise_testops_control_plane') or {}).get('preflight', {}).get('journey_count'))} · 高置信缺陷：{_html_escape((data.get('enterprise_testops_control_plane') or {}).get('defect_quality_summary', {}).get('high_confidence_count'))} · 环境问题（不计入业务 Bug）：{_html_escape((data.get('enterprise_testops_control_plane') or {}).get('defect_quality_summary', {}).get('environment_problem_count'))} · 去重压缩率：{_html_escape((data.get('enterprise_testops_control_plane') or {}).get('defect_quality_summary', {}).get('duplicate_compression_rate'))}</p><p>探针与缺陷均可追溯到企业资料、接口、状态机、权限边界和业务 Oracle；数据库/审计/异步状态验证以受控适配器或只读查询接入。</p></section>
<section class='panel'><h2>多行业业务理解</h2><p>识别行业：{_html_escape(', '.join((data.get('multi_industry_business_profile') or {}).get('summary', {}).get('recognized_industries') or []) or 'unknown_general_business')} · 对象：{_html_escape((data.get('multi_industry_business_profile') or {}).get('summary', {}).get('business_object_count', 0))} · 状态机：{_html_escape((data.get('multi_industry_business_profile') or {}).get('summary', {}).get('state_machine_count', 0))} · 权限边界：{_html_escape((data.get('multi_industry_business_profile') or {}).get('summary', {}).get('permission_boundary_count', 0))} · 行业 Oracle：{_html_escape((data.get('multi_industry_business_profile') or {}).get('summary', {}).get('oracle_count', 0))} · 高价值风险域：{_html_escape((data.get('multi_industry_business_profile') or {}).get('summary', {}).get('risk_domain_count', 0))} · 行业探针：{_html_escape(data.get('multi_industry_business_probe_count', 0))}</p></section>
<section class='panel'><h2>疑似高价值 Bug</h2><table><thead><tr><th>等级</th><th>标题</th><th>风险</th><th>置信度</th><th>实际结果</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5">暂无疑似缺陷</td></tr>'}</tbody></table></section>
<section class='panel'><h2>下一步</h2><p>请进入 QA 反馈评审，确认有效 / 误报 / 重复 / 低价值，并把反馈用于下一轮策略增强。</p></section></body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    data = run_real_project_discovery(project)
    print(json.dumps({"ok": True, "project_id": data.get("project_id"), "metrics": data.get("metrics")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
