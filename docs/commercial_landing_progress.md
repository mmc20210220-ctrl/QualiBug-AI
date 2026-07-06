# Commercial Landing Progress

Current focus: only fully verified and replayable findings should enter the customer-facing list.

Completed so far:

- Added a backend customer-delivery gate module.
- Added backend tests for ready findings and internal clues.
- Expanded rejection coverage for auth, route, coverage, execution, confirmation, request, response, and assertion gaps.
- Added structured rejection reasons so internal clue pages can explain why an item is not yet a customer-deliverable defect.
- Exposed `customer_delivery_gate_reasons` in the frontend finding type.
- Updated the internal clue page to display readable reasons for why a clue is not customer-deliverable.
- Added a frontend contract test to keep the internal clue explanation panel from regressing.

Current commercial rule:

- Customer pages should show defects only after the delivery gate accepts them.
- Internal clue pages should keep non-ready findings and show the missing evidence reasons.
- Showing zero defects is acceptable when evidence is incomplete; showing a non-reproducible defect is not acceptable.

Next engineering step:

- Wire the command center service to use the backend gate module as its single promotion point.
- Make formatter/service output populate `customer_delivery_gate_reasons` for every clue produced from legacy or display-ready sources.
