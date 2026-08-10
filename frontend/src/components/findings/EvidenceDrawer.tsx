import { Link } from 'react-router-dom';
import type { Finding } from '../../types';
import { buildProjectPath } from '../../lib/project-navigation';
import { evidenceDeepLinkSearch } from '../../lib/evidence-presentation';
import { AssertionDiff } from '../evidence/AssertionDiff';
import { EvidenceDistributionTools } from '../evidence/EvidenceDistributionTools';
import { QualityScore } from '../evidence/QualityScore';
import { EvidenceTimeline } from '../EvidenceTimeline';
import { FindingDecisionSnapshot } from './FindingDecisionSnapshot';
import { FindingVerificationRunSummary } from './FindingVerificationRunSummary';

interface EvidenceDrawerProps {
  finding: Finding | null;
  project: string;
  onClose: () => void;
  focusGeneratedAt?: string;
}

export function EvidenceDrawer({ finding, project, onClose, focusGeneratedAt = '' }: EvidenceDrawerProps) {
  if (!finding) return null;

  const chain = finding.evidence_chain || [];
  const evidenceCenterHref = buildProjectPath(
    '/evidence',
    project,
    evidenceDeepLinkSearch(finding.id, focusGeneratedAt),
  );

  return (
    <>
      <div className="evidence-drawer-backdrop open" onClick={onClose} />
      <div className="evidence-drawer open">
        <div className="evidence-drawer-head">
          <div>
            <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
            <strong style={{ marginLeft: 8, fontSize: 14 }}>{finding.title}</strong>
          </div>
          <div className="settings-actions">
            <Link className="btn btn-primary btn-sm" to={evidenceCenterHref} onClick={onClose}>
              证据中心完整查看
            </Link>
            <button className="btn btn-secondary btn-sm" type="button" onClick={onClose}>关闭</button>
          </div>
        </div>

        <div className="evidence-drawer-body">
          <FindingDecisionSnapshot finding={finding} compact />

          {focusGeneratedAt && (
            <FindingVerificationRunSummary finding={finding} generatedAt={focusGeneratedAt} />
          )}

          <section className="card mt-3" aria-label="核心证据">
            <div className="settings-card-head">
              <div>
                <span className="panel-kicker">核心证据</span>
                <h3>先核对问题为什么成立</h3>
                <p className="muted">这里只展示当前 Finding 已有的真实证据；完整原始上下文继续进入证据中心查看。</p>
              </div>
            </div>

            <QualityScore finding={finding} />

            <h4 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px' }}>预期 vs 实际</h4>
            <AssertionDiff
              comparison={finding.expected_actual_comparison}
              expected={finding.expected}
              actual={finding.actual}
            />

            {chain.length > 0 ? (
              <div style={{ marginTop: 16 }}>
                <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>证据链</h4>
                <EvidenceTimeline steps={chain} />
              </div>
            ) : (
              <p className="settings-inline-feedback mt-3">
                当前 Finding 没有可展示的 evidence_chain；Drawer 不会用质量分或摘要替代缺失的真实证据链。
              </p>
            )}

            <div className="settings-actions mt-3">
              <Link className="btn btn-primary btn-sm" to={evidenceCenterHref} onClick={onClose}>
                打开完整证据中心
              </Link>
            </div>
          </section>

          {finding.reproduction?.curl_command && (
            <details className="settings-auth-section mt-3">
              <summary>
                <strong>技术复现信息</strong>
                <span className="muted">登录态内部核对</span>
              </summary>
              <div className="mt-3">
                <pre style={{ fontSize: 12, background: 'var(--surface-2)', padding: 12, borderRadius: 8, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  <code>{finding.reproduction.curl_command}</code>
                </pre>
                <p className="settings-hint">
                  原始复现命令只在登录后的内部证据核对中展示；外部分发必须使用下方脱敏证据工具。
                </p>
              </div>
            </details>
          )}

          <EvidenceDistributionTools finding={finding} project={project} />
        </div>
      </div>
    </>
  );
}

export default EvidenceDrawer;
