# QualiBug AI Phase62 Release Notes

## Goal

Phase62 advances the business Bug discovery core without adding a second
runtime, frontend, orchestration framework or defect catalog. It extends the
existing metamorphic differential engine with one evidence-backed relation:
**explicit business partition conservation**.

## What Changed

- A configured filter can now declare `complete_partition: true`.
- Only for that explicit contract, QualiBug executes bounded GET-only variants
  for every configured value and compares their business identities against a
  baseline collection.
- It reports a verified counterexample when a baseline record is omitted,
  appears only in a filtered variant, or appears in more than one mutually
  exclusive category.
- Per-contract execution evidence is now persisted even when no defect is
  found, so a clean execution is observable evidence rather than an empty log.
- LLM output is stored only as `unverified_hypothesis`; it never enters the
  defect list or the evidence registry without deterministic observation.
- Onboarding, main discovery and metamorphic differential execution all reuse
  the existing hard safety boundary before any target reachability or login
  request. Production or undeclared live targets are blocked.

## Minimal Project Configuration

```json
{
  "target_environment": "staging",
  "metamorphic_differential_execution_mode": "safe_live",
  "metamorphic_differential_reasoning": {
    "contracts": [
      {
        "path": "/orders",
        "identity_fields": ["order_id"],
        "filters": [
          {
            "parameter": "status",
            "field": "status",
            "values": ["created", "paid", "cancelled"],
            "complete_partition": true
          }
        ]
      }
    ]
  }
}
```

`complete_partition` is intentionally opt-in. An OpenAPI enum alone does not
prove that a field is exhaustive or mutually exclusive, so QualiBug will not
invent this Oracle.

## Verification Evidence

- **89/89 test cases passed** in deterministic grouped regression runs after the
  final safety propagation change. Coverage included the business core (35),
  control-plane and safe-HTTP boundary (20), queue/multi-industry/multi-source
  reasoning (14), self-dogfood (1), and temporal/lifecycle/metamorphic/release/
  product UI paths (19).
- The new adversarial test demonstrates a real counterexample: an order can
  disappear from the `created` filter response while every returned row still
  satisfies `status=created`; the partition-conservation relation detects the
  omission with deterministic evidence.
- A production or undeclared target is blocked before any direct HTTP request.
  A top-level unsafe target also downgrades every invoked discovery sub-engine
  to `plan_only`, so the safety boundary cannot be bypassed through fan-out.
- Python source compilation completed successfully.

The canonical monolithic `pytest -q` invocation did not finish in this
container: it stalled after approximately 37 tests without a failing assertion.
The same 89 cases passed when run in bounded groups, including the suspected
lifecycle-to-Saga sequence. This is therefore **not** a formal GA manifest;
CI should run the canonical release verification workflow in a clean runner
before release approval.
