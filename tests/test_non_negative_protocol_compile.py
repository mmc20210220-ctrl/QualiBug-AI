"""Regression: non_negative conservation compile must not crash on bodies
without delta/identity fields.

Root cause (2026-08-08, found via a failed full scan — UnboundLocalError
`_resolvers`): the non_negative boundary arm of ``compile_family_protocol``
assigned ``_resolvers`` only inside ``if _delta_field and _identity_field:``
but referenced it afterwards in the observer construction. Any conservation
rule compiled as ``non_negative`` against a body lacking delta/identity keys
crashed the whole v12 pipeline before a single experiment could run.

The fix initializes ``_resolvers`` unconditionally at the top of the
non_negative arm. The compile must still return COMPILED (with an unmutated
treatment body and the plain observers) instead of raising.
"""
import pytest

from ai_test_asset_center.experiment_protocols_base import compile_family_protocol


def _non_negative_property(term_field: str = "available_qty") -> dict:
    return {
        "template": "conservation_write",
        "invariant_ref": "inv_non_neg",
        "expression": {
            "operator": "non_negative",
            "operands": [{"field_id": "cf_1", "field": term_field}],
        },
    }


class TestNonNegativeProtocolCompile:
    def test_body_without_delta_field_compiles_without_crash(self):
        """A delta-less body must not raise UnboundLocalError."""
        protocol = compile_family_protocol(
            risk_family="conservation",
            operation={
                "id": "op_adjust",
                "method": "POST",
                "path": "/api/inventory/adjust",
                "request_example": {"note": "manual adjustment"},
            },
            operation_ref="op_adjust",
            control_actor_ref="actor_admin",
            treatment_actor_ref="actor_warehouse",
            property_spec=_non_negative_property(),
        )
        assert protocol["status"] == "COMPILED"
        treatment = protocol["treatment_plan"][0]
        # No delta/identity field -> no boundary mutation descriptor.
        assert "mutation" not in treatment
        assert protocol["assertion"]["kind"] == "non_negative"

    def test_body_with_delta_and_identity_field_still_gets_boundary_mutation(self):
        """The happy path must keep the runtime_boundary_break descriptor."""
        protocol = compile_family_protocol(
            risk_family="conservation",
            operation={
                "id": "op_adjust",
                "method": "POST",
                "path": "/api/inventory/adjust",
                "request_example": {"sku": "SKU-1", "delta": 5},
            },
            operation_ref="op_adjust",
            control_actor_ref="actor_admin",
            treatment_actor_ref="actor_warehouse",
            property_spec=_non_negative_property(),
            behavior_ir={
                "operations": [
                    {"id": "op_get", "method": "GET", "path": "/api/inventory/{sku}"},
                    {"id": "op_health", "method": "GET", "path": "/api/inventory/health"},
                ]
            },
        )
        assert protocol["status"] == "COMPILED"
        mutation = protocol["treatment_plan"][0].get("mutation")
        assert mutation is not None
        assert mutation["class"] == "runtime_boundary_break"
        assert mutation["identity_field"] == "sku"
        assert mutation["delta_field"] == "delta"
        assert "op_get" in {
            row.get("operation_ref") for row in mutation["resolver_operations"]
        }
