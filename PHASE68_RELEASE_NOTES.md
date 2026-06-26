# Phase68 Release Notes — Evidence-Backed Negative Authentication Probing

## Scope

Phase68 focuses on detection precision and private-service read protection. It
fixes a real self-dogfood P1 found by QualiBug's own negative-authentication
probe: non-health GET routes accepted an anonymous viewer by default, so a
private-cloud deployment could expose pages or read-only project data when a
trusted reverse proxy header was absent.

## What changed

### 1. Negative authentication probes now test the intended boundary

The scan endpoint's unauthenticated GET probes now send the existing
`X-QualiBug-No-Local-Dev: 1` header. This explicitly disables the localhost
convenience identity before a `200` response is treated as evidence of missing
authentication.

A `200` is therefore no longer ambiguous between:

- an intentional localhost-only development fallback; and
- a real anonymous read path.

This preserves recall because a target that truly ignores authentication still
returns `200`, while eliminating incorrect conclusions caused by the local
development harness itself.

### 2. Non-health GET routes now require the existing trusted actor boundary

`PrivatePilotHandler.do_GET()` now calls the established `_require_actor()`
boundary for every non-health route. No new authentication framework was added.

- Public binding still requires `QUALIBUG_ALLOW_PUBLIC_BIND=1`.
- Public/private-cloud callers without trusted actor and role headers receive
  `401`.
- Localhost development can still use the existing constrained local actor
  fallback, only when public binding is disabled and the caller did not opt out
  through `X-QualiBug-No-Local-Dev: 1`.
- `/health` remains intentionally unauthenticated for local service readiness.

## Measured self-dogfood delta

Using the identical isolated self-dogfood inputs:

| Metric | Before | After |
|---|---:|---:|
| Total findings | 18 | 13 |
| P1 findings | 10 | 5 |
| Missing-auth findings | 5 | 0 |
| Self-dogfood audit findings | 0 | 0 |

The removed five P1 items were not suppressed. They disappeared only after the
negative probe confirmed `401` and the read boundary was repaired.

## Boundaries preserved

- `safety_boundary.py`, test files and `__init__.py` files were not changed.
- No destructive test mode, target environment or production-write rule changed.
- No credentials, logs, runtime outputs or self-dogfood artifacts are included
  in the delivery archive.
- LLM output remains advisory and cannot become a confirmed defect without
  deterministic replay.

## Provider verification status

The configured OpenAI-compatible provider remains unverified from this
isolated build runtime because outbound DNS/network access is unavailable. This
phase does not claim a successful external model call.
