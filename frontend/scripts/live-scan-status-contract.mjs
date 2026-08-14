import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const api = read('src/api/live-scan-status.ts');
const banner = read('src/components/run/RunLifecycleBanner.tsx');
const backendContinuous = read('../ai_test_asset_center/private_pilot_continuous.py');
const coordinator = read('../ai_test_asset_center/private_pilot_scan_coordinator.py');
const stageProgress = read('../ai_test_asset_center/scan_stage_progress.py');
const stageFinalization = read('../ai_test_asset_center/scan_stage_finalization_hook.py');
const scanPostHooks = read('../ai_test_asset_center/scan_post_hooks.py');
const discoveryRuntime = read('../ai_test_asset_center/discovery_runtime.py');
const scanOutcome = read('../ai_test_asset_center/scan_execution_outcome.py');
const routing = read('../ai_test_asset_center/private_pilot_http_routing.py');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(api.includes("fetchWithAuth('/api/v1/continuous/status'"), 'live scan status must reuse the existing server status endpoint');
assert(api.includes("method: 'POST'"), 'live scan status endpoint must use its real POST contract');
const sessionLayer = read('src/api/session.ts');
assert(sessionLayer.includes("credentials: 'include'"), 'live scan status must use the HttpOnly-cookie session via the shared transport');
assert(sessionLayer.includes("cache: init?.cache || 'no-store'"), 'live scan status must never serve cached run state via the shared transport');
assert(api.includes('active_scan_live'), 'live scan API must project the server-confirmed lease fact');
assert(api.includes('active_scan_elapsed_seconds'), 'live scan API must expose server-side elapsed time');
assert(api.includes('scan_stage_progress: parseStageProgress(payload.scan_stage_progress)'), 'live scan API must parse the server stage snapshot');
assert(api.includes("'qualibug.scan-stage-progress.v1'"), 'live scan API must reject unknown stage-progress schemas');

assert(banner.includes("detail?.phase !== 'submitted'"), 'live scan polling must be fenced to the submitted phase');
assert(banner.includes('getLiveScanStatus(detail.projectId)'), 'banner must poll the server-confirmed scan status');
assert(banner.includes('window.setInterval(() => void refreshLiveStatus(), 1000)'), 'submitted scan should refresh live server status once per second');
assert(banner.includes("liveStatus?.active_scan_live === true"), 'banner must distinguish server-confirmed scanning from request setup');
assert(banner.includes('任何阶段都不会按计时器或百分比推测'), 'banner must explicitly reject timer/percentage-invented progress');
for (const stage of [
  "['enterprise_understanding', '企业资料理解']",
  "['scenario_planning', '场景与义务生成']",
  "['test_data_assessment', '测试数据准备 / 就绪核验']",
  "['runtime_execution', '真实探针执行']",
  "['evidence_collection', '结果观察与证据收集']",
  "['delivery_finalization', '交付门禁与报告']",
]) {
  assert(banner.includes(stage), `banner must render server stage: ${stage}`);
}
assert(banner.includes('六个阶段都来自服务端真实执行边界'), 'banner must disclose the six native server boundaries');
assert(banner.includes('发布门禁已经给出 fail/blocked 结论时'), 'banner must distinguish gate verdict from report finalization');
assert(banner.includes('只有最终结果收口后才显示完成'), 'delivery stage must not finish before report/result finalization');

assert(routing.includes('parsed.path == "/api/v1/continuous/status"'), 'backend must expose the live status endpoint');
assert(routing.includes('_get_continuous_state(root, project)'), 'live status route must use the durable scan-state authority');
assert(backendContinuous.includes('live_owner = active_scan_owner(root, project)'), 'status projection must consult the live project scan lease');
assert(backendContinuous.includes('stage_progress = read_scan_stage_progress(root, project) if live_owner else {}'), 'historical stage files must not surface without a live lease');
assert(backendContinuous.includes('"active_scan_live": bool(live_owner)'), 'status response must expose whether the scan lease is live');
assert(backendContinuous.includes('"active_scan_elapsed_seconds": elapsed_seconds'), 'status response must expose server-side elapsed time');
assert(backendContinuous.includes('"scan_stage_progress": stage_progress'), 'status response must expose real stage telemetry');

