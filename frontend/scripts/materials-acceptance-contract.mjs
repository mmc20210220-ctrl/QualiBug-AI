import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, '..');

function read(relativePath) {
  return fs.readFileSync(path.join(frontendRoot, relativePath), 'utf8').replace(/\r\n/g, '\n');
}

function requireText(source, expected, context) {
  if (!source.includes(expected)) {
    throw new Error(`${context} missing required contract: ${expected}`);
  }
}

function forbidText(source, forbidden, context) {
  if (source.includes(forbidden)) {
    throw new Error(`${context} contains forbidden contract: ${forbidden}`);
  }
}

const api = read('src/api/connector-acceptance.ts');
const panel = read('src/components/ConnectorAcceptancePanel.tsx');
const page = read('src/pages/Materials.tsx');

for (const route of [
  "'/acceptance-reports'",
  "'/acceptance'",
  '/acceptance-jobs/${encodeURIComponent(jobId)}',
  'getConnectorAcceptanceReport',
  'getConnectorAcceptanceJob',
  'startConnectorAcceptance',
]) {
  requireText(api, route, 'connector acceptance API');
}

for (const safetyField of [
  'source_content_returned: false',
  'raw_cursor_returned: false',
  'credential_values_returned: false',
  'filesystem_path_returned: false',
  'arbitrary_diagnostic_text_returned: false',
]) {
  requireText(api, safetyField, 'connector acceptance API safety projection');
}

requireText(api, 'assertFalseFields(', 'fail-closed safety envelope');
requireText(api, 'background_execution', 'persistent acceptance job projection');
requireText(page, 'ConnectorAcceptancePanel', 'materials page');
requireText(page, 'disabled={busy || running || needsHelp', 'acceptance operation guard');
requireText(panel, '运行 Pilot 验收', 'acceptance action');
requireText(panel, '连续执行两轮只读同步', 'acceptance explanation');
requireText(panel, '不删除或修改原资料', 'non-mutating acceptance boundary');
requireText(panel, '查看阻断项', 'acceptance blockers');
requireText(panel, '关闭或刷新页面不会中断', 'persistent job behavior');
requireText(panel, '正在后台执行两轮验收', 'background job state');
requireText(panel, 'getConnectorAcceptanceJob', 'acceptance job polling');
requireText(panel, 'window.setInterval', 'acceptance polling cadence');
requireText(panel, '报告仅含指标与哈希，不含正文、凭据或原始游标', 'report privacy statement');

for (const forbidden of ['runConnectorAcceptance', 'report_path', 'next_cursor:', 'app_secret', 'tenant_access_token']) {
  forbidText(panel, forbidden, 'acceptance panel');
}
forbidText(api, 'runConnectorAcceptance', 'connector acceptance API');

process.stdout.write('materials acceptance contract passed\n');
