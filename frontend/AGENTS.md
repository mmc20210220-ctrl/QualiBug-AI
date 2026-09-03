# Frontend stack (source of truth)

This frontend is a **Vite + React SPA** — not Next.js. Stack: Vite 8, React 19, react-router 7. Dev server runs on port **5174** and proxies `/api` → backend `127.0.0.1:8088`.

- Routes live in the SPA router (`/requirements`, `/test-intelligence`, `/dashboard`, `/findings`, `/evidence`, `/release`, `/campaigns`, `/coverage`, `/settings`, `/login`).
- `/requirements` is the primary Requirement Intelligence surface. Its readiness state must come only from the authenticated Requirement Intelligence API; generic scan status must not be presented as requirement readiness.
- `/test-intelligence` is the Test Intelligence surface. Its coverage comes only from the authenticated Test Intelligence API. `COVERED` means current supported semantic units have evidence-backed Test Obligations; it is not total test completeness or execution coverage. Obligation state must remain `OBLIGATION_ONLY / NOT_MEASURED / NOT_EVALUATED` until the backend owns a real downstream execution contract.
- Requirement Intelligence and Test Intelligence workspaces must not display Bug Discovery run summaries as if they were product truth.
- Verify with `npm run lint`, `tsc --noEmit`, and `npm run build` (includes `brand:check`) after changes.
- Honesty semantics are non-negotiable: never invent metrics on the frontend; missing backend data renders as 「后端暂未提供 / 未上报 / 待评估 / NOT_MEASURED」states, never as healthy-looking defaults.
