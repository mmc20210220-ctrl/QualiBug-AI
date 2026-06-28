# Phase106 Frontend Product Acceptance Checklist

This checklist defines what the QualiBug product frontend must prove before it can be treated as more than a static demo shell.

## 1. Build And Runtime Gate

- `npm install` completes with no critical or high npm audit findings.
- `npm run lint:contract` passes.
- `npm run build` passes from a clean checkout.
- `npm run preview` serves the production build locally.
- All seven product routes render without console errors:
  - `/`
  - `/customer-intake`
  - `/environment`
  - `/business-flow`
  - `/test-execution`
  - `/risk-evidence`
  - `/report-roi`

## 2. Product Surface Gate

- The first screen is the actual QualiBug command center, not a landing page.
- Sidebar navigation exposes the full product journey.
- Topbar shows project name, API mode, and current backend status.
- Dashboard summarizes quality score, environment readiness, blocking risks, ROI, and launch recommendation.
- Each page has product-specific UI, not only raw JSON dumps.
- Empty, loading, failed, and offline states are visible and understandable.

## 3. Real API Gate

- Demo mode and real API mode are explicit and visually distinct.
- Real API mode performs a real health check before showing the backend as online.
- Configured API URLs or keys are never treated as healthy until a request succeeds.
- API failures show offline/error states instead of fake healthy states.
- API client normalizes success envelopes, error envelopes, network errors, and timeout errors.
- Buttons with side effects call the intended API or are disabled with a clear reason.

## 4. Page-Level Acceptance

- Customer intake supports PRD/OpenAPI/role data upload states, parse result states, and missing-information prompts.
- Environment diagnosis shows URL, DNS, HTTP, auth, API smoke, blocker reasons, and safe execution mode.
- Business flow map renders nodes, edges, coverage, risk overlays, and selected-flow details.
- Test execution shows plan generation, executable probes, blocked probes, run timeline, and evidence callbacks.
- Risk evidence shows risk list, severity, business impact, reproduction evidence, request/response summary, fix suggestion, and close criteria.
- Report/ROI shows executive summary, launch recommendation, coverage, saved effort, affected flows, and export/share states.

## 5. Interaction Gate

- Navigation does not reload unnecessarily and preserves selected project context.
- Long-running actions expose progress, cancel/disabled states, and completion/error results.
- Users can switch project/demo data without losing route state.
- Forms validate required fields before submission.
- Dangerous actions require explicit confirmation and never send raw secrets.

## 6. Security And Privacy Gate

- No raw token, cookie, password, session, client secret, traceback, or private header is rendered.
- Browser-side code does not forge trusted actor or role headers.
- Secret references may be displayed; secret values may not.
- Demo data is visibly redacted.
- Production mode disables external calls unless explicitly configured through the backend.

## 7. Responsive And Visual Gate

- Desktop, tablet, and mobile layouts are usable.
- Text does not overflow buttons, cards, nav items, or metric blocks.
- Cards are used for repeated items and tool panels, not nested page sections.
- Status colors are consistent across dashboard, risk evidence, and reports.
- Dense operational pages remain scannable without marketing-style filler.

## 8. Test Coverage Gate

- Contract tests cover route inventory and API method mapping.
- Smoke tests open all routes and assert no console errors.
- API mode tests cover healthy, offline, timeout, and error-envelope responses.
- Visual or DOM checks cover mobile and desktop breakpoints.
- Regression tests assert that raw JSON dumps do not remain as the primary UI for product pages.

## 9. Handoff Definition

A frontend iteration is acceptable when:

- It builds cleanly.
- It runs locally through Vite dev and production preview.
- It shows observable real/offline backend status from an actual health check.
- It has product UI for each major workflow.
- It keeps demo mode safe and clearly labeled.
- It passes the route/API contract tests and route smoke tests.