assert(stageProgress.includes('Only code that actually owns a stage boundary may update that stage'), 'stage authority must forbid heuristic advancement by design');
assert(stageProgress.includes('Persistence is observability-only'), 'stage persistence must be explicitly non-authoritative');
assert(stageProgress.includes('def _persist_best_effort'), 'stage writes must be isolated from the scan outcome');
assert(stageProgress.includes('scan_stage_progress_persist_failed'), 'stage persistence failure must stay visible in logs');
for (const stage of [
  '"enterprise_understanding"',
  '"scenario_planning"',
  '"runtime_execution"',
  '"evidence_collection"',
  '"test_data_assessment"',
  '"delivery_finalization"',
]) {
  assert(stageProgress.includes(stage), `stage authority missing ${stage}`);
}

assert(discoveryRuntime.includes('build_discovery_plan as _build_discovery_plan'), 'stage telemetry must wrap the real planning authority');
assert(discoveryRuntime.includes('run_experiment_candidate as _run_experiment_candidate'), 'stage telemetry must wrap the real execution authority');
assert(discoveryRuntime.includes('plan = _build_discovery_plan(inputs, campaign_handle)'), 'planning stage must be driven by the actual plan call');
assert(discoveryRuntime.includes('result = _run_experiment_candidate(inputs, campaign_handle, plan)'), 'execution stage must be driven by the actual experiment runner');
assert(discoveryRuntime.includes('"runtime_execution",\n        "completed"'), 'real experiment return must complete runtime execution');

assert(scanOutcome.includes('def _test_data_receipt_verifier(root: Path, project: str)'), 'test-data stage must use the real verifier boundary');
assert(scanOutcome.includes('"test_data_assessment",\n        "active"'), 'test-data verifier entry must activate the real stage');
assert(scanOutcome.includes('def _persist_execution_evidence('), 'evidence stage must use the real persistence boundary');
assert(scanOutcome.includes('执行观察、证据图与客户可交付证据正在归一化持久化'), 'evidence persistence must publish an active receipt');
assert(scanOutcome.includes('detail=f"evidence_bundle={bundle_status}"[:240]'), 'evidence persistence result must close the stage from the real bundle');
assert(scanOutcome.includes('def _evaluate_release_gate('), 'delivery stage must use the real release-gate boundary');
assert(scanOutcome.includes('发布门禁正在核验证据、测试数据、覆盖缺口与发布策略'), 'release gate entry must activate delivery finalization');
assert(scanOutcome.includes('报告与最终结果正在收口'), 'successful gate evaluation must remain active until final report/result closure');
assert(scanOutcome.includes('if gate_status in {"failed", "error", "invalid"}'), 'only gate execution failure may fail the delivery stage');

assert(scanPostHooks.includes('"ai_test_asset_center.scan_stage_finalization_hook"'), 'stage finalization must use the first-class scan post-hook convention');
assert(scanPostHooks.includes('"install_scan_stage_finalization_hook"'), 'stage finalization installer must be registered');
assert(stageFinalization.includes('result.get("evidence_bundle")'), 'final evidence state must come from the authoritative evidence bundle');
assert(stageFinalization.includes('result.get("test_data_plan")'), 'final test-data state must come from the authoritative test-data plan');
assert(stageFinalization.includes('result.get("release_gate")'), 'final delivery state must come from the authoritative release gate');
assert(stageFinalization.includes('report_state = "persisted" if _text(result.get("report_path")) else "not_persisted"'), 'delivery completion must record whether the report persisted');
assert(stageFinalization.includes('verdict={release_verdict or \'unspecified\'}'), 'release verdict must stay detail, not be confused with telemetry failure');

const projectionStart = backendContinuous.indexOf('def _public_scan_owner');
const projectionEnd = backendContinuous.indexOf('\ndef _stop_requested', projectionStart);
assert(projectionStart >= 0 && projectionEnd > projectionStart, 'safe scan owner projection must exist');
const projection = backendContinuous.slice(projectionStart, projectionEnd);
for (const forbidden of ['"token":', '"pid":', '"thread_id":', '"tenant_id":', '"actor":']) {
  assert(!projection.includes(forbidden), `public scan owner must not expose internal field ${forbidden}`);
}
for (const safeField of ['"project_id":', '"mode":', '"started_at_utc":']) {
  assert(projection.includes(safeField), `public scan owner missing safe field ${safeField}`);
}

assert(coordinator.includes('def active_scan_owner'), 'scan coordinator must own live lease lookup');
assert(coordinator.includes('if _stale(lease_dir, stale_after_seconds=6 * 60 * 60):'), 'dead-process leases must not be reported as live');
assert(packageJson.includes('"test:live-scan-status": "node scripts/live-scan-status-contract.mjs"'), 'package script missing live scan status contract');
assert(ciGate.includes('"test:live-scan-status"'), 'ci gate missing live scan status contract');

console.log('live scan status contract passed');
