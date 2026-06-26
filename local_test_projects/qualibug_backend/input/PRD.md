# QualiBug Backend Local Test Project

## 1. Scope

This local test project describes the bundled `backend.main` FastAPI service used for runtime validation.
It is a non-production localhost target.

## 2. Roles And Access Rules

- All diagnostic and tenant-specific endpoints except `/health` must require authentication.
- Anonymous callers must not receive tenant graph data, run traces, metrics, logs, or internal execution state.
- Tenant-scoped data must not be shared across anonymous, guest, and authenticated demo users.

## 3. Data And Evidence

- Runtime bug reports must include the request, response status, response body summary, and the source rule that was checked.
- A finding is not customer-signable until runtime evidence confirms the behavior.

