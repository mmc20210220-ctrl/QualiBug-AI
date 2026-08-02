import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, '..');

function read(relativePath) {
  return fs.readFileSync(path.join(frontendRoot, relativePath), 'utf8');
}

function requireText(source, expected, context) {
  if (!source.includes(expected)) {
    throw new Error(`${context} missing required contract: ${expected}`);
  }
}

const api = read('src/api/knowledge-connectors.ts');
const page = read('src/pages/Materials.tsx');
const panel = read('src/components/ConnectorCoverage.tsx');

for (const field of [
  'discovered_count',
  'covered_count',
  'unsupported_count',
  'coverage_ratio',
  'unsupported_resources',
  'KnowledgeConnectorHealth',
  'toConnectorHealth',
  'health_attention_connector_count',
]) {
  requireText(api, field, 'knowledge connector API projection');
}

requireText(page, '<ConnectorCoverage coverage={connector.coverage} />', 'materials page');
requireText(api, 'source-preflight', 'source entry preflight API');
requireText(page, '已读取 ${connector.coverage.covered_count}/${connector.coverage.discovered_count}', 'connector status');
requireText(page, '资料类型暂不支持', 'sync completion message');
requireText(page, 'materials-health-summary', 'connector health projection');
requireText(page, 'connectorHealthLabel(', 'connector health projection');
for (const marker of [
  'scopeProperties(',
  'serializeScope(',
  'missingRequiredScopeFields(',
  'quickConnectManifests(',
  'applyQuickConnectUrl(',
  'quick_connect_schema',
  'preflightConnectorSource(',
  'preflightSourceUrl',
  'permissionScopeLabel(',
  'source_identity_fingerprints',
  'updated_at_utc',
  '识别并填写范围',
  'materials-preflight-result',
  'materials-scope-editor-details',
  '调整同步范围（可选）',
  'credentialFieldLabel(',
  'authModeLabel(',
  'display_name',
  'property.enum',
  "type === 'array'",
  'JSON.stringify(properties)',
]) {
  requireText(page, marker, 'manifest-driven scope editor');
}

requireText(panel, 'role="progressbar"', 'coverage panel accessibility');
requireText(panel, '已发现 {coverage.discovered_count} 份资料', 'coverage explanation');
requireText(panel, '其余资料仍可正常用于分析和测试', 'partial coverage behavior');
requireText(panel, '已有资料不会被覆盖或删除', 'unknown coverage safety');
requireText(panel, '系统不会修改原资料', 'non-mutating coverage boundary');

if (panel.includes('重新授权')) {
  throw new Error('unsupported material types must not be presented as an authorization failure');
}

process.stdout.write('materials coverage contract passed\n');
