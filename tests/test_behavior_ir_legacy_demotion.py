"""P0-E: legacy Chinese-text parsing demoted to candidate hints behind the
frame-confirmation gate.

Contract (SPEC §20):
- With a frame ledger, GROUNDED frames confirm legacy Chinese-text parse
  products (action→operation binding, field-level ownership, umbrella
  exclusion) before they may act as final semantics; unconfirmed candidates
  are demoted to hints and skipped.
- An absent/ungrounded frame keeps the legacy behavior as an OBSERVABLE
  fallback (receipted, never silent).
- Assets without a frame ledger keep the legacy behavior byte-for-byte; the
  receipt only records NO_FRAME_LEDGER and never rotates model_id.
- Every fallback/demotion is counted in ``legacy_semantic_fallback_receipt``
  with ``LEGACY_FALLBACK_USED`` reason codes.
"""

from __future__ import annotations

import json

from ai_test_asset_center.behavior_ir_core import (
    _content_addressed_id,
    build_behavior_ir_from_knowledge_asset,
    validate_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_clause_parser import (
    parse_chinese_clause_trees,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_envelope import (
    build_chinese_semantic_context_envelopes,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_resolver import (
    resolve_chinese_semantic_context,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_frame_compiler import (
    enrich_frames_with_clause_structure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_grounding import (
    ground_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    project_business_facts_to_semantic_frames,
)

RECEIPT_SCHEMA = "qualibug.legacy-semantic-fallback-receipt.v1"


def _heading(block_id: str, text: str, order: int) -> dict:
    return {
        "block_id": block_id, "type": "HEADING", "parent_id": "", "order": order,
        "region": "body", "level": 1, "text": text,
        "source_locator": f"r.docx#block={order}",
    }


def _paragraph(block_id: str, text: str, order: int, parent: str) -> dict:
    return {
        "block_id": block_id, "type": "PARAGRAPH", "parent_id": parent, "order": order,
        "region": "body", "text": text, "source_locator": f"r.docx#block={order}",
    }


def _fact(*, fact_id: str, statement: str, block_id: str, actor: str = "买家",
          action: str = "查询", modality: str = "MAY", ownership: str = "") -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": "PERMISSION_RULE",
        "kind": "RULE",
        "language": "zh-CN",
        "statement_frame_id": f"statement_frame:{fact_id}",
        "subject": {
            "actor_refs": [actor] if actor else [],
            "entity_refs": ["订单"],
            "resolution_evidence": [],
        },
        "object": {"entity_refs": ["订单"]},
        "predicate": action,
        "action": {"canonical": action, "raw": action},
        "conditions": [],
        "condition_combinator": "",
        "condition_frame": {},
        "scope": {"tenant": "", "organization": "", "ownership": ownership, "data_scope": ""},
        "modality": modality,
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": [],
        "state_effects": [],
        "data_effects": [],
        "quantity_constraints": [],
        "time_window_constraints": [],
        "formula_constraints": [],
        "compensation": [],
        "raw_statement": statement,
        "source_spans": [
            {
                "evidence_address": {
                    "source_id": "s1",
                    "locator": f"r.docx#block={block_id}",
                    "document_block_id": block_id,
                    "block_type": "PARAGRAPH",
                },
                "quote": statement,
            }
        ],
        "confidence": 1.0,
        "status": "ACCEPTED",
        "ambiguities": [],
        "critical": True,
        "derivation": "structure_first_explicit_fact_compiler",
    }


def _pipeline_asset(*, facts: list[dict], blocks: list[dict],
                    permission_rows: list[dict] | None = None,
                    interfaces: list[dict] | None = None,
                    entities: list[dict] | None = None,
                    rule_library: list[dict] | None = None,
                    roles: list[dict] | None = None,
                    risk_domains: list[dict] | None = None) -> dict:
    asset = {
        "document_structure_assets": {
            "items": [{"source_id": "s1", "filename": "r.docx", "blocks": blocks}]
        },
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "fact_authority": "original_chinese_source_span",
            "items": facts,
        },
        "enterprise_understanding_model": {
            "actors": [{"actor_id": "business_actor:buyer", "name": "买家"}],
            "business_objects": [
                {"object_id": "business_object:order", "name": "订单", "aliases": ["orders"]}
            ],
        },
    }
    if permission_rows is not None:
        asset["permission_matrix"] = permission_rows
    if interfaces is not None:
        asset["interfaces"] = interfaces
    if entities is not None:
        asset["entities"] = entities
    if rule_library is not None:
        asset["rule_library"] = rule_library
    if roles is not None:
        asset["roles"] = roles
    if risk_domains is not None:
        asset["risk_domains"] = risk_domains
    asset = project_business_facts_to_semantic_frames(asset)
    asset = build_chinese_semantic_context_envelopes(asset)
    asset = parse_chinese_clause_trees(asset)
    asset = enrich_frames_with_clause_structure(asset)
    asset = resolve_chinese_semantic_context(asset)
    return ground_semantic_frames(asset)


def _without_ledger(asset: dict) -> dict:
    plain = dict(asset)
    plain.pop("chinese_semantic_frame_ledger", None)
    return plain


def _unground_frames(asset: dict) -> dict:
    """Reset every frame's technical grounding to PENDING so the frame
    channel contributes nothing (schema stays valid: signature covers typed
    slots only, PENDING is a legal grounding status)."""
    for frame in asset["chinese_semantic_frame_ledger"]["items"]:
        frame["technical_grounding"] = {
            "operation_refs": [], "entity_refs": [], "field_refs": [],
            "actor_refs": [], "state_value_refs": [], "permission_scope": "",
            "status": "PENDING",
        }
        frame["actor"]["grounded_actor_refs"] = []
        frame["action"]["grounded_operation_refs"] = []
        frame["object"]["grounded_entity_refs"] = []
    return asset


def _op_path(ir: dict, op_ref: str) -> str:
    for op in ir["operations"]:
        if op.get("id") == op_ref:
            return op.get("path")
    return ""


def _op_ref_for_path(ir: dict, path: str) -> str:
    for op in ir["operations"]:
        if op.get("path") == path:
            return op.get("id")
    raise AssertionError(f"operation {path} missing")


def _invariant(ir: dict, statement: str) -> dict:
    matches = [r for r in ir["invariants"] if r.get("description") == statement]
    assert len(matches) == 1, f"expected one invariant for {statement!r}, got {len(matches)}"
    return matches[0]


# ── shared fixtures ──

def _binding_asset(*, ungrounded: bool = False) -> dict:
    """Rule 已取消订单不能支付、确认收货。 — the frame grounds only the pay
    operation (POST:/api/orders/pay); the confirm operation is a legacy
    candidate the frame never confirms."""
    h2 = _heading("h2", "订单履约", 1)
    p2 = _paragraph("p2", "买家可以支付订单。", 2, "h2")
    fact = _fact(fact_id="f:b", statement="买家可以支付订单。", block_id="p2",
                 actor="买家", action="支付")
    rule = {
        "rule_id": "zh_business:b",
        "statement": "已取消订单不能支付、确认收货。",
        "kind": "business_rule",
        "entity": "orders",
        "source_id": "s1",
        "source_locator": "r.docx#block=p2",
        "confidence": 0.9,
    }
    asset = _pipeline_asset(
        facts=[fact], blocks=[h2, p2], rule_library=[rule],
        permission_rows=[
            {"permission_id": "p2", "role": "买家", "resource": "/api/orders",
             "actions": ["post"], "decision": "allow", "scope": "all"}
        ],
        interfaces=[
            {"interface_id": "api:POST:/api/orders/pay", "method": "POST",
             "path": "/api/orders/pay", "summary": "支付订单", "description": "",
             "entity_refs": ["orders"]},
            {"interface_id": "api:POST:/api/orders/confirm", "method": "POST",
             "path": "/api/orders/confirm", "summary": "确认收货", "description": "",
             "entity_refs": ["orders"]},
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    assert asset["chinese_semantic_frame_ledger"]["items"][0]["technical_grounding"]["status"] == "GROUNDED"
    if ungrounded:
        _unground_frames(asset)
    return asset


def _ownership_asset() -> dict:
    """Two ownership-declaring interfaces; the frame grounds only the pay
    operation with structured ownership + grounded buyer actor."""
    h3 = _heading("h3", "订单管理", 1)
    p3 = _paragraph("p3", "买家可以支付订单。", 2, "h3")
    fact = _fact(fact_id="f:c", statement="买家可以支付订单。", block_id="p3",
                 actor="买家", action="支付", ownership="自己的订单")
    rule = {
        "rule_id": "zh_business:c",
        "statement": "买家可以支付订单。",
        "kind": "business_rule",
        "entity": "orders",
        "source_id": "s1",
        "source_locator": "r.docx#block=p3",
        "confidence": 0.9,
    }
    decl = "目标用户 ID；普通用户只能使用自己的 ID"

    def _iface(iid: str, path: str, summary: str) -> dict:
        return {
            "interface_id": iid, "method": "POST", "path": path, "summary": summary,
            "description": decl,
            "technical_declarations": [
                {
                    "node_kind": "OPENAPI_SCHEMA_PROPERTY",
                    "property_name": "target_user_id",
                    "description": decl,
                }
            ],
            "entity_refs": ["orders"],
        }

    return _pipeline_asset(
        facts=[fact], blocks=[h3, p3], rule_library=[rule],
        roles=[{"role": "buyer", "source_id": "roles"}],
        permission_rows=[
            {"permission_id": "p3", "role": "buyer", "resource": "/api/orders/pay",
             "actions": ["pay"], "decision": "allow", "scope": "all"},
            {"permission_id": "p4", "role": "buyer", "resource": "/api/orders/refund",
             "actions": ["refund"], "decision": "allow", "scope": "all"},
        ],
        interfaces=[
            _iface("api:POST:/api/orders/pay", "/api/orders/pay", "支付订单"),
            _iface("api:POST:/api/orders/refund", "/api/orders/refund", "申请退款"),
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )


def _umbrella_asset() -> dict:
    """Umbrella-pattern rule whose frame is fully grounded."""
    h1 = _heading("h1", "订单管理", 1)
    p1 = _paragraph("p1", "系统应保证数据一致性：买家可以查询自己的订单。", 2, "h1")
    fact = _fact(
        fact_id="f:a", statement="系统应保证数据一致性：买家可以查询自己的订单。",
        block_id="p1", actor="买家", action="查询", ownership="自己的订单",
    )
    rule = {
        "rule_id": "zh_business:a",
        "statement": "系统应保证数据一致性：买家可以查询自己的订单。",
        "kind": "business_rule",
        "source_id": "s1",
        "source_locator": "r.docx#block=p1",
        "confidence": 0.9,
    }
    asset = _pipeline_asset(
        facts=[fact], blocks=[h1, p1], rule_library=[rule],
        permission_rows=[
            {"permission_id": "p1", "role": "买家", "resource": "/api/orders",
             "actions": ["get"], "decision": "allow", "scope": "own"}
        ],
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders",
             "summary": "查询订单列表", "description": "", "entity_refs": ["orders"]}
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    assert asset["chinese_semantic_frame_ledger"]["items"][0]["technical_grounding"]["status"] == "GROUNDED"
    return asset


# ── 1. no ledger → legacy behavior unchanged, receipt compat ──

def test_no_ledger_legacy_binding_unchanged_and_receipt_compat() -> None:
    asset = _binding_asset()
    ir = build_behavior_ir_from_knowledge_asset(_without_ledger(asset))
    assert validate_behavior_ir(ir) == []
    inv = _invariant(ir, "已取消订单不能支付、确认收货。")
    # Legacy binds BOTH operations (no gate).
    bound = {_op_path(ir, ref) for ref in inv.get("operation_refs", [])}
    assert "/api/orders/pay" in bound and "/api/orders/confirm" in bound

    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["frame_ledger_present"] is False
    # Compat path: only the NO_FRAME_LEDGER marker is recorded.
    assert set(receipt["kind_counts"]) == {"ACTION_PHRASE_BINDING_NO_FRAME_LEDGER"}
    assert receipt["reason_codes"] == ["LEGACY_FALLBACK_USED"]


def test_receipt_never_rotates_model_id() -> None:
    # The receipt is attached AFTER the content address, so model_id stays the
    # content address of everything else — no-ledger assets keep their ids.
    asset = _binding_asset()
    ir = build_behavior_ir_from_knowledge_asset(_without_ledger(asset))
    payload = {k: v for k, v in ir.items() if k not in {"model_id", "legacy_semantic_fallback_receipt"}}
    assert ir["model_id"] == _content_addressed_id(payload)
    # Same asset twice → stable id.
    again = build_behavior_ir_from_knowledge_asset(_without_ledger(asset))
    assert again["model_id"] == ir["model_id"]


def test_empty_frame_ledger_is_compat_path() -> None:
    # A ledger with zero frames is not a ledger: IR identical to no-ledger.
    asset = _binding_asset()
    asset = _without_ledger(asset)
    with_ledger = dict(asset)
    with_ledger["chinese_semantic_frame_ledger"] = {"schema": "qualibug.chinese-semantic-frame-ledger.v1", "items": []}
    ir_plain = build_behavior_ir_from_knowledge_asset(asset)
    ir_empty = build_behavior_ir_from_knowledge_asset(with_ledger)
    assert ir_empty["invariants"] == ir_plain["invariants"]
    assert ir_empty["relations"] == ir_plain["relations"]
    assert ir_empty["model_id"] == ir_plain["model_id"]
    assert ir_empty["legacy_semantic_fallback_receipt"]["frame_ledger_present"] is False


# ── 2. action binding: grounded frame confirms only its operations ──

def test_action_binding_confirmed_by_grounded_frame_only() -> None:
    asset = _binding_asset()
    frame = asset["chinese_semantic_frame_ledger"]["items"][0]
    assert frame["technical_grounding"]["operation_refs"] == ["POST:/api/orders/pay"]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    assert validate_behavior_ir(ir) == []
    inv = _invariant(ir, "已取消订单不能支付、确认收货。")
    bound = {_op_path(ir, ref) for ref in inv.get("operation_refs", [])}
    # Only the frame-grounded operation may be bound; the confirm candidate is
    # demoted and skipped.
    assert bound == {"/api/orders/pay"}

    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["frame_ledger_present"] is True
    assert receipt["kind_counts"].get("ACTION_PHRASE_BINDING_SKIPPED_WHEN_GROUNDED", 0) >= 1
    assert "ACTION_PHRASE_BINDING_NO_FRAME_FOR_RULE" not in receipt["kind_counts"]


def test_action_binding_falls_back_when_frame_ungrounded() -> None:
    # Ledger present but the rule's frame is PENDING → legacy binding applies
    # as an OBSERVABLE fallback (binds both operations again).
    asset = _binding_asset(ungrounded=True)
    frame = asset["chinese_semantic_frame_ledger"]["items"][0]
    assert frame["technical_grounding"]["status"] == "PENDING"
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "已取消订单不能支付、确认收货。")
    bound = {_op_path(ir, ref) for ref in inv.get("operation_refs", [])}
    assert "/api/orders/pay" in bound and "/api/orders/confirm" in bound

    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["kind_counts"].get("ACTION_PHRASE_BINDING_FALLBACK_WHEN_UNGROUNDED", 0) >= 1
    assert "ACTION_PHRASE_BINDING_SKIPPED_WHEN_GROUNDED" not in receipt["kind_counts"]


# ── 3. field-level ownership: frame confirms or demotes each candidate ──

def test_field_ownership_confirmed_or_demoted_by_frame() -> None:
    asset = _ownership_asset()
    ir = build_behavior_ir_from_knowledge_asset(asset)
    assert validate_behavior_ir(ir) == []
    pay_op = _op_ref_for_path(ir, "/api/orders/pay")
    refund_op = _op_ref_for_path(ir, "/api/orders/refund")
    owns = [
        (r.get("from_ref"), r.get("to_ref"))
        for r in ir["relations"]
        if r.get("relation_type") == "owns" and r.get("from_ref")
    ]
    # The frame-grounded operation's ownership candidate survives…
    assert any(t[1] == pay_op for t in owns)
    # …the unconfirmed candidate (refund) is demoted and skipped.
    assert not any(t[1] == refund_op for t in owns)

    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["kind_counts"].get("FIELD_OWNERSHIP_UNCONFIRMED_SKIPPED", 0) >= 1


def test_no_ledger_legacy_ownership_unchanged() -> None:
    # Without a ledger the legacy derivation derives BOTH ownership relations.
    asset = _ownership_asset()
    ir = build_behavior_ir_from_knowledge_asset(_without_ledger(asset))
    pay_op = _op_ref_for_path(ir, "/api/orders/pay")
    refund_op = _op_ref_for_path(ir, "/api/orders/refund")
    owns = [
        (r.get("from_ref"), r.get("to_ref"))
        for r in ir["relations"]
        if r.get("relation_type") == "owns" and r.get("from_ref")
    ]
    assert any(t[1] == pay_op for t in owns)
    assert any(t[1] == refund_op for t in owns)


# ── 4. umbrella: grounded frame is structured evidence ──

def test_grounded_frame_overrides_umbrella_exclusion() -> None:
    asset = _umbrella_asset()
    ir = build_behavior_ir_from_knowledge_asset(asset)
    assert validate_behavior_ir(ir) == []
    inv = _invariant(ir, "系统应保证数据一致性：买家可以查询自己的订单。")
    # Grounded frame → the rule carries structured evidence → not excluded.
    assert inv.get("binding_status") != "umbrella_rule_excluded"
    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["kind_counts"].get("UMBRELLA_PATTERN_OVERRIDDEN_BY_GROUNDED_FRAME", 0) >= 1


def test_umbrella_exclusion_unchanged_without_ledger() -> None:
    asset = _umbrella_asset()
    ir = build_behavior_ir_from_knowledge_asset(_without_ledger(asset))
    inv = _invariant(ir, "系统应保证数据一致性：买家可以查询自己的订单。")
    assert inv.get("binding_status") == "umbrella_rule_excluded"
    # No override receipt on the compat path.
    receipt = ir["legacy_semantic_fallback_receipt"]
    assert "UMBRELLA_PATTERN_OVERRIDDEN_BY_GROUNDED_FRAME" not in receipt["kind_counts"]


def test_umbrella_exclusion_fallback_receipted_when_frame_absent() -> None:
    # Ledger exists but no frame for the umbrella rule (rule id and statement
    # both differ from the frame's identity) → legacy exclusion is kept and
    # the fallback is receipted.
    asset = _umbrella_asset()
    variant = "系统应保证数据一致性：买家可以查询自己的订单"  # no 句号 → no frame
    asset["rule_library"] = [{
        "rule_id": "zh_business:zz",
        "statement": variant,
        "kind": "business_rule",
        "source_id": "s1",
        "source_locator": "r.docx#block=p1",
        "confidence": 0.9,
    }]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, variant)
    assert inv.get("binding_status") == "umbrella_rule_excluded"
    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["kind_counts"].get("UMBRELLA_PATTERN_FALLBACK", 0) >= 1


# ── 5. token-based promotions are receipted candidate hints ──

def test_idempotency_token_promotion_is_receipted() -> None:
    # Token-promoted idempotency (no risk-domain classification) is a legacy
    # candidate hint; the receipt counts it.
    h = _heading("h1", "库存管理", 1)
    p = _paragraph("p1", "买家可以支付订单。", 2, "h1")
    fact = _fact(fact_id="f:d", statement="买家可以支付订单。", block_id="p1",
                 actor="买家", action="支付")
    rule = {
        "rule_id": "zh_business:d",
        "statement": "订单重复支付不得重复扣款。",
        "kind": "business_rule",
        "entity": "orders",
        "source_id": "s1",
        "source_locator": "r.docx#block=p1",
        "confidence": 0.9,
    }
    asset = _pipeline_asset(
        facts=[fact], blocks=[h, p], rule_library=[rule],
        permission_rows=[
            {"permission_id": "p5", "role": "买家", "resource": "/api/orders",
             "actions": ["post"], "decision": "allow", "scope": "all"}
        ],
        interfaces=[
            {"interface_id": "api:POST:/api/orders/pay", "method": "POST",
             "path": "/api/orders/pay", "summary": "支付订单", "description": "",
             "entity_refs": ["orders"]}
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "订单重复支付不得重复扣款。")
    assert inv.get("expression", {}).get("kind") == "idempotency"
    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["kind_counts"].get("IDEMPOTENCY_TOKEN_CANDIDATE", 0) >= 1


# ── 6. receipt shape ──

def test_legacy_fallback_receipt_shape() -> None:
    asset = _binding_asset()
    ir = build_behavior_ir_from_knowledge_asset(asset)
    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["frame_ledger_present"] is True
    assert receipt["used"] is True
    assert receipt["reason_codes"] == ["LEGACY_FALLBACK_USED"]
    assert set(receipt["kind_counts"]) <= {
        "ACTION_PHRASE_BINDING_SKIPPED_WHEN_GROUNDED",
        "ACTION_PHRASE_BINDING_NO_FRAME_LEDGER",
        "ACTION_PHRASE_BINDING_NO_FRAME_FOR_RULE",
        "ACTION_PHRASE_BINDING_FALLBACK_WHEN_UNGROUNDED",
        "FIELD_OWNERSHIP_UNCONFIRMED_SKIPPED",
        "CJK_FIELD_TOKEN_EXTRACTION",
        "CAUSAL_DELTA_TOKEN_EXTRACTION",
        "IDEMPOTENCY_TOKEN_CANDIDATE",
        "UMBRELLA_PATTERN_OVERRIDDEN_BY_GROUNDED_FRAME",
        "UMBRELLA_PATTERN_FALLBACK",
    }
    assert receipt["contract"] == {
        "gate": "frame_confirmation",
        "frame_grounded_wins": True,
        "legacy_fallback_observable": True,
        "no_ledger_behavior_unchanged": True,
    }
    # Receipt content is deterministic JSON (stable for snapshots/audits).
    json.dumps(receipt, ensure_ascii=False, sort_keys=True)


def test_receipt_absent_for_empty_build() -> None:
    # The no-source early return has no legacy parse products and no receipt.
    ir = build_behavior_ir_from_knowledge_asset(None)
    assert "legacy_semantic_fallback_receipt" not in ir
