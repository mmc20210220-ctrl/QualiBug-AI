# -*- coding: utf-8 -*-
"""Symbolic numeric-bound declaration matching for semantic mutations.

Root fix: closed token lists rejected 763 schema-inferred negative-value
probes in CMP_77d5dfe1 r7 because source phrasings like 库存必须≥0 /
不能小于零 / cannot be negative were absent from the list.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center.experiment_protocols import _semantic_constraint_declared

_RESULTS: list[tuple[str, bool]] = []


def check(name, cond):
    _RESULTS.append((name, bool(cond)))
    print(("  [OK] " if cond else "  [FAIL] ") + name)


NEG = "semantic:negative_value"
ZERO = "semantic:zero_quantity"

# T1: previously-missed phrasings now accepted
for text in (
    "库存数量必须≥0",
    "库存不能小于零",
    "数量不允许出现负数",
    "stock cannot be negative",
    "quantity should not be negative",
    "金额必须大于等于0",
    "价格需大于0",
):
    check(f"T1 negative declared: {text[:24]}", _semantic_constraint_declared(NEG, text))

# T2: original token-list phrasings still accepted (no regression)
for text in ("库存非负", "数量不能为负", "库存必须大于0", "non-negative", "positive"):
    check(f"T2 legacy phrasing kept: {text[:20]}", _semantic_constraint_declared(NEG, text))

# T3: unrelated text still rejected (fail-closed preserved)
for text in (
    "商品名称不能为空",
    "用户下单时需要选择地址",
    "优惠券有效期七天",
    "status must be ACTIVE",
):
    check(f"T3 unrelated rejects: {text[:24]}", not _semantic_constraint_declared(NEG, text))

# T4: zero_quantity declarations
for text in ("购买数量不能为0", "数量非零", "qty must not be zero", "数量≠0"):
    check(f"T4 zero_quantity declared: {text[:22]}", _semantic_constraint_declared(ZERO, text))
check("T5 zero_quantity unrelated rejects",
      not _semantic_constraint_declared(ZERO, "名称必填"))

# T6: negative also satisfies zero_quantity family gate
check("T6 negative text satisfies zero_quantity constraint too",
      _semantic_constraint_declared(ZERO, "数量不能为负"))



def test_all_declarations():
    failed = [name for name, ok in _RESULTS if not ok]
    assert not failed, f"failed checks: {failed}"
