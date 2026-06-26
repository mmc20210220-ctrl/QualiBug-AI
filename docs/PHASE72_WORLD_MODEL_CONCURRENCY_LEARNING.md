# Phase72: Confirmed Business World Model, Disposable Concurrency Evidence and Cross-Industry Learning

## Purpose

Phase72 closes four gaps that block QualiBug from becoming a continuous
high-value business-defect discovery platform: deriving a shared business model,
proving retry/concurrency failures safely, distinguishing configured LLMs from
reachable LLMs, and reusing confirmed patterns without leaking customer data.

## 1. Business world model: inference is not a rule

`business_world_model.py` derives only schema-grounded candidates:

- entities from OpenAPI resources and response fields;
- referential relations when a response exposes an `*_id` whose target resource
  exists in the same OpenAPI;
- state-machine candidates when a documented `status`/`state` field has an enum.

Every candidate has `needs_human_confirmation` and `candidate_only`. A business
owner must add an explicit approval under `business_world_model.confirmations`.
Only then does the compiler produce a `confirmed_contract`, and the planner may
create a GET-only probe. It never auto-creates a formal defect.

## 2. Concurrency/async sandbox: evidence without unsafe load testing

`concurrency_async_sandbox.py` is not general load testing. It executes a
write replay only when all of the following are true:

1. target environment is exactly `sandbox`;
2. project configuration explicitly enables destructive tests;
3. `concurrency_async_sandbox.enabled=true` and `disposable=true`;
4. a nonempty approval id exists;
5. caller passes both `execute=true` and `approved_sandbox_execution=true`.

A contract must provide a write path, one idempotency-key injection mechanism,
a GET observation path and a numeric result field. All parallel requests reuse
the same logical idempotency key. A duplicate-side-effect finding exists only if
the follow-up GET observes a value above the configured maximum. Request
headers/body are redacted and the idempotency key is hashed in evidence.

## 3. LLM health: configured is not online

`probe_provider_health()` issues a bounded OpenAI-compatible health request and
persists `online=true` only after the provider returns parseable `{"ok":true}`.
The record stores a base-URL digest, model, status and redacted failure class;
it stores no key, prompt or raw response. An offline provider never changes
deterministic discovery behavior. LLM-generated Oracle ideas remain
`unverified_hypothesis` and require deterministic replay.

## 4. Cross-industry learning: consent and independent confirmation

`cross_industry_confirmed_learning.py` reads only approved flywheel metadata
from projects with `cross_industry_learning.share_confirmed_metadata=true`.
It stores no raw project identifier, payload or review note. A pattern becomes a
transfer hint only after the same risk/oracle family is independently confirmed
in at least two industries. The resulting maximum `0.06` bonus changes local
probe priority only; it never changes severity, execution policy or formal
finding status.

## Verification boundary

`tools/phase72_verification.py` uses a local disposable service and local
OpenAI-compatible test server. It proves that:

- an OpenAPI-derived relation becomes a GET-only probe only after approval;
- the concurrency sandbox is blocked by default, then detects a deliberate P0
  duplicate side effect under explicit approval;
- provider health reaches `online` only after a real local protocol roundtrip
  and persists no test key;
- two opt-in industries create one priority-only transfer hint.

This verifies protocol and governance in isolation. It does **not** claim a
third-party LLM provider is reachable from the build environment.
