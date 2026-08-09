"""UI surface declaration chain: visible UI material -> surface entities -> executable checks.

Covers the four-link reachability gap for ``ui_state_consistency``:

* surface entity extraction from visible UI requirement documents (generic, no hardcoded
  pages) — pages, controls, buttons, role-visibility matrices, action matrices, oracles;
* compilation into governed read-only browser-plan contracts (safe_read_only, no cleanup);
* the composition asset build keeps ui specs, ui-classified rules and surface contracts;
* the obligation compiler resolves the page URL by source-declared screen identity and
  carries surface contracts; the protocol compiles them into DOM assertions and the
  assertion evaluator judges them against the rendered page.
"""
from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center import _parse_source
from ai_test_asset_center.enterprise_knowledge_center._ui_surface_declarations import (
    compile_ui_surface_checks,
    compile_ui_surface_contracts,
    extract_ui_surface_entities,
    install_ui_surface_declaration_parser,
)


# The parser patch is registered by the composition root
# (``configure_source_parser_extensions``); a direct ``_parse_source`` call in
# tests mirrors that registration exactly like the product does.
install_ui_surface_declaration_parser()


def _payload() -> dict:
    return {
        "document": {"name": "Generic Store UI Requirements", "version": "1.0"},
        "screens": [
            {"id": "SHOP-01", "name": "Storefront", "url": "http://target.test/store", "regions": ["catalog", "checkout"]},
            {"id": "BACK-01", "name": "Back office", "url": "http://target.test/admin", "regions": ["ops", "reports"]},
        ],
        "role_visibility": {
            "clerk": {"inventory_adjust": "visible_enabled", "sales_report": "hidden"},
            "auditor": {"sales_report": "visible_enabled", "inventory_adjust": "hidden"},
        },
        "order_action_matrix": {
            "OPEN": {"cancel": "enabled", "pay": "enabled"},
            "PAID": {"cancel": "disabled", "pay": "disabled"},
        },
        "requirements": [{
            "id": "SHOP-CAT-01",
            "screen": "SHOP-01",
            "type": "visibility",
            "rule": "Only published items may render to shoppers.",
            "negative_examples": ["UNPUBLISHED", "ARCHIVED"],
        }],
        "oracles": [{
            "id": "ORACLE-SHOP-01",
            "given": "catalog renders",
            "when": "a shopper browses",
            "then": ["published items are visible", "UNPUBLISHED titles are absent"],
        }],
    }


def test_surface_entities_are_extracted_generically() -> None:
    entities = extract_ui_surface_entities(_payload(), "src_surface")
    by_type: dict[str, int] = {}
    for entity in entities:
        by_type[entity["entity_type"]] = by_type.get(entity["entity_type"], 0) + 1

    assert by_type.get("page") == 2
    # role matrix: 2 roles x 2 features; action matrix: 2 states x 2 actions
    assert by_type.get("control") == 4
    assert by_type.get("button") == 4
    assert by_type.get("page_display_state") == 1
    assert by_type.get("oracle") == 1

    pages = [e for e in entities if e["entity_type"] == "page"]
    assert {p["screen_id"] for p in pages} == {"SHOP-01", "BACK-01"}
    controls = [e for e in entities if e["entity_type"] == "control"]
    assert any(c["role"] == "clerk" and c["name"] == "inventory_adjust" and c["expected_state"] == "visible" for c in controls)
    assert any(c["role"] == "clerk" and c["name"] == "sales_report" and c["expected_state"] == "hidden" for c in controls)
    buttons = [e for e in entities if e["entity_type"] == "button"]
    assert any(b["state_context"] == "PAID" and b["name"] == "pay" and b["expected_state"] == "disabled" for b in buttons)


def test_surface_checks_compile_to_read_only_dom_assertions() -> None:
    checks, gaps = compile_ui_surface_checks(_payload(), "src_surface")
    assert not gaps, gaps
    assert checks

    display = next(c for c in checks if c["check_kind"] == "page_display_state")
    steps = display["plan_steps"]
    assert steps[0]["action"] == "goto"
    assert steps[0]["url"] == "http://target.test/store"
    hidden_steps = [s for s in steps if s["action"] == "expect_hidden"]
    assert {s["locator_intent"]["text"] for s in hidden_steps} == {"UNPUBLISHED", "ARCHIVED"}

    menu = next(c for c in checks if c["check_kind"] == "control_state" and c.get("role") == "clerk" and c["control"] == "sales_report")
    assert menu["expected_state"] == "hidden"
    assert menu["plan_steps"][1]["action"] == "expect_hidden"

    button = next(c for c in checks if c["check_kind"] == "control_state" and c["control"] == "pay" and c.get("state_context") == "PAID")
    assert button["expected_state"] == "disabled"
    assert button["plan_steps"][1]["action"] == "expect_disabled"


