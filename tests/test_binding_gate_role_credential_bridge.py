# -*- coding: utf-8 -*-
"""Actor-dimension authoritative fallback: role-level CANDIDATE actors whose
role has a runtime-bound (declared-credential) account pass the planning gate.

Evidence: CMP_77d5dfe1 round7 — 8,689 authorization obligations blocked with
BINDING_GATE_BLOCKED:actor(status=CANDIDATE) despite declared credentials for
the same roles existing in runtime_actors. Upstream: _auto_promote gated
executable_without_probe behind composite-confidence thresholds, stranding
even runtime_bound actors at CANDIDATE.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from ai_test_asset_center.binding_completeness_gate import (
    _actor_role_has_runtime_bound_credentials,
    gate_or_block,
)
from ai_test_asset_center.binding_ledger import BindingLedger
from ai_test_asset_center.binding_builder import build_all_bindings


def build_ir(with_runtime_bound_buyer: bool):
    actors = [
        {
            "id": "bir_matrix_buyer",
            "role": "buyer",
            # matrix-derived role actor: no credentials, not runtime_bound
        },
        {
            "id": "bir_runtime_admin",
            "role": "admin",
            "runtime_bound": True,
            "account_ref": "admin",
        },
    ]
    if with_runtime_bound_buyer:
        actors.append({
            "id": "bir_runtime_buyer01",
            "role": "buyer",
            "runtime_bound": True,
            "account_ref": "buyer01",
        })
    return {
        "actors": actors,
        "operations": [
            {"id": "bir_op_cart", "method": "GET", "path": "/api/cart/items"},
        ],
        "relations": [],
    }


def make_obligation():
    return {
        "obligation_id": "obl_test_authz",
        "risk_family": "authorization",
        "required_operations": ["bir_op_cart"],
        "required_actors": ["bir_matrix_buyer"],
        "property": {"type": "permits", "subject_ref": "bir_matrix_buyer"},
    }


def build_ledger(ir):
    ledger = BindingLedger()
    build_all_bindings(ir, ledger, source_module="test")
    return ledger


def test_same_role_runtime_credentials_satisfy_gate():
    ir = build_ir(with_runtime_bound_buyer=True)
    ledger = build_ledger(ir)
    ok, reason = gate_or_block(
        ledger, obligation=make_obligation(), behavior_ir=ir
    )
    assert ok is True, reason
    assert "actor:" not in reason


def test_no_declared_credentials_keeps_honest_block():
    ir = build_ir(with_runtime_bound_buyer=False)
    ledger = build_ledger(ir)
    ok, reason = gate_or_block(
        ledger, obligation=make_obligation(), behavior_ir=ir
    )
    assert ok is False
    assert "actor:" in reason and "CANDIDATE" in reason


def test_anonymous_role_never_bridged():
    ir = build_ir(with_runtime_bound_buyer=True)
    assert (
        _actor_role_has_runtime_bound_credentials(ir, "bir_does_not_exist")
        is False
    )


def test_runtime_bound_actor_promotes_to_executable():
    """The declared-confirmation bypass must reach EXECUTABLE regardless of
    composite-confidence coverage penalties (3/8 dimensions)."""
    ir = build_ir(with_runtime_bound_buyer=True)
    ledger = build_ledger(ir)
    statuses = {
        row.get("source_node_id"): row.get("status")
        for row in ledger.get_by_type("actor")
    }
    assert statuses["bir_runtime_buyer01"] == "EXECUTABLE"
    assert statuses["bir_runtime_admin"] == "EXECUTABLE"
    # Matrix-derived role actor without credentials stays CANDIDATE —
    # the gate bridge (not blind promotion) is what satisfies it.
    assert statuses["bir_matrix_buyer"] == "CANDIDATE"


def test_operation_declared_identity_still_executable():
    ir = build_ir(with_runtime_bound_buyer=True)
    ledger = build_ledger(ir)
    statuses = {
        row.get("source_node_id"): row.get("status")
        for row in ledger.get_by_type("operation")
    }
    assert statuses["bir_op_cart"] == "EXECUTABLE"
