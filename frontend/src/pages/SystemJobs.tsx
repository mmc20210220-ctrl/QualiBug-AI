import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  getKnowledgeAsset,
  listConnectors,
  registerConnector,
  type ConnectorRecord,
} from '../api/client';
import { usePageTitle } from '../lib/page-title';
import { asArray, asNum, asRecord, asText } from '../lib/value-guards';

type JsonRecord = Record<string, unknown>;

function statusLabel(value: string): string {
  if (value === 'EXECUTION_READY') return '可安全执行';
  if (value === 'PARTIALLY_EXECUTABLE') return '部分可执行';
  if (value === 'UNSAFE') return '高风险隔离';
  return value || '待分析';
}

function statusTone(value: string): string {
  if (value === 'EXECUTION_READY') return 'success';
  if (value === 'PARTIALLY_EXECUTABLE') return 'warning';
  if (value === 'UNSAFE') return 'danger';
  return 'neutral';
}

function connectorIsJobSource(connector: ConnectorRecord): boolean {
  return Boolean(
    connector.external_ref?.toLowerCase().startsWith('job_platform:')
    || ['job_platform', 'xxl_job', 'powerjob', 'quartz_scheduler', 'scheduler_export'].includes(
      connector.kind.toLowerCase(),
    ),
  );
}

