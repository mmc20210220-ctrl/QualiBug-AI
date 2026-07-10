from __future__ import annotations

from ai_test_asset_center.defect_discovery import (
    build_business_knowledge_steps,
    cross_resource_scenarios,
    generate_journey_defect_probes,
    infer_entity_dependencies,
    keyword_hits,
    read_path_for_resource,
)


def _op(method: str, path: str, resource: str, operation: str = "create_or_action", **extra) -> dict:
    return {
        "method": method,
        "path": path,
        "resource": resource,
        "operation": operation,
        "summary": extra.get("summary", ""),
        "risk_hints": extra.get("risk_hints", []),
    }


def test_read_path_for_resource_prefers_documented_openapi_detail():
    ops = [
        _op("GET", "/api/patients", "patients", "read"),
        _op("GET", "/api/patients/{patient_id}", "patients", "read"),
        _op("POST", "/api/patients", "patients", "create_or_action"),
    ]
    assert read_path_for_resource("patients", operations=ops) == "/api/patients/{patient_id}"
    assert read_path_for_resource("patients") == "/patients/{id}"


def test_journey_state_action_seeds_from_documented_create_path():
    ops = [
        _op("POST", "/api/appointments", "appointments", "create_or_action"),
        _op("GET", "/api/appointments/{appointment_id}", "appointments", "read"),
        _op("POST", "/api/appointments/{appointment_id}/cancel", "appointments", "state_cancel"),
    ]
    deps = infer_entity_dependencies(ops)
    state_deps = [d for d in deps if d["dependency"] == "state_action_requires_existing_resource"]
    assert state_deps
    assert state_deps[0]["setup"] == "POST /api/appointments"

    probes = generate_journey_defect_probes({"operations": ops, "entity_dependencies": deps})
    state_probes = [p for p in probes if p["probe_type"] == "journey_state_probe"]
    assert state_probes
    seed = state_probes[0]["steps"][0]
    assert seed["name"] == "seed_resource"
    assert seed["path"] == "/api/appointments"
    assert seed["path"] != "/appointments"
    read_step = state_probes[0]["steps"][-1]
    assert "{appointment_id}" in read_step["path"] or "appointments" in read_step["path"]


def test_cross_resource_scenarios_use_role_families_not_mall_paths():
    ops = [
        _op("POST", "/api/appointments", "appointments"),
        _op("POST", "/api/appointments/{id}/cancel", "appointments", "state_cancel"),
        _op("POST", "/api/settlements", "settlements", "payment"),
        _op("POST", "/api/refunds", "refunds", "refund"),
        _op("GET", "/api/patients/{id}", "patients", "read"),
        _op("GET", "/api/medical_records/{id}", "medical_records", "read"),
        _op("GET", "/api/beds", "beds", "read"),
    ]
    scenarios = cross_resource_scenarios(ops, prd="clinic appointment settlement")
    ids = {s["scenario_id"] for s in scenarios}
    assert "SCN_FULFILLMENT_PAY_REFUND" in ids
    assert "SCN_CAPACITY_FULFILL_CANCEL" in ids or "SCN_OWNER_SCOPE_ISOLATION" in ids
    assert "SCN_ORDER_PAY_REFUND" not in ids


def test_terminal_state_steps_include_create_cancel_attempt():
    ops = [
        _op("POST", "/api/bookings", "bookings"),
        _op("POST", "/api/bookings/{id}/cancel", "bookings", "state_cancel"),
        _op("POST", "/api/settlements", "settlements", "payment", summary="settle booking payment"),
        _op("GET", "/api/bookings/{id}", "bookings", "read"),
    ]
    payment_op = ops[2]
    steps = build_business_knowledge_steps("state_flow", payment_op, ops)
    names = [s["name"] for s in steps]
    assert "seed_fulfillment_resource" in names
    assert "move_to_terminal_state" in names
    assert "attempt_payment_after_terminal_state" in names


def test_keyword_hits_match_healthcare_tokens():
    assert keyword_hits("order", "/api/appointments/{id}")
    assert keyword_hits("payment", "/api/settlements")
    assert keyword_hits("idor", "/api/patients/{patient_id}/records")
    assert keyword_hits("stock", "/api/beds")
