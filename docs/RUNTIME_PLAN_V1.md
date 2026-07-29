# QualiBug Runtime Plan v1 Contract

## Purpose

Runtime Plan v1 converts a source-backed Scenario Execution Contract into a deterministic,
non-executable runtime template. It answers:

- which authoritative interface would be used;
- where each request field belongs;
- which runtime source must provide each value;
- which actor credential reference is required;
- which observation templates are required before and after the action;
- which cleanup obligations must be bound before any write;
- which environment capabilities are required.

It does not send a request, select a secret value, execute SQL, compile a concrete assertion,
perform cleanup or report a Bug.

## Upstream authority

```text
Business Behavior IR
  -> Implementation Binding
  -> Scenario IR
  -> Scenario Execution Contract
  -> Runtime Plan v1
```

Runtime Plan compilation is blocked unless the Scenario Execution Contract Gate passes.
The compiler consumes the governed contract and retained source interface metadata. It does not
re-read business documents or reinterpret business semantics.

## OpenAPI runtime metadata

The additive OpenAPI runtime-contract wrapper preserves:

- Path, Query, Header, Cookie, Form and Body locations;
- required flags;
- schema type, format and enum declarations;
- request-body field paths and media types;
- response status declarations and response field paths;
- security scheme names and scopes.

It never stores request example values, API keys, tokens, passwords or credential payloads.
The original `interface_id`, method, path and operation identity remain unchanged.

## Request template

Each source-declared request slot contains:

```text
field
location
required
schema_type / format / enum
value_source
location_derivation
runtime_value_materialized = false
```

A value source can be:

- `SOURCE_BACKED_SEMANTIC_VALUE`;
- `RUNTIME_ENTITY_IDENTIFIER`;
- `RUNTIME_REQUIRED_INPUT`;
- another explicit source named by the upstream contract.

The compiler may resolve a location only from the source interface contract. It may never place an
unknown field into JSON Body, Query, Header or Cookie by convention. Missing or ambiguous required
locations produce:

```text
RUNTIME_PLAN_REQUEST_FIELD_LOCATION_UNRESOLVED
RUNTIME_PLAN_REQUEST_FIELD_LOCATION_AMBIGUOUS
```

These gaps block Runtime Plan closure.

## Credentials

Runtime Plan reads only `credential_ref` metadata. It never reads or copies username, password,
API key, token, cookie or private-key values.

A unique exact actor-to-credential-ref mapping may be retained as a reference. No mapping creates a
runtime credential slot; it does not trigger a default administrator or current-user selection.
Multiple exact refs for one actor produce:

```text
RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS
```

The ambiguity blocks plan closure. Secret values remain unloaded in all cases.

## Oracle query templates

Runtime Plan can emit structured templates such as:

```text
DATABASE_FIELD_SNAPSHOT
HTTP_RESPONSE_CAPTURE
```

A database template identifies table, field, phase and entity-identity scope. It is not SQL and
opens no database connection. An HTTP response template declares capture requirements and retained
source response contracts. It does not infer a successful status code or JSON assertion.

All concrete assertion flags remain false.

## Snapshot template

Before and after templates reference observer templates and require the same scenario entity
identity. No identifier is materialized and no snapshot query is executed during compilation.

## Cleanup step templates

Read-only actions receive a `NO_CLEANUP_REQUIRED` template.

Write actions require one of:

- a source-backed compensation operation that must later be bound;
- an isolated disposable sandbox or a separately proven reversible write.

A template can include:

```text
CAPTURE_MUTATED_ENTITY_IDENTITY
BIND_SOURCE_BACKED_COMPENSATION_OPERATION
REQUIRE_ISOLATED_SANDBOX_RESET_OR_BOUND_REVERSAL
VERIFY_CLEANUP_RESTORED_STATE
```

Templates are obligations, not executable cleanup actions. Direct database deletion is never an
automatic fallback.

## Plan statuses

### `TEMPLATE_READY`

All required request locations, Oracle templates and cleanup template structure are closed.
Runtime values, credentials and environment validation may still be outstanding runtime inputs.

### `INCOMPLETE`

A blocking template gap remains, including unresolved field location, ambiguous credential ref,
missing Oracle template, missing evidence or missing cleanup strategy.

## Runtime Plan Gate

The Gate reports:

```text
PASS
BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE
BLOCKED_RUNTIME_PLAN_INCOMPLETE
NO_RUNTIME_PLAN_COMPILED
```

`PASS` permits only the next materialization stage. It never enables execution.

## Permanent false execution flags

Runtime Plan v1 always preserves:

```text
execution_allowed = false
network_calls_allowed = false
request_values_materialized = false
http_request_compiled = false
credentials_loaded = false
test_data_materialized = false
database_queries_executable = false
oracle_assertions_compiled = false
snapshots_materialized = false
cleanup_actions_executable = false
runtime_environment_validated = false
```

## Legacy Probe guard

When the formal scenario pipeline is present, legacy Probe generation requires the downstream
Runtime Materialization Gate in addition to the design gates:

```text
Scenario Planning Gate
Scenario IR Gate
Scenario Execution Contract Gate
Runtime Plan Gate
Runtime Materialization Gate
```

A missing, Partial or Blocked Runtime Plan returns zero legacy Probes. A Runtime Plan `PASS` with a
missing or Blocked Runtime Materialization also returns zero Probes. Both the public API entrypoint
and the direct linking entrypoint are fail-closed; risk-only Probe fallback is not permitted.

## Current limitations

Runtime Plan v1 does not:

- materialize IDs, timestamps, amounts or entity fixtures;
- choose or load credentials;
- decide an unresolved request-body media type;
- build a concrete HTTP request;
- generate SQL or open a database connection;
- infer success and failure status codes;
- compile JSONPath or database assertions;
- bind a compensation candidate to a concrete cleanup endpoint;
- validate a target environment;
- execute a scenario;
- classify a runtime difference as a Bug.
