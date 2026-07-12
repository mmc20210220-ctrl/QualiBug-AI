import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');

function read(path) {
  return readFileSync(resolve(root, path), 'utf8');
}

function requireText(source, token, context) {
  if (!source.includes(token)) {
    throw new Error(`${context}: missing ${JSON.stringify(token)}`);
  }
}

const mark = read('src/brand/BehaviorFieldMark.tsx');
const logo = read('src/components/BrandLogo.tsx');
const login = read('src/pages/Login.tsx');
const sidebar = read('src/components/Sidebar.tsx');

for (const detail of ["'master'", "'compact'", "'micro'"]) {
  requireText(mark, detail, 'brand detail');
}
for (const tone of ["'dark'", "'light'", "'mono-dark'", "'mono-light'"]) {
  requireText(mark, tone, 'brand tone');
}
requireText(mark, 'data-brand-detail={detail}', 'mark observability');
requireText(mark, 'data-brand-tone={tone}', 'mark observability');
requireText(logo, '<BehaviorFieldMark', 'BrandLogo source');
requireText(login, 'detail="compact"', 'login mark detail');
requireText(login, 'tone="dark"', 'login mark tone');
requireText(sidebar, 'detail="compact"', 'sidebar mark detail');
requireText(sidebar, 'tone="dark"', 'sidebar mark tone');

for (const forbidden of ['M60 50c5.52', 'm43 30-5-6', 'antenna', 'insect']) {
  if (mark.includes(forbidden) || logo.includes(forbidden)) {
    throw new Error(`Literal-insect brand geometry remains: ${forbidden}`);
  }
}

console.log('PASS brand-mark-contract');
