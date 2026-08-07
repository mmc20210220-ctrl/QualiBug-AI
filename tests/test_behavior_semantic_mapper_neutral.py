"""P0-E phase-4: behavior_semantic_mapper industry neutralization.

Contract:
- The finding-enrichment knowledge bases must not embed customer/benchmark
  business data: no built-in API-path→page map, no built-in role table, no
  built-in table names or SQL templates, no industry module names.
- SQL verification hints are generated from the finding's own declared
  source_entity only (fail-safe: no entity → no hint).
- Page/module enrichment falls back to neutral labels for industry paths;
  generic system concepts (auth/approvals/config/scan/…) stay mapped.
- RISK_IMPACT wording is industry-neutral (no 库存/资金/订单).
"""

from __future__ import annotations

from ai_test_asset_center.behavior_semantic_mapper import (
    RISK_IMPACT,
    _find_sql_hint,
    _guess_module,
    _path_to_page,
    attach_behavior_semantics,
    enrich_finding,
)

# Benchmark/customer terms that must never appear in the mapper.
_FORBIDDEN_TERMS = (
    "订单管理", "库存管理", "物料管理", "退款管理", "买家身份",
    "inventory", "inventory_ledger", "bom_line", "platform_admin",
)


def test_no_builtin_knowledge_bases_remain() -> None:
    import ai_test_asset_center.behavior_semantic_mapper as mapper

    assert not hasattr(mapper, "PAGE_MAP")
    assert not hasattr(mapper, "ROLE_ACTIONS")
    assert not hasattr(mapper, "SQL_HINTS")


def test_no_forbidden_business_terms_in_module_source() -> None:
    import io

    src = io.open(
        "ai_test_asset_center/behavior_semantic_mapper.py",
        encoding="utf-8",
    ).read()
    # The only allowed mentions are the explanatory comments listing what is
    # NOT invented; every other occurrence is a violation.
    comment_lines = {
        "orders/payments/", "inventory/materials/refunds)", "orders/payments/contracts)",
    }
    for term in _FORBIDDEN_TERMS:
        occurrences = [line for line in src.splitlines() if term in line]
        for line in occurrences:
            assert any(marker in line for marker in ("customer business", "never invented")), (
                f"forbidden term {term!r} in non-comment line: {line.strip()}"
            )


def test_sql_hint_generated_from_declared_entity_only() -> None:
    assert _find_sql_hint("库存不足") == ""
    assert _find_sql_hint("anything", "") == ""
    assert _find_sql_hint("anything", "orders") == "SELECT * FROM orders"
    assert _find_sql_hint("anything", "  orders  ") == "SELECT * FROM orders"


def test_path_to_page_has_no_industry_fallback() -> None:
    # Industry path → neutral label, never a guessed industry page.
    assert _path_to_page("/api/orders/1/pay") == "系统功能"
    assert _path_to_page("/api/orders") == "系统功能"
    # Generic system concepts stay mapped.
    assert _path_to_page("/api/approvals") == "审批流程"
    assert _path_to_page("/api/scan/run") == "扫描引擎"
    assert _path_to_page("/api/settings") == "系统设置"


def test_guess_module_has_no_industry_modules() -> None:
    assert _guess_module("订单金额异常") == "系统被测模块"
    assert _guess_module("库存不足") == "系统被测模块"
    # Generic system concepts stay mapped.
    assert _guess_module("auth failure") == "用户与权限"
    assert _guess_module("config error") == "系统配置"


def test_risk_impact_wording_is_industry_neutral() -> None:
    for key, text in RISK_IMPACT.items():
        assert "库存" not in text and "资金" not in text and "订单" not in text, (
            f"industry term in RISK_IMPACT[{key}]: {text}"
        )


def test_enrich_finding_still_works_with_neutral_outputs() -> None:
    finding = {
        "title": "GET /api/orders/1/pay 越权",
        "category": "permission",
        "risk_type": "permission_boundary",
        "severity": "P1",
        "source_entity": "orders",
    }
    out = enrich_finding(dict(finding))
    assert out["business_impact"]["summary"] == "未授权用户可访问或操作敏感功能，存在越权风险"
    # SQL hint comes from the declared entity, never a built-in table.
    assert out["investigation_guidance"]["sql_verify"] == "SELECT * FROM orders"
    assert out["investigation_guidance"]["primary_area"] == "系统被测模块"
    assert out["evidence_hint"]
    assert "inventory" not in out["evidence_hint"]
    assert "SELECT * FROM orders" in out["evidence_hint"]


def test_enrich_finding_fails_safe_without_entity() -> None:
    finding = {
        "title": "库存不足但未拒绝发货",
        "risk_type": "conservation",
        "severity": "P2",
    }
    out = enrich_finding(dict(finding))
    assert out["investigation_guidance"]["sql_verify"] == ""
    # Conservation impact wording is industry-neutral now.
    assert out["business_impact"]["summary"] == (
        "业务守恒规则被违反，可能导致核心业务数据异常"
    )


def test_attach_behavior_semantics_runs_on_scan_result() -> None:
    scan_result = {
        "real_findings": [
            {
                "title": "POST /api/orders/refund 越权",
                "risk_type": "permission_boundary",
                "severity": "P1",
            }
        ]
    }
    out = attach_behavior_semantics(scan_result, project="p", root=".")
    assert out["real_findings"][0]["business_impact"]
    assert out["real_findings"][0]["investigation_guidance"]
    assert "inventory" not in str(out)
