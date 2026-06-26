# Business Rules

## C03 Authentication And Authorization

- Rule 1: `/run`, `/replay`, `/metrics`, `/graph`, and `/logs` must reject anonymous requests with 401 or 403.
- Rule 2: Tenant graph and trace data must only be returned to an authenticated actor.
- Rule 3: Diagnostic logs must never be visible to anonymous callers.

## C05 Tenant Data Isolation

- Rule 1: Cached results, graph nodes, traces, and log entries must be scoped by tenant.
- Rule 2: Anonymous and guest callers must not read data created by an authenticated demo tenant.

## C31 Audit And Privacy

- Rule 1: Runtime logs and traces must avoid exposing credentials, authorization headers, and cross-tenant business data.
- Rule 2: Access to diagnostic logs must be audited and role-restricted.

