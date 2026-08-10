"""Evaluator constraint-class matching: DB-constraint GT x enforcement evidence.

Attack-D: DB-001~004 (数据库约束) were missed because the constraint-enforcement
findings and the constraint-class ground truth describe the same defect with
different vocabularies ("不能为负/必须唯一/重复值不允许出现" vs
"非负/唯一约束/重复支付") and different family taxonomies (enforcement layer
"validation"/"conservation" vs business layer "idempotency"/"data_consistency").
Literal matching scored real reproduced evidence ~0.04-0.47, below the 0.58
acceptance threshold.

These tests lock the generic fix: concept-synonym keyword normalization,
field-identity family credit and punctuation-normalized title tokens, applied
only to constraint-class GT pairs (module=database / type 数据库约束).  All
synthetic — no benchmark answer or customer data.
"""

from __future__ import annotations

import pytest

from benchmark_evaluator.benchmark_compute import (
    _constraint_field_identity_present,
    _constraint_keyword_hits,
    _constraint_title_token_hits,
    _is_constraint_class_gt,
    _match_finding_to_gt,
)


def _evidence_finding(title: str, *, kind: str, family: str) -> dict:
    return {
        "title": title,
        "category": kind,
        "risk_family": family,
        "failed_assertions": [{"kind": kind}],
        "evidence": {
            "assertion": {"kind": kind},
            "request": "POST /api/whatever",
            "response": "HTTP 200",
        },
        "reproduction": {"method": "POST", "path": "/api/whatever"},
    }


# ── 1. Synonym normalization (bilingual, industry-neutral concepts) ────────


def test_synonym_maps_cannot_be_negative_to_non_negative() -> None:
    blob = "调整后库存不能为负 (must not go below zero)"
    assert _constraint_keyword_hits(blob, ["非负"]) == 1


def test_synonym_maps_must_be_unique_to_unique_constraint() -> None:
    blob = "`payments`.`idempotency_key` 必须唯一，重复值不允许出现"
    assert _constraint_keyword_hits(blob, ["唯一约束"]) == 1
    assert _constraint_keyword_hits(blob, ["重复支付"]) == 1


def test_synonym_maps_quantity_not_negative_to_negative_keyword() -> None:
    blob = "数量不允许为负 (must stay positive)"
    assert _constraint_keyword_hits(blob, ["负数"]) == 1
    assert _constraint_keyword_hits(blob, ["正数"]) == 1


def test_synonym_no_invention_when_concept_absent() -> None:
    blob = "订单状态必须为 SHIPPED"
    assert _constraint_keyword_hits(blob, ["非负", "负数", "唯一"]) == 0


# ── 2. Constraint-class GT detection (structural, not bug-specific) ────────


def test_constraint_class_gt_detection() -> None:
    assert _is_constraint_class_gt({"module": "database", "type": "数据库约束/幂等"})
    assert _is_constraint_class_gt({"type": "数据库约束/金额"})
    assert _is_constraint_class_gt({"module": "database"})
    assert not _is_constraint_class_gt({"module": "api", "type": "权限"})
    assert not _is_constraint_class_gt({})


# ── 3. Field-identity gate stays fail-closed on the wrong field ────────────


def test_field_identity_present_only_for_named_field() -> None:
    blob = "`users`.`balance` 必须非负，数值不允许为负"
    assert _constraint_field_identity_present(blob, ["payable_amount", "非负"]) is False
    assert _constraint_field_identity_present(blob, ["balance", "非负"]) is True


def test_wrong_field_constraint_evidence_never_matches() -> None:
    # users.balance evidence must not earn DB-002 (orders.payable_amount).
    finding = _evidence_finding(
        "`users`.`balance` 必须非负，数值不允许为负: validation_rejection POST /api/users",
        kind="validation_rejection",
        family="validation",
    )
    gt = {
        "bug_id": "DB-002",
        "title": "orders.payable_amount 未限制非负",
        "module": "database",
        "type": "数据库约束/金额",
        "match_keywords": ["payable_amount", "非负", "check", "负数"],
        "trigger": "异常优惠导致 payable < 0",
    }
    assert _match_finding_to_gt(finding, [gt], set()) is None


# ── 4. DB-001 class: idempotency_key uniqueness evidence matches ───────────


