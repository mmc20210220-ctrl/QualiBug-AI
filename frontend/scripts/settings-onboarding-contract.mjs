import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const guide = read('src/components/settings/SettingsOnboardingGuide.tsx');
const metadata = read('src/components/settings/SettingsMetadataSection.tsx');
const topology = read('src/components/settings/SettingsTopologySection.tsx');
const serviceForm = read('src/components/settings/SettingsServiceForm.tsx');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

for (const label of ['1. 系统地址', '2. 测试账号', '3. 企业资料', '4. 数据库（可选）']) {
  assert(guide.includes(label), `settings onboarding step missing: ${label}`);
}
assert(guide.includes("getKnowledgeAsset(project)"), 'settings onboarding must read real material status');
assert(guide.includes("getServiceCredentials(project)"), 'settings onboarding must read real credential status');
assert(guide.includes("listConnectors(project)"), 'settings onboarding must read real connector status');
assert(metadata.includes('<SettingsOnboardingGuide project={project} />'), 'settings page must render onboarding guide');
assert(topology.includes('id="settings-system-access"'), 'system access section must expose onboarding anchor');
assert(serviceForm.includes('window.sessionStorage.setItem'), 'service setup must autosave a session draft');
assert(serviceForm.includes('window.sessionStorage.removeItem'), 'successful service setup must clear its session draft');

const startMarker = '// onboarding-draft:start';
const endMarker = '// onboarding-draft:end';
const start = serviceForm.indexOf(startMarker);
const end = serviceForm.indexOf(endMarker);
assert(start >= 0 && end > start, 'onboarding draft security markers missing');
const draftBlock = serviceForm.slice(start, end);
for (const forbidden of ['roleAccounts', 'bearerToken', 'apiKey', 'dbUser', 'dbPass']) {
  assert(!draftBlock.includes(forbidden), `secret field leaked into onboarding draft: ${forbidden}`);
}

assert(packageJson.includes('"test:settings-onboarding": "node scripts/settings-onboarding-contract.mjs"'), 'package script missing settings onboarding contract');
assert(ciGate.includes('"test:settings-onboarding"'), 'ci gate missing settings onboarding contract');

console.log('settings onboarding contract passed');
