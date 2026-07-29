# Frontend stack (source of truth)

This frontend is a **Vite + React SPA** — not Next.js. Stack: Vite 8, React 19, react-router 7. Dev server runs on port **5174** and proxies `/api` → backend `127.0.0.1:8088`.

- Routes live in the SPA router (`/dashboard`, `/findings`, `/evidence`, `/release`, `/campaigns`, `/coverage`, `/settings`, `/login`).
- Verify with `npm run lint`, `tsc --noEmit`, and `npm run build` (includes `brand:check`) after changes.
- Honesty semantics are non-negotiable: never invent metrics on the frontend; missing backend data renders as 「后端暂未提供 / 未上报 / 待评估 / NOT_MEASURED」states, never as healthy-looking defaults.
