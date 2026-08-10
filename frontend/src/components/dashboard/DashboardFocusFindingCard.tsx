import { FindingVerificationStatus } from '../findings/FindingVerificationStatus';
import { evidenceDeepLinkSearch } from '../../lib/evidence-presentation';
import { useProjectNavigation } from '../../lib/project-navigation';
import { getFindingModule } from '../../lib/dashboard-utils';
import type { Finding } from '../../types';

type Props = {
  finding: Finding;
  project: string;
};

export function DashboardFocusFindingCard({ finding, project }: Props) {
  const { navigateToProjectPath } = useProjectNavigation();

  return (
    <article className={`focus-card severity-${finding.severity.toLowerCase()}`}>
      <div className="focus-card-head">
        <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
        <strong>{finding.title}</strong>
      </div>
      <p>{finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成确认结论。'}</p>
      <div className="focus-card-meta">
        <span>模块 <b>{getFindingModule(finding)}</b></span>
        <span>证据 <b>{finding.evidence_quality?.label || '未评分'}</b></span>
        <span>复现 <b>{finding.proof?.repro_rate != null ? `${finding.proof.repro_rate}%` : '未上报'}</b></span>
      </div>
      <div className="mt-3">
        <FindingVerificationStatus finding={finding} />
      </div>
      <div className="settings-actions mt-3">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => navigateToProjectPath('/findings', project, evidenceDeepLinkSearch(finding.id))}
        >
          查看这条验证
        </button>
        {(finding.evidence_chain?.length || 0) > 0 && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => navigateToProjectPath('/evidence', project, evidenceDeepLinkSearch(finding.id))}
          >
            查看这条证据
          </button>
        )}
      </div>
    </article>
  );
}

export default DashboardFocusFindingCard;
