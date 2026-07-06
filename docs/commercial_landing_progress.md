# Commercial Landing Progress

Current focus: fully harden the core enterprise evidence chain: enterprise inputs -> parsing/knowledge -> test plan -> execution -> bug discovery -> evidence bundle.

Completed so far:

- Added a backend customer-delivery gate module.
- Added backend tests for ready findings and internal clues.
- Expanded rejection coverage for auth, route, coverage, execution, confirmation, request, response, and assertion gaps.
- Added structured rejection reasons so internal clue pages can explain why an item is not yet a customer-deliverable defect.
- Added backend explanation contracts for rejection reasons with label, detail, and next action.
- Exposed `customer_delivery_gate_reasons` in the frontend finding type.
- Updated the internal clue page to display readable reasons for why a clue is not customer-deliverable.
- Updated the internal clue page to prefer backend structured explanations when available and fall back to reason codes for older data.
- Added a frontend contract test to keep the internal clue explanation panel from regressing.
- Added a command-center delivery normalization adapter that rechecks legacy `defects`, `risks`, and `findings` through the backend gate.
- Added tests proving legacy non-ready `defects` are downgraded into internal clues.
- Added a private pilot server wrapper that patches the legacy delivery-track partitioning through the backend gate before startup.
- Routed the default `qualibug-server` entrypoint through the private pilot server wrapper.
- Added tests proving the patched entrypoint downgrades legacy light-gate results into internal clues when business evidence is not validated.
- Added patch diagnostics so runtime can report whether the strict delivery gate patch is active, where it came from, and which partition function is live.
- Added a restore helper for isolated diagnostics and tests, while keeping the default service startup strict.
- Exposed `customer_delivery_gate_patch` in the command-center payload so frontend/customer environments can verify that the strict gate is active.
- Surfaced the strict delivery-gate status on the dashboard summary and diagnostics cards.
- Added a frontend contract test so the dashboard cannot silently lose delivery-gate diagnostics.
- Added a main enterprise evidence chain contract that reads the actual runtime artifacts and reports which core stage is missing, partial, or passed.
- Added tests proving the main chain is ready only when inputs, parsed knowledge, plan, execution, discovered bugs, and evidence bundle are all present.
- Added tests proving candidate findings are not customer-delivery-ready when no validated bug exists.
- Added a chain-aware discovery runner that calls the existing real-project discovery engine and then writes/returns the main chain contract.
- Added a `qualibug-discover-chain` CLI entrypoint for running discovery with automatic main-chain validation.
- Added tests proving the chain-aware runner attaches `main_chain_contract` to the discovery result and persists the contract artifact.
- Added a chain-aware enterprise pilot runtime wrapper that patches the pilot runtime's `real_project_discovery` callable to the chain-aware discovery runner before task execution.
- Added a `qualibug-pilot-chain` CLI entrypoint for running pilot runtime tasks with automatic main-chain validation.
- Added tests proving the chain-aware pilot wrapper installs/restores the patch and delegates `run_next` only after the chain-aware discovery runner is active.
- Exposed `main_chain_contract` and `main_chain_contract_summary` in Command Center payloads from backend output artifacts.
- Added tests proving Command Center can surface the first blocked main-chain stage and next action from `main_chain_contract.json`.
- Added a Command Center readiness guard: when `main_chain_contract.chain_ready` is false, customer-delivery readiness and release-ready flags are forced to false.
- Added tests proving stale or optimistic payload fields cannot claim customer-delivery readiness while the main enterprise evidence chain is incomplete.
- Surfaced `main_chain_contract` on the dashboard with the six core stages, first blocked stage, and next action.
- Added a frontend contract test so dashboard main-chain diagnostics cannot be silently removed.

Current commercial rule:

- Customer pages should show defects only after the delivery gate accepts them.
- Internal clue pages should keep non-ready findings and show the missing evidence reasons.
- Showing zero defects is acceptable when evidence is incomplete; showing a non-reproducible defect is not acceptable.
- A project run is not commercially complete until the whole enterprise evidence chain is complete: inputs, parsing, plan, execution, discovery, and evidence.
- If `main_chain_contract.chain_ready=false`, Command Center must report `MAIN_CHAIN_NOT_READY` and must not claim customer-delivery readiness.

Current service integration:

- `qualibug-server` now starts through `ai_test_asset_center.private_pilot_server:run_server`.
- The wrapper installs the strict backend customer-delivery gate before delegating to the original private pilot service.
- The wrapper exposes patch status and restore helpers for operational diagnostics and isolated tests.
- Command-center responses now include `customer_delivery_gate_patch` at the top level and under `data`, `data_contract`, and `delivery_tracks`.
- Command-center responses now include `main_chain_contract` and `main_chain_contract_summary` loaded from backend output/workspace artifacts.
- Command-center responses now force readiness fields to false and attach `MAIN_CHAIN_NOT_READY` when the main enterprise evidence chain is not closed.
- Dashboard displays whether the strict delivery gate is enabled, the patch source, the active partition function, and whether the original function is retained.
- Dashboard displays the main enterprise evidence chain status, six-stage progress, first blocked stage, and next action from the backend contract.
- `main_chain_contract.py` can now generate `main_chain_contract.json` under both workspace and output folders for the core chain.
- `real_project_discovery_with_chain.py` provides the safe backend-owned discovery entrypoint for automatic chain validation.
- `enterprise_pilot_runtime_with_chain.py` provides the safe pilot-runtime entrypoint that routes `real_project_discovery` tasks through the chain-aware runner.

Next engineering step:

- Tighten `main_chain_contract` evidence checks so evidence-chain pass requires replay information, raw request/response, expected/actual, and a stable issue link.
- Replace the legacy `_partition_delivery_tracks()` implementation inside `private_pilot_service.py` directly when a safe small patch path is available.
- Keep the wrapper as a compatibility safety net for default startup.
