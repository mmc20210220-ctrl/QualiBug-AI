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
const routing = read('../ai_test_asset_center/private_pilot_http_routing.py');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(api.includes("fetch('/api/v1/continuous/status'"), 'live scan status must reuse the existing server status endpoint');
assert(api.includes("method: 'POST'"), 'live scan status endpoint must use its real POST contract');
assert(api.includes("credentials: 'include'"), 'live scan status must use the HttpOnly-cookie session');
assert(api.includes("cache: 'no-store'"), 'live scan status must never serve cached run state');
assert(api.includes('active_scan_live'), 'live scan API must project the server-confirmed lease fact');
assert(api.includes('active_scan_elapsed_seconds'), 'live scan API must expose server-side elapsed time');

assert(banner.includes("detail?.phase !== 'submitted'"), 'live scan polling must be fenced to the submitted phase');
assert(banner.includes('getLiveScanStatus(detail.projectId)'), 'banner must poll the server-confirmed scan status');
assert(banner.includes('window.setInterval(() => void refreshLiveStatus(), 1000)'), 'submitted scan should refresh live server status once per second');
assert(banner.includes("liveStatus?.active_scan_live === true"), 'banner must distinguish server-confirmed scanning from request setup');
assert(banner.includes('不会根据计时器推测内部进度'), 'banner must explicitly reject timer-invented internal progress');
assert(banner.includes("['企业资料理解', '等待服务端回执']"), 'inner stages must stay pending until a real server receipt exists');

assert(routing.includes('parsed.path == "/api/v1/continuous/status"'), 'backend must expose the live status endpoint');
assert(routing.includes('_get_continuous_state(root, project)'), 'live status route must use the durable scan-state authority');
assert(backendContinuous.includes('live_owner = active_scan_owner(root, project)'), 'status projection must consult the live project scan lease');
assert(backendContinuous.includes('"active_scan_live": bool(live_owner)'), 'status response must expose whether the scan lease is live');
assert(backendContinuous.includes('"active_scan_elapsed_seconds": elapsed_seconds'), 'status response must expose server-side elapsed time');

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
