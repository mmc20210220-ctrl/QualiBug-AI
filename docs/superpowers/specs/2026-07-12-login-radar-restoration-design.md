# QualiBug AI Login Radar Restoration Design

**Date:** 2026-07-12
**Status:** Approved direction
**Scope:** Login-stage decorative visual only

## 1. Decision

Restore the complete visual language of the earlier login radar while preserving the engineering guarantees introduced by the Behavior Field brand rollout.

The restored composition includes:

- circular radar rings and crosshairs;
- a rotating sweep sector and illuminated sweep line;
- timed pulse rings;
- connected state particles;
- moving light beams;
- the subtle Cartesian grid;
- the separate vertical scan-light overlay.

This decision changes only the login-stage background. The governed Behavior Field mark remains the product logo and continues to be used by the login header, sidebar, reports, favicons, and exported brand assets.

## 2. Meaning and Brand Boundary

The login radar represents **enterprise-system behavior observation**: a live view of states, relations, and signals inside an enterprise software system.

It must not contain or imply:

- insects, spiders, antennae, or crawler mascots;
- web scraping or content harvesting;
- fake service, model, campaign, evaluator, or commercial-health signals;
- customer-, benchmark-, industry-, or hidden-evaluator-specific semantics.

The word `Bug` continues to mean a verified divergence between observed and expected behavior. The radar is decorative and never a source of product truth.

## 3. Preserved Product Surface

The following remain unchanged:

- all visible login, registration, reset, health, navigation, and product copy;
- authentication behavior and API contracts;
- the real login-service health badge and its honest online/offline behavior;
- responsive form behavior and accessibility;
- the Behavior Field logo geometry, palettes, variants, SVG/ICO assets, and report mark;
- frontend port `5174` and backend port `8088`.

## 4. Rendering Architecture

### 4.1 Deterministic radar renderer

Replace the perspective-plane login renderer with a dedicated login-radar renderer under `frontend/src/visuals/`.

The renderer owns visual primitives and scene evolution:

- seeded particle and beam creation;
- grid, links, beams, radar rings, sweep wedge, pulse rings, core glow, and vignette drawing;
- pointer-driven radar-center drift;
- focus-driven glow and speed boost;
- bounded particle counts and device-pixel-ratio scaling.

The scene must not use `Math.random`. A fixed seeded generator produces reproducible particle and beam placement for the same viewport, making screenshots and failures traceable while retaining the earlier visual appearance.

The obsolete perspective-plane renderer is removed rather than left as unused production code.

### 4.2 React lifecycle and observability

`LoginStageCanvas` remains responsible only for DOM and animation lifecycle:

- canvas acquisition and sizing;
- media-query and resize subscriptions;
- animation-frame scheduling and cleanup;
- pointer/focus inputs;
- publishing `initializing`, `ready`, `reduced-motion`, or `failed` through `data-brand-visual-state`;
- structured failures using the existing `[login.brand-visual]` log prefix.

Missing canvas elements, unavailable 2D contexts, resize failures, scene-initialization failures, and render failures must become visible `failed` state; none may silently return.

### 4.3 Scan-light overlay

Restore the prior `login-stage-scan` DOM and CSS animation. It remains decorative, non-interactive, and below product content.

Desktop receives the complete animated composition. Responsive layouts retain the radar canvas at an adjusted scale, while the separate scan-light overlay may be hidden at the existing mobile breakpoint to protect readability.

## 5. Motion and Accessibility

When `prefers-reduced-motion: reduce` is active:

- render one stable radar frame;
- do not rotate the sweep, move particles or beams, or emit expanding pulse rings;
- disable the separate scan-light animation;
- publish `data-brand-visual-state="reduced-motion"`.

The canvas and scan-light overlay remain `aria-hidden="true"`. Product text, controls, focus order, and labels remain unchanged.

## 6. Performance Constraints

- Keep the earlier bounded particle range and a small fixed beam count.
- Cap device-pixel ratio at `2`.
- Cancel all animation frames on unmount or motion-mode changes.
- Recreate the deterministic scene only when dimensions change.
- Keep connection work bounded by the capped particle count.
- Add no runtime dependency.

## 7. Living Documentation

Update `AGENTS.md` so the brand direction states:

- the governed Behavior Field mark remains the logo source of truth;
- the login radar is an approved decorative metaphor for behavior observation;
- insect, crawler, spider-web, scraping, and fake-health semantics remain prohibited;
- decorative motion never represents actual system health.

This replaces the earlier blanket prohibition on radar imagery without weakening the no-crawler boundary.

## 8. Verification Strategy

Use test-first implementation.

### Focused contract

The login visual contract must initially fail until it can verify:

- the dedicated radar renderer exists;
- radar rings, sweep, particles, links, beams, and pulse rings are present;
- the scene is deterministic and contains no `Math.random`;
- `LoginStageCanvas` preserves observable visual states and structured failure logging;
- `login-stage-scan` DOM, animation, reduced-motion handling, and responsive handling exist;
- the obsolete perspective-plane renderer is no longer imported or shipped.

### Browser contract

The existing login browser contract continues to verify:

- three authentication modes across desktop and mobile viewports;
- exact current copy;
- the governed Behavior Field mark;
- no horizontal overflow;
- reduced-motion state;
- canvas failure state and structured error logging;
- valid favicon content.

### Build gates

Run:

- focused radar contract;
- brand-mark and generated-asset contracts;
- TypeScript typecheck;
- ESLint with zero warnings;
- production build;
- live `5174` browser contract and visual inspection.

## 9. Acceptance Criteria

- The login background visibly restores the complete earlier radar composition on desktop.
- The new Behavior Field logo remains unchanged everywhere.
- Existing visible product copy has no changes.
- Radar motion remains decorative and cannot be confused with real service health.
- Reduced-motion users receive a stable static radar.
- Canvas failures are visible and traceable.
- Desktop and mobile layouts remain readable without horizontal overflow.
- No crawler, insect, scraping, benchmark, customer, or industry-specific semantics are introduced.
- All focused, browser, type, lint, asset, and build checks pass.

## 10. Out of Scope

- Reworking the Behavior Field logo or generated brand assets.
- Changing authentication, backend behavior, service-health logic, product copy, or navigation.
- Adding new marketing claims or category copy to product screens.
- Starting or modifying backend services.
