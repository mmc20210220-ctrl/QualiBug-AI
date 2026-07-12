# QualiBug Behavior-Space Brand Design

## Status

Visual direction approved on 2026-07-12; awaiting written-spec review before implementation planning.

The approved direction is **Behavior Field**. The user also explicitly froze the current product copy: this brand rollout changes visual identity, assets, and decorative motion, but does not rewrite existing login, navigation, health, form, or product copy.

## Goal

Turn the supplied QualiBug logo concept into a coherent, scalable product identity that reflects the product's actual category:

> QualiBug AI is enterprise software behavior-space infrastructure.

QualiBug maps actors, states, data, rules, and observed execution trajectories into a computable, verifiable, evolvable behavior-space model. Defect discovery, governed experiments, evidence delivery, and release decisions are capabilities built on that model; they are not the entire brand.

The implementation must remain industry-neutral and must preserve the product ports: frontend `5174`, backend `8088`.

## Relationship to Existing Specifications

This design supersedes only the brand mark, decorative login-stage visual, and favicon portions of `2026-07-11-login-page-99-design.md`.

The earlier specification remains authoritative for authentication behavior, current visible copy, responsive form behavior, accessibility, verified login-service health, error visibility, and the rule that decorative visuals never become a source of product truth.

## Current-State Findings

The current product has a strong dark enterprise-technology surface, but its brand assets are not yet a coherent system:

- `BrandLogo.tsx` contains a hand-drawn approximation of the supplied raster logo rather than a governed brand source.
- The current icon contains a literal insect body, head, antennae, and legs. That can imply a crawler, spider, scraper, or data-harvesting agent, which conflicts with the intended infrastructure category.
- The supplied `品牌图标.png` has a white background, large surrounding whitespace, and fine internal detail, so it cannot serve directly as a dark-mode mark, sidebar icon, or favicon.
- `frontend/index.html` references `/favicon.svg`, but `frontend/public/favicon.svg` does not exist. A request currently resolves to the HTML application shell instead of an SVG image.
- `LoginStageCanvas.tsx` draws radar rings, a rotating scan wedge, scanning beams, pulse rings, random particles, and particle-to-particle links. These cues describe a scanner or crawler more strongly than a behavior-space model.
- The current runtime mark, the supplied raster mark, and the missing browser asset do not form a single source of truth.

## Brand Positioning

### Category

- Chinese: `企业软件行为空间基础设施`
- English: `Enterprise Behavior Space Infrastructure`

These category statements belong to the brand system and documentation. This rollout does not insert them into existing product screens where doing so would alter approved copy.

### Brand Promise

Make enterprise-system behavior computable.

The product turns enterprise materials and runtime facts into an explicit behavior space, then uses governed execution and evidence to validate divergence between expected and observed behavior.

### Meaning of `Bug`

`Bug` means a verifiable divergence between an enterprise system's observed behavior and its expected behavior model. It does not mean an insect, crawler, spider, scraper, scanning robot, or data-harvesting process.

### Permanent Semantic Guardrails

Brand assets, decorative motion, product illustrations, favicons, and marketing exports must not use:

- insect bodies, heads, antennae, legs, shells, or crawling silhouettes;
- spider webs or random particle meshes;
- crawling paths or data-scraping metaphors;
- radar circles, rotating scan wedges, or scanning beams as the primary identity;
- copy that describes QualiBug as a crawler or generic scanner.

## Selected Visual Direction: Behavior Field

The mark uses one consistent visual grammar:

- **Q boundary**: the outer Q is both the product-name anchor and the boundary of an enterprise system.
- **Spatial plane**: a perspective plane inside the Q represents the behavior space constructed from actors, states, data, and rules.
- **State nodes**: a small, bounded set of points represents observable states or facts.
- **Behavior trajectory**: a directed curve through the space represents real execution and state transition.
- **Q tail**: the tail remains part of the Q letterform; it must not be styled as a probe, leg, or crawling appendage.

The mark must read first as a Q, second as a modeled space, and third as a trajectory through system state.

## Adaptive Mark System

The system deliberately uses three levels of detail. The master mark must never be mechanically shrunk into every context.

### Master Mark

- Usage: `64px` and above.
- Contexts: login brand area, report cover, external brand material, large product surfaces.
- Content: full Q boundary, spatial plane, trajectory, and up to four state nodes.

### Compact Mark

- Usage: `24–63px`.
- Contexts: sidebar, avatar, product launcher, compact headers.
- Content: Q boundary, simplified plane, one trajectory, and up to three nodes.
- Fine grid lines are removed.

