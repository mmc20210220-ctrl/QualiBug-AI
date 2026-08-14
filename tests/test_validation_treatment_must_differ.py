"""A validation probe whose two arms are identical can only fabricate a finding.

Read from the live target's own compiled experiment, both arms came out the same:

    control   {"intent": "valid_source_control",       "path": "/api/products/:sku"}
    treatment {"intent": "single_constraint_mutation", "path": "/api/products/:sku",
               "mutation": {"operator": "remove_required_parameter",
                            "parameter_location": "path", ...}}

Byte-identical paths, while the mutation descriptor claimed the required ``sku``
parameter had been removed. Nothing in the main-chain executor reads ``step["mutation"]``
-- the descriptor is inert -- so the run sent the same request twice, the oracle expected
a 4xx, saw the control's own 200, and reported a violation.

The consequence is that **every read returning 200 becomes a "validation not enforced"
defect**. Both remaining published findings on the target were this.

Blocking is the same discipline the compiler already applies to a synthesized observer:
a probe that cannot distinguish its two arms must not run, because its only possible
output is a fabricated finding reported against correct code. Actually applying path and
query mutations at execution is the larger follow-up; until then the honest result is a
block with a reason, not a defect.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.experiment_protocols_privacy_base import compile_family_protocol


def _operation():
    return {
        "id": "bir_get_product",
        "operation_id": "get_api_products_sku",
        "method": "GET",
        "path": "/api/products/{sku}",
        "read_write": "read",
        "parameters": ["sku"],
        "request_schema": {
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
        "response_schema": {"200": {"description": "OK"}},
    }


def _compile(property_spec):
    return compile_family_protocol(
        risk_family="validation",
        operation=_operation(),
        operation_ref="bir_get_product",
        control_actor_ref="bir_buyer",
        treatment_actor_ref="bir_buyer",
        property_spec=property_spec,
        behavior_ir={"operations": [_operation()], "actors": [{"id": "bir_buyer", "role": "buyer"}]},
    )


# ── the refusal ─────────────────────────────────────────────────────────────

def test_identical_arms_block_rather_than_compile() -> None:
    """The exact shape observed on the live target."""
    result = _compile({
        "validation_constraint": "required",
        "field": "sku",
        "field_tokens": ["@path", "sku"],
    })
    if result.get("status") == "COMPILED":
        control = (result.get("control_plan") or [{}])[0]
        treatment = (result.get("treatment_plan") or [{}])[0]
        differs = any(
            control.get(field) != treatment.get(field)
            for field in ("path", "query", "header", "body")
        )
        assert differs, (
            "a COMPILED validation probe must have arms that actually differ; "
            f"control={control} treatment={treatment}"
        )
    else:
        assert result.get("status") == "BLOCKED"
        assert "identical_to_control" in str(result.get("detail")) or result.get("reason_code")


def test_the_block_names_the_unapplied_operator() -> None:
    """A reader must learn WHICH mutation failed to apply, not just that one did."""
    result = _compile({
        "validation_constraint": "required",
        "field": "sku",
        "field_tokens": ["@path", "sku"],
    })
    if result.get("status") == "BLOCKED":
        detail = str(result.get("detail"))
        assert "validation_treatment_identical_to_control" in detail or "unavailable" in detail


def test_the_guard_exists_at_the_call_site() -> None:
    """Pinned in source: the comparison must happen before the result is returned."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "experiment_protocols_privacy_base.py"
    ).read_text(encoding="utf-8")
    # Anchor on the guard's emitted reason string (trailing colon), not the
    # first prose mention in a comment above the arms — the refactor added a
    # comment that also names the reason, but the guard itself is the string
    # literal that ships in the BLOCKED detail.
    assert "validation_treatment_identical_to_control:" in source
    guard_at = source.index("validation_treatment_identical_to_control:")
    assign_at = source.index('treatment_plan[0]["mutation"] = mutation')
    assert assign_at < guard_at, "the guard must run after the arms are built"


