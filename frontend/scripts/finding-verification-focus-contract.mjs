import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const focus = read('src/lib/finding-verification-focus.ts');
const summary = read('src/components/findings/FindingVerificationRunSummary.tsx');
const panel = read('src/components/findings/FindingVerificationPanel.tsx');
const release = read('src/pages/ReleaseGate.tsx');

assert(focus.includes('deriveFocusedVerificationRunSummary(finding, normalizedGeneratedAt)'), 'focus context must reuse the exact-run shared summary');
assert(focus.includes('buildFindingVerificationTimeline(finding)'), 'focus context must derive latest run from real timeline history');
assert(focus.includes(".filter((event) => event.kind === 'verification')"), 'latest comparison must ignore the synthetic baseline');
assert(focus.includes('latestEvent && latestEvent.key === summary.event.key'), 'historical/latest identity must compare exact real timeline events');
assert(!focus.includes('Date.now('), 'historical/latest classification must not use time-window heuristics');
assert(!focus.includes('Math.abs('), 'historical/latest classification must not use nearest-run heuristics');

assert(summary.includes("isLatestRun ? '当前最新验证' : '历史验证轮次'"), 'focused summary must label current and historical runs explicitly');
assert(summary.includes("isLatestRun ? '最新' : '历史'"), 'focused summary must expose an explicit latest/history badge');
assert(summary.includes('你正在查看历史轮次'), 'historical focus must warn the customer that the requested run is not current');
assert(summary.includes('最新真实验证发生于 {latestGeneratedAt'), 'historical focus must expose the current latest real validation time');
assert(summary.includes('最新结论为“{latestLabel}”'), 'historical focus must expose the current latest real validation conclusion');
assert(summary.includes('下方本轮结果不会覆盖当前最新结论'), 'historical focus must not impersonate current finding state');
assert(summary.includes('历史本轮真实结果'), 'historical result copy must be time-scoped');
assert(summary.includes('历史轮次的发布含义'), 'historical release wording must be time-scoped');
assert(summary.includes('当前发布判断应结合该 Finding 的最新真实验证结论与项目级 Release Gate'), 'historical runs must not replace current release evidence');
assert(summary.includes('当前 Finding 状态以最新真实验证为准'), 'historical summary must defer current finding state to latest real validation');

assert(panel.includes('deriveFindingVerificationFocusContext(finding, focusGeneratedAt)'), 'finding/evidence panel must detect whether the focused run is historical');
assert(panel.includes('const viewingHistoricalRun = Boolean(focusContext && !focusContext.isLatestRun);'), 'panel must explicitly model historical focus');
assert(panel.includes('当前最新结论'), 'panel header must label the shared status as current truth');
assert(panel.includes('你当前定位的是历史验证轮次'), 'panel must explain historical focus before customer reads current status');
assert(panel.includes('上方状态始终表示这条 Finding 的当前最新结论'), 'panel must keep current truth distinct from historical context');
assert(panel.includes('当前最新修复后验证'), 'latest post-fix card must be explicitly current');

assert(release.includes('<FindingVerificationStatus finding={requestedFinding} />'), 'release review must keep the current finding status visible');
assert(release.includes('<FindingVerificationRunSummary finding={requestedFinding} generatedAt={requestedVerificationAt} />'), 'release review must reuse the historical-aware focused summary');
assert(release.includes('不会覆盖项目级门禁'), 'release review must keep project Release Gate authoritative');

console.log('finding verification focus contract passed');
