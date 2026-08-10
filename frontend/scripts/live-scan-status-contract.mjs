import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const api = read('src/api/live-scan-status.ts');
const banner = read('src/components/run/RunLifecycleBanner.tsx');
const backendContinuous = read('../ai_test_asset_center/private_pilot_continuous.py');
const coordinator = read('../ai_test_asset_center/private_pilot_scan_coordinator.py');
const stageProgress = read('../ai_test_asset_center/scan_stage_progress.py');
const discoveryRuntime = read('../ai_test_asset_center/discovery_runtime.py');
const routing = read('../ai_test_asset_center/private_pilot_http_routing.py');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(api.includes("fetch('/api/v1/continuous/status'"), 'live scan status must reuse the existing server status endpoint');
assert(api.includes("method: 'POST'"), 'live scan status endpoint must use its real POST contract');
assert(api.includes("credentials: 'include'"), 'live scan status must use the HttpOnly-cookie session');
assert(api.includes("cache: 'no-store'"), 'live scan status must never serve cached run state');
assert(api.includes('active_scan_live'), 'live scan API must project the server-confirmed lease fact');
assert(api.includes('active_scan_elapsed_seconds'), 'live scan API must expose server-side elapsed time');
assert(api.includes('scan_stage_progress: parseStageProgress(payload.scan_stage_progress)'), 'live scan API must parse the server stage snapshot');
assert(api.includes("'qualibug.scan-stage-progress.v1'"), 'live scan API must reject unknown stage-progress schemas');

assert(banner.includes("detail?.phase !== 'submitted'"), 'live scan polling must be fenced to the submitted phase');
assert(banner.includes('getLiveScanStatus(detail.projectId)'), 'banner must poll the server-confirmed scan status');
assert(banner.includes('window.setInterval(() => void refreshLiveStatus(), 1000)'), 'submitted scan should refresh live server status once per second');
assert(banner.includes("liveStatus?.active_scan_live === true"), 'banner must distinguish server-confirmed scanning from request setup');
assert(banner.includes('不会按计时器或百分比推测'), 'banner must explicitly reject timer/percentage-invented progress');
assert(banner.includes("['enterprise_understanding', '企业资料理解']"), 'banner must render the real enterprise-understanding stage');
assert(banner.includes("['scenario_planning', '场景与义务生成']"), 'banner must render the real scenario-planning stage');
assert(banner.includes("['runtime_execution', '真实探针执行']"), 'banner must render the real execution stage');
assert(banner.includes("['evidence_collection', '结果观察与证据收集']"), 'banner must render the real evidence stage');
assert(banner.includes('测试数据就绪核验与交付门禁目前仍由总控函数完成'), 'uninstrumented stages must remain explicitly unreported');

assert(routing.includes('parsed.path == "/api/v1/continuous/status"'), 'backend must expose the live status endpoint');
assert(routing.includes('_get_continuous_state(root, project)'), 'live status route must use the durable scan-state authority');
assert(backendContinuous.includes('live_owner = active_scan_owner(root, project)'), 'status projection must consult the live project scan lease');
assert(backendContinuous.includes('stage_progress = read_scan_stage_progress(root, project) if live_owner else {}'), 'historical stage files must not surface without a live lease');
assert(backendContinuous.includes('"active_scan_live": bool(live_owner)'), 'status response must expose whether the scan lease is live');
assert(backendContinuous.includes('"active_scan_elapsed_seconds": elapsed_seconds'), 'status response must expose server-side elapsed time');
assert(backendContinuous.includes('"scan_stage_progress": stage_progress'), 'status response must expose real stage telemetry');

assert(stageProgress.includes('Only code that actually owns a stage boundary may update that stage'), 'stage authority must forbid heuristic advancement by design');
assert(stageProgress.includes('"enterprise_understanding"'), 'stage authority missing enterprise-understanding stage');
assert(stageProgress.includes('"scenario_planning"'), 'stage authority missing scenario-planning stage');
assert(stageProgress.includes('"runtime_execution"'), 'stage authority missing runtime execution stage');
assert(stageProgress.includes('"evidence_collection"'), 'stage authority missing evidence collection stage');
assert(stageProgress.includes('"test_data_assessment"'), 'stage authority missing test-data placeholder stage');
assert(stageProgress.includes('"delivery_finalization"'), 'stage authority missing delivery placeholder stage');

assert(discoveryRuntime.includes('build_discovery_plan as _build_discovery_plan'), 'stage telemetry must wrap the real planning authority');
assert(discoveryRuntime.includes('run_experiment_candidate as _run_experiment_candidate'), 'stage telemetry must wrap the real execution authority');
assert(discoveryRuntime.includes('plan = _build_discovery_plan(inputs, campaign_handle)'), 'planning stage must be driven by the actual plan call');
assert(discoveryRuntime.includes('result = _run_experiment_candidate(inputs, campaign_handle, plan)'), 'execution stage must be driven by the actual experiment runner');
assert(discoveryRuntime.includes('"runtime_execution",\n        "completed"'), 'real experiment return must complete runtime execution');
assert(discoveryRuntime.includes('"evidence_collection",\n        "active"'), 'evidence telemetry must be active only when the real runner begins producing observations');

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
