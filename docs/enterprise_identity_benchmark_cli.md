# Enterprise Identity Benchmark CLI

## Purpose

Use the real enterprise identity annotation and benchmark workflow without operating the
Settings page manually. The CLI is intended for customer onboarding scripts, CI jobs and
enterprise AI Agents.

It does not implement a second identity engine. Ground Truth persistence, enterprise
understanding rebuilds, quality Gates, audit events and version snapshots still pass
through the existing transactional benchmark workflow.

## Installation entry point

After installing the project:

```bash
pip install .
qualibug-identity-benchmark --help
```

It can also run directly from a source checkout:

```bash
python -m ai_test_asset_center.identity_benchmark_cli --help
```

All command output is JSON.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Command completed and the authoritative Gate still allows downstream entry |
| `1` | Invalid input, unauthorized operator role, stale package, missing project asset or execution failure |
| `2` | Double-blind submissions disagree and independent adjudication is required |
| `3` | Ground Truth was imported or measured, but the enforced identity quality Gate denies downstream entry |

The final authority is the backend Gate field `entry_allowed`. A threshold may have a
`BLOCKED_*` reporting status while policy enforcement is disabled; when
`entry_allowed=true`, the CLI does not return exit code `3`. This preserves the difference
between report-only diagnostics and an enforced release/admission block.

An imported result and a passing quality result are deliberately separate states. Exit
code `3` prevents an Agent or CI job from treating a successfully persisted but
low-quality identity model as ready for downstream business understanding.

## Write-authorized operator roles

`import` and `remeasure` reuse the existing knowledge-management authorization contract.
The accepted `--actor-role` values are:

- `knowledge_admin`
- `project_owner`
- `qa_lead`
- `admin`

Other roles fail before Ground Truth persistence or benchmark mutation begins. `export`,
`validate` and `status` are read/compile operations in the local CLI; HTTP access remains
subject to the existing authenticated tenant, project-scope and role checks.

## 1. Export the current blind task package

```bash
qualibug-identity-benchmark export \
  --project customer-a \
  --root /srv/qualibug \
  --output /secure-review/customer-a-identity-tasks.json
```

Optional presentation batch size:

```bash
qualibug-identity-benchmark export \
  --project customer-a \
  --batch-size 50 \
  --output customer-a-identity-tasks.json
```

`task_package_id` depends only on the current Manifest and closed Mention universe.
Changing presentation batch size changes `batch_layout_id`, not `task_package_id`, so a
review is not invalidated merely because tasks are regrouped into different batches.

The task package includes redacted, bounded source context and no predicted entity,
predicted cluster, accepted identity edge or similarity candidate.

## 2. Validate a single submission without importing

```bash
qualibug-identity-benchmark validate \
  --project customer-a \
  --submission annotator-a.json \
  --output validation-result.json
```

This command performs closed-world validation and deterministic Ground Truth compilation,
but never writes Ground Truth.

## 3. Compare two independent submissions

```bash
qualibug-identity-benchmark validate \
  --project customer-a \
  --submission annotator-a.json \
  --submission annotator-b.json \
  --output double-blind-review.json
```

The CLI compares Mention partitions, not annotator-local cluster names. When the
partitions disagree, it returns exit code `2`, writes the exact disagreement queue when
`--output` is supplied and performs no mutation.

File selection order is not used to identify adjudication. The CLI reads
`annotator.role` from every file.

## 4. Validate adjudication

```bash
qualibug-identity-benchmark validate \
  --project customer-a \
  --submission adjudicator.json \
  --submission annotator-b.json \
  --submission annotator-a.json \
  --output adjudicated-result.json
```

The adjudication file must declare:

```json
{
  "annotator": {
    "name": "independent-reviewer",
    "role": "ADJUDICATOR"
  }
}
```

The adjudicator must differ from both original annotators. The CLI accepts one or two
ordinary annotator files and at most one adjudicator file.

## 5. Transactionally import resolved Ground Truth

Single-annotator import:

```bash
qualibug-identity-benchmark import \
  --project customer-a \
  --actor-name qa-lead \
  --actor-role qa_lead \
  --tenant-id tenant-a \
  --submission annotator-a.json \
  --output import-receipt.json
```

Double-blind import:

```bash
qualibug-identity-benchmark import \
  --project customer-a \
  --actor-name qa-lead \
  --actor-role qa_lead \
  --submission annotator-a.json \
  --submission annotator-b.json
```

Adjudicated import uses all three files in any selection order.

The command delegates to the existing authority:

```text
compile closed-world human partition
→ validate knowledge-management actor role
→ acquire project knowledge transaction lease
→ verify current Manifest
→ persist Ground Truth
→ rebuild through the canonical Composition Root
→ require MEASURED benchmark output
→ record first version snapshot
→ append audit event
```

If the submissions require adjudication, import returns exit code `2` and does not enter
the persistence workflow.

## 6. Read the current baseline status

```bash
qualibug-identity-benchmark status \
  --project customer-a
```

Return exit code `3` when CI must stop on an enforced blocked Gate:

```bash
qualibug-identity-benchmark status \
  --project customer-a \
  --fail-on-blocked
```

Use `--full` only when the caller needs the entire backend workspace:

```bash
qualibug-identity-benchmark status \
  --project customer-a \
  --full \
  --output customer-a-identity-workspace.json
```

The default status projection includes Manifest coverage, Ground Truth summary,
measurement metrics, regression deltas, final quality Gate, snapshot count and active or
resolved identity error counts.

## 7. Remeasure after an identity algorithm change

```bash
qualibug-identity-benchmark remeasure \
  --project customer-a \
  --actor-name qa-lead \
  --actor-role qa_lead \
  --output customer-a-remeasurement.json
```

This rebuilds through the same enterprise knowledge Composition Root and records a new
measurement event. Only a prior snapshot with the same Manifest ID and external Ground
Truth fingerprint is eligible as a regression baseline.

The command returns exit code `3` only when the resulting authoritative
`entry_allowed=false`. A report-only threshold miss remains visible in JSON but exits
successfully.

## Recommended real-project sequence

```text
export
→ external single or double-blind annotation
→ validate
→ adjudicate when exit code is 2
→ import
→ inspect status
→ change the identity algorithm
→ remeasure
→ inspect exact overmerge and undermerge evidence
```

Do not automate human labels from QualiBug's predicted clusters. Product-derived labels
are rejected because they would make Precision and Recall circular and untrustworthy.