def test_surface_contracts_are_read_only_and_need_no_cleanup() -> None:
    contracts, gaps = compile_ui_surface_contracts(_payload(), "src_surface")
    assert not gaps, gaps
    assert contracts
    for contract in contracts:
        request = contract["ui_request"]
        assert request["provider"] == "playwright_browser_plan"
        assert request["execution_mode"] == "safe_read_only"
        plan = request["browser_plan"]
        assert plan["execution_mode"] == "safe_read_only"
        assert contract["derivation"] == "explicit"
        assert contract["status"] == "accepted"


def test_missing_page_url_is_a_visible_gap_not_a_guess() -> None:
    payload = {
        "screens": [{"id": "SHOP-01", "name": "Storefront", "url": ""}],
        "requirements": [{
            "id": "R1",
            "screen": "SHOP-01",
            "type": "visibility",
            "rule": "Only live items render.",
            "negative_examples": ["OFFLINE"],
        }],
    }
    checks, gaps = compile_ui_surface_checks(payload, "src_no_url")
    assert not checks
    assert any(g["reason_code"] == "UI_SURFACE_PAGE_URL_MISSING" for g in gaps)


def test_interactive_obligation_fails_closed_without_cleanup_equivalence() -> None:
    """A confirmation-type requirement must not silently become a read-only
    probe or an invented interactive plan: it is a named gap until the source
    declares the write-mode contract with cleanup equivalence."""
    payload = {
        "screens": [{"id": "BACK-01", "name": "Back office", "url": "http://target.test/admin"}],
        "requirements": [{
            "id": "ADMIN-INV-04",
            "screen": "BACK-01",
            "type": "confirmation",
            "rule": "Large adjustments require a second confirmation with impact details.",
        }],
    }
    checks, gaps = compile_ui_surface_checks(payload, "src_interactive")
    assert not checks
    interactive_gap = next(
        g for g in gaps
        if g["reason_code"] == "UI_SURFACE_INTERACTION_CLEANUP_NOT_DECLARED"
    )
    assert interactive_gap["name"] == "ADMIN-INV-04"

    contracts, contract_gaps = compile_ui_surface_contracts(payload, "src_interactive")
    assert not contracts
    assert any(
        g["reason_code"] == "UI_SURFACE_INTERACTION_CLEANUP_NOT_DECLARED"
        for g in contract_gaps
    )


def test_parser_attaches_surface_declarations_to_requirements_specs() -> None:
    parsed = _parse_source(
        json.dumps(_payload()).encode("utf-8"),
        "ui_requirements.json",
        "uiux_requirements",
        "src_surface_parse",
    )
    specs = parsed["ui_specs"]
    assert len(specs) == 2
    with_surfaces = [s for s in specs if (s.get("surface_contracts") or [])]
    assert with_surfaces, "no spec carried surface contracts"
    for spec in with_surfaces:
        assert spec["surface_entity_count"] >= 1
        assert spec["surface_contract_count"] >= 1
        assert spec["surface_contract_gaps"] == []
        for contract in spec["surface_contracts"]:
            # Screen-scoped checks only land on their own declared page.
            start_url = contract["ui_request"]["start_url"]
            assert start_url in {spec["url"] for spec in specs}
    # The display-state check (screen=SHOP-01) must be attached only to SHOP-01.
    shop = next(s for s in with_surfaces if s["ui_spec_id"].endswith("SHOP-01"))
    back = next(s for s in with_surfaces if s["ui_spec_id"].endswith("BACK-01"))
    shop_kinds = {c["check_kind"] for c in shop["surface_contracts"]}
    back_kinds = {c["check_kind"] for c in back["surface_contracts"]}
    assert "page_display_state" in shop_kinds
    assert "page_display_state" not in back_kinds


def test_parser_patch_is_idempotent() -> None:
    from ai_test_asset_center.enterprise_knowledge_center import _parsing
    from ai_test_asset_center.enterprise_knowledge_center._ui_surface_declarations import (
        install_ui_surface_declaration_parser,
    )

    install_ui_surface_declaration_parser()
    install_ui_surface_declaration_parser()
    parsed = _parse_source(
        json.dumps(_payload()).encode("utf-8"),
        "ui_requirements.json",
        "uiux_requirements",
        "src_surface_idem",
    )
    assert all((s.get("surface_contracts") or []) for s in parsed["ui_specs"])


