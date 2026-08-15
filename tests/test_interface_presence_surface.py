"""Interface-presence surface: the four-link chain for documentation drift.

A source document that declares a GET/HEAD endpoint the deployed target does not
implement is a real, reproducible documentation/implementation-drift defect.
This guards the four-link reachability chain (risk family -> assertion kind ->
observer -> protocol) plus the obligation binding that turns every declared
read operation into one deduplicated ``interface_contract`` obligation, and the
tri-state verdict semantics: framework 404 -> VIOLATION, business 404/2xx ->
PASS, 5xx / unattempted -> INDETERMINATE (never a fabricated verdict).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center import discovery_runtime_semantic_binding  # noqa: E402,F401
from ai_test_asset_center.assertion_dsl_base import (  # noqa: E402
    registered_assertion_kinds,
)
from ai_test_asset_center.experiment_protocol_registry import (  # noqa: E402
    registered_family_protocols,
    resolve_family_protocol,
)
from ai_test_asset_center.formal_interface_presence_surface import (  # noqa: E402
    ASSERTION_KIND,
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
    _evaluate_interface_presence,
    _observe_interface_presence,
    install_formal_interface_presence_surface,
    presence_probe_path,
)
from ai_test_asset_center.observer_contracts_base import (  # noqa: E402
    OBSERVER_REGISTRY,
)
from ai_test_asset_center.source_interface_presence_obligation_binding import (  # noqa: E402
    compile_obligations_with_source_interface_presence,
)
from ai_test_asset_center.test_obligation import (  # noqa: E402
    canonical_risk_families,
    make_obligation,
)


def _base_compile(behavior_ir: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return {"obligations": [], "obligation_count": 0, "coverage_gaps": [], "by_family": {}}


def test_all_four_links_register() -> None:
    installed = install_formal_interface_presence_surface()
    assert installed["observer"] == OBSERVER_ID
    assert installed["assertion"] == ASSERTION_KIND
    assert installed["risk_family"] == RISK_FAMILY
    assert installed["protocol"] == f"{RISK_FAMILY}:{PROTOCOL_TEMPLATE}"

    assert OBSERVER_REGISTRY.get(OBSERVER_ID, {}).get("implemented") is True
    assert ASSERTION_KIND in registered_assertion_kinds()
    assert RISK_FAMILY in canonical_risk_families()
    assert resolve_family_protocol(RISK_FAMILY, PROTOCOL_TEMPLATE) is not None


def test_presence_probe_path_materializes_placeholders() -> None:
    assert presence_probe_path("/api/inventory/recount/{sku}") == (
        "/api/inventory/recount/qbg_interface_presence"
    )
    assert presence_probe_path("/api/products/:id") == (
        "/api/products/qbg_interface_presence"
    )
    assert presence_probe_path("/api/health") == "/api/health"


def test_obligation_binding_emits_deduped_read_obligations() -> None:
    ir = {
        "operations": [
            {
                "id": "get_product",
                "method": "GET",
                "path": "/api/products/{id}",
                "status": "accepted",
                "confidence": 0.9,
                "source_refs": [{"source_id": "s1", "locator": "openapi.yaml#/paths/~1api~1products~1{id}/get"}],
            },
            {
                "id": "get_product_dup",
                "method": "GET",
                "path": "/api/products/{id}",
                "status": "accepted",
                "source_refs": [{"source_id": "s1", "locator": "openapi.yaml#/paths/~1api~1products~1{id}/get"}],
            },
            {
                "id": "create_order",
                "method": "POST",
                "path": "/api/orders",
                "status": "accepted",
                "source_refs": [{"source_id": "s1", "locator": "openapi.yaml#/paths/~1api~1orders/post"}],
            },
        ],
    }
    out = compile_obligations_with_source_interface_presence(ir, base_compile=_base_compile)
    additions = [o for o in out["obligations"] if o["risk_family"] == RISK_FAMILY]
    # POST write is skipped; the two GET paths with identical method+normalized
    # path collapse to one obligation.
    assert len(additions) == 1
    assert additions[0]["required_operations"] == ["get_product"]
    assert additions[0]["property"]["template"] == PROTOCOL_TEMPLATE
    receipt = out["source_interface_presence_obligation_receipt"]
    assert receipt["status"] == "COMPILED"
    assert receipt["obligation_count"] == 1


def _observe(status_code: int, content_type: str) -> dict[str, Any]:
    return _observe_interface_presence({
        "treatment_observation": {
            "status_code": status_code,
            "headers": {"content-type": content_type},
        },
    })


def _evaluate(evidence: dict[str, Any]) -> dict[str, Any]:
    return _evaluate_interface_presence({
        "spec": {"method": "GET", "path": "/api/inventory/recount/{sku}"},
        "observations": evidence,
    })


def test_framework_404_is_a_violation() -> None:
    receipt = _observe(404, "text/html; charset=utf-8")
    assert receipt["status"] == "OBSERVED"
    verdict = _evaluate(receipt["evidence"])
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "DECLARED_INTERFACE_NOT_IMPLEMENTED"


def test_business_404_is_a_pass() -> None:
    receipt = _observe(404, "application/json")
    verdict = _evaluate(receipt["evidence"])
    assert verdict["passed"] is True


def test_2xx_is_a_pass() -> None:
    receipt = _observe(200, "application/json")
    verdict = _evaluate(receipt["evidence"])
    assert verdict["passed"] is True


def test_5xx_is_indeterminate_not_a_defect() -> None:
    receipt = _observe(500, "application/json")
    verdict = _evaluate(receipt["evidence"])
    assert verdict["passed"] is None
    assert verdict["reason_code"] == "INTERFACE_PRESENCE_INDETERMINATE"


def test_unattempted_is_indeterminate() -> None:
    receipt = _observe(0, "")
    assert receipt["status"] == "INDETERMINATE"
    verdict = _evaluate(receipt["evidence"])
    assert verdict["passed"] is None
    assert verdict["reason_code"] == "INTERFACE_PRESENCE_NOT_ATTEMPTED"


def test_obligation_compiles_end_to_end() -> None:
    from ai_test_asset_center.experiment_compiler_obligation import (
        compile_experiment_for_obligation,
    )

    ir = {
        "operations": [
            {
                "id": "get_recount",
                "method": "GET",
                "path": "/api/inventory/recount/{sku}",
                "status": "accepted",
                "confidence": 0.9,
                "source_refs": [{"source_id": "s1", "locator": "openapi.yaml#/paths/~1api~1inventory~1recount~1{sku}/get"}],
            },
        ],
        "actors": [
            {
                "id": "actor-admin",
                "role": "admin",
                "account_ref": "admin@test.com",
                "runtime_bound": True,
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:admin@test.com",
            },
        ],
        "relations": [],
        "invariants": [],
        "entities": [],
    }
    obligation = make_obligation(
        risk_family=RISK_FAMILY,
        subject_refs=["get_recount"],
        property_spec={
            "template": PROTOCOL_TEMPLATE,
            "operation_ref": "get_recount",
            "method": "GET",
            "path": "/api/inventory/recount/{sku}",
            "interface_presence": True,
        },
        required_operations=["get_recount"],
        required_observers=[OBSERVER_ID],
        cleanup_requirement={"required": False, "reason": "read_only_interface_presence_probe"},
        source_refs=[{"source_id": "s1", "locator": "openapi.yaml"}],
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
        available_adapters={"http_api"},
    )
    receipt = experiment.get("compile_receipt", {})
    assert receipt.get("status") == "COMPILED", receipt
    treatment = experiment.get("treatment_plan") or []
    assert len(treatment) == 1
    assert treatment[0]["path"] == "/api/inventory/recount/qbg_interface_presence"
    assert [a.get("kind") for a in experiment.get("assertions") or []] == [ASSERTION_KIND]
    assert [o.get("observer_id") for o in experiment.get("observers") or []] == [OBSERVER_ID]
    assert experiment.get("cleanup_plan") in (None, [])
