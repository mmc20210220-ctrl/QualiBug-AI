# Discovery Funnel Report

- Report status: `READY`
- Quality status: `NOT_MEASURED`
- Receipt authority: `qualibug.obligation-attempt-ledger.v1`

## Funnel counts

| Stage | Count |
| --- | ---: |
| Generated | NOT_MEASURED |
| Selected | 1270 |
| Terminal | 1270 |
| Compiled | 999 |
| Executed | 999 |
| Oracle evaluated | 939 |
| Formal delivery | 16 |
| Delivery occurrences | 79 |

## Conservation

- Status: `INCOMPLETE`
- Identity status: `INCOMPLETE`
- Identity stage gaps: `3208`
- Attempt identity gaps: `1270`
- Missing evidence: `test_obligations.obligations, ledger.identity`

## Top blocking reasons

### `BLOCKED_MISSING_BINDING` (109)

- Family: `BINDING_GRAPH_GAP`
- Registry: `REGISTERED`
- Recoverability: `RECOVERABLE`
- Customer materials needed: source-declared path/body binding and resolver operation

- Example `obl_0139fa8c508dc6a6b53a`: runtime_read_binding_unresolved:order_id:resolver_status_200_fixture_setup_not_generated:/api/orders
- Example `obl_072771d83a3307fd1243`: runtime_read_binding_unresolved:order_id:resolver_status_200_fixture_setup_not_generated:/api/orders
- Example `obl_1feb747715f24e3da617`: runtime_read_binding_unresolved:id:resolver_status_200_fixture_setup_not_generated:/api/orders

### `BLOCKED_NON_REVERSIBLE_WRITE` (91)

- Family: `CLEANUP_CAPABILITY_GAP`
- Registry: `REGISTERED`
- Recoverability: `SOURCE_DEPENDENT`
- Customer materials needed: source-declared compensating action or adapter cleanup authority

- Example `obl_b4df2ebd8ce00dd88ff6`: cleanup_unresolved:
- Example `obl_ae1b5aa72039c5f2a836`: cleanup_unresolved:
- Example `obl_b3b76391fbae1a19c5c7`: cleanup_unresolved:

### `BLOCKED_MISSING_OBSERVER` (59)

- Family: `OBSERVER_CAPABILITY_GAP`
- Registry: `REGISTERED`
- Recoverability: `RECOVERABLE`
- Customer materials needed: source-declared read or observable effect contract

- Example `obl_20fc1c0b927166a051b9`: CONTROL_SUCCESS_NOT_PROVEN:control_1,OBSERVER_RECEIPT_INDETERMINATE:authorization_comparison
- Example `obl_15e7d1454f0553878b45`: CONTROL_SUCCESS_NOT_PROVEN:control_1,OBSERVER_RECEIPT_INDETERMINATE:authorization_comparison
- Example `obl_af2efda895b77d7ec5de`: CONTROL_SUCCESS_NOT_PROVEN:control_1,OBSERVER_RECEIPT_INDETERMINATE:authorization_comparison

### `FIELD_LEVEL_RULE_NOT_EXECUTABLE` (36)

- Family: `COMPILER_GAP`
- Registry: `REGISTERED`
- Recoverability: `RECOVERABLE`
- Customer materials needed: source-backed field/state contract with exact expected values

- Example `obl_8a11b6e29edf8e96a6dc`: postcondition_missing_field_observer
- Example `obl_646e31652eab12563272`: postcondition_missing_field_observer
- Example `obl_f04cb1a2eb184f0154ad`: postcondition_missing_field_observer

### `STATE_RULE_PRECONDITION_NOT_ESTABLISHED` (14)

- Family: `COMPILER_GAP`
- Registry: `REGISTERED`
- Recoverability: `RECOVERABLE`
- Customer materials needed: source-backed field/state contract with exact expected values

- Example `obl_70ecbb9240393c6f`: state_transition_requires_concrete_from_to
- Example `obl_eedf15749472a725`: state_transition_requires_concrete_from_to
- Example `obl_2bb3c8658bdd30cf`: state_transition_requires_concrete_from_to

### `BLOCKED_MISSING_OPERATION` (12)

- Family: `BEHAVIOR_MODEL_GAP`
- Registry: `REGISTERED`
- Recoverability: `RECOVERABLE`
- Customer materials needed: source operation/interface definition

- Example `obl_84b8a9a218b1e35b3112`: conservation_requires_write_operation
- Example `obl_39267b7c411438744566`: conservation_requires_write_operation
- Example `obl_4effe874b0018e081efb`: conservation_requires_write_operation

### `BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE` (4)

- Family: `ORACLE_INPUT_GAP`
- Registry: `REGISTERED`
- Recoverability: `RECOVERABLE`
- Customer materials needed: source-backed assertion inputs and observer evidence

- Example `obl_93b39bd067c70189`: assertion_kind=cross_surface_consistency:missing_observation_key=surfaces_agree
- Example `obl_5e7e14d6d2ffccad`: assertion_kind=cross_surface_consistency:missing_observation_key=surfaces_agree
- Example `obl_0ed5f7bc38eaa6f3`: assertion_kind=cross_surface_consistency:missing_observation_key=surfaces_agree

### `BLOCKED_MISSING_FIXTURE` (4)

- Family: `FIXTURE_CAPABILITY_GAP`
- Registry: `REGISTERED`
- Recoverability: `RECOVERABLE`
- Customer materials needed: source-declared fixture setup and ownership scope

- Example `obl_fa1c4e4b290b81b47471`: owned_resource
- Example `obl_efcbb219f156f1772188`: owned_resource
- Example `obl_bb8bc1381e7d1bc524be`: owned_resource

### `CONTRACT_ORACLE_HARNESS_FAILED` (4)

- Family: `TARGET_SYSTEM_RESPONSE`
- Registry: `REGISTERED`
- Recoverability: `UNKNOWN`
- Customer materials needed: target health evidence and the original transport receipt

- Example `obl_6f4e6f562ab6766c`: no detail recorded
- Example `obl_b4fb3d6bfb0cca83`: no detail recorded
- Example `obl_7ccf332d16072c52`: no detail recorded

### `HARNESS_CONNECTION_FAILED` (2)

- Family: `TARGET_SYSTEM_RESPONSE`
- Registry: `REGISTERED`
- Recoverability: `UNKNOWN`
- Customer materials needed: target health evidence and the original transport receipt

- Example `obl_55e456f6fdc4aa8a`: HARNESS_CONNECTION_FAILED
- Example `obl_e045db1b6d717783`: HARNESS_CONNECTION_FAILED

## Quality boundary

Internal funnel counts are diagnostic only. Recall and precision remain `NOT_MEASURED` until an authenticated external evaluator receipt is verified.