export function SystemJobs() {
  usePageTitle('后台任务');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const [asset, setAsset] = useState<JsonRecord>({});
  const [connectors, setConnectors] = useState<ConnectorRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [formOpen, setFormOpen] = useState(false);
  const [platform, setPlatform] = useState('xxl_job');
  const [displayName, setDisplayName] = useState('');
  const [endpointRef, setEndpointRef] = useState('');
  const [credentialRef, setCredentialRef] = useState('');

  const reload = async () => {
    if (!project) {
      setAsset({});
      setConnectors([]);
      return;
    }
    setLoading(true);
    setStatus('');
    try {
      const [knowledge, connectorRows] = await Promise.all([
        getKnowledgeAsset(project),
        listConnectors(project),
      ]);
      setAsset(asRecord(asRecord(knowledge).knowledge_asset));
      setConnectors(connectorRows);
    } catch (error: unknown) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  const jobAssets = useMemo(
    () => asArray(asset.job_assets).map(asRecord),
    [asset],
  );
  const summary = asRecord(asset.job_asset_summary);
  const executionCounts = asRecord(summary.execution_status_counts);
  const jobConnectors = connectors.filter(connectorIsJobSource);

  const connectPlatform = async () => {
    if (!project) {
      setStatus('✗ 请先选择客户');
      return;
    }
    const name = displayName.trim() || `${platform} Job 平台`;
    setStatus('连接中...');
    try {
      await registerConnector({
        project_id: project,
        display_name: name,
        kind: 'http_api',
        enabled: true,
        system_name: 'Job Platform',
        module_name: platform,
        endpoint_ref: endpointRef.trim() || undefined,
        credential_ref: credentialRef.trim() || undefined,
        external_ref: `job_platform:${platform}`,
      });
      setDisplayName('');
      setEndpointRef('');
      setCredentialRef('');
      setFormOpen(false);
      setStatus('✓ Job 平台资料源已连接；QualiBug 将从代码、配置和平台导出中自动恢复 Job 资产');
      await reload();
    } catch (error: unknown) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 连接失败');
    }
  };

  if (!project) {
    return (
      <section className="state-panel">
        <div className="state-panel-badge">客户选择</div>
        <h2>请先选择客户项目</h2>
        <p>Job 资产按客户项目隔离，选择客户后即可自动读取和分析。</p>
      </section>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>系统 Job 与异步任务</h1>
          <p>客户只负责连接资料源；QualiBug 自动发现 Job、恢复行为并判断可测试性，不提供人工 Job 编辑器。</p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-secondary" onClick={() => void reload()} disabled={loading}>
            {loading ? '分析中…' : '重新分析'}
          </button>
          <button className="btn btn-primary" onClick={() => setFormOpen((open) => !open)}>
            连接 Job 平台
          </button>
        </div>
      </div>

      {status && <div className="alert" style={{ marginBottom: 16 }}>{status}</div>}

      {formOpen && (
        <section className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-head">
            <div>
              <h2>连接 Job 平台资料源</h2>
              <p>当前连接仅保存受控地址和凭证引用，不要求客户填写 Job 定义、步骤、Oracle 或 Cleanup。</p>
            </div>
          </div>
          <div className="settings-form-grid">
            <label>
              <span>平台类型</span>
              <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
                <option value="xxl_job">XXL-JOB</option>
                <option value="powerjob">PowerJob</option>
                <option value="quartz_scheduler">Quartz / Spring Scheduler</option>
                <option value="scheduler_export">自研调度平台 / 导出</option>
              </select>
            </label>
            <label>
              <span>显示名称</span>
              <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：任务调度平台" />
            </label>
            <label>
              <span>平台地址</span>
              <input value={endpointRef} onChange={(event) => setEndpointRef(event.target.value)} placeholder="HTTPS 或 localhost 测试地址" />
            </label>
            <label>
              <span>凭证引用</span>
              <input value={credentialRef} onChange={(event) => setCredentialRef(event.target.value)} placeholder="secret_ref:JOB_PLATFORM_TOKEN" />
            </label>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button className="btn btn-primary" onClick={() => void connectPlatform()}>保存连接</button>
            <button className="btn btn-secondary" onClick={() => setFormOpen(false)}>取消</button>
          </div>
        </section>
      )}

      <div className="customer-summary-grid mb-4">
        <article className="customer-summary-card tone-primary">
          <span>已发现 Job</span>
          <strong>{asNum(summary.asset_count) || jobAssets.length}</strong>
          <small>来自代码、配置与平台资料源</small>
        </article>
        <article className="customer-summary-card tone-success">
          <span>可安全执行</span>
          <strong>{asNum(executionCounts.EXECUTION_READY)}</strong>
          <small>触发、身份、观察与恢复契约完整</small>
        </article>
        <article className="customer-summary-card tone-warning">
          <span>部分可执行</span>
          <strong>{asNum(executionCounts.PARTIALLY_EXECUTABLE)}</strong>
          <small>只阻塞依赖缺口的实验</small>
        </article>
        <article className="customer-summary-card tone-danger">
          <span>高风险隔离</span>
          <strong>{asNum(executionCounts.UNSAFE)}</strong>
          <small>缺少可逆写或沙箱恢复能力</small>
        </article>
      </div>

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-head">
          <div>
            <h2>Job 平台资料源</h2>
            <p>连接一次后持续自动分析；不在 QualiBug 中重复维护 Job 配置。</p>
          </div>
        </div>
        {jobConnectors.length === 0 ? (
          <div className="empty-state">
            <h3>尚未连接 Job 平台</h3>
            <p>QualiBug 仍会从已接入代码中自动识别 @Scheduled、XXL-JOB、Quartz、PowerJob 和 Airflow 入口。</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>资料源</th><th>平台</th><th>状态</th><th>最近同步</th></tr></thead>
              <tbody>
                {jobConnectors.map((connector) => (
                  <tr key={connector.connector_id}>
                    <td><strong>{connector.display_name}</strong><div className="muted">{connector.endpoint_ref || '通过平台导出/代码关联'}</div></td>
                    <td>{connector.module_name || connector.external_ref?.replace('job_platform:', '') || connector.kind}</td>
                    <td><span className="status-pill success">{connector.enabled ? '已连接' : '已停用'}</span></td>
                    <td>{connector.last_sync_at_utc || '等待首次扫描'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>自动识别 Job 资产</h2>
            <p>系统自动整理触发、运行身份、步骤、读写集合、业务影响与 Cleanup 准备度。</p>
          </div>
        </div>
        {jobAssets.length === 0 ? (
          <div className="empty-state">
            <h3>当前尚未恢复 Job 资产</h3>
            <p>接入代码或 Job 平台资料源后重新分析。无需手工创建 Job 或填写测试步骤。</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Job</th><th>触发</th><th>行为范围</th><th>执行状态</th><th>安全等级</th></tr>
              </thead>
              <tbody>
                {jobAssets.map((job) => {
                  const identity = asRecord(job.identity);
                  const trigger = asRecord(job.trigger);
                  const behavior = asRecord(job.behavior);
                  const testability = asRecord(job.testability);
                  const executionStatus = asText(testability.execution_status);
                  const objects = asArray(behavior.object_refs).map(String).filter(Boolean);
                  const steps = asArray(behavior.process_steps).map(asRecord);
                  return (
                    <tr key={asText(job.job_asset_id) || asText(job.platform_job_id)}>
                      <td>
                        <strong>{asText(job.display_name) || asText(job.platform_job_id)}</strong>
                        <div className="muted">{asText(identity.handler) || asText(job.platform_type)}</div>
                      </td>
                      <td>{asText(trigger.type) || 'UNKNOWN'}{asText(trigger.cron) ? ` · ${asText(trigger.cron)}` : ''}</td>
                      <td>
                        {objects.length > 0 ? objects.join('、') : '待恢复业务对象'}
                        <div className="muted">{steps.length > 0 ? `${steps.length} 个已识别步骤` : '步骤仍待运行时确认'}</div>
                      </td>
                      <td><span className={`status-pill ${statusTone(executionStatus)}`}>{statusLabel(executionStatus)}</span></td>
                      <td>{asText(testability.safety_level) || '待评估'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
