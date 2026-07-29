# QualiBug Scenario Execution Contract v1

## Purpose

Scenario Execution Contract v1 converts a source-backed, plannable Scenario IR into an explicit
ledger of runtime obligations. It answers:

- which authoritative interface is involved;
- which path and request fields require values;
- which actor identity and test data are required;
- which observations are required before and after the action;
- which semantic Oracle obligations must later become concrete assertions;
- which cleanup or sandbox obligations are required for writes.

It does not execute the scenario.

## Mainline

```text
accepted enterprise facts
  -> Business Behavior IR
  -> governed implementation binding
  -> Scenario IR v1
  -> Scenario Execution Contract v1
  -> future runtime-plan compiler
```

The contract is compiled only after Scenario IR Gate passes.

## Required upstream authority

A formal execution contract requires:

- a `PLANNABLE` Scenario IR;
- a unique authoritative action entry;
- source-backed scenario evidence;
- an observable permission or effect channel;
- no semantic or implementation-binding bypass.

Token-overlap endpoints, UI design labels and arbitrary first-interface fallbacks are not action
authority.

## Request contract

The request section may contain:

- path parameter requirements extracted from the authoritative path template;
- source-backed precondition values that match declared interface contract fields;
- preconditions that must be established as existing entity or system state;
- declared contract fields copied from the governed implementation binding.

The compiler does not decide whether a non-path field belongs to query, header, cookie or body
unless that location is explicitly available. It records:

```text
location = UNRESOLVED_CONTRACT_LOCATION
```

A source-backed semantic value is a requirement, not a materialized runtime value.

## Credentials

Actor references become identity requirements. They do not select accounts, tokens, sessions or
secrets. When a source rule does not name an actor, the contract records an unspecified
authentication-context requirement rather than selecting a default user.

Automatic role substitution is forbidden.

## Test data requirements

A precondition that is not declared as an action request field becomes an existing-state or
entity-fixture requirement. This prevents the compiler from putting every business condition into
the request payload.

The contract does not create database rows, fixtures, identifiers or adjacent boundary values.

## Oracle plan

The Oracle plan preserves:

- permission decision requirements;
- semantic effect requirements;
- state-effect requirements;
- data-effect requirements;
- condition observers;
- effect observers;
- API response observers.

It does not invent HTTP status codes, response JSON paths, SQL assertions or UI assertions.
Concrete assertions remain uncompiled.

A permission rule without a response outcome channel produces:

```text
EXECUTION_CONTRACT_PERMISSION_RESPONSE_OBSERVER_UNRESOLVED
```

A source-backed effect without any effect or response channel produces:

```text
EXECUTION_CONTRACT_EFFECT_OBSERVER_UNRESOLVED
```

## Snapshot requirements

The contract may require:

- a Before snapshot for preconditions or affected state;
- an After snapshot for effects or response outcomes;
- the same scenario entity identity across snapshots.

Database queries, API reads and UI observations remain uncompiled.

## Cleanup and safety

Read-only actions do not require cleanup.

`POST`, `PUT`, `PATCH` and `DELETE` actions require one of:

- a source-backed compensation;
- a reversible cleanup plan;
- an isolated disposable sandbox.

A write may never become executable merely because a Scenario IR exists. Destructive execution
without cleanup is explicitly forbidden.

## Statuses

### `REQUIREMENTS_READY`

The contract has authoritative action, evidence and Oracle channels. Runtime values, credentials,
queries and assertions may still be unmaterialized because they are outputs of later stages.

### `INCOMPLETE`

A structural execution requirement is missing, such as authoritative action, source evidence or
an observation channel.

### Gate statuses

- `PASS`
- `BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE`
- `BLOCKED_EXECUTION_CONTRACT_INCOMPLETE`
- `NO_EXECUTION_CONTRACT_COMPILED`

Even when the gate passes:

```text
execution_allowed = false
```

## Relationship graph

The asset graph adds:

```text
scenario_ir_to_execution_contract
execution_contract_to_interface
```

These relationships describe design authority. They do not mean that a request was sent.

## Legacy Probe compatibility

Once an asset declares the new scenario-planning chain, legacy candidate Probe generation is
fail-closed unless all of these gates pass:

- Scenario Planning Gate;
- Scenario IR Gate;
- Scenario Execution Contract Gate.

This prevents the historical risk-to-probe path from bypassing the formal contract.

## Explicitly not compiled

Version 1 does not compile:

- request payloads;
- field locations without source contract evidence;
- concrete identifiers;
- credentials or secrets;
- test fixtures;
- database connections or SQL;
- UI locators;
- concrete assertions;
- Before/After queries;
- cleanup calls;
- runtime environment selection;
- execution;
- Bug findings.

## Quality claim

Execution Contract Gate measures execution-requirement closure. It is not runtime executability,
test effectiveness, Bug recall or business-understanding accuracy.
