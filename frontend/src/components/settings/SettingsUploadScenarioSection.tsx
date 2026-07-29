import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset } from '../../api/client';
import {
  listUploadFixtures,
  type UploadFixtureRecord,
} from '../../api/upload-fixtures';
import {
  approveUploadScenario,
  listUploadScenarios,
  registerUploadScenario,
  revokeUploadScenario,
  type UploadScenarioRecord,
} from '../../api/ui-upload-scenarios';

type JsonRecord = Record<string, unknown>;
type SourceOption = { source_id: string; label: string; source_type: string };
type OperationOption = {
  interface_id: string;
  operation_id: string;
  method: string;
  path: string;
  summary: string;
};

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function knowledgeAsset(payload: unknown): JsonRecord {
  return asRecord(asRecord(payload).knowledge_asset);
}

function approvedFixtures(rows: UploadFixtureRecord[]): UploadFixtureRecord[] {
  return rows.filter((row) => (
    row.status === 'active'
    && row.authority === 'approved_copy'
    && Boolean(text(row.binding_ref))
  ));
}

function activeSources(asset: JsonRecord): SourceOption[] {
  const output: SourceOption[] = [];
  for (const value of asArray(asset.sources || asset.source_inventory)) {
    const row = asRecord(value);
    const status = text(row.status) || 'active';
    const sourceId = text(row.source_id) || text(row.id);
    if (status !== 'active' || !sourceId) continue;
    const filename = text(row.filename) || text(row.original_name) || text(row.name);
    output.push({
      source_id: sourceId,
      label: filename || sourceId,
      source_type: text(row.source_type) || text(row.type),
    });
  }
  return output;
}

function safeOperations(asset: JsonRecord): OperationOption[] {
  const seen = new Set<string>();
  const output: OperationOption[] = [];
  for (const value of asArray(asset.interfaces || asset.operations)) {
    const row = asRecord(value);
    const interfaceId = text(row.interface_id);
    const method = text(row.method || row.http_method).toUpperCase();
    const path = text(row.path || row.endpoint || row.url);
    if (!interfaceId || !path || !SAFE_METHODS.has(method) || seen.has(interfaceId)) continue;
    seen.add(interfaceId);
    output.push({
      interface_id: interfaceId,
      operation_id: text(row.operation_id || row.operationId),
      method,
      path,
      summary: text(row.summary || row.title),
    });
  }
  return output.sort((left, right) => (
    `${left.method} ${left.path} ${left.interface_id}`
      .localeCompare(`${right.method} ${right.path} ${right.interface_id}`)
  ));
}

function sourceRoles(asset: JsonRecord): string[] {
  const byKey = new Map<string, string>();
  const add = (value: unknown) => {
    const role = text(value);
    if (role) byKey.set(role.toLocaleLowerCase(), role);
  };
  for (const value of asArray(asset.roles)) {
    const row = asRecord(value);
    add(row.role || row.name || row.id);
  }
  for (const value of asArray(asset.permission_matrix || asset.permissions)) {
    const row = asRecord(value);
    add(row.role || row.actor || row.principal);
  }
  add('public');
  add('anonymous');
  return [...byKey.values()].sort((left, right) => left.localeCompare(right));
}

