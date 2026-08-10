import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const runCenter = read('src/pages/EnterpriseCampaigns.tsx');
const presentation = read('src/lib/run-preflight-presentation.ts');
const snapshot = read('src/components/run/RunPreflightDecisionSnapshot.tsx');
const styles = read('src/components/run/RunPreflightDecisionSnapshot.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(runCenter.includes('const [preflightError, setPreflightError] = useState'), 'preflight read failure must be distinct from run execution failure');
assert(runCenter.includes('setPreflightError(preflightResult.reason instanceof Error'), 'preflight fetch failures must remain explicit');
assert(runCenter.includes("source.status.trim().toLowerCase() === 'active'"), 'run auxiliary material facts must only count active sources');
assert(runCenter.includes('enabledServices.filter(hasConfiguredAuth)'), 'disabled service credentials must not be counted as current run context');
assert(runCenter.includes('enabledServices.filter(hasConfiguredDb)'), 'disabled service DB config must not be counted as current run context');
assert(runCenter.includes('return activeSources.filter'), 'automatic API source selection must only use active materials');
assert(runCenter.includes('deriveRunPreflightPresentation({'), 'run center must use the shared preflight decision interpreter');
assert(runCenter.includes('<RunPreflightDecisionSnapshot'), 'run center must lead with the shared preflight decision snapshot');
assert(runCenter.includes('if (!preflightReady) {'), 'scan submission must stay fail-closed on backend preflight');
assert(runCenter.includes("navigateToProjectPath('/materials', project)"), 'run blocker review must provide the canonical enterprise-material path');
assert(runCenter.includes("navigateToProjectPath('/settings', project)"), 'run blocker review must provide the real system/settings path');
assert(runCenter.includes('首屏只突出第一个上报阻断'), 'run center must not claim a frontend-derived blocker priority');
assert(runCenter.includes('前端不会根据代码名称自行判断哪个资料或配置一定是根因'), 'run center must not infer root cause from blocker codes');

assert(presentation.includes('const preflightReady = Boolean(input.preflight?.ready);'), 'backend ready must be the only positive preflight authority');
assert(presentation.includes('submissionAllowed = preflightReady'), 'auxiliary context must never independently unlock scan submission');
assert(presentation.includes("headline: '当前无法确认是否可以开始检测'"), 'preflight read failure must fail closed');
assert(presentation.includes("headline: '运行前检查未通过，暂不启动检测'"), 'not-ready preflight must block execution');
assert(presentation.includes("headline: '运行前检查已通过，可以开始检测'"), 'positive run wording must require backend ready');
assert(presentation.includes('配置存在不等于登录已经验证通过'), 'credential presence must not be presented as successful authentication');
assert(presentation.includes('真实连通性仍由后端运行前检查与实际执行确认'), 'saved target URL must not be presented as proven connectivity');
assert(presentation.includes('资料类型不限'), 'run material context must preserve open-ended enterprise material types');
assert(presentation.includes('首个上报阻断代码'), 'first blocker presentation must describe backend report order rather than invented severity');
assert(!presentation.includes('score >='), 'run readiness must not invent a frontend score threshold');
assert(!presentation.includes('configuredAuthCount > 0 && input.activeSourceCount > 0'), 'auxiliary credentials/materials must not become a hidden readiness gate');

assert(snapshot.includes('运行前检查'), 'run snapshot must clearly identify the decision surface');
assert(snapshot.includes('当前运行结论'), 'run snapshot must surface one current run conclusion');
assert(snapshot.includes('运行辅助事实'), 'run snapshot must distinguish context facts from authority');
assert(snapshot.includes('只有后端 Preflight 的 <code>ready=true</code> 可以解释为“运行条件已通过”'), 'run snapshot must state the authority boundary');
assert(snapshot.includes("presentation.primaryAction === 'run'"), 'run CTA must come from shared preflight presentation');
assert(snapshot.includes("presentation.primaryAction === 'refresh'"), 'failed preflight reads must expose recheck through shared presentation');
assert(snapshot.includes("presentation.primaryAction === 'review'"), 'blocked preflight must expose blocker review through shared presentation');

assert(styles.includes('grid-template-columns: repeat(5, minmax(0, 1fr));'), 'desktop preflight facts must remain compact');
assert(styles.includes('@media (max-width: 640px)'), 'run preflight decision must remain usable on mobile');
assert(styles.includes('grid-template-columns: minmax(0, 1fr);'), 'run preflight facts must collapse to one column on narrow screens');

assert(packageJson.includes('"test:run-preflight-decision": "node scripts/run-preflight-decision-contract.mjs"'), 'package script missing run preflight decision contract');
assert(ciGate.includes('"test:run-preflight-decision"'), 'ci gate missing run preflight decision contract');

console.log('run preflight decision contract passed');
