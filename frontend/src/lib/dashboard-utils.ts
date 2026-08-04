/**
 * Dashboard utility functions and constants.
 * Extracted from Dashboard.tsx for better maintainability.
 */
import type { CommercialAssets, Finding } from '../types';
import { asNum, asRecord, asText } from './value-guards';

export type JsonRecord = Record<string, unknown>;

// ─── Constants ───────────────────────────────────────────────────────────────

export const MAIN_CHAIN_STAGE_LABELS: Record<string, string> = {
  enterprise_inputs: '企业资料', knowledge_parse: '解析知识', test_plan: '测试计划',
  execution: '真实执行', bug_discovery: 'Bug 发现', evidence_chain: '证据链',
};

export const MAIN_CHAIN_STATUS_LABELS: Record<string, string> = {
  passed: '通过', partial: '部分', missing: '缺失',
};

export const EVIDENCE_MISSING_FIELD_LABELS: Record<string, string> = {
  issue_id: '稳定 issue_id', request: '原始 request', response: '原始 response',
  expected: 'expected', actual: 'actual', reproduction_or_replay: 'reproduction / replay',
  execution_receipt: 'execution_receipt', non_synthetic_evidence: '非 synthetic 证据',
  missing_raw_request: '原始 request', missing_raw_response: '原始 response',
  missing_expected_actual_pair: 'expected + actual', missing_replay_or_reproduction: 'reproduction / replay',
  missing_execution_receipt: 'execution_receipt', synthetic_evidence_present: 'synthetic 证据',
  strict_evidence_not_linked_to_all_issues: '严格证据未覆盖全部 issue',
};

export const FUNNEL_STAGE_LABELS: Record<string, string> = {
  candidate_generation: '候选生成', probe_selection: '探针入选', execution: '执行',
  verification: '验证', formal_accounting: '正式记账',
};

// ─── Generic helpers ─────────────────────────────────────────────────────────
// 守卫实现统一收敛于 ./value-guards（全仓唯一来源），此处保留再导出兼容既有调用方。

export { asRecord, asText, asNum } from './value-guards';

export function firstNum(...values: unknown[]): number {
  for (const v of values) {
    const p = typeof v === 'number' ? v : Number(v);
    if (Number.isFinite(p)) return p;
  }
  return 0;
}

export function formatScanTime(value: string): string {
  if (!value) return '暂无';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString('zh-CN', { hour12: false });
}

// ─── Finding helpers ─────────────────────────────────────────────────────────

export function getSeverityWeight(s: Finding['severity']): number {
  if (s === 'P0') return 3;
  if (s === 'P1') return 2;
  return 1;
}

export function getFindingModule(f: Finding): string {
  return String(f.business_impact?.module || f.source_entity || f.defect_family_label || '核心业务').trim() || '核心业务';
}

// ─── Risk / Release decision ─────────────────────────────────────────────────

export function riskLevel(conclusion: string): 'safe' | 'attention' | 'blocked' {
  if (conclusion.includes('阻断') || conclusion.includes('暂停') || conclusion.includes('异常') || conclusion.includes('阻塞')) return 'blocked';
  if (conclusion.includes('整改') || conclusion.includes('关注') || conclusion.includes('递延') || conclusion.includes('未完成')) return 'attention';
  return 'safe';
}

export function releaseDecision(p0: number, defects: number, unhealthy: boolean, blocked: boolean): { color: 'red' | 'yellow' | 'green'; label: string; advice: string } {
  if (unhealthy || blocked) return { color: 'yellow', label: '待确认', advice: '检测流程尚未完成，暂不能形成发布结论' };
  if (p0 > 0) return { color: 'red', label: '建议阻断', advice: `${p0} 个严重问题需优先修复，建议暂停发布` };
  if (defects > 0) return { color: 'yellow', label: '有条件发布', advice: `${defects} 个已确认问题建议评估后决策` };
  return { color: 'green', label: '可以发布', advice: '当前未发现阻断性问题，可正常推进发布' };
}

// ─── Campaign helpers ────────────────────────────────────────────────────────

