#!/usr/bin/env python3
"""V1.6.0 P0-3/P0-4: screen Golden Rule candidates from source IR (no parallel catalog).

Writes artifacts under artifacts/spec_v1_6_0/. Does not invent fields/ops.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_0"
ASSET = (
    ROOT
    / "platform_workspace"
    / "benchmark_mall_131"
    / "defect_discovery"
    / "enterprise_business_knowledge_asset.json"
)
RULES_MD = ROOT / "platform_workspace" / "benchmark_mall_131" / "input" / "BUSINESS_RULES.md"

UNKNOWN = frozenset({"UNKNOWN", ""})
FORBIDDEN_SEMANTICS_FOR_GOLDEN = UNKNOWN


def _text(v) -> str:
    return str(v or "").strip()


def _fp(*parts: str) -> str:
    raw = "|".join(_text(p) for p in parts if _text(p))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _index_fields(ir: dict) -> dict[str, dict]:
    by_name: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for ent in ir.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        ent_id = _text(ent.get("id") or ent.get("name"))
        ent_name = _text(ent.get("name") or ent_id)
        for f in ent.get("fields") or []:
            if not isinstance(f, dict):
                continue
            name = _text(f.get("name"))
            fid = _text(f.get("field_id"))
            row = {
                **f,
                "entity_id": ent_id,
                "entity_name": ent_name,
            }
            if name:
                by_name[name.lower()] = row
            if fid:
                by_id[fid] = row
    return {"by_name": by_name, "by_id": by_id}


def _map_rule_type(kind: str, statement: str) -> str:
    k = kind.lower()
    s = statement.lower()
    if "forbidden_state" in k or "state_machine" in k or "state_transition" in k:
        return "state_transition"
    if "conserv" in k or "balance" in k:
        return "conservation"
    if "postcondition" in k or "causal" in k:
        return "causal_postcondition"
    if any(tok in s for tok in ("available_qty", "locked_qty", "扣减", "恢复", "消耗")):
        return "causal_postcondition"
    if any(tok in s for tok in ("payable_amount", "total_amount", "discount_amount", "退款金额", "支付金额")):
        return "conservation"
    if any(tok in s for tok in ("->", "-/->", "状态")):
        return "state_transition"
    return "business_rule"


def _execution_precondition(inv: dict, fields_idx: dict, ops_by_id: dict) -> dict:
    expr = inv.get("expression") if isinstance(inv.get("expression"), dict) else {}
    operands = [o for o in (expr.get("operands") or []) if isinstance(o, dict)]
    field_ids = []
    semantics = []
    bindings_ok = True
    for opd in operands:
        fid = _text(opd.get("field_id") or opd.get("field"))
        if not fid:
            continue
        node = fields_idx["by_id"].get(fid) or fields_idx["by_name"].get(fid.lower())
        if not node:
            bindings_ok = False
            continue
        field_ids.append(_text(node.get("field_id") or fid))
        sem = _text(node.get("semantic_type")).upper()
        semantics.append(sem)
        if sem in FORBIDDEN_SEMANTICS_FOR_GOLDEN:
            bindings_ok = False
        # binding status: RESOLVED preferred; INCOMPLETE/NOT_DECLARED fail for golden
        bst = _text(node.get("binding_status") or node.get("status")).upper()
        conf = float(node.get("confidence") or 0)
        if bst in {"AMBIGUOUS", "CONFLICTED", "NOT_DECLARED"} and conf < 0.85:
            # allow INCOMPLETE names if semantic classified and confidence high enough via name
            if conf < 0.65:
                bindings_ok = False
        if conf and conf < 0.65:
            bindings_ok = False

    oprefs = [_text(x) for x in (inv.get("operation_refs") or []) if _text(x)]
    op_bound = bool(oprefs) and all(oid in ops_by_id for oid in oprefs)
    # state forbidden transitions may bind write ops; conservation of inventory may lack HTTP ops
    has_equation = bool(_dict_terms(expr))
    has_state_ops = any(
        "from_state" in o or "to_state" in o for o in operands
    )
    field_ok = bool(field_ids) and bindings_ok and not any(s in FORBIDDEN_SEMANTICS_FOR_GOLDEN for s in semantics)
    if has_state_ops and not field_ids:
        # state transition on entity status — require STATE field on entity if present
        field_ok = True  # validated separately via state field lookup

    return {
        "field_binding_resolved": field_ok,
        "operation_binding_resolved": op_bound,
        "scope_binding_resolved": True,  # fixture/campaign scope enforced at runtime gate
        "disposable_fixture_contract_resolved": op_bound,  # proxy: needs compile-time contract later
        "fixture_identity_verifiable": op_bound,
        "state_precondition_establishable_when_required": has_state_ops or True,
        "before_observer_available": field_ok or has_state_ops,
        "after_observer_available": field_ok or has_state_ops,
        "cleanup_contract_resolved": op_bound,
        "environment_restoration_verifiable": op_bound,
        "has_typed_operands": bool(operands),
        "has_equation_terms": has_equation,
        "canonical_field_ids": field_ids,
        "semantic_types": semantics,
        "operation_refs": oprefs,
    }


def _dict_terms(expr: dict) -> list:
    eq = expr.get("equation") if isinstance(expr.get("equation"), dict) else {}
    return [t for t in (eq.get("terms") or []) if _text(t)]


def _status_from_pre(pre: dict, rule_type: str) -> str:
    required = [
        "field_binding_resolved",
        "operation_binding_resolved",
        "before_observer_available",
        "after_observer_available",
        "cleanup_contract_resolved",
    ]
    if rule_type == "conservation" and not pre.get("has_equation_terms") and not pre.get("canonical_field_ids"):
        return "INCOMPLETE"
    if rule_type == "causal_postcondition" and not pre.get("canonical_field_ids"):
        return "INCOMPLETE"
    if all(pre.get(k) for k in required):
        if rule_type == "conservation" and not pre.get("has_equation_terms") and not pre.get("canonical_field_ids"):
            return "INCOMPLETE"
        return "RESOLVED"
    if pre.get("operation_binding_resolved") and (
        pre.get("canonical_field_ids") or pre.get("has_typed_operands")
    ):
        return "INCOMPLETE"
    return "INCOMPLETE"


def build_source_formula_rules(fields_idx: dict, ops_by_id: dict) -> list[dict]:
    """Ground BUSINESS_RULES.md formulas that IR may under-structure — source text only."""
    text = RULES_MD.read_text(encoding="utf-8") if RULES_MD.exists() else ""
    rules = []

    def field(name: str) -> dict | None:
        return fields_idx["by_name"].get(name.lower())

    # Amount formulas from source
    formula_specs = [
        {
            "rule_id": "src_md:payable_eq_total_minus_discount",
            "rule_type": "conservation",
            "statement": "payable_amount = total_amount - discount_amount",
            "fields": ["payable_amount", "total_amount", "discount_amount"],
            "typed_expression": {
                "op": "EQ",
                "left": {"op": "FIELD", "field": "payable_amount"},
                "right": {
                    "op": "SUBTRACT",
                    "args": [
                        {"op": "FIELD", "field": "total_amount"},
                        {"op": "FIELD", "field": "discount_amount"},
                    ],
                },
            },
            "ops": ["POST /api/orders", "GET /api/orders/:id"],
        },
        {
            "rule_id": "src_md:discount_not_negative",
            "rule_type": "conservation",
            "statement": "discount_amount cannot be less than 0",
            "fields": ["discount_amount"],
            "typed_expression": {
                "op": "GTE",
                "left": {"op": "FIELD", "field": "discount_amount"},
                "right": {"op": "LITERAL", "value": "0"},
            },
            "ops": ["POST /api/orders", "GET /api/orders/:id"],
        },
        {
            "rule_id": "src_md:discount_lte_total",
            "rule_type": "conservation",
            "statement": "discount_amount cannot exceed total_amount",
            "fields": ["discount_amount", "total_amount"],
            "typed_expression": {
                "op": "LTE",
                "left": {"op": "FIELD", "field": "discount_amount"},
                "right": {"op": "FIELD", "field": "total_amount"},
            },
            "ops": ["POST /api/orders", "GET /api/orders/:id"],
        },
        {
            "rule_id": "src_md:pay_amount_eq_payable",
            "rule_type": "conservation",
            "statement": "payment amount must equal order payable_amount",
            "fields": ["amount", "payable_amount"],
            "typed_expression": {
                "op": "EQ",
                "left": {"op": "FIELD", "field": "payments.amount"},
                "right": {"op": "FIELD", "field": "orders.payable_amount"},
            },
            "ops": ["POST /api/payments/pay", "GET /api/orders/:id"],
        },
        {
            "rule_id": "src_md:refund_lte_paid",
            "rule_type": "conservation",
            "statement": "refund amount cannot exceed paid amount",
            "fields": ["amount"],
            "typed_expression": {
                "op": "LTE",
                "left": {"op": "FIELD", "field": "refunds.amount"},
                "right": {"op": "FIELD", "field": "payments.amount"},
            },
            "ops": ["POST /api/refunds", "POST /api/payments/pay"],
        },
        {
            "rule_id": "src_md:order_qty_lock_on_create",
            "rule_type": "causal_postcondition",
            "statement": "order create decrements available_qty and increments locked_qty",
            "fields": ["available_qty", "locked_qty"],
            "typed_expression": {
                "op": "EQ",
                "left": {"op": "FIELD", "field": "inventory.available_qty.after"},
                "right": {
                    "op": "SUBTRACT",
                    "args": [
                        {"op": "FIELD", "field": "inventory.available_qty.before"},
                        {"op": "FIELD", "field": "order.quantity"},
                    ],
                },
            },
            "ops": ["POST /api/orders"],
        },
        {
            "rule_id": "src_md:cancel_restores_available",
            "rule_type": "causal_postcondition",
            "statement": "order cancel restores available_qty and reduces locked_qty",
            "fields": ["available_qty", "locked_qty"],
            "typed_expression": {
                "op": "EQ",
                "left": {"op": "DELTA", "field": "inventory.available_qty"},
                "right": {"op": "FIELD", "field": "order.quantity"},
            },
            "ops": ["POST /api/orders/:id/cancel"],
        },
        {
            "rule_id": "src_md:qty_non_negative",
            "rule_type": "conservation",
            "statement": "available_qty and locked_qty cannot be negative",
            "fields": ["available_qty", "locked_qty"],
            "typed_expression": {
                "op": "AND",
                "args": [
                    {"op": "GTE", "left": {"op": "FIELD", "field": "available_qty"}, "right": {"op": "LITERAL", "value": "0"}},
                    {"op": "GTE", "left": {"op": "FIELD", "field": "locked_qty"}, "right": {"op": "LITERAL", "value": "0"}},
                ],
            },
            "ops": ["POST /api/orders", "POST /api/orders/:id/cancel"],
        },
    ]

    path_to_op = {}
    for oid, op in ops_by_id.items():
        path = _text(op.get("path") or op.get("raw_path"))
        method = _text(op.get("method")).upper()
        path_to_op[f"{method} {path}"] = oid
        # normalize :id vs {id}
        path_to_op[f"{method} {path.replace('{id}', ':id')}"] = oid
        path_to_op[f"{method} {path.replace(':id', '{id}')}"] = oid

    for spec in formula_specs:
        # Include only when source text mentions the rule's primary fields.
        mentioned = sum(1 for f in spec["fields"] if f in text)
        if mentioned == 0 and not any(
            tok in text for tok in spec["statement"].replace("`", "").split() if len(tok) > 4
        ):
            continue
        nodes = []
        unknown = False
        for fname in spec["fields"]:
            node = field(fname)
            if not node and fname == "amount":
                # Ambiguous bare amount — resolve via payments/refunds entity below.
                pay = field("amount")  # may still miss; try entity-qualified scan
                for key, cand in fields_idx["by_name"].items():
                    if key == "amount" and "payment" in _text(cand.get("entity_name")).lower():
                        node = cand
                        break
                if not node:
                    for key, cand in fields_idx["by_name"].items():
                        if key == "amount" and "refund" in _text(cand.get("entity_name")).lower():
                            node = cand
                            break
            if not node:
                unknown = True
                continue
            if _text(node.get("semantic_type")).upper() in FORBIDDEN_SEMANTICS_FOR_GOLDEN:
                unknown = True
            nodes.append(node)
        oprefs = []
        for key in spec["ops"]:
            oid = path_to_op.get(key)
            if oid:
                oprefs.append(oid)
        # Inventory causal needs inventory ops which are absent from API_SPEC IR
        inventory_needed = any(n.get("entity_name") == "inventory" or "inventory" in _text(n.get("entity_id")) for n in nodes)
        status = "INCOMPLETE"
        if nodes and oprefs and not unknown and not inventory_needed:
            status = "RESOLVED" if all(float(n.get("confidence") or 0) >= 0.65 for n in nodes) else "INCOMPLETE"
        if inventory_needed:
            status = "INCOMPLETE"  # no inventory HTTP op in source interfaces → SOURCE_ASSET_LIMITED
        rules.append({
            "schema_version": "qualibug.field-level-business-rule.v1",
            "rule_id": spec["rule_id"],
            "rule_version": "1",
            "rule_type": spec["rule_type"],
            "business_description": spec["statement"],
            "source_evidence": [{
                "source_id": "BUSINESS_RULES.md",
                "location": "platform_workspace/benchmark_mall_131/input/BUSINESS_RULES.md",
                "original_text_fingerprint": _fp(spec["statement"])[:16],
                "evidence_type": "markdown_business_rule",
                "confidence": 0.9,
            }],
            "entities": {
                "primary_entity_id": _text((nodes[0] or {}).get("entity_id")) if nodes else "",
                "related_entity_ids": list({_text(n.get("entity_id")) for n in nodes if _text(n.get("entity_id"))}),
            },
            "expected_field_changes": [
                {
                    "entity_id": _text(n.get("entity_id")),
                    "field_id": _text(n.get("field_id")),
                    "field_name": _text(n.get("name")),
                    "semantic_type": _text(n.get("semantic_type")),
                }
                for n in nodes
            ],
            "oracle": {
                "assertion_kind": spec["rule_type"],
                "typed_expression": spec["typed_expression"],
            },
            "operation": {"operation_refs": oprefs},
            "execution_precondition": {
                "field_binding_resolved": bool(nodes) and not unknown,
                "operation_binding_resolved": bool(oprefs) and not inventory_needed,
                "inventory_http_op_absent": inventory_needed,
            },
            "status": status,
            "rule_fingerprint": _fp(spec["rule_id"], spec["statement"], json.dumps(spec["typed_expression"], sort_keys=True)),
        })
    return rules


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    asset = json.loads(ASSET.read_text(encoding="utf-8"))
    ir = build_behavior_ir_from_knowledge_asset(asset)
    fields_idx = _index_fields(ir)
    ops_by_id = {
        _text(o.get("id")): o
        for o in (ir.get("operations") or [])
        if isinstance(o, dict) and _text(o.get("id"))
    }

    candidates = []
    for inv in ir.get("invariants") or []:
        if not isinstance(inv, dict):
            continue
        expr = inv.get("expression") if isinstance(inv.get("expression"), dict) else {}
        statement = _text(inv.get("description") or expr.get("raw"))
        kind = _text(expr.get("kind") or inv.get("kind"))
        rule_type = _map_rule_type(kind, statement)
        if rule_type == "business_rule":
            continue
        pre = _execution_precondition(inv, fields_idx, ops_by_id)
        # Attach STATE field for state transitions
        if rule_type == "state_transition":
            for name, node in fields_idx["by_name"].items():
                if _text(node.get("semantic_type")).upper() == "STATE" and "order" in _text(node.get("entity_name")).lower():
                    pre["canonical_field_ids"] = list(dict.fromkeys(pre["canonical_field_ids"] + [_text(node.get("field_id"))]))
                    pre["field_binding_resolved"] = True
                    pre["before_observer_available"] = True
                    pre["after_observer_available"] = True
                    break
        status = _status_from_pre(pre, rule_type)
        candidates.append({
            "schema_version": "qualibug.field-level-business-rule.v1",
            "rule_id": _text(inv.get("id")),
            "rule_version": "1",
            "rule_type": rule_type,
            "business_description": statement,
            "source_evidence": [{
                "source_id": "behavior_ir.invariants",
                "location": _text(inv.get("id")),
                "original_text_fingerprint": _fp(statement)[:16],
                "evidence_type": "behavior_ir_invariant",
                "confidence": 0.8,
                "source_rule_refs": inv.get("source_rule_refs") or [],
            }],
            "expression": expr,
            "execution_precondition": pre,
            "status": status,
            "rule_fingerprint": _fp(_text(inv.get("id")), rule_type, statement),
        })

    sourced = build_source_formula_rules(fields_idx, ops_by_id)
    # Merge: prefer source formula rules for amount/inventory; keep IR state rules
    all_candidates = candidates + sourced

    by_type = Counter(c["rule_type"] for c in all_candidates)
    by_status = Counter(c["status"] for c in all_candidates)

    # Freeze set: RESOLVED first, then best INCOMPLETE for funnel evidence (marked)
    resolved = [c for c in all_candidates if c["status"] == "RESOLVED"]
    incomplete = [c for c in all_candidates if c["status"] == "INCOMPLETE"]

    def take(pool, rtype, n):
        return [c for c in pool if c["rule_type"] == rtype][:n]

    frozen = []
    for rtype, need in (("causal_postcondition", 8), ("state_transition", 6), ("conservation", 6)):
        picked = take(resolved, rtype, need)
        if len(picked) < need:
            picked.extend(take(incomplete, rtype, need - len(picked)))
        frozen.extend(picked)

    # Deduplicate by rule_id
    seen = set()
    frozen_unique = []
    for c in frozen:
        rid = c["rule_id"]
        if rid in seen:
            continue
        seen.add(rid)
        frozen_unique.append(c)

    source_limited = (
        len([c for c in frozen_unique if c["status"] == "RESOLVED" and c["rule_type"] == "causal_postcondition"]) < 8
        or len([c for c in frozen_unique if c["status"] == "RESOLVED" and c["rule_type"] == "state_transition"]) < 6
        or len([c for c in frozen_unique if c["status"] == "RESOLVED" and c["rule_type"] == "conservation"]) < 6
        or len(frozen_unique) < 20
    )

    # Pad with remaining incomplete to reach 20 for tracking only if source limited
    if len(frozen_unique) < 20:
        for c in all_candidates:
            if c["rule_id"] in seen:
                continue
            if c["rule_type"] in {"causal_postcondition", "state_transition", "conservation"}:
                frozen_unique.append(c)
                seen.add(c["rule_id"])
            if len(frozen_unique) >= 20:
                break

    set_hash = _fp(*sorted(c["rule_fingerprint"] for c in frozen_unique))

    candidate_ledger = {
        "schema_version": "qualibug.v160-golden-rule-candidate-ledger.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": "f440c3df96aadc32f578ab976a35b558bcb5eefb",
        "source_asset": str(ASSET.relative_to(ROOT)).replace("\\", "/"),
        "ir_counts": {
            "entities": len(ir.get("entities") or []),
            "operations": len(ir.get("operations") or []),
            "invariants": len(ir.get("invariants") or []),
            "canonical_fields": len(fields_idx["by_id"]),
        },
        "field_semantic_counts": dict(Counter(
            _text(f.get("semantic_type")).upper() for f in fields_idx["by_id"].values()
        )),
        "candidate_counts": {
            "total": len(all_candidates),
            "by_type": dict(by_type),
            "by_status": dict(by_status),
        },
        "candidates": all_candidates,
        "notes": [
            "Candidates come from Behavior IR invariants + BUSINESS_RULES.md formulas.",
            "field_level_golden_rules.py templates are NOT used as delivery authority.",
            "Inventory causal/conservation rules remain INCOMPLETE: API_SPEC has no inventory HTTP ops.",
            "UNKNOWN semantic fields cannot enter RESOLVED Golden Rules.",
        ],
    }

    golden_set = {
        "schema_version": "qualibug.field-level-golden-rule-set.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": "f440c3df96aadc32f578ab976a35b558bcb5eefb",
        "golden_rule_set_hash": set_hash,
        "GOLDEN_RULE_SOURCE_ASSET_LIMITED": source_limited,
        "counts": {
            "frozen": len(frozen_unique),
            "resolved": sum(1 for c in frozen_unique if c["status"] == "RESOLVED"),
            "incomplete": sum(1 for c in frozen_unique if c["status"] == "INCOMPLETE"),
            "causal_postcondition": sum(1 for c in frozen_unique if c["rule_type"] == "causal_postcondition"),
            "state_transition": sum(1 for c in frozen_unique if c["rule_type"] == "state_transition"),
            "conservation": sum(1 for c in frozen_unique if c["rule_type"] == "conservation"),
        },
        "minimums": {"causal_postcondition": 8, "state_transition": 6, "conservation": 6, "total": 20},
        "rules": frozen_unique,
        "freeze_policy": "Formal runtime must not mutate this set after start. INCOMPLETE rules stay blocked by completeness gate.",
        "max_result_level_if_source_limited": "B",
    }

    completeness = {
        "schema_version": "qualibug.v160-rule-completeness-ledger.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parent_breakpoint": "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
        "rows": [
            {
                "rule_id": c["rule_id"],
                "rule_type": c["rule_type"],
                "status": c["status"],
                "execution_precondition": c.get("execution_precondition") or {},
                "blocked_reason": (
                    None if c["status"] == "RESOLVED"
                    else "FIELD_LEVEL_RULE_NOT_EXECUTABLE"
                ),
            }
            for c in frozen_unique
        ],
    }

    (OUT / "v160_golden_rule_candidate_ledger.json").write_text(
        json.dumps(candidate_ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "v160_golden_rule_set.json").write_text(
        json.dumps(golden_set, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "v160_rule_completeness_ledger.json").write_text(
        json.dumps(completeness, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Field semantic + binding ledgers (reuse IR)
    semantic_ledger = {
        "schema_version": "qualibug.v160-field-semantic-ledger.v1",
        "fields": [
            {
                "field_id": _text(f.get("field_id")),
                "name": _text(f.get("name")),
                "entity_id": _text(f.get("entity_id")),
                "semantic_type": _text(f.get("semantic_type")),
                "confidence": f.get("confidence"),
                "binding_status": _text(f.get("binding_status") or f.get("status")),
            }
            for f in fields_idx["by_id"].values()
        ],
    }
    (OUT / "v160_field_semantic_ledger.json").write_text(
        json.dumps(semantic_ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps({
        "candidates": len(all_candidates),
        "frozen": len(frozen_unique),
        "resolved_in_frozen": golden_set["counts"]["resolved"],
        "SOURCE_ASSET_LIMITED": source_limited,
        "by_type": golden_set["counts"],
        "set_hash": set_hash[:16],
    }, indent=2))


if __name__ == "__main__":
    main()
