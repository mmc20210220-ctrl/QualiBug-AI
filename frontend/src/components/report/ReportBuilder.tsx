import { useState } from 'react';
import type { Finding } from '../../types';
import { buildReportData, renderReportHTML } from '../../api/report';

interface ReportBuilderProps {
  projectName: string;
  industry?: string;
  findings: Finding[];
  metrics: {
    aiTestPoints: number;
    savedHours: number;
    p0Count: number;
    modulesCount: number;
    evidenceTrust: number;
  };
}

type ReportSection = 'cover' | 'summary' | 'value' | 'findings' | 'evidence' | 'release';

const sectionLabels: Record<ReportSection, string> = {
  cover: '封面',
  summary: '执行摘要',
  value: '价值量化',
  findings: '问题清单',
  evidence: '证据附录',
  release: '发布建议',
};

export function ReportBuilder({ projectName, industry, findings, metrics }: ReportBuilderProps) {
  const [selectedSections, setSelectedSections] = useState<Set<ReportSection>>(
    new Set(['cover', 'summary', 'value', 'findings', 'evidence', 'release'])
  );
  const [generating, setGenerating] = useState(false);

  const toggleSection = (section: ReportSection) => {
    setSelectedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      return next;
    });
  };

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const reportData = buildReportData({
        projectName,
        industry: industry || '',
        totalBugs: findings.length,
        beiScore: metrics.evidenceTrust,
        bdsScore: String(metrics.evidenceTrust),
        bcsScore: metrics.evidenceTrust,
        runtimeProbes: metrics.aiTestPoints,
        dbConfirmed: 0,
        findings,
        dbFindings: [],
      });
      const html = renderReportHTML(reportData);
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      window.open(URL.createObjectURL(blob), '_blank');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div style={{ padding: 20, background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>
      <h3 style={{ font: '700 16px var(--font)', marginBottom: 12 }}>报告构建器</h3>
      <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 16 }}>
        选择要包含的章节，生成可直接发送给客户的 HTML 报告。
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
        {(Object.keys(sectionLabels) as ReportSection[]).map((section) => (
          <button
            key={section}
            onClick={() => toggleSection(section)}
            style={{
              padding: '6px 12px',
              borderRadius: 6,
              fontSize: 12.5,
              fontWeight: 600,
              border: '1px solid',
              borderColor: selectedSections.has(section) ? 'var(--primary)' : 'var(--line)',
              background: selectedSections.has(section) ? 'var(--primary-muted)' : 'var(--surface)',
              color: selectedSections.has(section) ? 'var(--primary)' : 'var(--muted)',
              cursor: 'pointer',
            }}
          >
            {selectedSections.has(section) ? '✓ ' : ''}{sectionLabels[section]}
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={handleGenerate}
          disabled={generating || selectedSections.size === 0}
        >
          {generating ? '生成中...' : '生成报告'}
        </button>
        <span style={{ fontSize: 12, color: 'var(--subtle)' }}>
          {findings.length} 个问题 · {metrics.p0Count} 个 P0 · 已选 {selectedSections.size} 个章节
        </span>
      </div>
    </div>
  );
}
