/**
 * Main-chain contract diagnostics for the dashboard.
 *
 * Restores the observability surfaces that the value-driven UI redesign
 * dropped while keeping the helpers alive in lib/dashboard-utils.ts:
 * delivery gate patch status (交付 Gate 诊断), main-chain closure status
 * (主链路闭合状态), and evidence normalization blockers with per-item
 * actions (证据标准化阻断项 / blockedEvidenceActionItems).
 *
 * Every value below comes from backend bookkeeping fields; anything the
 * backend has not reported is shown as 未上报, never estimated.
 */
import {
  asNum, asText,
  getGatePatchStatus, gatePatchLabel,
  getMainChainContract, getMainChainSummary, getMainChainStages,
  mainChainStageLabel, mainChainStatusLabel, mainChainReadyLabel,
  getEvidenceNormalizationSummary, getEvidenceNormalizationReport,
  evidenceNormalizationItems, evidenceMissingEntries, evidenceMissingFieldLabel,
  evidenceNormalizationLabel, evidenceItemTitle, evidenceItemAction,
  type JsonRecord,
} from '../../lib/dashboard-utils';

type Props = {
  record: JsonRecord;
};

export function MainChainContractPanel({ record }: Props) {
  // ── 交付 Gate 诊断 ──
  const gatePatch = getGatePatchStatus(record);
  const gatePatchEnabled = Boolean(gatePatch.core_gate_direct);
  const hasGatePatch = Object.keys(gatePatch).length > 0;

  // ── 主链路闭合状态 ──
  const mainChainContract = getMainChainContract(record);
  const mainChainSummary = getMainChainSummary(record, mainChainContract);
  const mainChainStages = getMainChainStages(mainChainContract);
  const hasMainChainContract =
    Object.keys(mainChainContract).length > 0 || Object.keys(mainChainSummary).length > 0;
  const mainChainReady = Boolean(mainChainSummary.chain_ready);
  const firstBlockedStage = asText(mainChainSummary.first_blocked_stage);
  const firstBlockedStageLabel = mainChainStageLabel({ stage: firstBlockedStage });
  const firstBlockedNextAction =
    asText(mainChainSummary.first_blocked_next_action) || '等待上一步完成';

  // ── 证据标准化 ──
  const evidenceNormalizationSummary = getEvidenceNormalizationSummary(record);
  const evidenceNormalizationReport = getEvidenceNormalizationReport(record);
  const evidenceNormalizationItemReports = evidenceNormalizationItems(evidenceNormalizationReport);
  const blockedEvidenceActionItems = evidenceNormalizationItemReports.filter(
    (i) => i.normalized !== true,
  );
  const evidenceMissingFields = evidenceMissingEntries(evidenceNormalizationSummary);
  const evidenceBlockedItemCount = asNum(
    evidenceNormalizationSummary.blocked_item_count,
    blockedEvidenceActionItems.length,
  );
  const evidenceFullyNormalizedCount = Math.max(
    evidenceNormalizationItemReports.length - blockedEvidenceActionItems.length,
    0,
  );
  const hasEvidenceNormalizationSummary =
    Object.keys(evidenceNormalizationSummary).length > 0 ||
    Object.keys(evidenceNormalizationReport).length > 0;

  if (!hasGatePatch && !hasMainChainContract && !hasEvidenceNormalizationSummary) {
    return null;
  }

  return (
    <section className="customer-secondary-grid" aria-label="主链路合同诊断">
      {/* 交付 Gate 诊断 */}
      <article className="customer-secondary-card">
        <span className="customer-value-kicker">交付 Gate 诊断</span>
        <h3>{hasGatePatch ? gatePatchLabel(gatePatchEnabled) : '未上报'}</h3>
        <p>
          {hasGatePatch
            ? gatePatchEnabled
              ? '严格 Gate 已启用：进入客户交付的问题都必须通过正式交付门禁。'
              : '严格 Gate 未确认：后端尚未证明核心门禁直连生效。'
            : '交付 Gate 补丁状态尚未随本轮结果上报。'}
        </p>
        <div className="customer-secondary-meta">
          <span><em>active_partition_name</em><b>{asText(gatePatch.active_partition_name) || '未上报'}</b></span>
          <span><em>core_gate_direct</em><b>{hasGatePatch ? String(gatePatchEnabled) : '未上报'}</b></span>
        </div>
      </article>

      {/* 主链路闭合状态 */}
      {hasMainChainContract && (
        <article className="customer-secondary-card">
          <span className="customer-value-kicker">主链路闭合状态</span>
          <h3>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</h3>
          <p>
            {mainChainReady
              ? '全部阶段已完成。'
              : firstBlockedStage
                ? `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}`
                : '主链路尚未闭合，见各阶段状态。'}
          </p>
          <div className="customer-secondary-meta">
            {mainChainStages.length > 0 ? (
              mainChainStages.map((s) => (
                <span key={`${asText(s.stage)}-${asText(s.status)}`}>
                  <em>{mainChainStageLabel(s)}</em><b>{mainChainStatusLabel(s)}</b>
                </span>
              ))
            ) : (
              <>
                <span><em>通过</em><b>{asNum(mainChainSummary.passed_stage_count)}</b></span>
                <span><em>部分</em><b>{asNum(mainChainSummary.partial_stage_count)}</b></span>
                <span><em>缺失</em><b>{asNum(mainChainSummary.missing_stage_count)}</b></span>
              </>
            )}
          </div>
        </article>
      )}

      {/* 证据标准化阻断项 */}
      {hasEvidenceNormalizationSummary && (
        <article className="customer-secondary-card">
          <span className="customer-value-kicker">证据标准化阻断项</span>
          <h3>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</h3>
          <p>
            {evidenceMissingFields.length > 0
              ? `证据标准化未完成，仍缺字段：${evidenceMissingFields.map(([f, c]) => `${evidenceMissingFieldLabel(f)}×${c}`).join('、')}。`
              : evidenceBlockedItemCount > 0
                ? `证据标准化未完成：仍有 ${evidenceBlockedItemCount} 个证据项缺字段。`
                : `证据字段已标准化，共 ${evidenceFullyNormalizedCount} 项证据。`}
          </p>
          <div className="customer-secondary-meta">
            <span><em>已标准化</em><b>{evidenceFullyNormalizedCount}</b></span>
            <span><em>阻断项</em><b>{evidenceBlockedItemCount}</b></span>
          </div>
          {blockedEvidenceActionItems.length > 0 && (
            <div className="customer-secondary-meta">
              {blockedEvidenceActionItems.slice(0, 3).map((i) => (
                <span key={`${evidenceItemTitle(i)}-${asText(i.trace_id)}`}>
                  <em>{evidenceItemTitle(i)}</em><b>{evidenceItemAction(i)}</b>
                </span>
              ))}
            </div>
          )}
        </article>
      )}
    </section>
  );
}

export default MainChainContractPanel;
