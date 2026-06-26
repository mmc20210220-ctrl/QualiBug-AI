# Phase91 Cognitive Memory Graph Verification

## Result

`COMPLETED_PHASE91` for the implementation and controlled validation scope.

## Measured release verification

| Gate | Result | Evidence |
|---|---:|---|
| Compileall | Passed | Release manifest |
| Full test suite | 356 passed, 1 skipped | 52/52 isolated test files |
| Product UI | Passed | 3/3 tests |
| Customer text quality | Passed | Release manifest |
| Private service smoke | Passed | Dashboard, control plane, knowledge, release, benchmark + read-only APIs |
| Production HTTP | 0 | Phase91 tests and controlled local A/B |
| Cleanup failure blocking | Passed | Flow + graph integration tests |
| Cross-project/environment isolation | Passed | graph isolation test |
| Markdown export boundary | Passed | read-only redaction test |

## Mainline proof

1. `test_discovery_round_updates_graph_and_frontier` verifies a discovery round records graph context, a frontier selection, and updates the graph after finding processing.
2. `test_agent_loop_emits_graph_frontier_as_planning_only` verifies the Agent Loop surfaces graph selection as planning-only work and does not execute it.
3. `test_flow_compiler_attaches_shadow_graph_context_without_authorizing_write` verifies flow compilation receives graph context while existing safety/cleanup gates remain authoritative.
4. `test_flow_run_records_cleanup_failure_and_blocks_matching_frontier` verifies a cleanup failure blocks matching high-risk work.
5. `test_replay_sandbox_updates_graph_with_evidence_only_packets` verifies replay evidence enriches graph evidence but remains non-confirming.

## A/B result

The local contract fixture measured document context at `6600` characters and graph context at `1844` characters. The graph pack had `16` traceable source references.

Mode is `shadow`. This is intentional: an explicit operator must enable `active` only after customer-approved replay/shadow metrics demonstrate that quality gates do not regress.

## External limitations

- Live DeepSeek latency and live customer finding quality were not run in this isolated environment and are not claimed.
- A customer project should begin in `shadow` mode, capture replay/shadow metrics, then make an explicit approved move to `active`.