def _requirements_specs() -> list[dict]:
    parsed = _parse_source(
        json.dumps(_payload()).encode("utf-8"),
        "ui_requirements.json",
        "uiux_requirements",
        "src_surface_fourlink",
    )
    return parsed["ui_specs"]


def _four_link_asset() -> dict:
    """A minimal knowledge asset carrying specs AND the UI rules the
    invariant chain compiles kind=ui invariants from."""
    parsed = _parse_source(
        json.dumps(_payload()).encode("utf-8"),
        "ui_requirements.json",
        "uiux_requirements",
        "src_surface_fourlink",
    )
    return {
        "asset_id": "knowledge_asset:fourlink:test",
        "ui_design_specs": parsed["ui_specs"],
        "rule_library": parsed["rules"],
    }


def test_four_link_chain_compiles_surface_obligations_and_dom_assertions() -> None:
    """IR -> obligation -> protocol -> assertion: the four ui_state_consistency links."""
    from ai_test_asset_center import obligation_compiler as oc
    from ai_test_asset_center.assertion_dsl_base import evaluate_assertion
    from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
    from ai_test_asset_center.experiment_protocols_base import compile_family_protocol
    from ai_test_asset_center.source_ui_obligation_binding import (
        compile_obligations_with_source_ui,
    )

    specs = _requirements_specs()
    asset = _four_link_asset()
    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="fourlink",
        source_snapshot_hash="hash",
        api_operations=[],
        runtime_actors=[],
        available_surfaces={"http_api": True, "ui_browser": True, "db_snapshot": False},
    )
    # Link 1+2: obligation compile produces ui_state_consistency obligations
    # from surface contracts and the invariant chain (no API operation needed).
    compiled = compile_obligations_with_source_ui(
        ir,
        base_compile=oc.compile_obligations_from_behavior_ir,
    )
    ui_obligations = [
        row for row in (compiled.get("obligations") or [])
        if row.get("risk_family") == "ui_state_consistency"
    ]
    with_surface = [
        row for row in ui_obligations
        if (row.get("property") or {}).get("surface_contracts")
    ]
    assert ui_obligations, "no ui_state_consistency obligations"
    assert with_surface, "no obligation carried surface contracts"

    # Link 3: protocol compiles surface checks into the assertion (DOM assertions).
    protocol = None
    for row in with_surface:
        prop = row.get("property") or {}
        if prop.get("ui_url"):
            protocol = compile_family_protocol(
                risk_family="ui_state_consistency",
                operation={},
                operation_ref="",
                control_actor_ref="",
                treatment_actor_ref="",
                property_spec=prop,
                behavior_ir=ir,
            )
            if protocol.get("status") == "COMPILED":
                break
    assert protocol is not None and protocol.get("status") == "COMPILED", protocol
    assertion = protocol.get("assertion") or {}
    surface_checks = assertion.get("surface_checks") or []
    assert surface_checks, "protocol emitted no surface checks"
    # goto steps are navigation, not checks; the rest must be DOM expectations.
    expectation_actions = {
        c.get("action") for c in surface_checks if c.get("action") != "goto"
    }
    assert expectation_actions, "no DOM expectation compiled"

    # Link 4: the assertion evaluator judges the rendered page by the
    # document's own control vocabulary (present = visible, absent = hidden).
    # Pick non-conflicting controls: sales_report is hidden for clerk while
    # inventory_adjust is visible for clerk (both document-declared).
    hidden = next(
        c for c in surface_checks
        if c.get("action") == "expect_hidden"
        and (c.get("text") or (c.get("locator_intent") or {}).get("text")) == "sales_report"
    )
    visible = next(
        c for c in surface_checks
        if c.get("action") == "expect_visible"
        and (c.get("text") or (c.get("locator_intent") or {}).get("text")) == "inventory_adjust"
    )
    violating = evaluate_assertion(
        {"kind": "ui_state_consistency", "surface_checks": [hidden, visible]},
        observations={"body_text": "sales_report inventory_adjust"},
    )
    assert violating.get("passed") is False
    assert violating.get("reason_code") == "UI_SURFACE_CHECK_VIOLATED"
    clean = evaluate_assertion(
        {"kind": "ui_state_consistency", "surface_checks": [hidden, visible]},
        observations={"body_text": "inventory_adjust"},
    )
    assert clean.get("passed") is True
