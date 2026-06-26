# Phase71 — Explicit Project-Scope Isolation Oracle

## Problem

Role headers answer *who* may act; they do not answer *which project* that
principal may read or change. A service that accepts `?project=` therefore has
a separate isolation boundary from RBAC or response-level tenant checks.

## Oracle

Phase71 extends `consistency_isolation_reasoning` rather than adding a new
engine. An enterprise may declare a `project_scope_contracts` entry only for an
OpenAPI-declared GET endpoint and must provide:

- the project query parameter;
- an isolated authenticated context;
- the context's allowed project; and
- a distinct external project.

The Oracle requests the allowed project and then the external project with the
same identity. It reports P0 only when the external request returns a configured
success status. An allowed-project denial is a separate P2 availability or
scope-mapping issue.

## Safety

- GET only; no writes, replay, load test or destructive operation.
- Target must pass the shared non-production safety verdict before the first
  request.
- Request headers and credentials are redacted; project identifiers and response
  shapes are hashed in persisted evidence.
- LLM output remains a candidate only and cannot create a project-scope finding.

## Service boundary

The private service now checks `X-QualiBug-Project-Scopes` after trusted actor
and role checks for every non-health GET and POST route. Public/private-cloud
bindings require the trusted reverse proxy to supply the allow-list. The
localhost-only development fallback remains narrow and is disabled by public
binding or an explicit negative-auth probe.