# Commercial Landing Progress

Current focus: only fully verified and replayable findings should enter the customer-facing list.

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

Current commercial rule:

- Customer pages should show defects only after the delivery gate accepts them.
- Internal clue pages should keep non-ready findings and show the missing evidence reasons.
- Showing zero defects is acceptable when evidence is incomplete; showing a non-reproducible defect is not acceptable.

Current service integration:

- `qualibug-server` now starts through `ai_test_asset_center.private_pilot_server:run_server`.
- The wrapper installs the strict backend customer-delivery gate before delegating to the original private pilot service.
- The wrapper exposes patch status and restore helpers for operational diagnostics and isolated tests.
- Command-center responses now include `customer_delivery_gate_patch` at the top level and under `data`, `data_contract`, and `delivery_tracks`.
- Dashboard displays whether the strict delivery gate is enabled, the patch source, the active partition function, and whether the original function is retained.

Next engineering step:

- Replace the legacy `_partition_delivery_tracks()` implementation inside `private_pilot_service.py` directly when a safe small patch path is available.
- Keep the wrapper as a compatibility safety net for default startup.
