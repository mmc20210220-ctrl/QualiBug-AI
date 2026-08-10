import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const dashboard = read('src/pages/Dashboard.tsx');
const findings = read('src/pages/Findings.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const releaseGate = read('src/pages/ReleaseGate.tsx');
const evidencePresentation = read('src/lib/evidence-presentation.ts');
const projectNavigation = read('src/lib/project-navigation.ts');
const responsive = read('src/styles/customer-responsive.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(evidencePresentation.includes("params.set('finding', normalized);"), 'finding deep-link helper must encode a stable finding identity');
assert(projectNavigation.includes("const navigateToProjectPath = useCallback((pathname: string, projectId?: string, currentSearch = '') =>"), 'project navigation must accept entity query context');
assert(projectNavigation.includes('buildProjectPath(pathname, projectId, currentSearch)'), 'project navigation must merge project with the supplied finding context');

assert(dashboard.includes("import { evidenceDeepLinkSearch } from '../lib/evidence-presentation';"), 'dashboard focus flow must use the shared finding deep-link helper');
assert(dashboard.includes("navigateToProjectPath('/findings', project, evidenceDeepLinkSearch(f.id))"), 'dashboard focus card must open the exact finding instead of the generic list');
assert(dashboard.includes('处理这条问题'), 'dashboard focus card must expose an exact finding action');
assert(dashboard.includes("(f.evidence_chain?.length || 0) > 0"), 'dashboard must only expose exact evidence navigation when the finding has a real evidence package');
assert(dashboard.includes("navigateToProjectPath('/evidence', project, evidenceDeepLinkSearch(f.id))"), 'dashboard focus evidence action must keep the exact finding identity');
assert(dashboard.includes('查看这条证据'), 'dashboard focus card must label the exact evidence action clearly');

assert(findings.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'findings must read the exact finding identity');
assert(findings.includes('setExpandedId(requestedFindingId);'), 'findings must reopen the requested finding on round trip');
assert(findings.includes('当前不会用标题相似的问题代替它'), 'stale finding context must never fall back by title');

assert(evidence.includes('const findingContextSearch = evidenceDeepLinkSearch(selected?.id || requestedFindingId);'), 'evidence must retain the selected finding context');
assert(evidence.includes("navigateToProjectPath('/release', project, findingContextSearch)"), 'evidence to release navigation must preserve the exact finding');
assert(evidence.includes('证据中心不会静默切换到另一条问题来冒充当前证据'), 'evidence must never silently swap a requested finding');

assert(releaseGate.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'release review must accept a finding context');
assert(releaseGate.includes('customerFindings.find((finding) => finding.id === requestedFindingId) || null'), 'release review must resolve the exact finding by id only');
assert(releaseGate.includes('发布门禁仍按整个项目的真实 Gate 判定'), 'single finding context must not replace project-level release authority');
assert(releaseGate.includes('发布页不会按标题猜测替代问题'), 'stale release finding context must not guess a replacement');
assert(releaseGate.includes("navigateToProjectPath('/findings', project, findingContextSearch)"), 'release review must return to the exact finding');
assert(releaseGate.includes("navigateToProjectPath('/evidence', project, findingContextSearch)"), 'release review must return to the exact evidence when it exists');
assert(releaseGate.includes('deriveReleasePresentation({'), 'release decision must remain driven by the existing project-level release presentation authority');

assert(responsive.includes('.focus-card .settings-actions {'), 'dashboard focus actions must wrap on narrow layouts');
assert(responsive.includes('.focus-card .settings-actions .btn {'), 'dashboard focus buttons must become touch-friendly at the mobile breakpoint');

assert(packageJson.includes('"test:finding-context-navigation": "node scripts/finding-context-navigation-contract.mjs"'), 'package script missing finding context navigation contract');
assert(ciGate.includes('"test:finding-context-navigation"'), 'ci gate missing finding context navigation contract');

console.log('finding context navigation contract passed');
