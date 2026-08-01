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

function forbidText(source, forbidden, context) {
  if (source.includes(forbidden)) {
    throw new Error(`${context} contains forbidden contract: ${forbidden}`);
  }
}

const api = read('src/api/knowledge-connectors.ts');
const component = read('src/components/ConnectorCoverage.tsx');

for (const field of [
  'remote_lifecycle',
  'unconfirmed_missing_count',
  'retirement_eligible_count',
  'retired_count',
  'renamed_resource_count',
  'moved_resource_count',
  'reappeared_resource_count',
  'remote_deletion_inferred',
  'permission_loss_inferred',
  'historical_source_bytes_retained',
  'remote_resource_identities_returned',
  'source_refs_returned',
]) {
  requireText(api, field, 'remote lifecycle API projection');
}
requireText(api, 'assertRemoteLifecycleSafety', 'remote lifecycle fail-closed parser');
requireText(api, "row[field] !== false", 'remote lifecycle false-proof checks');
requireText(api, 'row.historical_source_bytes_retained !== true', 'history-retention proof');

for (const wording of [
  '未在配置范围内发现',
  '单次未发现不用于判断远端原因',
  '历史内容和证据仍完整保留',
  '已重命名',
  '范围内移动',
  '重新出现并恢复',
  '不会修改原资料',
]) {
  requireText(component, wording, 'remote lifecycle user wording');
}

for (const forbidden of [
  '已被远端删除',
  '权限丢失',
  'remote_resource_id',
  'source_ref',
  'retired_source_occurrences',
]) {
  forbidText(component, forbidden, 'remote lifecycle component');
}

process.stdout.write('materials remote lifecycle contract passed\n');
