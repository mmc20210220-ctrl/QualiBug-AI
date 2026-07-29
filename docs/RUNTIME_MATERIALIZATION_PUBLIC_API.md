# Runtime Materialization Public API Contract

## Single package authority

The public package surface is:

```python
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_runtime_materializations_v1,
    project_runtime_materializations_to_asset,
)
```

For backward compatibility, these names are retained. They now resolve to:

```text
build_secure_runtime_materializations_v1
project_secure_runtime_materializations_to_asset
```

Both entrypoints apply the governed environment/connector merge, request-value security checks,
test-data source validation, readable Unknown receipts and final Gate recalculation.

## Builder behavior

`build_runtime_materializations_v1(asset, model)` is the detached builder authority.

It deep-copies the caller-owned asset and model, runs the complete secure projection on those
working copies, and returns:

```text
runtime_materializations
runtime_materialization_unknowns
runtime_materialization_gate
```

The caller-owned input objects are not mutated.

An unapproved value, required `null`, empty test-data binding, missing environment identity or any
other blocking safety gap remains visible in the returned Unknowns and Gate. Such a value is not
copied into the returned draft.

## Projection behavior

`project_runtime_materializations_to_asset(asset, model)` is the in-place projection authority used
by the enterprise-understanding mainline.

It projects the same secure collections into the existing knowledge asset and understanding model,
updates relationships, summaries, coverage gaps and governance, and never enables execution.

## Internal compiler primitives

The low-level module retains explicit internal primitives for layered implementation and tests:

```text
build_runtime_materializations_core_v1
project_runtime_materializations_core_to_asset
project_governed_runtime_materializations_to_asset
```

These names are not exported through the package `__all__` contract. Their intermediate output is
not an execution or scan authority and must not be used to bypass the secure projection, the Runtime
Materialization Gate or the legacy Probe guard.

## Permanent safety boundary

Both public entrypoints preserve:

```text
execution_allowed = false
request_sendable = false
request_serialized = false
network_calls_allowed = false
secret_values_loaded = false
database_queries_executable = false
assertions_executable = false
cleanup_executable = false
bug_classification_allowed = false
```

A package API compatibility name does not weaken these guarantees.
