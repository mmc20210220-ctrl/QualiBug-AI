# Phase75 — Agent Experiment Compiler & Evidence Dispatch

## Purpose

Phase74 established the canonical Agent Loop ledger and selected the next
highest-information experiment. Phase75 closes the next gap: a planned
experiment now becomes a persistent, reproducible execution packet that can be
sent to the existing safe-read or disposable-sandbox executor without creating
a second scheduler, state store or safety system.

The runtime does not know how many Bugs exist in a target. It manages unknown
business hypotheses, their runnable experiments, evidence receipts and human
review state.

## Canonical state

The project-local SQLite ledger remains the single source of truth. Phase75
adds `loop_experiments` to the same database; it stores:

- the parent hypothesis or document contract item;
- a deterministic scenario fingerprint;
- precondition, mutation, verification and cleanup blueprint;
- executor receipt state (`COMPILED`, `BLOCKED_BY_APPROVAL`,
  `BLOCKED_BY_FIXTURE`, `EXECUTED`, `EVIDENCE_CAPTURED`);
- redacted execution receipt.

The CSV remains a human review projection only. Scenario packs and execution
receipts are output artefacts, not an alternative state database.

## Experiment packet

Each document-backed scenario has four phases:

1. **Preconditions** — explicit fixture requirements, project sandbox policy,
   required headers and path parameters.
2. **Fixture creation** — only from an explicit project fixture catalog. The
   Agent does not invent foreign-key data.
3. **Mutation** — a small PRD/API-backed invalid value, duplicate create,
   repeated idempotency key or unauthorised read.
4. **Verification** — documented rejection, one business identity, or declared
   role boundary; a formal Bug still requires runtime evidence and human
   verdict.

Write scenarios are never auto-approved. They must pass the existing document
contract sandbox gate: target environment `sandbox`, disposable sandbox,
`execute=true`, `approved_sandbox_execution=true` and a non-empty approval ID.
The shared safety boundary is checked again before fixture creation.

## Optional fixture catalog

A project may opt in to automatic precondition construction by providing an
explicit catalog in `real_project_config.json`:

```json
{
  "agent_discovery_loop": {
    "fixture_catalog": [
      {
        "fixture_id": "material",
        "method": "POST",
        "path": "/master/materials",
        "role": "PLANNER",
        "body": {
          "code": "MAT-${run_key}",
          "name": "QualiBug sandbox fixture"
        },
        "captures": {
          "materialCode": "data.code"
        }
      }
    ],
    "fixture_bindings": {
      "materialCode": {
        "fixture_id": "material",
        "context_key": "materialCode"
      }
    }
  }
}
```

The compiler creates no fixture when a referenced field has no explicit
binding. It records `BLOCKED_BY_FIXTURE` instead. Fixture values are preserved
when a mutation namespaces an owned key, so a valid foreign reference is not
silently corrupted by the test harness.

## Commands

Compile planned scenarios without target traffic:

```bash
python -m aitestops.cli agent-experiments \
  --project <project_id> --root . --max-experiments 24
```

Execute only after the project itself enables a disposable sandbox and a human
provides an approval ID:

```bash
python -m aitestops.cli agent-experiments \
  --project <project_id> --root . --execute --approval-id <approval_id>
```

This command delegates target execution to the existing document-contract
sandbox executor. It cannot auto-confirm findings or create regression guards.
Those transitions remain governed by deterministic evidence plus human review.
