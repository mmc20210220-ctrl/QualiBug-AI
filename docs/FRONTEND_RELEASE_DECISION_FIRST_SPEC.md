# Frontend Release Decision-First SPEC

## 1. Purpose

Release Gate is the final customer-facing validation decision surface. Its first screen must answer only these questions:

1. Can this project be released now?
2. Why is the current release conclusion red / yellow / green?
3. Which real project-level Gate check is currently failing or pending, if the backend reported one?
4. What is the latest real regression Gate state, and how does it participate in the current conclusion?
5. What is the single highest-value next action?

This surface does not manage the customer's R&D workflow and does not invent project-management states.

## 2. Release authority

### 2.1 Shared presentation authority

Customer-facing release color and wording must continue to use:

`deriveReleasePresentation(...)`

The frontend may resolve presentation priority between already-reported facts, but must not recompute backend quality facts.

### 2.2 Real project-level Gate

The Release page must read the backend pipeline field:

`release_gate`

Directly from the current project pipeline snapshot.

The following fields are authoritative when actually reported:

- `release_gate.overall_status` / compatible explicit backend overall status
- `release_gate.checks[].name`
- `release_gate.checks[].status`
- `release_gate.checks[].detail`

The Release page must not treat frontend-generated checks as backend project-level Gate checks.

### 2.3 Missing Gate data

Missing Gate data is not a pass.

These facts are not sufficient to synthesize green release status:

- zero confirmed Findings
- zero P0 Findings
- no currently visible security Finding
- no currently visible DB inconsistency
- a passed single-Finding replay
- a passed single regression run by itself
- frontend scores or thresholds

Green requires the shared Release Presentation to receive an explicit project-level Gate pass under its existing authority rules.

## 3. Frontend-synthesized Gate prohibition

The Release page must not create local checks such as:

- `P0 缺陷阻塞 = pass` because P0 count is zero
- `认证授权检测 = pass` because no current security Finding is visible
- `数据完整性校验 = pass` because no current integrity Finding is visible
- `DB 验证 = pass` because a frontend count is zero

P0 count, pipeline health, campaign status and regression state may still participate in `deriveReleasePresentation(...)` as separate reported facts. They must not impersonate backend `release_gate.checks`.

The legacy frontend `useReleaseData()` helper is not a Release-page authority. The Release page must not import or use it for the customer decision surface.

## 4. First-screen information architecture

The first screen must appear in this order:

1. **项目级发布结论**
   - current conclusion
   - red / yellow / green presentation
   - shared Release Presentation advice

2. **真实项目级 Gate**
   - first backend-reported failing check, when present
   - otherwise first backend-reported pending check, when present
   - explicit overall status when no concrete failing/pending check is supplied
   - missing / ambiguous overall remains non-green

3. **最新修复后回归**
   - latest real regression Gate status from the persisted pipeline fields
   - latest generated time when available
   - explicit statement that a regression pass does not independently release the project

4. **现在最应该做**
   - exactly one primary action
   - chosen from already-known release / regression / execution facts

## 5. Gate blocker wording

When a real backend failed check exists, the first screen may show:

`首个上报失败：<check name>`

This is not a severity ranking and must not be described as "the most severe" or "the root cause" unless the backend explicitly provides such semantics.

If overall=`fail` but no failed check is supplied, the frontend must say the overall Gate failed and that the concrete reason was not provided. It must not guess from Finding titles, categories or check-code naming.

## 6. Regression semantics

Regression is a release input, not the project-level Release Gate itself.

### Failed regression

A real latest regression `failed` state participates directly in the current release conclusion and can make the shared presentation red.

### Passed regression

A real latest regression `passed` state may be shown as passed, but the UI must explicitly state:

- one regression pass does not equal project-level Gate pass;
- unreported / incomplete release Gate data remains unresolved;
- without a persisted previous release snapshot, the frontend cannot claim the release conclusion "changed from blocked to pass".

### Missing regression

Missing regression Gate status is `未上报明确 Gate 状态`, not `passed`.

## 7. Finding context

When `finding=<id>` and optionally `verification_at=<timestamp>` are present:

- keep exact Finding identity;
- keep exact verification-run context;
- current Finding status remains based on the shared verification interpreter;
- historical validation remains historical;
- a Finding disappearing from the current result list must not imply it is fixed;
- a single Finding state never replaces project-level Release Gate authority.

## 8. Secondary information

The following stay available after the decision-first screen:

- complete real backend Gate checklist;
- exact Finding review context;
- delivery guard / customer delivery state;
- links to Findings / Evidence / Coverage / Dashboard.

These must not compete with the first-screen primary action.

## 9. Delivery guard boundary

`customer_delivery_guard` is independent from project-level Release Gate.

- Gate pass does not automatically mean delivery guard pass.
- Delivery guard pass must not override a red/yellow Release Gate.
- The UI must label this as an independent downstream fact.

## 10. Mobile behavior

Below the mobile breakpoint:

- project conclusion and verdict stack cleanly;
- Gate and regression fact cards become one column;
- the primary action becomes full width;
- no wording may depend on spatial phrases such as "right side" or "left side".

## 11. Non-goals

This frontend work does not implement or modify:

- backend release-gate algorithms;
- regression execution;
- bug discovery;
- evidence generation;
- test execution;
- customer repair workflow;
- owner / assignee / development status;
- release version management;
- ticketing or project management.

QualiBug remains an independent validation layer:

`检测 -> Finding -> Evidence -> 客户自行修复 -> 重新验证 -> Pass/Fail -> 项目级 Release Gate`
