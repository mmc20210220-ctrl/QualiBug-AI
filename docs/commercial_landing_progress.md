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

Current commercial rule:

- Customer pages should show defects only after the delivery gate accepts them.
- Internal clue pages should keep non-ready findings and show the missing evidence reasons.
- Showing zero defects is acceptable when evidence is incomplete; showing a non-reproducible defect is not acceptable.

Next engineering step:

- Wire the command center service to call `normalize_command_center_delivery()` as the final response step.
- Make formatter/service output populate `customer_delivery_gate_reasons` for every clue produced from legacy or display-ready sources.
