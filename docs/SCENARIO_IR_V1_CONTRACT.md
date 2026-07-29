# QualiBug Scenario IR v1 Contract

## Purpose

Scenario IR v1 converts confirmed Business Behavior IR plus governed implementation bindings into
source-backed, non-executable test-design units.

It answers:

- which behavior is being covered;
- which actor, object and operation are involved;
- which source-backed preconditions apply;
- which authoritative system action entry is intended;
- which observation channels are available;
- which semantic outcome is expected;
- which parts are still unresolved.

It does not execute tests or claim that a Bug has been found.

## Mainline

```text
enterprise source materials
  -> accepted business facts
  -> Business Behavior IR
  -> governed implementation binding
  -> final scenario-planning gate
  -> Scenario IR v1
  -> later request/oracle/data compiler
  -> later execution plan
```

Scenario IR may be compiled only when the final scenario-planning gate passes. The final gate
already requires both semantic understanding and implementation binding to pass.

## Scenario families

### Positive

A confirmed non-denial behavior becomes a positive design scenario. Approval and confirmation
requirements remain explicit coverage dimensions and are not rewritten as ordinary success.

### Rejection

A confirmed `DENY` behavior without an explicit denied actor becomes a rejection scenario.

### Unauthorized

A confirmed `DENY` behavior with an explicit actor becomes an unauthorized scenario. The compiler
must not invent a substitute role, role hierarchy or credential.

### Boundary

A numeric condition with an explicit comparison operator may produce an `AT_THRESHOLD` boundary
scenario.

- `EQUALS`, `GREATER_THAN_OR_EQUAL` and `LESS_THAN_OR_EQUAL` preserve the source behavior outcome at
  the exact threshold;
- strict `GREATER_THAN` and `LESS_THAN` do not provide a source-backed complementary outcome at the
  exact threshold, so that boundary scenario remains `INCOMPLETE` with
  `BOUNDARY_COMPLEMENT_OUTCOME_UNRESOLVED`;
- adjacent values are not generated because numeric step size, precision and domain units may be
  unknown.

### State transition

A behavior with explicit state effects creates a specialized state-transition scenario. State
changes are copied from source-backed Behavior IR; missing transitions are never invented.

## Required Scenario IR fields

A formal scenario contains:

```text
scenario_id
scenario_family_id
scenario_type
coverage_dimensions
behavior_ref
implementation_binding_ref
actor_refs
object_refs
operation_ref
trigger
preconditions
condition_combinator
action_entry
observer_plan
expected_outcome
evidence
unresolved_semantics
status
```

The action entry must reference one authoritative bound interface. UI design labels remain
non-executable candidates.

## Expected outcome level

Scenario IR stores only:

```text
oracle_level = SEMANTIC_EXPECTATION_ONLY
concrete_assertion_compiled = false
```

A semantic expectation such as `ALLOW`, `DENY` or a state change is not yet a concrete HTTP, UI or
database assertion.

## Gate

`enterprise-test-scenario-ir-gate.v1` passes when:

- the upstream final scenario-planning gate passes;
- every scenario-ready implementation binding is covered by at least one `PLANNABLE` Scenario IR;
- no critical Scenario IR unknown remains.

Optional incomplete boundary variants do not invalidate an otherwise complete base scenario, but
remain visible in the Scenario IR unknown ledger.

## Non-executable contract

Every scenario and gate must preserve:

```text
execution_ready = false
execution_allowed = false
request_payload_compiled = false
credentials_selected = false
test_data_compiled = false
ui_locator_compiled = false
database_query_compiled = false
expected_assertion_compiled = false
cleanup_plan_compiled = false
runtime_environment_validated = false
```

## Governance prohibitions

Scenario IR v1 must never:

- create an endpoint from token similarity;
- invent a user role or unauthorized actor;
- infer a complementary boundary outcome without evidence;
- choose credentials or test accounts;
- generate request bodies;
- generate executable UI locators;
- create database queries;
- assert a concrete status code, response body or state mutation;
- infer cleanup or compensation execution;
- run against a live enterprise system;
- generate a Bug finding.

## Current limitations

This phase does not yet provide:

- request parameter and body compilation;
- role-account-credential resolution;
- test-data construction;
- adjacent boundary values;
- concrete API, UI or database assertions;
- before/after snapshots;
- cleanup and compensation plans;
- cross-behavior sequence planning;
- concurrency, idempotency or retry scenarios;
- runtime safety checks;
- execution or Bug adjudication.
