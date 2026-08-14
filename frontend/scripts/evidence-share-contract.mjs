import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const api = read('src/api/evidence-share.ts');
const drawer = read('src/components/findings/EvidenceDrawer.tsx');
const distributionTools = read('src/components/evidence/EvidenceDistributionTools.tsx');
const publicPage = read('src/pages/SharedEvidence.tsx');
const app = read('src/App.tsx');
const backend = read('../ai_test_asset_center/finding_evidence_shares.py');
const handler = read('../ai_test_asset_center/private_pilot_finding_collaboration_handlers.py');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(api.includes("fetch('/api/v1/findings/evidence-shares'"), 'authenticated share creation endpoint is missing');
assert(api.includes("fetch('/api/v1/findings/evidence-shares/revoke'"), 'share revoke endpoint is missing');
assert(api.includes("fetch('/api/public/v1/evidence-share/resolve'"), 'public share resolution endpoint is missing');
assert(api.includes("credentials: 'omit'"), 'public share resolution must not send the user session');
assert(api.includes("credentials: 'include'"), 'share creation/revocation must use the authenticated session');
assert(api.includes("cache: 'no-store'"), 'share APIs must be non-cacheable');

assert(drawer.includes('<EvidenceDistributionTools finding={finding} project={project} />'), 'evidence drawer must expose sharing only through the secondary tools surface');
assert(drawer.indexOf('<FindingDecisionSnapshot finding={finding} compact />') < drawer.indexOf('<EvidenceDistributionTools finding={finding} project={project} />'), 'sharing must not precede the finding decision context');
assert(distributionTools.includes('生成只读链接'), 'evidence tools must expose explicit share creation');
assert(distributionTools.includes('撤销'), 'evidence tools must expose revocation');
assert(distributionTools.includes('明文 Token 仅本次可见'), 'evidence tools must explain one-time plaintext token handling');
assert(distributionTools.includes('finding_persistence_id'), 'share creation must be bound to a stable finding identity');
assert(distributionTools.includes('当前 Finding 尚未唯一绑定持久化 ID'), 'unresolved finding identity must fail closed for sharing');
assert(!distributionTools.includes('finding-collaboration'), 'evidence sharing UI must not depend on enterprise collaboration workflow types');
assert(!distributionTools.includes('localStorage.setItem'), 'share token must never be persisted in localStorage');
assert(!distributionTools.includes('sessionStorage.setItem'), 'share token must never be persisted in sessionStorage');

assert(publicPage.includes('window.location.hash.slice(1)'), 'public page must read the capability from the URL fragment');
assert(publicPage.includes('resolveEvidenceShare(token)'), 'public page must resolve only the supplied snapshot capability');
assert(publicPage.includes('只能解析这一份冻结快照'), 'public page must explain the capability boundary');
const publicRouteIndex = app.indexOf('<Route path="/shared-evidence"');
const authRouteIndex = app.indexOf('<Route element={<RequireAuth />}>');
assert(publicRouteIndex >= 0 && authRouteIndex >= 0 && publicRouteIndex < authRouteIndex, 'only the shared evidence route may sit outside RequireAuth');

assert(backend.includes('secrets.token_urlsafe(32)'), 'share token must use at least 256 bits of random capability material');
assert(backend.includes('hashlib.sha256'), 'share token must be persisted only by cryptographic hash');
assert(backend.includes('token_hash TEXT NOT NULL UNIQUE'), 'share token hash must be unique in storage');
assert(backend.includes('revoked_unix'), 'share authority must support immediate revocation');
assert(backend.includes('expires_unix'), 'share authority must enforce expiration');
assert(backend.includes('if not _share_table_exists(db):'), 'anonymous resolution must not create or migrate tables');
assert(backend.includes('raw credentials/bodies/curl are omitted'), 'external snapshot must explicitly omit raw credential-bearing data');
assert(!backend.includes('snapshot["curl_command"]'), 'external share snapshot must not add raw curl commands');

const postStart = handler.indexOf('def do_POST');
const publicResolveIndex = handler.indexOf('if parsed.path == _PUBLIC_EVIDENCE_SHARE_RESOLVE_PATH:', postStart);
const protectedActorIndex = handler.indexOf('actor = self._require_actor()', publicResolveIndex);
assert(postStart >= 0 && publicResolveIndex > postStart, 'public resolve branch must exist in POST routing');
assert(protectedActorIndex > publicResolveIndex, 'public resolve must be handled before authenticated mutation routes');
assert(handler.includes('share_path": f"/shared-evidence#{share[\'token\']}"'), 'share URL must carry the token in the fragment, not path/query');
assert(handler.includes('FINDING_SHARE_SOURCE_UNRESOLVED'), 'share creation must fail closed when persistence identity is unresolved');

assert(packageJson.includes('"test:evidence-share": "node scripts/evidence-share-contract.mjs"'), 'package script missing evidence share contract');
assert(ciGate.includes('"test:evidence-share"'), 'ci gate missing evidence share contract');

console.log('evidence share contract passed');
