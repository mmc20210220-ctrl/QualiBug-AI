# Business Behavior IR Implementation Binding Contract

## Purpose

This contract binds source-backed Business Behavior IR to observable enterprise-system surfaces.
It closes the gap between what enterprise materials say and where that behavior can be exercised
or observed in the system.

It does **not** create executable tests, request payloads or expected assertions.

## Mainline

```text
enterprise materials
  -> accepted Chinese business facts
  -> Business Behavior IR
  -> governed implementation bindings
  -> final scenario-planning gate
  -> later scenario compiler
  -> later execution planner
```

## Separate authorities

Three gates remain separate:

1. `enterprise-understanding-model-gate`
   - decides whether the source-backed business meaning is closed;
2. `business-behavior-implementation-binding-gate`
   - decides whether behaviors are bound to authoritative action and observation surfaces;
3. `business-behavior-scenario-planning-gate`
   - allows scenario generation only when both preceding gates pass.

A missing endpoint cannot rewrite business meaning. A well-understood business rule cannot
substitute for a missing implementation binding.

## Action binding

An action entry may become authoritative only through source-backed evidence such as:

- an accepted rule/behavior/operation relationship to an interface;
- one exact operation identity in the interface contract;
- another explicit binding accepted by the governed binding layer.

Token overlap, list position, nearest endpoint and first-write-endpoint fallback are diagnostic
only. They may not select an executable action.

Exactly one authoritative primary interface is required for scenario planning. Multiple accepted
interfaces remain ambiguous unless the source explicitly defines orchestration or alternatives.

## Condition observers

Every formal precondition must have an observable binding.

Currently accepted observer families include:

- an exact database field whose table identity matches the behavior object;
- an explicit runtime state observer;
- an explicit API response field observer;
- an explicit UI state observer.

An API request-contract field is not, by itself, proof that a condition can be observed at
runtime. Field aliases are not inferred automatically.

## Effect observers

A behavior needs at least one effect or outcome observation channel:

- exact database field;
- runtime state observer;
- API response outcome channel;
- another explicit source-backed observer.

A response channel proves only that an outcome can be inspected. It does not mean the expected
assertion has already been compiled.

## UI binding

An exact UI label or component name creates a design candidate only:

```text
CANDIDATE_DESIGN_BINDING
```

It is not executable until a concrete locator, page state, account context and navigation path are
available.

## Final scenario-planning gate

The final gate passes only when all are true:

- the semantic understanding gate passes;
- the implementation binding gate passes;
- each behavior is confirmed;
- one authoritative action entry exists;
- all formal preconditions are observable;
- an effect or outcome channel exists;
- no binding is ambiguous or conflicted.

The gate always declares:

```text
execution_allowed = false
request_payload_compiled = false
expected_assertion_compiled = false
runtime_environment_validated = false
```

Passing this gate allows only the next scenario-compilation stage.

## Fail-visible conditions

Examples include:

```text
BEHAVIOR_API_BINDING_UNRESOLVED
BEHAVIOR_API_BINDING_AMBIGUOUS
BEHAVIOR_API_BINDING_MULTIPLE_AUTHORITATIVE
BEHAVIOR_AUTHORITATIVE_INTERFACE_MISSING
BEHAVIOR_API_BINDING_CONFLICT
IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED
IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED
IMPLEMENTATION_FIELD_OBJECT_TABLE_UNRESOLVED
API_CONTRACT_FIELD_IS_NOT_RUNTIME_OBSERVER
IMPLEMENTATION_BEHAVIOR_NOT_CONFIRMED
BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE
```

No unresolved condition may be cleared by endpoint similarity or field-name proximity.

## Asset projection

The final enterprise asset exposes:

```text
behavior_implementation_bindings
implementation_binding_unknowns
implementation_binding_conflicts
implementation_binding_relationships
implementation_binding_gate
scenario_planning_gate
```

The summary exposes semantic readiness, implementation readiness and final scenario readiness as
separate values.

## Governance prohibitions

The implementation-binding stage must never:

- alter Business Behavior IR meaning;
- promote a candidate behavior to confirmed;
- treat token overlap as authoritative;
- treat a UI design label as an executable locator;
- treat request fields as runtime observers;
- choose the first or nearest endpoint;
- generate a request body;
- generate an expected assertion;
- claim runtime executability;
- execute against the enterprise system.

## Current limitations

This phase does not yet provide:

- request payload compilation;
- account and role credential selection;
- test-data construction;
- concrete UI locators and navigation paths;
- database connection and transaction plans;
- expected response/body assertion compilation;
- before/after snapshot plans;
- cleanup and compensation execution;
- sandbox or production safety validation;
- automatic test scenario generation;
- runtime execution or bug findings.

Those belong to later, separately gated stages.
