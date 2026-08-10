import { useEffect, useState } from 'react';
import { resolveEvidenceShare, type ResolvedEvidenceShare } from '../api/evidence-share';

function verificationLabel(status: string): string {
  if (status === 'resolved') return '真实验证已确认修复';
  if (status === 'falsified') return '真实验证已证伪';
  if (status === 'open') return '真实验证仍打开';
  return status || '未上报';
}

export function SharedEvidence() {
  const [share, setShare] = useState<ResolvedEvidenceShare | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    document.title = 'QualiBug · 只读证据分享';
    const token = window.location.hash.slice(1).trim();
    if (!token) {
      setError('分享链接缺少访问 Token。');
      setLoading(false);
      return;
    }

    let cancelled = false;
    resolveEvidenceShare(token)
      .then((result) => {
        if (cancelled) return;
        setShare(result);
        setError('');
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setError(caught instanceof Error ? caught.message : '分享链接读取失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <main className="content" style={{ maxWidth: 960, margin: '0 auto', paddingTop: 48 }}>
        <section className="state-panel">
          <div className="state-panel-badge">只读证据</div>
          <h2>正在验证分享链接</h2>
          <p>只会读取该链接创建时冻结的脱敏证据快照。</p>
        </section>
      </main>
    );
  }

  if (!share || error) {
    return (
      <main className="content" style={{ maxWidth: 960, margin: '0 auto', paddingTop: 48 }}>
        <section className="state-panel">
          <div className="state-panel-badge">链接不可用</div>
          <h2>无法打开这份证据</h2>
          <p>{error || '该链接不存在、已过期或已被撤销。'}</p>
        </section>
      </main>
    );
  }

  const snapshot = share.snapshot;
  return (
    <main className="content" style={{ maxWidth: 960, margin: '0 auto', paddingTop: 32, paddingBottom: 64 }}>
      <div className="page-header">
        <div>
          <span className="panel-kicker">QualiBug · 只读脱敏证据</span>
          <h1>{snapshot.title || '问题证据包'}</h1>
          <p>{snapshot.project_name || '未公开项目名称'} · {snapshot.severity || '未评级'} · {snapshot.module || '未归类'}</p>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn btn-primary" onClick={() => window.print()}>打印 / 保存 PDF</button>
        </div>
      </div>

      <section className="card mb-4 status-card status-warning">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">访问边界</span>
            <h2>这是冻结的只读快照</h2>
            <p className="muted">{snapshot.notice || '此页面不提供项目后台访问权限。'}</p>
          </div>
          <span className="summary-pill strong">有效期至 {share.expires_at || '未上报'}</span>
        </div>
      </section>

      <div className="customer-summary-grid mb-4">
        <article className="customer-summary-card tone-warning">
          <span>严重级别</span>
          <strong>{snapshot.severity || '—'}</strong>
          <small>由原始 Finding 在创建分享时冻结</small>
        </article>
        <article className="customer-summary-card tone-success">
          <span>证据质量</span>
          <strong>{snapshot.evidence_quality.label || '未评分'}</strong>
          <small>{snapshot.evidence_quality.score}/100 · 复现率 {snapshot.repro_rate}%</small>
        </article>
        <article className="customer-summary-card tone-neutral">
          <span>自动验证状态</span>
          <strong>{verificationLabel(snapshot.verification_status)}</strong>
          <small>人工协作不能修改该状态</small>
        </article>
        <article className="customer-summary-card tone-neutral">
          <span>修复版本</span>
          <strong>{snapshot.fix_version || '未记录'}</strong>
          <small>人工协作信息，仅作为处理上下文</small>
        </article>
      </div>

      <section className="card mb-4">
        <span className="panel-kicker">业务结论</span>
        <h2>影响与预期 / 实际</h2>
        <p className="muted">{snapshot.business_impact || '未公开业务影响说明'}</p>
        <div className="assertion-diff mt-3">
          <div className="assertion-diff-row">
            <span className="assertion-diff-label expected">预期</span>
            <span className="assertion-diff-value">{snapshot.expected || '未指定'}</span>
          </div>
          <div className="assertion-diff-row">
            <span className="assertion-diff-label actual">实际</span>
            <span className="assertion-diff-value">{snapshot.actual || '未捕获'}</span>
          </div>
        </div>
      </section>

      {snapshot.reproduction_steps.length > 0 && (
        <section className="card mb-4">
          <span className="panel-kicker">复现路径</span>
          <h2>脱敏复现步骤</h2>
          <ol style={{ paddingLeft: 20 }}>
            {snapshot.reproduction_steps.map((step, index) => <li key={`${index}-${step.slice(0, 24)}`}>{step}</li>)}
          </ol>
        </section>
      )}

      {snapshot.evidence_chain.length > 0 && (
        <section className="card mb-4">
          <span className="panel-kicker">证据链</span>
          <h2>可外发证据摘要</h2>
          <div className="focus-list mt-3">
            {snapshot.evidence_chain.map((step, index) => (
              <article className="focus-card" key={`${index}-${step.label}`}>
                <strong>{step.label || `证据 ${index + 1}`}</strong>
                {step.content && <p>{step.content}</p>}
                {step.detail && <p className="muted">{step.detail}</p>}
              </article>
            ))}
          </div>
        </section>
      )}

      {(snapshot.relevant_apis.length > 0 || snapshot.relevant_tables.length > 0 || snapshot.trace_id) && (
        <section className="card mb-4">
          <span className="panel-kicker">研发定位</span>
          <h2>脱敏技术上下文</h2>
          <div className="settings-grid mt-3">
            <div><span className="muted">相关接口</span><p>{snapshot.relevant_apis.join('、') || '未公开'}</p></div>
            <div><span className="muted">相关数据表</span><p>{snapshot.relevant_tables.join('、') || '未公开'}</p></div>
            <div><span className="muted">Trace ID</span><p>{snapshot.trace_id || '未公开'}</p></div>
            <div><span className="muted">人工处理状态</span><p>{snapshot.handling_status || '未记录'}</p></div>
          </div>
        </section>
      )}

      {snapshot.regression_obligations.length > 0 && (
        <section className="card mb-4">
          <span className="panel-kicker">修复后验收</span>
          <h2>回归验证义务</h2>
          <ul style={{ paddingLeft: 20 }}>
            {snapshot.regression_obligations.map((item, index) => <li key={`${index}-${item.slice(0, 24)}`}>{item}</li>)}
          </ul>
        </section>
      )}

      <p className="settings-hint">
        分享 Token 只存在于当前 URL 的 #fragment 中，不会作为页面路径或查询参数发送给 Web 服务器。该 Token 只能解析这一份冻结快照，不能访问 QualiBug 项目、账号、原始证据或执行接口。
      </p>
    </main>
  );
}

export default SharedEvidence;
