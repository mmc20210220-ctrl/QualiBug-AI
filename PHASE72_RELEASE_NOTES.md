# Phase72 Release Notes — Confirmed Business World Model and Disposable Concurrency Evidence

## Scope

Phase72 advances the four remaining capability loops without adding a public
service, second orchestrator or unbounded LLM authority.

- **Business world model:** compiles OpenAPI/PRD entities, relation candidates
  and enum-state candidates; business-owner confirmation is required before a
  GET-only Oracle plan is generated.
- **Concurrency/async validation:** adds an approval-gated disposable sandbox
  that replays the same idempotency key in parallel and proves duplicate durable
  side effects through a subsequent GET observation.
- **LLM online evidence:** records provider health only after an actual
  OpenAI-compatible roundtrip returns `{"ok":true}`; credential and raw-response
  persistence is forbidden.
- **Cross-industry feedback:** aggregates approved, opt-in metadata only after
  independent confirmation in two industries; transfer changes priority only.

## Security and precision boundaries

- `safety_boundary.py` is unchanged.
- Production and undeclared targets remain blocked before sandbox writes.
- World-model and LLM inference never become formal findings by themselves.
- Concurrent verification requires a disposable sandbox, project flag,
  approval id, and per-call dual opt-in.
- Cross-industry aggregation excludes raw project IDs, payloads and notes.

## Measured local evidence

The Phase72 isolated verification target intentionally creates four durable
orders for four concurrent writes with one idempotency key. The sandbox emits
one P0 `concurrent_idempotency_violation` with `runtime_strong` evidence only
when the explicit sandbox gates are satisfied. The same scenario is blocked by
default.

An OpenAPI `order.customer_id -> customer` candidate produces no probe before
business-owner approval and one GET-only plan after approval. A local
OpenAI-compatible provider returns the exact health contract and no key is
persisted. Two opt-in industries with independent approved confirmations create
one priority-only cross-industry transfer hint.
