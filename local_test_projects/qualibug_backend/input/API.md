# API Contract

### /run?q=smoke

- Capability: C03 authentication boundary for executing the engine.
- Actors: authenticated demo user, service operator.
- Must validate: auth, tenant.
- Failure statuses: 401, 403.

### /graph

- Capability: C03 authentication boundary for tenant graph data.
- Actors: authenticated demo user, service operator.
- Must validate: auth, tenant.
- Failure statuses: 401, 403.

### /logs

- Capability: C31 diagnostic log privacy boundary.
- Actors: service operator.
- Must validate: auth, tenant.
- Failure statuses: 401, 403.

### /metrics

- Capability: C31 diagnostic metric privacy boundary.
- Actors: service operator.
- Must validate: auth, tenant.
- Failure statuses: 401, 403.

### /health

- Capability: C17 public health contract.
- Actors: anonymous health checker.
- Must validate: response schema.

