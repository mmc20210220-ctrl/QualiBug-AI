"""Deterministic verification of the observability fix at the receipt layer:
simulate the enriched evidence that experiment_executor.py now emits for a
failed runtime_read_binding fixture, and prove build_contract_evidence_receipt
round-trips it (schema not broken, detail preserved)."""
import json, sys
sys.path.insert(0, r"D:\QualiBug-AI\QualiBug-AI-main")
from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    validate_contract_evidence_receipt,
)

# What experiment_executor now writes into the fixture receipt on failure:
enriched_evidence = {
    "fixture_kind": "runtime_read_binding",
    "value_fingerprint": "",
    "binding_status": "BLOCKED",
    "binding_reason_code": "BLOCKED_MISSING_BINDING",
    "binding_detail": "runtime_read_binding_unresolved:fix_37e4859011c9c713:resolver_no_http_response:/api/cart/items",
    "resolver_path": "/api/cart/items",
    "resolver_status_code": 0,
}
receipt = build_contract_evidence_receipt(
    kind="fixture",
    experiment_id="exp_x",
    obligation_id="obl_x",
    campaign_id="CMP_x",
    execution_id="exec_x",
    subject_id="fix_37e4859011c9c713",
    status="FAILED",
    evidence=enriched_evidence,
)
validated = validate_contract_evidence_receipt(receipt)  # raises if schema broken
print("schema round-trip: OK")
print("status:", validated["status"])
print("evidence keys:", sorted(validated["evidence"].keys()))
assert validated["evidence"]["binding_detail"].startswith("runtime_read_binding_unresolved:fix_37e4859011c9c713"), "detail lost!"
assert validated["evidence"]["resolver_status_code"] == 0
print("binding_detail PRESERVED:", validated["evidence"]["binding_detail"])
print("VERIFICATION PASSED")