def test_the_mutation_descriptor_is_still_inert_at_runtime() -> None:
    """Documents WHY the guard is needed, so removing it needs a real reason.

    If some executor ever starts applying step["mutation"], this test fails and whoever
    wired it can lift the guard deliberately instead of by accident.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
    consumers = []
    for name in ("experiment_runtime_support.py", "experiment_executor.py",
                 "experiment_batch_executor.py", "sandbox_write_executor.py",
                 "sandbox_write_executor_base.py"):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if 'get("mutation")' in text or '["mutation"]' in text:
            consumers.append(name)
    assert consumers == [], (
        "an executor now reads step['mutation']; the identical-arms guard can be "
        f"reconsidered deliberately: {consumers}"
    )


# ── a genuinely differing probe must still compile ──────────────────────────

def test_a_body_mutation_still_compiles() -> None:
    """The write path mutates the body at compile time and must not be blocked."""
    write_op = {
        "id": "bir_post_order",
        "operation_id": "post_api_orders",
        "method": "POST",
        "path": "/api/orders",
        "read_write": "write",
        "parameters": ["qty"],
        "request_schema": {"properties": {"qty": {"type": "integer", "minimum": 1}},
                           "required": ["qty"]},
        "request_example": {"qty": 1},
        "response_schema": {"201": {"description": "created"}},
    }
    result = compile_family_protocol(
        risk_family="validation",
        operation=write_op,
        operation_ref="bir_post_order",
        control_actor_ref="bir_buyer",
        treatment_actor_ref="bir_buyer",
        property_spec={"validation_constraint": "minimum", "field": "qty",
                       "field_tokens": ["qty"]},
        behavior_ir={"operations": [write_op], "actors": [{"id": "bir_buyer", "role": "buyer"}]},
    )
    if result.get("status") == "COMPILED":
        control = (result.get("control_plan") or [{}])[0]
        treatment = (result.get("treatment_plan") or [{}])[0]
        assert control.get("body") != treatment.get("body"), (
            "a compiled body-mutation probe must carry differing bodies"
        )


def test_a_non_validation_family_is_untouched() -> None:
    """The guard is scoped to validation; other families must pass through."""
    result = compile_family_protocol(
        risk_family="authorization",
        operation=_operation(),
        operation_ref="bir_get_product",
        control_actor_ref="bir_buyer",
        treatment_actor_ref="bir_admin",
        property_spec={"template": "owner_viewer_isolation"},
        behavior_ir={"operations": [_operation()],
                     "actors": [{"id": "bir_buyer", "role": "buyer"},
                                {"id": "bir_admin", "role": "admin"}]},
    )
    assert "validation_treatment_identical_to_control" not in str(result.get("detail") or "")


# ── executor-realizability: path/header-only mutations block at compile ──────

def test_path_only_mutation_blocks_at_compile_time() -> None:
    """The step executor renders only query/body as a wire difference; it treats
    step["path"] as a route template and never reads step["header"]. A mutation
    confined to the path therefore yields two byte-identical wire requests, so
    the compiler must block it honestly rather than emit a vacuous probe."""
    result = _compile({
        "validation_constraint": "required",
        "field": "sku",
        "field_tokens": ["@path", "sku"],
    })
    assert result.get("status") == "BLOCKED", (
        "a path-only mutation the executor cannot render on the wire must "
        f"block at compile time, got: {result}"
    )
    assert "identical_to_control" in str(result.get("detail"))


def test_header_only_mutation_blocks_at_compile_time() -> None:
    """No executor dispatch path consumes a header plan dict today, so a
    header-only mutation cannot be realized on the wire and must block."""
    result = _compile({
        "validation_constraint": "required",
        "field": "X-Api-Version",
        "field_tokens": ["@header", "X-Api-Version"],
    })
    assert result.get("status") == "BLOCKED", (
        "a header-only mutation the executor cannot render on the wire must "
        f"block at compile time, got: {result}"
    )
    assert "identical_to_control" in str(result.get("detail"))
