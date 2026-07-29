# QualiBug Runtime Materialization Contract v1

## Purpose

Runtime Materialization v1 converts a source-backed Runtime Plan into an auditable runtime draft.
It resolves the references required to describe one concrete future execution without performing
that execution.

The materialization layer may bind:

- one explicit test-environment reference and its target address;
- one existing `credential_ref` for each required actor;
- source-backed, non-sensitive semantic literals;
- approved runtime-input bindings;
- approved fixture references and value paths;
- approved generator descriptors;
- one request-body media type when the interface declares alternatives;
- one business-entity identity binding for before/after observation;
- one safe cleanup or sandbox-reset binding for writes.

It does not read a secret, execute a generator, connect to a service or database, serialize or send
an HTTP request, run setup or cleanup, evaluate an assertion, or classify a Bug.

## Upstream authority

```text
Business Behavior IR
  -> Implementation Binding
  -> Scenario IR
  -> Scenario Execution Contract
  -> Runtime Plan
  -> Runtime Materialization v1
```

Materialization is blocked unless the governed Runtime Plan Gate passes. The compiler consumes the
existing Runtime Plan and project runtime metadata. It does not reinterpret enterprise documents,
change behavior semantics, or create another interface authority.

## Environment binding

A runtime draft requires an explicit environment identity and target address.

Environment information may be split across existing project metadata:

```text
project environment_ref / environment kind / safety capabilities
+ unique enabled connector endpoint_ref
```

The governance projection may merge these values only when the connector is uniquely determined by
an exact interface reference, an exact environment reference, or the existence of exactly one
enabled connector. Multiple candidates are never selected by order.

Write scenarios additionally require affirmative evidence that the environment is non-production.
Accepted environment kinds include test, QA, SIT, UAT, staging and sandbox equivalents. An unknown
environment is not treated as test. An explicitly production environment produces:

```text
RUNTIME_MATERIALIZATION_PRODUCTION_WRITE_FORBIDDEN
```

No environment probe or network request is performed during materialization.

## Credential binding

Materialization retains only:

```text
credential_ref
actor_ref
environment_ref
security scheme placeholder
```

For example:

```text
{{secret_ref:credential-ref:warehouse-user}}
```

The placeholder is not a secret value. The compiler never reads or copies usernames, passwords,
tokens, cookies, API keys, private keys or client secrets.

A security requirement without a usable credential reference produces:

```text
RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED
```

Automatic role substitution and default-administrator selection are forbidden.

## Runtime value binding

### Source-backed semantic literal

A non-sensitive semantic value already proven by the Scenario Execution Contract may enter the
request draft, for example:

```text
status = approved
amount limit = 1000
permission decision = ALLOW
```

The value must remain a bounded JSON-compatible literal and must be compatible with the declared
request schema. Source-backed text is not permission to copy authentication material.

### Dynamic value

Entity IDs, tenant IDs, timestamps, sequence values and similar runtime-dependent values require an
explicit approved binding. Supported binding kinds include:

```text
APPROVED_RUNTIME_LITERAL
APPROVED_FIXTURE_LITERAL
APPROVED_FIXTURE_VALUE_REFERENCE
EXPLICIT_VALUE_REFERENCE
RUNTIME_GENERATOR_DESCRIPTOR
```

Missing or ambiguous bindings block materialization:

```text
RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_MISSING
RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_AMBIGUOUS
```

The compiler never creates example identifiers such as `123`, `test-order-1` or a current timestamp.

## Generator descriptor

The following generator descriptions may be retained:

```text
UUID
TIMESTAMP
SEQUENCE
RANDOM_SUFFIX
```

A descriptor produces a placeholder such as:

```text
{{generator:uuid}}
```

The generator is not executed. Its output, collision scope, persistence behavior and cleanup
identity remain responsibilities of a later execution-preparation layer.

## Fixture and test-data binding

Fixtures and test-data references must be explicitly approved. The materialization compiler may
copy a bounded, non-sensitive fixture literal or retain a value reference. It does not query a
database to locate an entity and does not run setup steps.

Unapproved, missing or ambiguous test data produces a blocking Unknown rather than a fabricated
entity.

## Sensitive fields

