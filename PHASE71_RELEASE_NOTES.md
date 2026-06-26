# Phase71 Release Notes — Project-Scoped Data Isolation

## Objective

Phase71 closes a P0 cross-project data-read path discovered through a deep,
evidence-first analysis of QualiBug's own private service. It does not add a
new scanning framework, UI surface or mutation probe.

## Discovery capability

The existing consistency/isolation engine now supports explicit
`project_scope_contracts`. The contract is intentionally narrow:

- OpenAPI must declare the GET path and its project query parameter;
- an enterprise must supply isolated authentication plus own/foreign project
  mapping; and
- only a successful foreign-project response creates P0 evidence.

This catches a class that tenant-row and role-only checks cannot represent:
an authenticated user changing a project/workspace selector to read another
customer's data.

## Remediation

The private service requires `X-QualiBug-Project-Scopes` outside localhost-only
development mode. The trusted reverse proxy is responsible for injecting the
explicit project allow-list. Requests outside the list return HTTP 403 before
the route loads project data.

## Scope

Phase71 uses GET-only checks, preserves the shared production safety boundary,
redacts project identities and credentials from evidence, and retains LLM output
as `unverified_hypothesis` only.

The measured Phase71 release verifier passed source compilation, the full
95-test regression suite, product UI tests, customer-text checks and private
service smoke checks.