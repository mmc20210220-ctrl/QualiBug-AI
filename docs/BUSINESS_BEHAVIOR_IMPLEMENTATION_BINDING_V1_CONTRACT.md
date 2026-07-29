# Business Behavior IR → System Implementation Binding v1

## Purpose

This contract binds governed Business Behavior IR to observable enterprise-system surfaces.
It answers four implementation questions without changing the business meaning:

1. Which system action surface performs the behavior?
2. Which source-backed fields can establish its preconditions?
3. Which source-backed channels can observe its effects?
4. Is there enough evidence to enter scenario planning?

It does **not** compile requests, assertions, credentials, test data or executable probes.

## Mainline

```text
accepted enterprise facts / governed decision rows
  -> Business Behavior IR v1
  -> implementation binding base observations
  -> implementation binding governance
  -> behavior implementation bindings
  -> independent implementation binding gate
  -> scenario planning (later phase)
```

The enterprise-understanding gate and implementation-binding gate are independent:

- the understanding gate states what the enterprise materials say;
- the implementation gate states whether that meaning is connected to observable system surfaces.

Missing endpoints or observers must not retroactively invalidate a source-backed business fact.
They block scenario planning instead.

## Action binding authority

An API action binding is authoritative only when one of these conditions holds:

- an existing accepted source-backed relationship resolves the behavior or its source rule to an interface;
- one and only one interface has an exact normalized operation identity.

Token overlap, nearest-path selection and first-endpoint fallback are diagnostic only.

Scenario planning requires exactly one primary authoritative API action in v1. Multiple accepted
interfaces remain visible but produce:

```text
BEHAVIOR_API_BINDING_MULTIPLE_AUTHORITATIVE
```

A relationship pointing to an interface that is no longer present produces:

```text
BEHAVIOR_AUTHORITATIVE_INTERFACE_MISSING
```

The binder may still display an exact-name candidate, but it cannot use that candidate to clear
the missing-authority gap.

When an accepted relationship and exact operation identity point to different interfaces, the
binding is blocked with:

```text
BEHAVIOR_API_BINDING_CONFLICT
```

## Condition and effect observers

A database field is a formal observer only when:

- its field identity matches exactly;
- its table identity matches one of the behavior's business objects;
- the result does not span multiple object-compatible tables.

A same-named field on an unrelated object is not sufficient and produces:

```text
IMPLEMENTATION_FIELD_OBJECT_TABLE_UNRESOLVED
```

An API request/contract field is a controllable contract candidate, not a runtime observer. It is
kept as evidence with:

```text
API_CONTRACT_FIELD_IS_NOT_RUNTIME_OBSERVER
```

Future formal observer kinds may include explicit API response fields, runtime state observers
and UI state observers. Their provider must supply source-backed identities.

## Response outcome channel

A uniquely bound API can expose a response outcome channel for permission decisions such as
ALLOW or DENY. This is enough to plan a future assertion surface, but not enough to execute:

```text
status = BOUND_CHANNEL_ONLY
expected_assertion_compiled = false
```

The implementation gate therefore always keeps:

```text
execution_allowed = false
```

until a later assertion compiler defines the exact expected status, response body, database
delta or UI state.

## UI binding

An exact UI label or component name may produce a design binding. Without a stable executable
locator, it remains:

```text
status = CANDIDATE_DESIGN_BINDING
authoritative = false
executable_locator_available = false
```

The system must not manufacture CSS selectors, XPath expressions or coordinates from a design
label.

## Scenario-planning gate

A behavior binding becomes scenario-planning ready only when all conditions hold:

- the Business Behavior IR status is `CONFIRMED`;
- a multi-condition behavior has an explicit combinator;
- exactly one primary authoritative API action exists;
- every explicit precondition has a formal runtime observer;
- at least one formal effect observer or API response channel exists;
- no action-binding conflict or observer ambiguity remains.

Gate statuses:

```text
PASS
PARTIAL_IMPLEMENTATION_BINDING
BLOCKED_IMPLEMENTATION_BINDING_CONFLICT
NO_BEHAVIOR_IMPLEMENTATION_BINDING
```

`PASS` permits scenario planning only. It does not permit execution.

## Graph projection

The asset exposes governed relationship edges:

- `behavior_to_interface` — accepted only for an authoritative action binding;
- `behavior_condition_observed_by_field` — accepted only after field and object-table identity;
- `behavior_effect_observed_by_field` — same observer authority rules;
- `behavior_effect_observed_by_api_response` — accepted response channel, assertion not compiled;
- `behavior_to_ui_design_candidate` — candidate only;
- `behavior_field_candidate_in_interface` — candidate only because request fields are not observers.

Original relationships are preserved and deduplicated by edge identity.

## Fail-visible rules

The following conditions cannot be silently repaired:

- no authoritative action surface;
- multiple authoritative action surfaces;
- stale authoritative interface reference;
- authoritative relationship and exact operation identity conflict;
- condition field missing;
- field exists only on an unrelated business object;
- same field exists on multiple compatible tables;
- request contract field is the only purported observer;
- effect has no observable channel;
- Business Behavior IR is still candidate, incomplete or conflicted.

## Runtime metrics

The binding gate exposes:

- behavior binding count;
- scenario-ready binding count and rate;
- bound, partial, unbound, ambiguous and conflicted counts;
- implementation unknown count;
- implementation conflict count.

These are closure metrics, not runtime correctness, test coverage or bug-detection accuracy.

## Current limitations

Version 1 does not yet provide:

- request-payload construction;
- authentication or test-account selection;
- identity-value acquisition;
- database connection selection;
- response-schema assertion compilation;
- before/after snapshot planning;
- UI executable locator recovery;
- log, event or message-queue observers;
- multi-endpoint operation orchestration;
- transaction and compensation execution;
- executable test scenarios.

Those capabilities must consume this governed binding layer rather than bypass it.