export function SettingsUploadScenarioSection() {
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const [sources, setSources] = useState<SourceOption[]>([]);
  const [operations, setOperations] = useState<OperationOption[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [fixtures, setFixtures] = useState<UploadFixtureRecord[]>([]);
  const [scenarios, setScenarios] = useState<UploadScenarioRecord[]>([]);
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const [selectedFixtures, setSelectedFixtures] = useState<string[]>([]);
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [revocationReason, setRevocationReason] = useState('来源、页面或上传规则已发生变化');
  const [form, setForm] = useState({
    title: '上传文件并验证结果',
    source_id: '',
    source_locator: 'UI 上传场景说明',
    operation_ref: '',
    actor_role: '',
    start_url: '/upload',
    upload_selector: 'input[type=file]',
    assertion_selector: '#upload-result',
    assertion_text: '上传成功',
    rendered_probe_selector: '#upload-result',
    persistent_probe_url: '/api/upload/state',
    persistent_json_pointer: '/count',
    frame_selector: '',
    frame_origin: '',
  });

  const refresh = useCallback(async () => {
    if (!project) {
      setSources([]);
      setOperations([]);
      setRoles([]);
      setFixtures([]);
      setScenarios([]);
      return;
    }
    try {
      const [knowledge, fixtureList, scenarioList] = await Promise.all([
        getKnowledgeAsset(project),
        listUploadFixtures(project, false),
        listUploadScenarios(project, includeRevoked),
      ]);
      const asset = knowledgeAsset(knowledge);
      const nextSources = activeSources(asset);
      const nextOperations = safeOperations(asset);
      const nextRoles = sourceRoles(asset);
      const nextFixtures = approvedFixtures(fixtureList.fixtures);
      setSources(nextSources);
      setOperations(nextOperations);
      setRoles(nextRoles);
      setFixtures(nextFixtures);
      setScenarios(scenarioList.scenarios);
      setForm((current) => ({
        ...current,
        source_id: nextSources.some((row) => row.source_id === current.source_id)
          ? current.source_id
          : nextSources[0]?.source_id || '',
        operation_ref: nextOperations.some((row) => row.interface_id === current.operation_ref)
          ? current.operation_ref
          : '',
        actor_role: nextRoles.includes(current.actor_role)
          ? current.actor_role
          : '',
      }));
      const activeRefs = new Set(nextFixtures.map((row) => text(row.binding_ref)));
      setSelectedFixtures((current) => current.filter((ref) => activeRefs.has(ref)));
      setStatus('');
    } catch (error) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 无法读取上传场景治理数据');
    }
  }, [includeRevoked, project]);

  useEffect(() => { void refresh(); }, [refresh]);

  const summary = useMemo(() => ({
    candidates: scenarios.filter((row) => row.status === 'active' && row.authority === 'source_declared_candidate').length,
    approved: scenarios.filter((row) => row.status === 'active' && row.authority === 'approved_copy').length,
  }), [scenarios]);

  const setField = (key: keyof typeof form, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    setStatus('');
  };

  const toggleFixture = (bindingRef: string) => {
    setSelectedFixtures((current) => (
      current.includes(bindingRef)
        ? current.filter((value) => value !== bindingRef)
        : [...current, bindingRef]
    ));
  };

  const register = async () => {
    if (!project) { setStatus('✗ 请先选择客户项目'); return; }
    if (!form.source_id) { setStatus('✗ 必须选择活动企业来源'); return; }
    if (!form.operation_ref) { setStatus('✗ 必须选择真实 GET/HEAD/OPTIONS 前置操作'); return; }
    if (!form.actor_role) { setStatus('✗ 必须选择来源声明角色'); return; }
    if (selectedFixtures.length === 0) { setStatus('✗ 至少选择一个已审批 Fixture'); return; }
    setBusy('register');
    setStatus('正在校验来源、前置操作、角色、Fixture 和完整合同…');
    try {
      const result = await registerUploadScenario(project, {
        ...form,
        fixture_binding_refs: selectedFixtures,
        frame_selector: form.frame_selector || undefined,
        frame_origin: form.frame_origin || undefined,
      });
      setStatus(result.status === 'DUPLICATE_ACTIVE'
        ? '✓ 相同候选场景已经存在'
        : '✓ 场景已登记为候选，审批后才会出现在运行中心');
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 上传场景登记失败');
    } finally {
      setBusy('');
    }
  };

  const approve = async (scenario: UploadScenarioRecord) => {
    setBusy(scenario.scenario_id);
    try {
      const result = await approveUploadScenario(project, scenario.scenario_id);
      setStatus(`✓ 场景已审批：${result.scenario?.scenario_ref || ''}`);
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 审批失败');
    } finally {
      setBusy('');
    }
  };

  const revoke = async (scenario: UploadScenarioRecord) => {
    setBusy(scenario.scenario_id);
    try {
      await revokeUploadScenario(project, scenario.scenario_id, revocationReason);
      setStatus('✓ 场景 authority 已撤销，运行中心会自动移除');
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 撤销失败');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="section-card browser-matrix-section upload-fixture-section">
      <div className="settings-card-head browser-matrix-head">
        <div>
          <span className="panel-kicker">来源 UI 上传场景</span>
          <h2>上传场景合同登记与审批</h2>
          <p>把真实企业来源、安全前置操作、来源角色、审批 Fixture、页面断言和 persistent cleanup 组合为正式 UI 合同。</p>
        </div>
        <strong className={summary.approved > 0 ? 'is-positive' : 'is-neutral'}>
          可运行 {summary.approved} 个
        </strong>
      </div>

      <div className="browser-matrix-policy">
        系统不推断 selector、成功文本或 cleanup。前置操作只允许 GET/HEAD/OPTIONS；来源版本、角色、合同哈希和 Fixture authority 任一变化，旧场景都会在扫描前被阻断。
      </div>

      <div className="settings-form-grid">
        <label className="form-field"><span>场景名称</span><input className="form-input" value={form.title} onChange={(event) => setField('title', event.target.value)} /></label>
        <label className="form-field"><span>活动企业来源</span><select className="form-input" value={form.source_id} onChange={(event) => setField('source_id', event.target.value)}><option value="">请选择来源</option>{sources.map((source) => <option key={source.source_id} value={source.source_id}>{source.label} · {source.source_type || 'source'}</option>)}</select></label>
        <label className="form-field"><span>来源定位</span><input className="form-input" value={form.source_locator} onChange={(event) => setField('source_locator', event.target.value)} /></label>
        <label className="form-field"><span>安全前置接口操作</span><select className="form-input" value={form.operation_ref} onChange={(event) => setField('operation_ref', event.target.value)}><option value="">请选择 GET / HEAD / OPTIONS</option>{operations.map((operation) => <option key={operation.interface_id} value={operation.interface_id}>{operation.method} {operation.path}{operation.summary ? ` · ${operation.summary}` : ''}</option>)}</select>{operations.length === 0 && <small className="muted">当前知识资产没有可绑定的只读接口操作，请先导入 OpenAPI、Postman 或接口文档。</small>}</label>
        <label className="form-field"><span>来源角色</span><select className="form-input" value={form.actor_role} onChange={(event) => setField('actor_role', event.target.value)}><option value="">请选择角色</option>{roles.map((role) => <option key={role} value={role}>{role}</option>)}</select><small className="muted">非 public/anonymous 角色仍需在项目账号配置中存在同 role 的运行账号。</small></label>
        <label className="form-field"><span>页面路径</span><input className="form-input" value={form.start_url} onChange={(event) => setField('start_url', event.target.value)} /></label>
        <label className="form-field"><span>上传控件 selector</span><input className="form-input" value={form.upload_selector} onChange={(event) => setField('upload_selector', event.target.value)} /></label>
        <label className="form-field"><span>断言 selector</span><input className="form-input" value={form.assertion_selector} onChange={(event) => setField('assertion_selector', event.target.value)} /></label>
        <label className="form-field"><span>来源声明的成功文本</span><input className="form-input" value={form.assertion_text} onChange={(event) => setField('assertion_text', event.target.value)} /></label>
        <label className="form-field"><span>Rendered state probe selector</span><input className="form-input" value={form.rendered_probe_selector} onChange={(event) => setField('rendered_probe_selector', event.target.value)} /></label>
        <label className="form-field"><span>Persistent probe GET URL</span><input className="form-input" value={form.persistent_probe_url} onChange={(event) => setField('persistent_probe_url', event.target.value)} /></label>
        <label className="form-field"><span>Persistent JSON pointer</span><input className="form-input" value={form.persistent_json_pointer} onChange={(event) => setField('persistent_json_pointer', event.target.value)} /></label>
        <label className="form-field"><span>iframe selector（可选）</span><input className="form-input" value={form.frame_selector} onChange={(event) => setField('frame_selector', event.target.value)} /></label>
        <label className="form-field"><span>iframe 精确 origin（可选）</span><input className="form-input" value={form.frame_origin} onChange={(event) => setField('frame_origin', event.target.value)} /></label>
      </div>

      <div className="upload-fixture-toolbar"><strong>选择已审批 Fixture</strong><span> 已选 {selectedFixtures.length} 个</span></div>
      <div className="browser-matrix-profile-grid">
        {fixtures.map((fixture) => {
          const ref = text(fixture.binding_ref);
          return <label key={fixture.fixture_id} className="browser-matrix-profile"><input type="checkbox" checked={selectedFixtures.includes(ref)} onChange={() => toggleFixture(ref)} /><span><strong>{fixture.fixture_name || fixture.fixture_id}</strong><small>{fixture.content_type || 'application/octet-stream'}</small><em>{ref}</em></span></label>;
        })}
        {fixtures.length === 0 && <p className="settings-inline-feedback">尚无已审批 Fixture，请先完成上方文件治理。</p>}
      </div>
      <div className="settings-actions"><button type="button" className="btn btn-primary" disabled={busy === 'register' || !form.operation_ref || !form.actor_role} onClick={() => void register()}>{busy === 'register' ? '登记中…' : '登记候选场景'}</button></div>

      <div className="upload-fixture-toolbar">
        <div><strong>场景登记表</strong><span> 候选 {summary.candidates} · 可运行 {summary.approved}</span></div>
        <label className="upload-fixture-toggle"><input type="checkbox" checked={includeRevoked} onChange={(event) => setIncludeRevoked(event.target.checked)} />显示已撤销</label>
      </div>
      <label className="form-field upload-fixture-reason"><span>撤销原因</span><input className="form-input" value={revocationReason} onChange={(event) => setRevocationReason(event.target.value)} /></label>
      <div className="upload-fixture-list">
        {scenarios.map((scenario) => {
          const active = scenario.status === 'active';
          const candidate = scenario.authority === 'source_declared_candidate';
          return <article key={scenario.scenario_id} className={`upload-fixture-row ${active ? '' : 'is-revoked'}`}><div className="upload-fixture-main"><div className="upload-fixture-title"><strong>{scenario.title || scenario.scenario_id}</strong><span>{candidate ? '候选待审批' : '已审批可运行'}</span>{!active && <span>已撤销</span>}</div><small>来源 {scenario.source_id} · Fixture {scenario.fixture_binding_refs?.length || 0} 个</small>{scenario.scenario_ref && <code>{scenario.scenario_ref}</code>}{!active && scenario.revocation_reason && <em>撤销原因：{scenario.revocation_reason}</em>}</div><div className="upload-fixture-actions">{active && candidate && <button type="button" className="btn btn-primary settings-btn-compact" disabled={busy === scenario.scenario_id} onClick={() => void approve(scenario)}>审批为可运行</button>}{active && <button type="button" className="btn btn-secondary settings-btn-compact" disabled={busy === scenario.scenario_id || !revocationReason.trim()} onClick={() => void revoke(scenario)}>撤销</button>}</div></article>;
        })}
      </div>
      {status && <p className="settings-inline-feedback" role="status">{status}</p>}
    </div>
  );
}
