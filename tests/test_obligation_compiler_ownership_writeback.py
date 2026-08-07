"""Regression: source-declared ownership relations must be visible to the
canonical Behavior IR after obligation compilation.

E2E regression (2026-08-07): the obligation compiler normalized a private
copy of the IR with synthetic ``owns`` relations, compiled obligations that
reference those relation IDs, but never wrote them back to the canonical IR.
The planning authority then rejected every such obligation as
``behavior_ir_reference_invalid`` and the whole discovery pipeline died with
zero findings. This test pins the single-source-of-truth write-back: after
``compile_obligations_from_behavior_ir`` every obligation's ``relation_refs``
must exist in the canonical ``behavior_ir["relations"]``.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


def _ownership_ir() -> dict:
    """Minimal IR: one own-scope read operation + two account-bound actors."""
    ir = empty_behavior_ir(project_id="ownership-writeback")
    ir["operations"] = [
        {
            "id": "op-list-things",
            "operation_id": "list_things",
            "method": "GET",
            "path": "/api/things",
            "read_write": "read",
            "summary": "用户只能查询自己的数据",
            "description": "返回当前用户自己的资源；只能查询自己的列表",
            "parameters": [{"name": "userId", "in": "query"}],
            "source_refs": [{"source_id": "api-source"}],
            "derivation": "explicit",
            "status": "accepted",
        },
        {
            "id": "op-create-thing",
            "operation_id": "create_thing",
            "method": "POST",
            "path": "/api/things",
            "read_write": "write",
            "source_refs": [{"source_id": "api-source"}],
            "derivation": "explicit",
            "status": "accepted",
        },
    ]
    ir["actors"] = [
        {
            "id": "actor-buyer-a",
            "role": "buyer",
            "account_ref": "buyer01@example.com",
            "account_status": "active",
            "credential_secret_ref": "secret:buyer01@example.com",
            "source_refs": [{"source_id": "runtime-accounts"}],
        },
        {
            "id": "actor-buyer-b",
            "role": "buyer",
            "account_ref": "buyer02@example.com",
            "account_status": "active",
            "credential_secret_ref": "secret:buyer02@example.com",
            "source_refs": [{"source_id": "runtime-accounts"}],
        },
    ]
    ir["relations"] = [
        {
            "id": "rel-permits-read",
            "relation_type": "permits",
            "from_ref": "actor-buyer-a",
            "to_ref": "op-list-things",
            "operation_ref": "op-list-things",
            "actor_ref": "actor-buyer-a",
            "preconditions": [],
            "effects": [],
            "source_refs": [{"source_id": "permission-matrix"}],
            "status": "accepted",
        },
        {
            "id": "rel-permits-write",
            "relation_type": "permits",
            "from_ref": "actor-buyer-a",
            "to_ref": "op-create-thing",
            "operation_ref": "op-create-thing",
            "actor_ref": "actor-buyer-a",
            "preconditions": [],
            "effects": [],
            "source_refs": [{"source_id": "permission-matrix"}],
            "status": "accepted",
        },
    ]
    return ir


def test_ownership_relations_written_back_to_canonical_ir() -> None:
    ir = _ownership_ir()
    before = {row.get("id") for row in ir["relations"]}

    compiled = compile_obligations_from_behavior_ir(ir)

    assert compiled["obligations"]
    after = {row.get("id") for row in ir["relations"]}
    # The normalization appended synthetic source-declared owns relations to
    # the canonical IR instead of keeping them on a private copy.
    assert after > before
    synthetic = after - before
    assert all(
        any(
            row.get("relation_type") == "owns"
            and row.get("operation_ref") == "op-list-things"
            and row.get("actor_ref")
            in {"actor-buyer-a", "actor-buyer-b"}
            for row in ir["relations"]
            if row.get("id") == rid
        )
        for rid in synthetic
    )


def test_every_obligation_relation_ref_exists_in_canonical_ir() -> None:
    """The planner invariant that crashed the E2E pipeline."""
    ir = _ownership_ir()
    compile_obligations_from_behavior_ir(ir)

    ir_relation_ids = {
        str(row.get("id"))
        for row in ir["relations"]
        if isinstance(row, dict) and row.get("id")
    }
    for obligation in compile_obligations_from_behavior_ir(ir)["obligations"]:
        unknown = set(obligation.get("relation_refs") or []) - ir_relation_ids
        assert not unknown, (
            f"obligation {obligation.get('obligation_id')} references "
            f"relations missing from canonical IR: {sorted(unknown)}"
        )


def test_writeback_is_idempotent() -> None:
    ir = _ownership_ir()
    compile_obligations_from_behavior_ir(ir)
    first_ids = {row.get("id") for row in ir["relations"]}

    # A second compile on the same IR must not duplicate the synthetic owns.
    compile_obligations_from_behavior_ir(ir)
    second_ids = {row.get("id") for row in ir["relations"]}

    assert second_ids == first_ids
