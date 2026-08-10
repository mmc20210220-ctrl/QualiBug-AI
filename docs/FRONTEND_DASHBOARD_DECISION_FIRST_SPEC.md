# Dashboard Decision-First Frontend SPEC

## 1. Scope

This document defines the customer-facing first screen of the QualiBug Dashboard after a real validation run has materialized results.

Frontend only. The Dashboard must not recompute backend bug discovery, evidence validity, regression verdicts, coverage, or release gates.

QualiBug remains an independent validation product. The Dashboard must not become an enterprise project-management or R&D workflow surface.

## 2. First-screen questions

The first screen must answer, in this order:

1. What did this validation run conclude?
2. How many customer-deliverable problems are currently confirmed?
3. What is the current project-level release risk?
4. What should the customer look at or validate next?

Secondary engineering metrics must not compete with these questions.

## 3. Release authority

Dashboard release presentation must delegate to the shared `deriveReleasePresentation(...)` authority.

The Dashboard must not add a local green fallback.

In particular:

- `0 confirmed Finding` does not imply safe.
- Missing Release Gate data does not imply pass.
- Missing regression data does not imply pass.
- Incomplete/blocked/deferred execution does not imply safe.
- A green first-screen release presentation requires the shared release authority to return green, which currently requires an explicit project-level gate pass.

The visual risk ring must follow the same shared release presentation color:

- red -> blocked
- yellow -> attention
- green -> safe

The ring must not independently derive a greener conclusion from text such as "no P0".

## 4. Problem scale

The first screen may show only real customer-facing validation facts:

- confirmed customer-deliverable problems
- confirmed P0
- confirmed P1
- Findings that have a real evidence chain / evidence package

Equivalent test points, module reach, evidence-trust scores, funnel stages, chain positioning, and other analytical metrics remain available below the first-screen decision area.

They must not be used to infer safety or release readiness.

## 5. Highest-priority Finding

When real confirmed Findings exist, the first screen should expose one highest-priority Finding.

Priority must reuse existing validation truth:

1. shared `deriveFindingVerification(...).priority`
2. severity
3. existing evidence quality only as a final presentation tie-breaker

The frontend must not invent a new risk score.

Opening the first-screen Finding must preserve its exact Finding identity using the existing deep-link helper. It must not search by title similarity or silently substitute another Finding.

The displayed verification status must reuse the shared `FindingVerificationStatus` / verification interpreter.

## 6. Next action

The Dashboard exposes one primary next action in the decision Hero.

Examples grounded in existing state:

- pipeline unhealthy -> return to Run Center
- campaign blocked -> review required setup
- coverage deferred -> continue validation
- confirmed Findings -> open validation results
- failed regression -> review Release Gate
- otherwise -> review Release Gate

Additional actions such as evidence center, coverage, rerun, report export, and project-level regression remain available as secondary controls below the Hero.

## 7. Validation-only wording

Dashboard wording must describe QualiBug-owned validation states, not customer internal development workflow.

Preferred pattern:

`已确认问题 -> 客户自行修复 -> QualiBug 重新验证`

Avoid turning the Dashboard into owner assignment, issue workflow, fix-version tracking, project status, or internal delivery management.

## 8. Enterprise materials navigation

When the Dashboard links back to enterprise knowledge/materials, the canonical destination is `/materials`.

Materials remain Online-first and open-ended by source type.

## 9. Responsive behavior

On desktop, "next action" and "highest-priority Finding" may sit side-by-side.

On narrow screens, they must stack into one column and primary buttons must remain touch-friendly.

## 10. Contract

`frontend/scripts/dashboard-decision-first-contract.mjs` locks this presentation boundary and is executed by `frontend/scripts/ci-gate.mjs`.