### Micro Mark

- Usage: `16–23px`.
- Contexts: favicon and browser tab.
- Content: Q boundary and a two-node trajectory only.
- The plane and grid are removed; this is a purpose-built micro mark, not a scaled master mark.

### Tone Variants

Each applicable mark supports:

- dark-surface color;
- light-surface color;
- one-color dark;
- one-color light.

Components must select an explicit supported variant. They must not dynamically recolor arbitrary SVG paths or invent new gradients at call sites.

## Color and Typography

### Core Palette

| Token | Value | Role |
|---|---:|---|
| Space Navy | `#06101E` | Infrastructure foundation and dark brand surface |
| System Blue | `#2563EB` | Trusted system boundary and primary brand action |
| Trajectory Cyan | `#0EA5E9` | Behavior trajectory and spatial structure |
| State Teal | `#2DD4BF` | Active state nodes and verified model activity |
| Cloud | `#F8FAFC` | Light-surface contrast and reversed wordmark |
| Slate | `#64748B` | Supporting brand metadata |

Deep navy and blue dominate. Cyan and teal are bounded signals, not large decorative washes. Health, warning, and danger colors remain semantic product tokens and must not be reclassified as brand colors.

### Typography

Retain the existing production font stack:

- brand and display: `Instrument Sans`, `DM Sans`, then the existing Chinese system-font fallbacks;
- product body: `DM Sans`, `PingFang SC`, `Microsoft YaHei`, `Segoe UI`, sans-serif.

The wordmark obtains its identity through weight, spacing, and the restrained `AI` color treatment. No science-fiction display font or new font dependency is added.

## Product Application

### Copy Freeze

All current visible product copy remains unchanged in this rollout, including:

- login kicker, hero title, supporting line, and proof cards;
- login, registration, and password-reset headings, labels, actions, and trust note;
- verified login-service health labels;
- sidebar subtitle, navigation labels, page headings, topbar text, and product-page copy;
- accessibility names unless a name must change because the literal insect mark is removed.

Tests must guard the existing approved login phrases so a visual refactor cannot silently rewrite them.

### Login Brand Area

- Replace the current literal-insect mark with the Behavior Field master or compact mark at the existing visual size.
- Preserve the current `QualiBug AI` wordmark, health badge, layout, and visible copy.
- The health badge continues to reflect only the real `/api/health` result defined by the existing login specification.

### Login-Stage Motion

Replace the radar and random network animation with a deterministic behavior-space scene:

- one perspective coordinate plane;
- a bounded, deterministic set of state nodes;
- one or more explicit trajectories through those nodes;
- subtle pointer parallax and focus response only when motion is allowed;
- a static equivalent under `prefers-reduced-motion: reduce`.

Remove radar rings, rotating wedges, scanning beams, pulse rings, random particle seeding, and particle-to-particle link generation.

The scene is decorative and `aria-hidden`. It must never display or imply service health, scan progress, model coverage, defect count, or external-provider availability.

The scene uses governed, industry-neutral brand geometry. It must not embed customer data, project identifiers, benchmark entities, domain-specific labels, or runtime findings.

### Product Shell

- Replace the sidebar mark through the shared brand component.
- Preserve the current sidebar subtitle and all navigation copy.
- Keep existing product-status components independent of the brand mark.
- Do not add the new category statement to the topbar or sidebar as part of this rollout.

### Browser Assets and Metadata

- Add a real micro-mark `favicon.svg` under the Vite public asset tree.
- Add `favicon.ico` for browser compatibility.
- Preserve the existing page-title and description copy.
- Verify the favicon URL returns an image content type and valid image bytes, never the HTML SPA fallback.

## Component and Asset Boundaries

### Canonical Runtime Geometry

One brand module owns all official path geometry, gradients, view boxes, and supported variants. `BrandLogo` consumes that module and must not maintain its own duplicate paths.

The module exposes explicit concepts rather than arbitrary styling:

- detail: `master | compact | micro`;
- lockup: `icon | full`;
- tone: `dark | light | mono-dark | mono-light`;
- size and existing optional subtitle behavior.

Call sites select a documented combination. Unsupported combinations fail during TypeScript checking instead of silently degrading.

### Static Export Boundary

Browser and external assets are generated from the governed brand source and checked into the public asset directory. They are outputs, not independently editable drawings.

The frontend package exposes two explicit commands:

- `brand:export` generates governed SVG and ICO outputs;
- `brand:check` parses every required asset and fails on missing, malformed, or drifted output.