Fields matching authentication or secret-bearing semantics, including authorization headers,
passwords, tokens, API keys, cookies and private keys, cannot receive a source or fixture literal.
They must be injected later through a credential reference.

The blocking reason is:

```text
RUNTIME_MATERIALIZATION_SENSITIVE_FIELD_REQUIRES_CREDENTIAL_REF
```

The rejected literal is not copied into the request draft.

## Request-body media type

When an interface declares exactly one media type, the draft may retain it.

When multiple equivalent variants exist, the draft requires one explicit approved media-type
binding. It never chooses JSON, XML, form or multipart by declaration order.

```text
RUNTIME_MATERIALIZATION_MEDIA_TYPE_SELECTION_MISSING
RUNTIME_MATERIALIZATION_MEDIA_TYPE_SELECTION_AMBIGUOUS
```

Conflicting request schemas remain blocked by the upstream Runtime Plan governance layer.

## Request draft

A request draft may contain:

```text
method
interface_id
operation_id
base_url
path_draft
url_draft
query_draft
header_draft
cookie_draft
body_media_type
body_draft
form_field_drafts
security_placeholders
```

A path may contain approved literals, value references or generator placeholders. A body may contain
nested field drafts. This structure is for review and later compilation only.

It always preserves:

```text
request_serialized = false
request_sendable = false
network_call_allowed = false
```

## Entity identity and observation drafts

A uniquely resolved business-entity binding may feed a structured database observation draft:

```text
DATABASE_SNAPSHOT_QUERY_AST
```

The draft identifies a table reference, selected fields, phase and entity-identity binding. It does
not contain executable SQL and does not open a database connection.

HTTP observation becomes:

```text
HTTP_RESPONSE_SEMANTIC_ASSERTION_DRAFT
```

It retains capture requirements, permission semantics and source-declared response contracts. It
does not infer a successful status code or compile JSONPath assertions.

## Cleanup draft

Read-only scenarios retain `NO_CLEANUP_REQUIRED`.

A write scenario must bind either:

- one source-backed compensation operation; or
- a non-production disposable/resettable sandbox capability.

Multiple compensation candidates are not selected automatically. Direct database deletion is not a
fallback. Cleanup remains a draft and is never performed by this layer.

## Materialization statuses

### `DRAFT_READY`

All required environment, credential-reference, dynamic-value, test-data, media-type, identity and
cleanup bindings are closed enough to describe a future execution draft.

This status does not mean the draft is executable.

### `INCOMPLETE`

At least one required binding or safety proof remains unresolved.

## Runtime Materialization Gate

The Gate reports:

```text
PASS
BLOCKED_RUNTIME_MATERIALIZATION_UPSTREAM_PLAN_GATE
BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE
NO_RUNTIME_MATERIALIZATION_COMPILED
```

`PASS` permits only a later execution-compilation and validation stage. It never permits network or
database execution.

## Permanent false flags

Runtime Materialization v1 always preserves:

```text
execution_allowed = false
request_sendable = false
request_serialized = false
network_calls_allowed = false
secret_values_loaded = false
credential_injection_executed = false
generators_executed = false
test_data_setup_executed = false
database_queries_executable = false
assertions_executable = false
snapshots_materialized = false
cleanup_executable = false
cleanup_executed = false
bug_classification_allowed = false
```

## Legacy Probe guard

When the formal scenario pipeline exists, legacy Probe generation now requires:

```text
Scenario Planning Gate
Scenario IR Gate
Scenario Execution Contract Gate
Runtime Plan Gate
Runtime Materialization Gate
```

A missing, blocked or incomplete Materialization Gate returns zero Probes from both the public API
entrypoint and the direct linking entrypoint. Risk-only Probe fallback is not allowed.

## Current limitations

Runtime Materialization v1 does not:

- resolve a secret value from a secret store;
- authenticate to the target system;
- execute UUID, timestamp or sequence generators;
- create or locate a real business entity;
- serialize an HTTP request;
- open a network connection;
- compile SQL;
- execute before/after snapshots;
- infer concrete HTTP success or failure assertions;
- compile JSONPath assertions;
- execute setup, compensation or sandbox reset;
- compare observed and expected outcomes;
- decide whether a difference is a real Bug.
