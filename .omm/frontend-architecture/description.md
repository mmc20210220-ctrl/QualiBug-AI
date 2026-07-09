# Frontend Architecture

## Framework
- **React 19** + TypeScript + Vite 8
- **React Router v7** for client-side routing
- **Three.js** (via @react-three/fiber) for 3D risk visualization (BEIRing)
- **@xyflow/react** + elkjs for graph/flow visualization
- **Zero Tailwind** — fully custom CSS design system (~6900 lines of `index.css`)

## Route Map (12 protected routes)

| Path | Component | Purpose |
|------|-----------|---------|
| `/login` | Login | Authentication |
| `/dashboard` | Dashboard | Risk overview |
| `/findings` | Findings | Customer-facing defects |
| `/clues` | InternalClues | Internal risk clues |
| `/evidence` | EvidenceChain | Evidence explorer |
| `/behavior-space` | BehaviorSpace | Behavior visualization |
| `/test-tasks` | TestTasks | Task kanban |
| `/coverage` | CoverageMatrix | Risk coverage |
| `/materials` | EnterpriseMaterials | Knowledge assets |
| `/campaigns` | EnterpriseCampaigns | Scan run center |
| `/release` | ReleaseGate | Release decisions |
| `/settings` | Settings | Project config |
| `/products` | Products | Product matrix |

## State Management
- **No global state library** (no Redux, Zustand, etc.)
- URL query param `?project=<id>` is source of truth for project context
- Custom hooks per page (`data.ts`) with `useState` + `refetch`
- Custom DOM events for cross-component refresh (`qualibug:scan-completed`)
- JWT token in `localStorage` for auth

## Backend Communication
- Vite dev proxy: `/api` → `http://127.0.0.1:8088`
- Display-ready pattern: backend pre-formats all data; frontend is "zero computation, pure rendering"
