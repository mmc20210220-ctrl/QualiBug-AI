# QualiBug AI Phase63 Release Notes

## Goal

Phase63 continues the shortest evidence-backed path to stronger business Bug
mining. It reuses the existing metamorphic differential engine and adds no new
runtime, UI framework, orchestrator, defect catalog or network capability.

The new relation is **temporal partition conservation**: an explicitly declared
left-closed/right-open set of adjacent time windows must be a complete,
non-overlapping partition of the same whole business range.

## What Changed

- Added explicit `temporal_partitions` contracts to the existing metamorphic
  differential engine.
- Added bounded GET-only comparison of a whole range against up to eight
  adjacent windows.
- Detects boundary-record loss, boundary duplication and records visible only
  in split windows or only in the whole range.
- Requires `complete_response: true` and rejects incomplete, truncated or
  paginated snapshots instead of producing a false positive.
- Added `metamorphic_temporal_relation` to risk prioritization at high business
  impact, so verified settlement/report range defects do not compete with
  generic low-signal probes.
- Preserved the Phase62 shared safety boundary, evidence redaction, persistent
  fingerprinting and LLM-hypothesis-only policy.

## Verification

- Targeted cross-engine regression (metamorphic differential, reconciliation,
  temporal regression, consistency/isolation, causality, Saga and release gate):
  **27/27 passed**.
- The adversarial test server contains a genuine date-boundary defect: the
  record exactly at `2026-01-02T00:00:00Z` disappears only from the second
  `[start, end)` daily window. Phase63 reports a deterministic
  `temporal_partition` counterexample while issuing GET requests only.
- Python source compilation for the changed engine, planner and tests passed.

A full release verification remains CI-owned because the package's canonical
single-process suite has previously stalled in this container without a failed
assertion. This release is therefore a controlled engineering increment, not a
GA sign-off.