export function campaignStatusLabel(s: string): string {
  if (s === 'blocked') return '检测暂停（需补充条件）';
  if (s === 'coverage_deferred') return '部分范围待后续检测';
  if (s === 'completed') return '本轮检测已完成';
  if (s === 'active') return '检测进行中';
  return '检测状态待同步';
}

export function campaignDetail(s: string, reason: string, next: string): string {
  if (s === 'blocked') return `当前检测未进入执行阶段：${reason || '缺少必要条件'}。${next ? ` 下一步：${next}。` : ''}`;
  if (s === 'coverage_deferred') return `自动范围已到边界，剩余覆盖被明确递延。${reason ? ` 原因：${reason}。` : ''}`;
  if (s === 'completed') return '本轮可执行范围已完成。';
  if (s === 'active') return '当前检测仍在执行中。';
  return '当前没有可用的检测治理状态。';
}

// ─── Commercial / Delivery helpers ──────────────────────────────────────────

export function commercialHandoffLabel(a: CommercialAssets | null): string {
  const s = a?.commercial_handoff.status || a?.status || '';
  if (s === 'commercial_handoff_ready_with_validated_findings') return '交付已就绪';
  if (s === 'ready_for_customer_acceptance') return '待验收';
  if (s === 'materialized') return '交付资产已生成';
  if (s === 'empty') return '尚未生成';
  return s || '未上报';
}

export function trackerSyncLabel(a: CommercialAssets | null): string {
  const s = a?.tracker_sync.payload_status || '';
  if (s === 'external_tracker_sync_payloads_blocked_or_empty') return '待同步草稿';
  if (s === 'external_tracker_sync_payloads_ready') return '同步已就绪';
  return s || '未上报';
}

export function deliveryPackageLabel(a: CommercialAssets | null): string {
  const s = a?.delivery_package.status || '';
  if (s === 'created') return '交付包已创建';
  if (s === 'not_created') return '交付包未生成';
  return s || '未上报';
}

// ─── Regression helpers ──────────────────────────────────────────────────────

export function regressionGateLabel(s: string): string {
  const n = s.trim().toLowerCase();
  if (n === 'failed') return '回归失败';
  if (n === 'passed') return '回归通过';
  if (n === 'manual_approval_required') return '回归待审批';
  return n ? '回归待执行' : '回归未上报';
}

export function regressionTrendLabel(d: string): string {
  const n = d.trim().toLowerCase();
  if (n === 'improving') return '趋势向好';
  if (n === 'regressing') return '风险上升';
  if (n === 'stable') return '趋势持平';
  return '趋势待观察';
}

export function releaseRecommendationLabel(v: string, fb: string): string {
  if (fb) return fb;
  const n = v.trim().toLowerCase();
  if (n === 'block_release') return '建议阻断发布';
  if (n === 'continue_regression') return '建议继续回归';
  if (n === 'hold_for_validation') return '建议先完成回归';
  if (n === 'candidate_release') return '可进入候选发布';
  if (n === 'candidate_acceptance') return '可进入验收';
  return '继续观察';
}

// ─── Executive headline ──────────────────────────────────────────────────────

export function getExecutiveHeadline(defects: number, family: number, p0: number, clues: number, cs: string, reason: string): string {
  if (cs === 'blocked') return `检测已暂停：${reason || '还需补齐必要条件'}。`;
  if (cs === 'coverage_deferred') return `本轮覆盖已到边界：${reason || '剩余范围已明确递延'}。`;
  if (defects > 0 && p0 > 0) return family > defects ? `已确认 ${defects} 个问题，累计 ${family} 个，其中 ${p0} 个会直接影响发布。` : `已确认 ${defects} 个问题，其中 ${p0} 个会直接影响发布。`;
  if (defects > 0) return family > defects ? `已确认 ${defects} 个问题，累计 ${family} 个，可直接进入整改。` : `已确认 ${defects} 个问题，可直接进入整改与验收。`;
  if (clues > 0) return `本轮尚未形成确认问题，仍有 ${clues} 条线索在补证中。`;
  return '当前未发现问题，可结合覆盖状态判断发布结论。';
}

