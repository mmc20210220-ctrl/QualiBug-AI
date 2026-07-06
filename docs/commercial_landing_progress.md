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

Current commercial rule:

- Customer pages should show defects only after the delivery gate accepts them.
- Internal clue pages should keep non-ready findings and show the missing evidence reasons.
- Showing zero defects is acceptable when evidence is incomplete; showing a non-reproducible defect is not acceptable.
- A project run is not commercially complete until the whole enterprise evidence chain is complete: inputs, parsing, plan, execution, discovery, and evidence.

Current service integration:

- `qualibug-server` now starts through `ai_test_asset_center.private_pilot_server:run_server`.
- The wrapper installs the strict backend customer-delivery gate before delegating to the original private pilot service.
- The wrapper exposes patch status and restore helpers for operational diagnostics and isolated tests.
- Command-center responses now include `customer_delivery_gate_patch` at the top level and under `data`, `data_contract`, and `delivery_tracks`.
- Dashboard displays whether the strict delivery gate is enabled, the patch source, the active partition function, and whether the original function is retained.
- `main_chain_contract.py` can now generate `main_chain_contract.json` under both workspace and output folders for the core chain.
- `real_project_discovery_with_chain.py` provides the safe backend-owned discovery entrypoint for automatic chain validation.

Next engineering step:

- Route the private pilot runtime task `real_project_discovery` through the chain-aware runner when a safe small patch path is available.
- Then expose `main_chain_contract` in Command Center from backend output, not by frontend guessing.
- Replace the legacy `_partition_delivery_tracks()` implementation inside `private_pilot_service.py` directly when a safe small patch path is available.
- Keep the wrapper as a compatibility safety net for default startup.