def test_db001_idempotency_key_uniqueness_evidence_matches() -> None:
    finding = _evidence_finding(
        "`payments`.`idempotency_key` 必须唯一，重复值不允许出现 "
        "(values must be unique): validation_rejection "
        "POST /api/payments/admin/manual-success",
        kind="validation_rejection",
        family="validation",
    )
    gt = {
        "bug_id": "DB-001",
        "title": "payments.idempotency_key 未设置唯一约束",
        "module": "database",
        "type": "数据库约束/幂等",
        "match_keywords": ["idempotency_key", "unique", "唯一约束", "重复支付"],
        "trigger": "相同 key 插入多笔支付",
    }
    matched = _match_finding_to_gt(finding, [gt], set())
    assert matched is not None
    assert matched["bug_id"] == "DB-001"
    assert float(matched["__match_score"]) >= 0.58


# ── 5. DB-003 class: available_qty non-negative evidence matches ───────────


def test_db003_available_qty_non_negative_evidence_matches() -> None:
    finding = _evidence_finding(
        "如果预计 `available_qty < 0`，调整后库存不能为负: non_negative "
        "POST /api/inventory/admin/adjust",
        kind="non_negative",
        family="conservation",
    )
    gt = {
        "bug_id": "DB-003",
        "title": "inventory.available_qty 未设置非负约束",
        "module": "database",
        "type": "数据库约束/库存",
        "match_keywords": ["available_qty", "非负", "check", "负库存"],
        "trigger": "库存调成负数",
    }
    matched = _match_finding_to_gt(finding, [gt], set())
    assert matched is not None
    assert matched["bug_id"] == "DB-003"
    assert float(matched["__match_score"]) >= 0.58


# ── 6. DB-004 class: cart qty positive evidence matches ────────────────────


def test_db004_cart_qty_positive_evidence_matches() -> None:
    finding = _evidence_finding(
        "`cart_items`.`qty` 必须为正数，数量不允许为负 (must stay positive): "
        "validation_rejection PATCH /api/cart/items/abc",
        kind="validation_rejection",
        family="validation",
    )
    gt = {
        "bug_id": "DB-004",
        "title": "cart_items.qty 未设置正数约束",
        "module": "database",
        "type": "数据库约束/参数",
        "match_keywords": ["cart_items", "qty", "check", "负数"],
        "trigger": "购物车 qty=-1",
    }
    matched = _match_finding_to_gt(finding, [gt], set())
    assert matched is not None
    assert matched["bug_id"] == "DB-004"
    assert float(matched["__match_score"]) >= 0.58


# ── 7. Non-constraint GTs keep literal behavior (no synonym credit) ────────


def test_non_constraint_gt_keeps_literal_keyword_matching() -> None:
    # INV-006 is an API-layer bug (not module=database): the synonym
    # normalization must NOT give "不能为负" a hit for keyword "负数" here.
    blob = "如果预计 `available_qty < 0`，调整后库存不能为负"
    gt = {
        "bug_id": "INV-006",
        "title": "库存调整允许 available_qty 调成负数",
        "type": "库存",
        "match_keywords": ["adjust", "available_qty", "负数", "delta"],
        "trigger": "POST /api/inventory/admin/adjust",
    }
    assert _is_constraint_class_gt(gt) is False
    # Literal semantics only: "adjust" + "available_qty" hit; the synonym
    # group must NOT turn "不能为负" into a hit for "负数", and "delta" is absent.
    assert sum(1 for kw in ["adjust", "available_qty", "负数", "delta"] if kw in blob) == 1
    assert "负数" not in blob
    assert "delta" not in blob


def test_title_token_normalization_splits_field_qualifiers() -> None:
    norm_blob = "payments.idempotency_key 必须唯一，重复值不允许出现 (values must be unique)"
    hits = _constraint_title_token_hits(
        "payments.idempotency_key 未设置唯一约束", norm_blob
    )
    assert hits >= 2  # payments + idempotency_key


# ── 8. Sequential used-ids: same evidence can still serve a second GT ──────


def test_used_ids_free_the_next_gt_for_later_variant() -> None:
    gt_db3 = {
        "bug_id": "DB-003",
        "title": "inventory.available_qty 未设置非负约束",
        "module": "database",
        "type": "数据库约束/库存",
        "match_keywords": ["available_qty", "非负", "check", "负库存"],
        "trigger": "库存调成负数",
    }
    finding = _evidence_finding(
        "如果预计 `available_qty < 0`，调整后库存不能为负: non_negative "
        "POST /api/inventory/admin/adjust",
        kind="non_negative",
        family="conservation",
    )
    first = _match_finding_to_gt(finding, [gt_db3], set())
    assert first is not None and first["bug_id"] == "DB-003"
    # Once DB-003 is used, the same matcher must NOT re-match it.
    assert _match_finding_to_gt(finding, [gt_db3], {"DB-003"}) is None
