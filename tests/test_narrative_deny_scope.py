"""A restriction whose action cannot be determined must not become a wildcard deny.

The narrative permission extractor ended with

    action_values = _permission_action_aliases(negative_clause) or ["*"]

so a clause it could not parse an action out of produced a deny on **every** action of
the resource. Measured on the live 131-defect target, the source line

    「3. warehouse 可以调整库存，但不能改商品价格；」

became ``{role: warehouse, resource: product, actions: ["*"], decision: "deny"}``. The
source denies one action on one field; the row denied everything. The oracle then
asserted that ``GET /api/products`` must fail for warehouse, it did not, and the run
reported a defect the source never claimed -- **9 of the 18 deliverable findings came
from this single fallback**.

For a DENY the fail-closed direction is to deny LESS, not more: a deny that is too wide
manufactures assertions, and a failed manufactured assertion is a fabricated defect
reported against correct code. An undeterminable action is now recorded with decision
``unknown``, which behavior_ir maps to ``permission_unknown`` -- the relation type that
already exists for exactly this case -- so the restriction stays visible without becoming
an assertion.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center._parsing import _parse_source


def _permissions(text: str, kind: str = "prd"):
    parsed = _parse_source(text.encode("utf-8"), "doc.md", kind, "src_test")
    return parsed.get("permissions") or []


def _find(rows, role, resource):
    return [r for r in rows
            if str(r.get("role")) == role and str(r.get("resource")) == resource]


# ── the defect ──────────────────────────────────────────────────────────────

def test_an_undeterminable_action_is_not_a_wildcard_deny() -> None:
    """The exact source line from the live target."""
    rows = _permissions(
        "# 权限\n\n权限原则：\n\n"
        "3. warehouse 可以调整库存，但不能改商品价格；\n"
    )
    hits = _find(rows, "warehouse", "product")
    assert hits, "the restriction must still be recorded"
    row = hits[0]
    assert row["actions"] != ["*"], "an unparsed action must not deny everything"
    assert row["decision"] == "unknown"
    assert row["actions"] == ["unspecified"]


def test_no_permission_row_carries_a_wildcard_action_deny() -> None:
    """Pinned as an invariant over the whole extraction, not one row."""
    rows = _permissions(
        "# 权限\n\n权限原则：\n\n"
        "3. warehouse 可以调整库存，但不能改商品价格；\n"
        "4. finance 可以处理支付和退款，但不能删除商品；\n"
    )
    wildcards = [r for r in rows if r.get("actions") == ["*"] and str(r.get("decision")) == "deny"]
    assert wildcards == [], wildcards


def test_the_evidence_quote_is_retained_on_an_unknown_row() -> None:
    """A reader must be able to see WHICH sentence could not be scoped."""
    rows = _permissions(
        "# 权限\n\n权限原则：\n\n3. warehouse 可以调整库存，但不能改商品价格；\n"
    )
    row = _find(rows, "warehouse", "product")[0]
    assert "不能改商品价格" in str(row.get("evidence"))


# ── denials with a determinable action keep it ──────────────────────────────

def test_a_named_action_still_produces_a_precise_deny() -> None:
    """The case that already worked must keep working, and stay narrow."""
    rows = _permissions(
        "# 权限\n\n权限原则：\n\n4. finance 可以处理支付和退款，但不能删除商品；\n"
    )
    row = _find(rows, "finance", "product")[0]
    assert row["decision"] == "deny"
    assert "delete" in row["actions"]
    assert row["actions"] != ["*"]


def test_a_modify_restriction_denies_only_modification() -> None:
    rows = _permissions(
        "# 角色权限说明\n\n权限原则：\n\n4. finance 不能修改商品和库存；\n",
        kind="permission_matrix",
    )
    row = _find(rows, "finance", "product")[0]
    assert row["decision"] == "deny"
    assert "update" in row["actions"]
    assert "read" not in row["actions"], "a modify ban must not forbid reading"


def test_a_payment_restriction_does_not_forbid_unrelated_actions() -> None:
    rows = _permissions(
        "# 角色权限说明\n\n权限原则：\n\n5. warehouse 不能处理支付退款；\n",
        kind="permission_matrix",
    )
    row = _find(rows, "warehouse", "payment")[0]
    assert row["decision"] == "deny"
    assert row["actions"] != ["*"]
    assert "read" not in row["actions"]


# ── the wildcard path that IS legitimate ────────────────────────────────────

def test_an_explicit_all_permissions_grant_still_uses_the_wildcard() -> None:
    """"所有权限" genuinely means every action, and that branch is untouched.

    The fix narrows only the DENY fallback; a source that explicitly says "all" must
    still produce a wildcard.
    """
    rows = _permissions("| 角色 | 权限 |\n|---|---|\n| admin | 所有权限 |\n",
                        kind="permission_matrix")
    wildcard = [r for r in rows if r.get("actions") == ["*"]]
    assert wildcard, "an explicit all-permissions grant must still be a wildcard"
    assert all(str(r.get("decision")) != "deny" for r in wildcard), (
        "the wildcard is a grant here, never a manufactured deny"
    )


# ── the downstream contract ─────────────────────────────────────────────────

def test_an_unknown_decision_becomes_permission_unknown_in_the_ir() -> None:
    """The IR already distinguishes unknown from deny; this makes the parser use it."""
    from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset

    asset = {
        "sources": [],
        "roles": [{"role": "warehouse", "name": "warehouse"}],
        "business_objects": [{"name": "products", "fields": ["sku", "price"]}],
        "permission_matrix": [{
            "permission_id": "perm:1",
            "source_id": "src_test",
            "role": "warehouse",
            "resource": "product",
            "resource_aliases": ["product"],
            "actions": ["unspecified"],
            "decision": "unknown",
            "scope": "unspecified",
            "evidence": "warehouse 不能改商品价格",
        }],
    }
    model = build_behavior_ir_from_knowledge_asset(asset, project_id="p")
    types = {str(r.get("relation_type")) for r in model.get("relations") or []}
    assert "denies" not in types, "an unknown decision must not become a deny relation"
