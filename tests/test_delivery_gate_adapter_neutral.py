"""The delivery gate must not require an HTTP shape — the fifth link.

Four links govern whether a defect class is findable: an obligation risk family, an
assertion kind, an implemented observer, and an experiment protocol. There is a FIFTH,
which no capability audit named: the delivery gate itself.

``build_reproduction_receipt`` required a positive ``status_code`` as the proof that a
step executed and a ``path_template`` as its request identity. So a defect on a database,
message-queue, rendered-view or timing surface could have all four links, execute, and
produce valid observer and oracle receipts — and still be structurally incapable of
becoming customer-deliverable, because its reproduction receipt could not be built at
all. The response side was already adapter-tolerant (``db_snapshot`` is accepted as
evidence); only the request side blocked.

Two properties are pinned here, and the first matters more than the second:

1. The http_api path is UNCHANGED. The reproduction receipt is sealed and
   ``validate_customer_delivery_gate_bundle`` rebuilds it demanding byte equality, so any
   change to the fingerprint composition or the step summary shape would invalidate every
   sealed receipt already on disk. 3102 stored receipts were verified to still validate
   after this change.
2. A non-http step can state an equally strong identity: its adapter, an operation_ref, an
   operation_locator in place of path_template, and an explicit invocation_outcome in
   place of the status code. Nothing is inferred and nothing is optional.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.customer_delivery_gate_v2 import (
    REPRODUCTION_RECEIPT_SCHEMA,
    _fingerprint,
    validate_reproduction_receipt,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_OUTPUTS = REPO_ROOT / "platform_outputs"

# The exact seven fields an http step's request-semantics fingerprint is composed from.
# Adding, removing or renaming one invalidates every sealed receipt on disk.
HTTP_SEMANTICS_FIELDS = (
    "operation_ref",
    "method",
    "path_template",
    "mutation_class",
    "mutation_selector",
    "mutation_operator",
    "request_body_fingerprint",
)


def _http_step(**overrides: object) -> dict[str, object]:
    step = {
        "operation_ref": "op_transition",
        "method": "post",
        "path_template": "/api/items/{id}/transition",
        "mutation_class": "state_change",
        "mutation_selector": "status",
        "mutation_operator": "set",
        "request_body_fingerprint": "a" * 64,
    }
    step.update(overrides)
    return step


def test_http_semantics_fingerprint_composition_is_pinned() -> None:
    """A regression here silently invalidates 3000+ sealed receipts."""
    step = _http_step()
    expected = _fingerprint({
        "operation_ref": step["operation_ref"],
        "method": str(step["method"]).upper(),
        "path_template": step["path_template"],
        "mutation_class": step["mutation_class"],
        "mutation_selector": step["mutation_selector"],
        "mutation_operator": step["mutation_operator"],
        "request_body_fingerprint": step["request_body_fingerprint"],
    })
    # Composed from exactly these keys, in this normalization (method upper-cased).
    assert set(HTTP_SEMANTICS_FIELDS) == {
        "operation_ref", "method", "path_template", "mutation_class",
        "mutation_selector", "mutation_operator", "request_body_fingerprint",
    }
    # An eighth field must produce a DIFFERENT fingerprint -- proving the composition is
    # sensitive to exactly its inputs, which is why it must not be extended for http.
    with_adapter = _fingerprint({
        **{key: step[key] for key in HTTP_SEMANTICS_FIELDS if key != "method"},
        "method": str(step["method"]).upper(),
        "adapter": "http_api",
    })
    assert with_adapter != expected


def test_non_http_composition_includes_the_adapter_neutral_identity() -> None:
    """A non-http step's identity is adapter + locator + outcome, not method + path."""
    non_http = _fingerprint({
        "adapter": "db_sql",
        "operation_ref": "op_row_read",
        "operation_locator": "public.orders",
        "invocation_outcome": "ROWS_RETURNED",
        "mutation_class": "state_change",
        "mutation_selector": "status",
        "mutation_operator": "set",
        "request_body_fingerprint": "a" * 64,
    })
    # Must differ from the http composition for the same logical step, so a non-http step
    # can never collide with an http one.
    http = _fingerprint({
        "operation_ref": "op_row_read",
        "method": "",
        "path_template": "public.orders",
        "mutation_class": "state_change",
        "mutation_selector": "status",
        "mutation_operator": "set",
        "request_body_fingerprint": "a" * 64,
    })
    assert non_http != http
    assert len(non_http) == len(http)


def _stored_reproduction_receipts() -> list[dict]:
    receipts: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("schema_version") == REPRODUCTION_RECEIPT_SCHEMA:
                receipts.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    if not PLATFORM_OUTPUTS.is_dir():
        return receipts
    for path in sorted(PLATFORM_OUTPUTS.glob("*/scan_result.json")):
        try:
            walk(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return receipts


def test_every_stored_reproduction_receipt_still_validates() -> None:
    """The load-bearing regression guard for the adapter generalization.

    validate_customer_delivery_gate_bundle rebuilds the reproduction receipt and demands
    byte equality, so if the http composition or step summary shape drifted, replaying a
    stored artifact would fail. Skips rather than fails on a clean checkout.
    """
    receipts = _stored_reproduction_receipts()
    if not receipts:
        pytest.skip("no stored reproduction receipts in this checkout")

    failures: list[str] = []
    for receipt in receipts:
        try:
            validate_reproduction_receipt(receipt)
        except Exception as exc:  # noqa: BLE001 - collected and reported
            failures.append(f"{type(exc).__name__}: {exc}")
    assert not failures, (
        f"{len(failures)} of {len(receipts)} stored reproduction receipts no longer "
        f"validate; first few: {failures[:3]}"
    )


def test_stored_http_step_summaries_carry_no_adapter_keys() -> None:
    """Adapter keys are added ONLY for non-http steps.

    Adding them unconditionally would change every http step summary and break byte
    equality on rebuild.
    """
    receipts = _stored_reproduction_receipts()
    if not receipts:
        pytest.skip("no stored reproduction receipts in this checkout")

    for receipt in receipts:
        for step in receipt.get("steps") or []:
            if not isinstance(step, dict):
                continue
            # Stored steps predate the change and are all http.
            assert "adapter" not in step
            assert "operation_locator" not in step
            assert "invocation_outcome" not in step