The production build runs `brand:check` before Vite compilation so a missing favicon or stale export cannot ship silently.

The export/check workflow must fail when:

- a required variant is missing;
- an exported SVG is malformed;
- the favicon content type or bytes are wrong;
- a generated asset no longer matches the governed source manifest.

### Existing Components

- `BrandLogo.tsx` remains the public React lockup component.
- `LoginStageCanvas.tsx` remains the decorative login visualization boundary but changes from radar/network animation to behavior-space rendering.
- `ServiceHealthBadge.tsx` remains the sole login-service health presentation and is not coupled to brand animation.
- `Sidebar.tsx`, `Login.tsx`, and other call sites consume the shared lockup without embedding brand geometry.

No backend API or authentication contract changes are included.

## Data and State Flow

The brand path is intentionally static and separate from operational truth:

```text
governed brand geometry
  -> React BrandLogo variants
  -> generated SVG/ICO assets
  -> login, sidebar, browser tab

local pointer/focus/reduced-motion state
  -> deterministic behavior-space canvas
  -> decorative pixels only

/api/health response
  -> ServiceHealthBadge
  -> verified service status only
```

Brand rendering must not consume campaign, finding, evaluator, or model-provider state. Operational status must not be inferred from animation state.

## Failure Behavior and Observability

- Missing or malformed required brand assets fail the asset check and production build.
- Unsupported brand variants fail type checking.
- A missing canvas or failed 2D context emits a structured console error with the operation and failure reason. It is not silently ignored.
- The always-present CSS surface remains usable when decorative canvas initialization fails; this is progressive enhancement, not a hidden claim that the visual succeeded.
- The canvas root exposes a testable visual state such as `initializing`, `ready`, `reduced-motion`, or `failed` without presenting that internal state as product health.
- Rendering exceptions remain observable in the browser console and automated checks.
- Authentication and service-health failures keep the behavior from the existing login specification and are not caught by brand code.

## Accessibility and Responsive Behavior

- Full and icon-only brand lockups expose one stable accessible name: `QualiBug AI`.
- Internal decorative SVG and canvas shapes are hidden from assistive technology.
- The replacement mark must not reduce text contrast or cover the health badge.
- Existing keyboard, form, touch-target, and responsive requirements remain unchanged.
- Master, compact, and micro variants are visually checked at `390px`, `768px`, `1280px`, and `1440px` viewports.
- Reduced-motion mode renders the same spatial meaning without animation.

## Verification

### Automated Contract Checks

- `BrandLogo` renders the documented detail, lockup, and tone variants.
- Unsupported variants fail TypeScript compilation.
- Existing login visible copy remains unchanged.
- Existing health-state checks still cover checking, available, and unavailable states.
- The old radar and crawler-adjacent implementation concepts are absent: `drawRadar`, rotating wedge, scan beams, pulse rings, random particle links, and literal insect geometry.
- Every required static asset exists and parses successfully.
- `/favicon.svg` returns SVG image content rather than HTML.
- Reduced-motion mode produces a stable non-animated behavior-space scene.
- Canvas initialization failure is logged and exposes `failed` state.

### Build and Quality Gates

- frontend lint;
- TypeScript checking;
- production build;
- login contract test;
- brand asset export/check command;
- browser inspection at desktop and mobile widths;
- no browser console errors in the successful path;
- no horizontal overflow.

### Visual Acceptance

- The mark reads as `Q` at micro size.
- The master mark reads as a bounded spatial model with a trajectory.
- No variant resembles an insect, crawler, spider, radar, or scraping tool.
- Dark and light lockups remain legible without glow.
- Sidebar and login marks are visibly the same brand family.

## Documentation

Implementation must update `AGENTS.md` with a concise Brand Direction Contract covering:

- the enterprise software behavior-space infrastructure positioning;
- the no-crawler/no-insect semantic boundary;
- the Behavior Field asset source of truth;
- the rule that brand visuals never imply operational health;
- the `5174` frontend and `8088` backend ports.

This design document is the implementation specification for the approved rollout. Visual-companion mockups are exploratory artifacts and are not a second editable source of truth.

## Non-Goals

- Renaming `QualiBug AI`.
- Changing current visible product copy.
- Changing authentication, service-health, campaign, evaluator, or backend behavior.
- Adding a new font, icon, animation, or analytics dependency.
- Rebranding the product as a crawler, scanner, observability tool, knowledge graph, or generic AI assistant.
- Encoding benchmark, customer, industry, or hidden-evaluator information in brand assets or copy.
- Claiming commercial capability from decorative brand visuals.
