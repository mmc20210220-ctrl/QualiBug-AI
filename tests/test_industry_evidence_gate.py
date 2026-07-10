from __future__ import annotations

from ai_test_asset_center.business_adaptation_layer import (
    _select_domains,
    generate_business_adaptive_probes,
)
from ai_test_asset_center.defect_discovery import infer_industry
from ai_test_asset_center.enterprise_knowledge_center import _oracle_dsl_pack_from_recognized_industries
from ai_test_asset_center.multi_industry_business_reasoning import _select_industries
from ai_test_asset_center.oracle_dsl.rule_library import (
    RuleLibrary,
    get_rules_for_recognized_industries,
    normalize_industry_key,
)
from ai_test_asset_center.oracle_engine import OracleRegistry, _scenario_allows_ecommerce_oracles
from ai_test_asset_center.phase103_enterprise_command_center import resolve_industry_template_or_general


def test_select_industries_fails_closed_on_weak_evidence():
    selected, mode = _select_industries({
        "ecommerce": {
            "score": 1.5,
            "confidence": 0.4,
            "object_hits": ["order"],
            "flow_hits": [],
            "role_hits": [],
            "evidence": [],
        }
    })
    assert selected == []
    assert mode == "unknown_general_business"


def test_select_industries_requires_object_hit_even_with_high_score():
    selected, mode = _select_industries({
        "finance": {
            "score": 5.0,
            "confidence": 0.9,
            "object_hits": [],
            "flow_hits": ["transaction"],
            "role_hits": ["auditor"],
            "evidence": [],
        }
    })
    assert selected == []
    assert mode == "unknown_general_business"


def test_select_industries_activates_with_sufficient_evidence():
    selected, mode = _select_industries({
        "healthcare": {
            "score": 4.2,
            "confidence": 0.72,
            "object_hits": ["patient", "prescription"],
            "flow_hits": ["diagnosis"],
            "role_hits": ["doctor"],
            "evidence": [{"source": "document"}],
        },
        "ecommerce": {
            "score": 1.0,
            "confidence": 0.2,
            "object_hits": [],
            "flow_hits": [],
            "role_hits": [],
            "evidence": [],
        },
    })
    assert selected == ["healthcare"]
    assert mode == "single_industry"


def test_rule_library_never_defaults_unknown_to_ecommerce():
    lib = RuleLibrary()
    assert lib.get_rules("") == []
    assert lib.get_rules("unknown_general_business") == []
    assert lib.get_rules("auto") == []
    assert get_rules_for_recognized_industries([]) == []
    assert get_rules_for_recognized_industries(["ecommerce"], confidences={"ecommerce": 0.2}) == []
    rules = get_rules_for_recognized_industries(["finance"], confidences={"finance": 0.8})
    assert rules
    assert all("inventory" not in str(getattr(rule, "raw_text", "") or getattr(rule, "text", "") or "").lower() for rule in rules)


def test_resolve_industry_template_or_general_never_defaults_to_ecommerce():
    general = resolve_industry_template_or_general("")
    assert general["industry"] == "general_business"
    assert general["default_business_flows"] == []
    assert "ecommerce" not in str(general).lower() or general["industry"] != "ecommerce"

    finance = resolve_industry_template_or_general("finance")
    assert finance["industry"] == "finance"
    assert finance["default_business_flows"]


def test_business_adaptation_never_defaults_unknown_to_ecommerce():
    assert _select_domains({}) == []
    assert _select_domains({"ecommerce": 0.0, "finance": 0.0}) == []
    assert _select_domains({"ecommerce": 1.5}) == []  # below min score
    assert _select_domains({"healthcare": 3.2, "ecommerce": 1.0}) == ["healthcare"]

    probes = generate_business_adaptive_probes(
        {
            "paths": {
                "/api/patients/{id}": {
                    "get": {"summary": "patient record", "operationId": "getPatient"},
                }
            }
        },
        {"discovery_mode": "safe", "max_probe_count": 20},
        project_id="__industry_gate_tmp__",
    )
    assert all(str(p.get("business_domain") or "") != "ecommerce" for p in probes)


def test_industry_key_aliases_bridge_healthcare_and_saas():
    assert normalize_industry_key("healthcare") == "medical"
    assert normalize_industry_key("saas_multitenant") == "saas"
    assert normalize_industry_key("unknown_general_business") == ""
    lib = RuleLibrary()
    medical = lib.get_rules("healthcare")
    assert medical
    assert any("patient" in (getattr(r, "raw_text", "") or "").lower() for r in medical)
    saas = get_rules_for_recognized_industries(
        ["saas_multitenant"],
        confidences={"saas_multitenant": 0.8},
    )
    assert saas
    assert any("tenant" in (getattr(r, "raw_text", "") or "").lower() for r in saas)


def test_oracle_dsl_pack_empty_without_recognized_industry():
    rules, oracles = _oracle_dsl_pack_from_recognized_industries([])
    assert rules == []
    assert oracles == []
    rules, oracles = _oracle_dsl_pack_from_recognized_industries([
        {"industry": "ecommerce", "confidence": 0.2},
    ])
    assert rules == []
    assert oracles == []


def test_oracle_dsl_pack_activates_for_confident_healthcare():
    rules, oracles = _oracle_dsl_pack_from_recognized_industries([
        {"industry": "healthcare", "confidence": 0.8},
    ])
    assert rules
    assert oracles
    assert all(row.get("industry") == "medical" for row in rules)
    assert all(row.get("source_id") == "oracle_dsl_library" for row in rules)
    joined = " ".join(str(row.get("statement") or "") for row in rules).lower()
    assert "patient" in joined or "prescription" in joined
    assert "coupon" not in joined


def test_coupon_oracle_not_attached_from_bare_entity_without_ecommerce():
    registry = OracleRegistry()
    scenario = {"entity": "coupon", "steps": [{"path": "/api/reports/summary"}], "oracle_rules": []}
    assert _scenario_allows_ecommerce_oracles(scenario) is False
    names = [o.name for o in registry.get_for_scenario(scenario)]
    assert "CouponOracle" not in names

    ecommerce_scenario = {
        "entity": "coupon",
        "business_domain": "ecommerce",
        "steps": [{"path": "/api/reports/summary"}],
        "oracle_rules": [],
    }
    assert _scenario_allows_ecommerce_oracles(ecommerce_scenario) is True
    names = [o.name for o in registry.get_for_scenario(ecommerce_scenario)]
    assert "CouponOracle" in names

    path_scenario = {
        "entity": "item",
        "steps": [{"path": "/api/coupons/{id}/apply"}],
        "oracle_rules": [],
    }
    names = [o.name for o in registry.get_for_scenario(path_scenario)]
    assert "CouponOracle" in names


def test_infer_industry_fails_closed_on_weak_or_tied_keywords():
    assert infer_industry("generic workflow system", {}) == "generic_enterprise_software"
    assert infer_industry("order management", {"/api/orders": {}}) == "generic_enterprise_software"
    # Distinct multi-keyword healthcare evidence should win.
    assert infer_industry(
        "patient prescription medical record clinic",
        {"/api/patients": {}, "/api/prescriptions": {}},
    ) == "healthcare"
