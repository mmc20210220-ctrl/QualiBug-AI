import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const card = read('src/components/findings/FindingCard.tsx');
const page = read('src/pages/Findings.tsx');
const api = read('src/api/finding-collaboration.ts');
const backend = read('../ai_test_asset_center/finding_collaboration.py');
const handler = read('../ai_test_asset_center/private_pilot_finding_collaboration_handlers.py');
const service = read('../ai_test_asset_center/private_pilot_service.py');
const types = read('src/types/index.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(card.includes('复制研发交接'), 'finding card must expose developer handoff');
assert(card.includes('navigator.clipboard.writeText(handoffSummary(finding))'), 'developer handoff must use real finding data');
assert(card.includes('finding.regression.lifecycle_label'), 'finding collaboration must surface real regression lifecycle');
assert(card.includes('finding.regression.latest_status_label'), 'finding collaboration must surface real latest regression status');
assert(card.includes('finding.regression.gate_status'), 'finding collaboration must surface real regression gate');

assert(api.includes("fetch('/api/v1/findings/collaboration'"), 'collaboration must use the real backend persistence endpoint');
assert(api.includes("credentials: 'include'"), 'collaboration persistence must use the HttpOnly-cookie session');
assert(api.includes("cache: 'no-store'"), 'collaboration writes must not rely on cached state');
assert(api.includes('finding_persistence_id: findingId'), 'collaboration writes must target the stable persistence id');
assert(!api.includes('verification_status: patch'), 'frontend API must not expose verification truth as a human mutation');

assert(card.includes('updateFindingCollaboration(project, persistenceId, collaborationDraft)'), 'finding card must persist collaboration through the backend API');
assert(card.includes('自动验证状态'), 'finding card must expose execution-owned verification truth');
assert(card.includes('人工处理状态'), 'finding card must expose separate human handling status');
assert(card.includes('当前 Finding 未能唯一绑定 SQLite 持久化记录'), 'unresolved persistence identity must fail closed');
assert(card.includes('两者互不覆盖'), 'UI must explain the split between verification and human workflow authority');
assert(!card.includes('localStorage.setItem'), 'finding collaboration must not persist workflow state in localStorage');
assert(!card.includes('sessionStorage.setItem'), 'finding collaboration must not persist workflow state in sessionStorage');
assert(page.includes('project={project}'), 'Findings page must scope collaboration writes to the current project');
assert(page.includes('onCollaborationUpdated={refetch}'), 'Findings page must refetch server truth after collaboration save');

assert(backend.includes('verification status is execution-owned; use handling_status for human workflow'), 'backend must reject human writes to verification truth');
assert(backend.includes('CREATE TABLE IF NOT EXISTS finding_collaboration'), 'backend must persist collaboration outside the evidence finding record');
assert(backend.includes('finding["finding_persistence_id"] = persistence_id'), 'command center must project a stable persistence crosswalk');
assert(backend.includes('if len(hash_matches) == 1'), 'evidence-hash identity binding must require uniqueness');
assert(backend.includes('if len(endpoint_matches) == 1'), 'endpoint identity binding must require uniqueness');
assert(handler.includes('update_finding_status(\n                    root,\n                    persistence_id,'), 'replay must persist verification state by stable SQLite finding id');
assert(handler.includes('FINDING_PERSISTENCE_ID_UNRESOLVED'), 'conclusive replay must fail closed when persistence identity is unresolved');
assert(service.includes('FindingCollaborationHandlersMixin,'), 'private pilot runtime must compose the collaboration mixin');
assert(types.includes('regression?: {'), 'finding schema must remain the source for regression facts');
assert(packageJson.includes('"test:finding-collaboration": "node scripts/finding-collaboration-contract.mjs"'), 'package script missing finding collaboration contract');
assert(ciGate.includes('"test:finding-collaboration"'), 'ci gate missing finding collaboration contract');

console.log('finding collaboration contract passed');