// ─── Gate / Main-chain / Evidence helpers ────────────────────────────────────

export function getGatePatchStatus(r: JsonRecord): JsonRecord {
  const d = asRecord(r.customer_delivery_gate_patch);
  if (Object.keys(d).length > 0) return d;
  return asRecord(asRecord(r.data_contract).customer_delivery_gate_patch);
}

export function getMainChainContract(r: JsonRecord): JsonRecord {
  const d = asRecord(r.main_chain_contract);
  if (Object.keys(d).length > 0) return d;
  return asRecord(asRecord(r.data_contract).main_chain_contract);
}

export function getMainChainSummary(r: JsonRecord, c: JsonRecord): JsonRecord {
  const d = asRecord(r.main_chain_contract_summary);
  if (Object.keys(d).length > 0) return d;
  const s = asRecord(c.summary);
  if (Object.keys(s).length > 0) return { chain_ready: c.chain_ready, first_blocked_stage: s.first_blocked_stage, first_blocked_next_action: s.first_blocked_next_action, passed_stage_count: s.passed_stage_count, partial_stage_count: s.partial_stage_count, missing_stage_count: s.missing_stage_count };
  return {};
}

export function getMainChainStages(c: JsonRecord): JsonRecord[] {
  if (!Array.isArray(c.stages)) return [];
  return c.stages.map(asRecord).filter((s) => Object.keys(s).length > 0);
}

export function getEvidenceNormalizationSummary(r: JsonRecord): JsonRecord {
  const d = asRecord(r.evidence_bundle_normalization_summary);
  if (Object.keys(d).length > 0) return d;
  return asRecord(asRecord(r.data_contract).evidence_bundle_normalization_summary);
}

export function getEvidenceNormalizationReport(r: JsonRecord): JsonRecord {
  const d = asRecord(r.evidence_bundle_normalization_report);
  if (Object.keys(d).length > 0) return d;
  return asRecord(asRecord(r.data_contract).evidence_bundle_normalization_report);
}

export function evidenceNormalizationItems(rep: JsonRecord): JsonRecord[] {
  if (!Array.isArray(rep.items)) return [];
  return rep.items.map(asRecord).filter((i) => Object.keys(i).length > 0);
}

export function evidenceMissingEntries(s: JsonRecord): Array<[string, number]> {
  const m = asRecord(s.missing_fields);
  return Object.entries(m).map(([f, c]) => [f, asNum(c)] as [string, number]).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1]);
}

export function evidenceMissingFieldLabel(f: string): string {
  return EVIDENCE_MISSING_FIELD_LABELS[f] || f;
}

export function evidenceItemTitle(i: JsonRecord): string {
  return [asText(i.evidence_id), asText(i.issue_id), asText(i.probe_id)].filter(Boolean).join(' / ') || '未命名证据项';
}

export function evidenceItemAction(i: JsonRecord): string {
  return asText(i.next_action) || '补齐该证据项缺失字段后重新运行主链路合同';
}

export function evidenceNormalizationLabel(s: JsonRecord): string {
  if (Object.keys(s).length === 0) return '未上报';
  const b = asNum(s.blocked_item_count);
  return b > 0 ? `未完成：缺 ${b} 项` : '已标准化';
}

export function mainChainStageLabel(s: JsonRecord): string {
  const k = asText(s.stage);
  return MAIN_CHAIN_STAGE_LABELS[k] || k || '未知';
}

export function mainChainStatusLabel(s: JsonRecord): string {
  const st = asText(s.status) || (s.ok === true ? 'passed' : 'missing');
  return MAIN_CHAIN_STATUS_LABELS[st] || st || '未知';
}

export function mainChainReadyLabel(ready: boolean, has: boolean): string {
  if (!has) return '主链路未上报';
  return ready ? '主链路已闭合' : '主链路未闭合';
}

export function gatePatchLabel(p: boolean): string {
  return p ? '严格 Gate 已启用' : '严格 Gate 未确认';
}
