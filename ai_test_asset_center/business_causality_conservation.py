from __future__ import annotations

"""Phase51: business causality and conservation counterexample engine.

Enterprise defects often hide behind individually successful APIs.  An order can
be marked paid while no payment record exists, a refund can appear twice, a
ledger row can reference a deleted order, or the amount on a business document
can stop matching its line items after a release.  This module turns those
relationships into read-only, executable Oracles.

The engine derives contracts from OpenAPI, PRD semantics and optional enterprise
configuration.  It validates four durable business properties:

* causality coverage: a qualifying business state must produce the required
  dependent record(s), e.g. paid order -> payment/ledger/invoice;
* idempotent side effects: a one-time business action must not create duplicate
  dependent records;
* referential causality: every dependent record must point to a real source
  entity within a complete bounded snapshot;
* conservation: document totals must agree with component fields and, where
  configured/inferred, source amounts must equal the sum of linked effects;
* inventory reservation conservation: explicitly mapped stock snapshots,
  active reservations and available quantities must reconcile per stock key.

All live execution is GET-only.  Any mutation/race validation is emitted only
as a sandbox-required candidate plan.  Persisted evidence stores field names,
aggregates and hashed identities, never raw business rows or credentials.
"""

import argparse
import hashlib
import json
import re
import time
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from pathlib import Path
from typing import Any

from .business_invariant_mining import _infer_identity, _is_collection_read, _item_fields
from .business_outcome_validation import (
    _build_url,
    _http_get,
    _normal_token,
    _private_leak_check,
    _redact,
    _update_registry,
)
from .business_reconciliation import _extract_records, _fetch_source_pages, _numeric, _parse_json, _row_value
from .llm_reasoning import reason as _llm_reason
from .multisource_reasoning import _learning_bonus, ingest_confirmed_bug_feedback
from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    execution_safety_verdict,
    load_real_project_config,
)
from .universal_defect_mining import _operations


STATE_RE = re.compile(r"(?:^|[_\-.])(status|state|phase)(?:$|[_\-.])|状态|阶段", re.I)
AMOUNT_RE = re.compile(r"amount|total|price|cost|fee|tax|balance|revenue|gmv|金额|金额|总额|价格|费用|税|余额|收入|销售", re.I)
ID_RE = re.compile(r"(?:^|[_\-.])(id|uuid|guid|code|number|no|serial)(?:$|[_\-.])|编号|编码|单号", re.I)
ACTION_RE = re.compile(r"payment|pay|invoice|receipt|transaction|ledger|journal|refund|return|shipment|delivery|fulfillment|dispatch|approval|approve|inventory|reserve|付款|支付|发票|收据|流水|台账|退款|退货|发货|物流|履约|审批|库存|预占", re.I)
FORMULA_TOTALS = ("total_amount", "grand_total", "payable_amount", "order_amount", "total", "amount", "总金额", "应付金额", "合计", "总额")
FORMULA_POSITIVES = ("subtotal_amount", "subtotal", "item_amount", "base_amount", "tax_amount", "shipping_amount", "fee_amount", "商品金额", "小计", "税额", "运费", "服务费")
FORMULA_NEGATIVES = ("discount_amount", "discount", "coupon_amount", "promotion_amount", "优惠金额", "折扣金额", "优惠券金额")

# Phase65: accounting facts are intentionally opt-in. Field names alone are
# insufficient to infer a company's ledger semantics, so executable financial
# contracts are built only from explicit enterprise configuration.
#
# Phase70 applies the same rule to inventory: `reserved`, `available`, `SKU`
# and `warehouse` are not enough to infer a company's stock semantics.  The
# stock/reservation conservation Oracle only runs after the company explicitly
# supplies the stock key, quantity fields, active reservation states and both
# collection paths.

# Deliberately conservative semantics.  Ambiguous relationships are still
# covered by referential integrity; causality/amount requirements only run when
# resource names clearly express an enterprise side effect.
RELATION_HINTS: dict[str, dict[str, Any]] = {
    "payment": {"states": ["paid", "completed", "confirmed", "success", "已支付", "支付成功", "已完成"], "min": 1, "amount": "sum_equal"},
    "transaction": {"states": ["paid", "completed", "confirmed", "success", "已支付", "支付成功", "已完成"], "min": 1, "amount": "sum_equal"},
    "invoice": {"states": ["paid", "completed", "invoiced", "已支付", "已完成", "已开票"], "min": 1, "amount": "sum_equal"},
    "receipt": {"states": ["paid", "completed", "已支付", "已完成"], "min": 1, "amount": "sum_equal"},
    "refund": {"states": ["refunded", "returned", "退款成功", "已退款", "已退货"], "min": 1, "max": 1, "amount": "sum_equal"},
    "shipment": {"states": ["shipped", "delivered", "completed", "已发货", "已送达", "已完成"], "min": 1},
    "delivery": {"states": ["shipped", "delivered", "completed", "已发货", "已送达", "已完成"], "min": 1},
    "fulfillment": {"states": ["fulfilled", "shipped", "completed", "已履约", "已发货", "已完成"], "min": 1},
    "approval": {"states": ["approved", "completed", "已审批", "已通过", "已完成"], "min": 1},
    "ledger": {"states": ["paid", "completed", "refunded", "已支付", "已完成", "已退款"], "min": 1},
    "journal": {"states": ["paid", "completed", "refunded", "已支付", "已完成", "已退款"], "min": 1},
    "inventory": {"states": ["reserved", "paid", "completed", "已预占", "已支付", "已完成"], "min": 1},
    "reservation": {"states": ["reserved", "paid", "completed", "已预占", "已支付", "已完成"], "min": 1},
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _short(value: Any, size: int = 12) -> str:
    return _hash(value)[:size]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _canon(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value).strip()


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = (
        cfg.get("business_causality_conservation")
        or cfg.get("business_causality_reasoning")
        or cfg.get("business_side_effect_reasoning")
        or cfg.get("business_effect_oracles")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "business_causality_conservation",
        "workspace": workspace,
        "registry": workspace / "business_causality_conservation_evidence_registry.json",
    }


def _resource_key(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part and not part.startswith("{")]
    raw = parts[-1] if parts else "resource"
    value = _norm(raw)
    if value.endswith("ies") and len(value) > 4:
        return value[:-3] + "y"
    return value.rstrip("s") or "resource"


def _field_name(fields: dict[str, Any], desired: str | None) -> str | None:
    target = _norm(desired)
    if not target:
        return None
    for name in fields:
        if _norm(name) == target:
            return str(name)
    for name in fields:
        candidate = _norm(name)
        if target in candidate or candidate in target:
            return str(name)
    return None


def _field_value(row: dict[str, Any], field: str | None, mappings: dict[str, Any] | None = None) -> Any:
    if not isinstance(row, dict) or not field:
        return None
    value = _row_value(row, field, mappings or {})
    if value is not None:
        return value
    desired = _norm(field)
    for key, candidate in row.items():
        if _norm(key) == desired:
            return candidate
    return None


def _identity_fields(resource: str, fields: dict[str, dict[str, Any]], configured: Any = None) -> list[str]:
    raw = configured or []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        values = [str(item) for item in raw if str(item).strip()]
        if values:
            return values[:4]
    inferred = _infer_identity(resource, fields, {})
    return [str(inferred)] if inferred else []


def _identity(row: dict[str, Any], fields: list[str], mappings: dict[str, Any] | None = None) -> str | None:
    values = [_canon(_field_value(row, field, mappings)) for field in fields]
    if not values or any(not item for item in values):
        return None
    return "|".join(values)


def _normal_states(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(_norm(item) for item in values if _norm(item)))[:40]


def _configured_contracts(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("contracts") or section.get("side_effect_contracts") or section.get("causality_contracts") or []
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _catalog(openapi: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    catalog: list[dict[str, Any]] = []
    for operation in _operations(openapi):
        if not _is_collection_read(operation, components):
            continue
        path = str(operation.get("path") or "")
        fields = _item_fields(operation, components)
        if not fields:
            continue
        catalog.append({
            "path": path,
            "method": "GET",
            "parameters": operation.get("parameters") or [],
            "summary": operation.get("summary") or "",
            "resource": _resource_key(path),
            "fields": fields,
        })
    return catalog[:200]


def _find_collection(catalog: list[dict[str, Any]], path_or_resource: Any) -> dict[str, Any] | None:
    wanted = str(path_or_resource or "").rstrip("/")
    if not wanted:
        return None
    for row in catalog:
        if str(row.get("path") or "").rstrip("/") == wanted:
            return row
    token = _resource_key(wanted)
    candidates = [row for row in catalog if _norm(row.get("resource")) == _norm(token)]
    if not candidates:
        candidates = [row for row in catalog if _norm(token) in _norm(row.get("resource")) or _norm(row.get("resource")) in _norm(token)]
    return sorted(candidates, key=lambda row: len(str(row.get("path") or "")))[0] if candidates else None


def _foreign_key_for_parent(fields: dict[str, Any], parent_resource: str, configured: str | None = None) -> str | None:
    if configured:
        return _field_name(fields, configured) or str(configured)
    parent = _norm(parent_resource)
    exact = [f"{parent}_id", f"{parent}id", f"{parent}_no", f"{parent}no", f"{parent}_code", f"{parent}code"]
    for desired in exact:
        value = _field_name(fields, desired)
        if value:
            return value
    for field in fields:
        norm = _norm(field)
        if parent and parent in norm and (norm.endswith("id") or norm.endswith("no") or norm.endswith("code") or "编号" in str(field) or "单号" in str(field)):
            return str(field)
    return None


def _state_field(fields: dict[str, Any], configured: str | None = None) -> str | None:
    if configured:
        return _field_name(fields, configured) or str(configured)
    for name in fields:
        if STATE_RE.search(str(name)):
            return str(name)
    return None


def _amount_field(fields: dict[str, Any], configured: str | None = None) -> str | None:
    if configured:
        return _field_name(fields, configured) or str(configured)
    ranked: list[tuple[int, str]] = []
    for name in fields:
        norm = _norm(name)
        score = 0
        if norm in {_norm(item) for item in FORMULA_TOTALS}:
            score += 50
        if AMOUNT_RE.search(str(name)):
            score += 20
        if score:
            ranked.append((score, str(name)))
    return sorted(ranked, key=lambda row: (-row[0], row[1]))[0][1] if ranked else None

def _field_list(fields: dict[str, Any], configured: Any) -> list[str]:
    raw = configured
    if isinstance(raw, str):
        raw = [raw]
    values: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            resolved = _field_name(fields, str(item))
            if resolved and resolved not in values:
                values.append(resolved)
    return values[:4]


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '')
    text = re.sub(r'^[¥$€£]\s*', '', text)
    if not text:
        return None
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), 'f') if value != 0 else '0'



def _relation_semantics(resource: str) -> dict[str, Any] | None:
    token = _norm(resource)
    for key, value in RELATION_HINTS.items():
        if _norm(key) in token or token in _norm(key):
            return dict(value)
    return None


def _formula_fields(fields: dict[str, Any], configured: dict[str, Any]) -> tuple[str | None, list[str], list[str]]:
    target = _field_name(fields, str(configured.get("expected_field") or configured.get("total_field") or "")) if configured else None
    positives: list[str] = []
    negatives: list[str] = []
    if configured:
        raw_pos = configured.get("positive_fields") or configured.get("add_fields") or []
        raw_neg = configured.get("negative_fields") or configured.get("subtract_fields") or []
        if isinstance(raw_pos, str):
            raw_pos = [raw_pos]
        if isinstance(raw_neg, str):
            raw_neg = [raw_neg]
        positives = [(_field_name(fields, str(item)) or str(item)) for item in raw_pos if str(item).strip()]
        negatives = [(_field_name(fields, str(item)) or str(item)) for item in raw_neg if str(item).strip()]
        if target and positives:
            return target, positives[:10], negatives[:10]
    for candidate in FORMULA_TOTALS:
        target = _field_name(fields, candidate)
        if target:
            break
    positives = [value for candidate in FORMULA_POSITIVES if (value := _field_name(fields, candidate)) and value != target]
    negatives = [value for candidate in FORMULA_NEGATIVES if (value := _field_name(fields, candidate)) and value != target]
    # Do not infer formulas from only a single arbitrary component.
    return target, list(dict.fromkeys(positives))[:8], list(dict.fromkeys(negatives))[:8]


def _configured_for_path(rows: list[dict[str, Any]], path: str) -> dict[str, Any]:
    target = str(path or "").rstrip("/")
    for row in rows:
        candidate = str(row.get("source_path") or row.get("path") or row.get("collection_path") or "").rstrip("/")
        if candidate and candidate == target:
            return row
    return {}


def _contract_from_config(row: dict[str, Any], catalog: list[dict[str, Any]], number: int) -> dict[str, Any] | None:
    raw_kind = str(row.get("contract_kind") or row.get("type") or "side_effect").lower()
    aliases = {
        "double_entry": "journal_balance",
        "double_entry_balance": "journal_balance",
        "journal_entry_balance": "journal_balance",
        "ledger_balance": "journal_balance",
        "rollforward": "period_rollforward",
        "trial_balance_rollforward": "period_rollforward",
        "period_balance_rollforward": "period_rollforward",
        "stock_reservation_balance": "inventory_reservation_balance",
        "inventory_available_balance": "inventory_reservation_balance",
        "inventory_stock_balance": "inventory_reservation_balance",
        "inventory_reservation_conservation": "inventory_reservation_balance",
    }
    kind = aliases.get(raw_kind, raw_kind)
    single_source_kinds = {"record_conservation", "journal_balance", "period_rollforward"}
    source = _find_collection(catalog, row.get("source_path") or row.get("path") or row.get("source_resource"))
    if not source:
        return None
    dependent = _find_collection(catalog, row.get("dependent_path") or row.get("effect_path") or row.get("dependent_resource")) if kind not in single_source_kinds else None
    if kind not in single_source_kinds and not dependent:
        return None
    source_fields = source.get("fields") or {}
    dependent_fields = (dependent or {}).get("fields") or {}
    identities = _identity_fields(str(source.get("resource") or ""), source_fields, row.get("source_identity_fields") or row.get("identity_fields"))
    # Accounting field semantics are enterprise-specific.  Do not infer a
    # publishable financial contract from field names; every grouping and
    # amount field must be supplied explicitly by the configured contract.
    journal_group_fields = _field_list(
        source_fields,
        row.get("journal_group_fields") or row.get("entry_identity_fields") or row.get("voucher_fields"),
    )
    account_fields = _field_list(source_fields, row.get("account_fields") or row.get("account_field"))
    inventory_identity_fields = _field_list(
        source_fields,
        row.get("inventory_identity_fields") or row.get("stock_identity_fields") or row.get("source_identity_fields") or row.get("identity_fields"),
    )
    reservation_identity_fields = _field_list(
        dependent_fields,
        row.get("reservation_identity_fields") or row.get("reservation_stock_fields") or row.get("dependent_identity_fields"),
    )
    if kind == "journal_balance" and journal_group_fields:
        identities = journal_group_fields
    if kind == "period_rollforward" and account_fields:
        identities = account_fields
    if kind == "inventory_reservation_balance" and inventory_identity_fields:
        identities = inventory_identity_fields
    fk = _foreign_key_for_parent(dependent_fields, str(source.get("resource") or ""), row.get("dependent_foreign_key")) if dependent else None
    period_sequence = row.get("period_sequence") or row.get("periods") or []
    if isinstance(period_sequence, str):
        period_sequence = [period_sequence]
    return {
        "contract_id": f"BCC_CONTRACT_{number:04d}",
        "contract_kind": kind,
        "resource": str(row.get("resource") or source.get("resource") or "resource"),
        "source": {key: source.get(key) for key in ("path", "method", "parameters", "summary", "resource")},
        "dependent": {key: (dependent or {}).get(key) for key in ("path", "method", "parameters", "summary", "resource")} if dependent else None,
        "source_query": dict(row.get("source_query") or row.get("sample_query") or {}),
        "dependent_query": dict(row.get("dependent_query") or {}),
        "pagination": {"source": dict(row.get("source_pagination") or row.get("pagination") or {}), "dependent": dict(row.get("dependent_pagination") or row.get("pagination") or {})},
        "source_identity_fields": identities,
        "dependent_foreign_key": fk,
        "state_field": _state_field(source_fields, row.get("state_field")),
        "required_states": _normal_states(row.get("required_states") or row.get("states") or row.get("when_states")),
        "min_count": max(0, min(int(row.get("min_count") if row.get("min_count") is not None else 1), 100)),
        "max_count": (max(0, min(int(row.get("max_count")), 100)) if row.get("max_count") is not None else None),
        "source_amount_field": _amount_field(source_fields, row.get("source_amount_field") or row.get("amount_field")),
        "dependent_amount_field": _amount_field(dependent_fields, row.get("dependent_amount_field") or row.get("effect_amount_field")) if dependent else None,
        "amount_relation": str(row.get("amount_relation") or row.get("conservation_relation") or ("sum_equal" if kind == "side_effect" else "")).lower(),
        "tolerance": abs(float(row.get("tolerance") or 0.01)),
        "formula": {"expected_field": row.get("expected_field") or row.get("total_field"), "positive_fields": row.get("positive_fields") or row.get("add_fields") or [], "negative_fields": row.get("negative_fields") or row.get("subtract_fields") or []},
        "field_mappings": dict(row.get("field_mappings") or {}),
        "journal_group_fields": journal_group_fields,
        "journal_debit_field": _field_name(source_fields, str(row.get("debit_field") or row.get("debit_amount_field") or "")),
        "journal_credit_field": _field_name(source_fields, str(row.get("credit_field") or row.get("credit_amount_field") or "")),
        "currency_field": _field_name(source_fields, str(row.get("currency_field") or "")),
        "account_fields": account_fields,
        "period_field": _field_name(source_fields, str(row.get("period_field") or "")),
        "opening_balance_field": _field_name(source_fields, str(row.get("opening_balance_field") or "")),
        "closing_balance_field": _field_name(source_fields, str(row.get("closing_balance_field") or "")),
        "period_sequence": [str(item).strip() for item in period_sequence if str(item).strip()][:60],
        "movement_sign": str(row.get("movement_sign") or "debit_minus_credit").lower(),
        "verify_movement_formula": bool(row.get("verify_movement_formula", True)),
        "inventory_identity_fields": inventory_identity_fields,
        "reservation_identity_fields": reservation_identity_fields,
        "inventory_on_hand_field": _field_name(source_fields, str(row.get("on_hand_field") or row.get("stock_on_hand_field") or "")),
        "inventory_reserved_field": _field_name(source_fields, str(row.get("reserved_field") or row.get("reserved_quantity_field") or row.get("stock_reserved_field") or "")),
        "inventory_available_field": _field_name(source_fields, str(row.get("available_field") or row.get("available_quantity_field") or row.get("stock_available_field") or "")),
        "reservation_quantity_field": _field_name(dependent_fields, str(row.get("reservation_quantity_field") or row.get("quantity_field") or "")) if dependent else None,
        "reservation_status_field": _state_field(dependent_fields, str(row.get("reservation_status_field") or row.get("status_field") or "")) if dependent else None,
        "active_reservation_states": _normal_states(row.get("active_reservation_states") or row.get("reservation_active_states") or []),
        "allow_negative_available": bool(row.get("allow_negative_available", False)),
        "execution_policy": "safe_read_only",
        "discovery": "enterprise_config",
        "source_evidence": ["enterprise_config", "openapi"],
    }


def _auto_contracts(catalog: list[dict[str, Any]], configured: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    configured_source_paths = {str(row.get("source_path") or row.get("path") or "").rstrip("/") for row in configured}
    for parent in catalog:
        fields = parent.get("fields") or {}
        source_cfg = _configured_for_path(configured, str(parent.get("path") or ""))
        identity_fields = _identity_fields(str(parent.get("resource") or ""), fields, source_cfg.get("source_identity_fields") or source_cfg.get("identity_fields"))
        total, positives, negatives = _formula_fields(fields, source_cfg)
        if total and positives:
            contracts.append({
                "contract_id": f"BCC_CONTRACT_{len(contracts)+1:04d}",
                "contract_kind": "record_conservation",
                "resource": str(parent.get("resource") or "resource"),
                "source": {key: parent.get(key) for key in ("path", "method", "parameters", "summary", "resource")},
                "dependent": None,
                "source_query": dict(source_cfg.get("source_query") or source_cfg.get("sample_query") or {}),
                "dependent_query": {},
                "pagination": {"source": dict(source_cfg.get("source_pagination") or source_cfg.get("pagination") or {}), "dependent": {}},
                "source_identity_fields": identity_fields,
                "dependent_foreign_key": None,
                "state_field": None,
                "required_states": [],
                "min_count": 0,
                "max_count": None,
                "source_amount_field": total,
                "dependent_amount_field": None,
                "amount_relation": "formula",
                "tolerance": abs(float(source_cfg.get("tolerance") or 0.01)),
                "formula": {"expected_field": total, "positive_fields": positives, "negative_fields": negatives},
                "field_mappings": dict(source_cfg.get("field_mappings") or {}),
                "execution_policy": "safe_read_only",
                "discovery": "openapi_inferred_formula",
                "source_evidence": ["openapi"],
            })
        if not identity_fields:
            continue
        for child in catalog:
            if child is parent:
                continue
            child_fields = child.get("fields") or {}
            fk = _foreign_key_for_parent(child_fields, str(parent.get("resource") or ""))
            if not fk:
                continue
            relation = _relation_semantics(str(child.get("resource") or ""))
            base = {
                "contract_id": f"BCC_CONTRACT_{len(contracts)+1:04d}",
                "contract_kind": "referential_integrity",
                "resource": f"{parent.get('resource')}->{child.get('resource')}",
                "source": {key: parent.get(key) for key in ("path", "method", "parameters", "summary", "resource")},
                "dependent": {key: child.get(key) for key in ("path", "method", "parameters", "summary", "resource")},
                "source_query": {},
                "dependent_query": {},
                "pagination": {"source": {}, "dependent": {}},
                "source_identity_fields": identity_fields,
                "dependent_foreign_key": fk,
                "state_field": None,
                "required_states": [],
                "min_count": 0,
                "max_count": None,
                "source_amount_field": None,
                "dependent_amount_field": None,
                "amount_relation": "",
                "tolerance": 0.01,
                "formula": {},
                "field_mappings": {},
                "execution_policy": "safe_read_only",
                "discovery": "openapi_foreign_key_inferred",
                "source_evidence": ["openapi"],
            }
            contracts.append(base)
            if not relation:
                continue
            state_field = _state_field(fields)
            if not state_field:
                continue
            amount_left = _amount_field(fields)
            amount_right = _amount_field(child_fields)
            contracts.append({
                **base,
                "contract_id": f"BCC_CONTRACT_{len(contracts)+1:04d}",
                "contract_kind": "side_effect",
                "state_field": state_field,
                "required_states": _normal_states(relation.get("states")),
                "min_count": int(relation.get("min") or 1),
                "max_count": relation.get("max"),
                "source_amount_field": amount_left,
                "dependent_amount_field": amount_right,
                "amount_relation": str(relation.get("amount") or "") if amount_left and amount_right else "",
                "discovery": "openapi_semantic_causality_inferred",
                "source_evidence": ["openapi", "resource_semantics"],
            })
    # Let users know why configured causal expectations cannot execute.
    for row in configured:
        path = str(row.get("source_path") or row.get("path") or "").rstrip("/")
        dep = str(row.get("dependent_path") or row.get("effect_path") or "").rstrip("/")
        if path and (not _find_collection(catalog, path) or (dep and not _find_collection(catalog, dep))):
            candidates.append({"candidate_id": f"BCC_GAP_{len(candidates)+1:04d}", "risk_type": "business_causality_contract_gap", "severity": "P2", "title": "业务副作用契约无法映射到可读取集合", "detail": "补充 source_path/dependent_path 的 OpenAPI GET 响应 schema，或修正 business_causality_conservation.contracts 配置。"})
    return contracts[:300], candidates[:100]


def build_business_causality_contracts(openapi: dict[str, Any], cfg: dict[str, Any], prd_text: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    section = _section(cfg)
    catalog = _catalog(openapi, cfg)
    configured = _configured_contracts(section)
    contracts: list[dict[str, Any]] = []
    for row in configured:
        candidate = _contract_from_config(row, catalog, len(contracts)+1)
        if candidate:
            contracts.append(candidate)
    auto, candidates = _auto_contracts(catalog, configured)
    configured_keys = {(str((item.get("source") or {}).get("path") or ""), str((item.get("dependent") or {}).get("path") or ""), str(item.get("contract_kind") or "")) for item in contracts}
    for item in auto:
        key = (str((item.get("source") or {}).get("path")), str((item.get("dependent") or {}).get("path")), str(item.get("contract_kind")))
        if key not in configured_keys:
            item["contract_id"] = f"BCC_CONTRACT_{len(contracts)+1:04d}"
            contracts.append(item)
            configured_keys.add(key)
    if re.search(r"支付|付款|退款|库存|扣减|台账|审批|发票|同步|通知|payment|refund|ledger|approval|inventory|side effect|conservation", prd_text or "", re.I) and not contracts:
        candidates.append({"candidate_id": "BCC_PRD_UNMAPPED", "risk_type": "business_causality_contract_gap", "severity": "P2", "title": "PRD 包含业务副作用/守恒要求，但未发现可读取的关联集合", "detail": "为主实体和支付、库存、审批、台账等副作用集合补充 GET 响应 schema，或在 business_causality_conservation.contracts 中显式配置关系。"})
    single_source_kinds = {"record_conservation", "journal_balance", "period_rollforward"}
    for contract in contracts:
        kind = str(contract.get("contract_kind") or "")
        if kind == "inventory_reservation_balance":
            missing: list[str] = []
            if not contract.get("inventory_identity_fields"):
                missing.append("inventory_identity_fields")
            if not contract.get("reservation_identity_fields"):
                missing.append("reservation_identity_fields")
            for field in ("inventory_on_hand_field", "inventory_reserved_field", "inventory_available_field", "reservation_quantity_field", "reservation_status_field"):
                if not contract.get(field):
                    missing.append(field)
            if not contract.get("active_reservation_states"):
                missing.append("active_reservation_states")
            if missing:
                candidates.append({"candidate_id": f"{contract['contract_id']}_INVENTORY_FIELDS", "risk_type": "inventory_reservation_contract_gap", "severity": "P1", "title": f"{contract.get('resource')} 缺少库存预占守恒映射", "detail": "显式配置库存键、预占键、在库/预占/可用字段、预占数量/状态字段及 active_reservation_states；库存语义不得仅凭字段名猜测。", "missing": missing})
            continue
        if kind not in single_source_kinds and not contract.get("source_identity_fields"):
            candidates.append({"candidate_id": f"{contract['contract_id']}_NO_ID", "risk_type": "business_causality_contract_gap", "severity": "P2", "title": f"{contract.get('resource')} 缺少稳定主键，无法验证副作用因果链", "detail": "配置 source_identity_fields，例如订单号、工单号、审批单号。"})
        if kind not in single_source_kinds and not contract.get("dependent_foreign_key"):
            candidates.append({"candidate_id": f"{contract['contract_id']}_NO_FK", "risk_type": "business_causality_contract_gap", "severity": "P2", "title": f"{contract.get('resource')} 缺少可追溯外键，无法验证副作用归属", "detail": "在 dependent_foreign_key 中配置关联主单的字段，例如 order_id、invoice_id。"})
        if kind == "journal_balance" and (not contract.get("journal_group_fields") or not contract.get("journal_debit_field") or not contract.get("journal_credit_field")):
            candidates.append({"candidate_id": f"{contract['contract_id']}_JOURNAL_FIELDS", "risk_type": "financial_accounting_contract_gap", "severity": "P1", "title": f"{contract.get('resource')} 缺少双分录字段映射", "detail": "显式配置 journal_group_fields、debit_field 与 credit_field；账务关系不得只靠字段猜测。"})
        if kind == "period_rollforward" and (not contract.get("account_fields") or not contract.get("period_field") or not contract.get("opening_balance_field") or not contract.get("closing_balance_field") or len(contract.get("period_sequence") or []) < 2):
            candidates.append({"candidate_id": f"{contract['contract_id']}_ROLLFORWARD_FIELDS", "risk_type": "financial_accounting_contract_gap", "severity": "P1", "title": f"{contract.get('resource')} 缺少账期滚动映射", "detail": "显式配置 account_field、period_field、opening_balance_field、closing_balance_field 与至少两个 period_sequence。"})
    return contracts[:300], candidates[:120]


def _summary(contracts: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "causality_contract_count": len(contracts),
        "side_effect_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "side_effect"),
        "referential_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "referential_integrity"),
        "conservation_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "record_conservation"),
        "journal_balance_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "journal_balance"),
        "period_rollforward_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "period_rollforward"),
        "inventory_reservation_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "inventory_reservation_balance"),
        "amount_conservation_contract_count": sum(1 for item in contracts if item.get("amount_relation") in {"sum_equal", "equal"}),
        "contract_gap_count": len(candidates),
    }


def build_business_causality_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    contracts, candidates = build_business_causality_contracts(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    learning = ingest_confirmed_bug_feedback(project, root)
    memory = learning.get("memory") or {}
    for contract in contracts:
        bonus, matches = _learning_bonus(contract, memory)
        contract["learning_bonus"] = bonus
        contract["learning_matches"] = matches
    profile = {
        "phase": "phase51_business_causality_conservation",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "contracts": contracts,
        "candidates": candidates,
        "summary": {**_summary(contracts, candidates), "confirmed_bug_memory_count": int((learning.get("summary") or {}).get("confirmed_bug_memory_count") or 0)},
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_get": True, "financial_contracts_require_explicit_field_mapping": True, "inventory_contracts_require_explicit_field_mapping": True, "write_and_race_validation_are_sandbox_required": True, "evidence_uses_hashed_identities": True, "raw_business_rows_are_not_persisted": True, "findings_need_human_review": True},
    }
    profile["private_leak_check"] = _private_leak_check(profile)
    output = _output_paths(project, root)
    _write_json(output["out"] / "business_causality_profile.json", profile)
    _write_json(output["workspace"] / "business_causality_profile.json", profile)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "business_causality_profile_report.html").write_text(render_business_causality_profile_report(profile), encoding="utf-8")
    return profile


def load_business_causality_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["workspace"] / "business_causality_profile.json", {})
    return data if isinstance(data, dict) and data.get("phase") == "phase51_business_causality_conservation" else None


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, risk_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "probe_id": f"BCC_PROBE_{number:04d}",
        "source": "business_causality_conservation",
        "business_causality_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "risk_type": risk_type,
        "severity": extra.pop("severity", "P1"),
        "expected": extra.pop("expected", "业务因果关系与金额守恒必须成立。"),
        "method": extra.pop("method", "GET"),
        "path": extra.pop("path", str((contract.get("source") or {}).get("path") or "")),
        "actor": "normal_user",
        "destructive": bool(extra.pop("destructive", False)),
        "execution_policy": extra.pop("execution_policy", "safe_read_only"),
        "learning_bonus": contract.get("learning_bonus") or 0.0,
        "learning_matches": contract.get("learning_matches") or [],
        **extra,
    }


def generate_business_causality_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_business_causality_profile(project_id, root) or build_business_causality_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        kind = str(contract.get("contract_kind") or "")
        resource = str(contract.get("resource") or "resource")
        if kind == "record_conservation":
            probes.append(_probe(contract, len(probes)+1, "record_conservation", f"业务金额守恒：{resource} 合计必须等于明细组成", "business_amount_conservation", expected="单据合计必须等于正向组成字段之和减去优惠/抵扣字段。"))
        elif kind == "journal_balance":
            probes.append(_probe(contract, len(probes)+1, "journal_double_entry_balance", f"账务双分录守恒：{resource} 每张凭证借贷必须平衡", "financial_double_entry_balance", severity="P0", expected="同一凭证、币种下借方金额之和必须等于贷方金额之和。"))
        elif kind == "period_rollforward":
            probes.append(_probe(contract, len(probes)+1, "period_rollforward_continuity", f"账期余额连续性：{resource} 期末必须衔接下期期初", "financial_period_rollforward", severity="P0", expected="相邻已声明账期下，同一科目与币种的期末余额必须等于下一账期期初余额。"))
            if contract.get("verify_movement_formula"):
                probes.append(_probe(contract, len(probes)+1, "period_balance_formula", f"账期余额滚动：{resource} 期初加本期发生额必须等于期末", "financial_period_rollforward", severity="P0", expected="账期内余额必须符合显式配置的借贷方向滚动公式。"))
        elif kind == "inventory_reservation_balance":
            probes.append(_probe(contract, len(probes)+1, "inventory_reservation_quantity", f"库存预占守恒：{resource} 库存预占必须等于有效预占明细之和", "inventory_reservation_conservation", severity="P1", expected="同一显式库存键下，库存快照 reserved 数量必须等于有效预占记录数量之和。"))
            probes.append(_probe(contract, len(probes)+1, "inventory_available_balance", f"可用库存守恒：{resource} 可用库存必须等于在库减预占", "inventory_stock_conservation", severity="P1", expected="同一库存快照中 available 必须等于 on_hand 减 reserved，误差不得超过配置容差。"))
            if not contract.get("allow_negative_available"):
                probes.append(_probe(contract, len(probes)+1, "inventory_negative_available_stock", f"库存超卖风险：{resource} 可用库存不得为负", "inventory_oversell_risk", severity="P0", expected="未显式允许负可用库存时，任何完整库存快照的 available 均不得小于零。"))
        elif kind == "referential_integrity":
            probes.append(_probe(contract, len(probes)+1, "orphan_side_effect", f"副作用可追溯：{resource} 不得引用不存在的主业务单据", "business_causality_orphan", expected="每条支付、退款、库存、审批或台账记录都必须关联到可读取的主业务实体。"))
        elif kind == "side_effect":
            probes.append(_probe(contract, len(probes)+1, "required_side_effect", f"业务副作用完整性：{resource} 达成状态后必须产生必要结果", "business_causality_missing", expected="满足业务状态条件的主实体必须拥有足量的关联副作用记录。"))
            if contract.get("max_count") is not None:
                probes.append(_probe(contract, len(probes)+1, "duplicate_side_effect", f"业务副作用幂等：{resource} 不得重复产生", "business_causality_duplicate", expected="一次性业务动作生成的关联记录数量不得超过配置上限。"))
            if contract.get("amount_relation") in {"sum_equal", "equal"}:
                probes.append(_probe(contract, len(probes)+1, "side_effect_amount_conservation", f"跨实体金额守恒：{resource} 主单与副作用金额必须一致", "business_amount_conservation", expected="主业务金额必须等于其关联支付、退款、发票或交易记录金额之和。"))
            probes.append(_probe(contract, len(probes)+1, "duplicate_submit_sandbox", f"沙箱幂等验证：{resource} 重复提交不得重复产生副作用", "business_causality_idempotency", severity="P1", method="POST", path=str((contract.get("source") or {}).get("path") or ""), destructive=True, execution_policy="sandbox_required", expected="在隔离环境对同一幂等键重放业务动作，副作用总数和金额不得重复。"))
    for candidate in profile.get("candidates") or []:
        probes.append({"probe_id": f"BCC_GAP_{len(probes)+1:04d}", "source": "business_causality_conservation", "business_causality_type": "contract_gap", "contract_id": candidate.get("candidate_id"), "title": candidate.get("title"), "risk_type": candidate.get("risk_type") or "business_causality_contract_gap", "severity": candidate.get("severity") or "P2", "expected": candidate.get("detail"), "method": "GET", "path": "", "actor": "normal_user", "destructive": False, "execution_policy": "candidate_only"})
    return probes[:max(1, int(max_count or cfg.get("max_probe_count") or 160))]


def _collection_context(base_url: str, collection: dict[str, Any], query: dict[str, Any], pagination: dict[str, Any], token: str | None, timeout: int, max_bytes: int, max_pages: int, max_records: int) -> dict[str, Any]:
    contract = {"source": {"path": collection.get("path"), "parameters": collection.get("parameters") or []}, "sample_query": dict(query or {}), "pagination": dict(pagination or {})}
    context = _fetch_source_pages(base_url, contract, token, timeout, max_bytes, max_pages)
    context["records"] = list(context.get("records") or [])[:max_records]
    context["request_path"] = collection.get("path")
    context["query"] = _redact(query or {})
    return context


def _index_rows(rows: list[dict[str, Any]], field: str, mappings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = _canon(_field_value(row, field, mappings))
        if value:
            index[value].append(row)
    return dict(index)


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], severity: str = "P1", confidence: float = 0.9, key: Any | None = None) -> dict[str, Any]:
    fingerprint = _hash({"contract": contract.get("contract_id"), "kind": kind, "key": key or evidence})
    return {
        "issue_id": f"BCC_{fingerprint[:12].upper()}",
        "fingerprint": fingerprint,
        "source": "business_causality_conservation",
        "risk_type": {
            "missing_side_effect": "business_causality_missing",
            "duplicate_side_effect": "business_causality_duplicate",
            "orphan_side_effect": "business_causality_orphan",
            "record_conservation_mismatch": "business_amount_conservation",
            "side_effect_amount_mismatch": "business_amount_conservation",
            "journal_double_entry_unbalanced": "financial_double_entry_balance",
            "period_opening_balance_mismatch": "financial_period_rollforward",
            "period_balance_formula_mismatch": "financial_period_rollforward",
            "inventory_reservation_quantity_mismatch": "inventory_reservation_conservation",
            "inventory_available_balance_mismatch": "inventory_stock_conservation",
            "inventory_negative_available_stock": "inventory_oversell_risk",
            "inventory_reservation_without_stock": "inventory_reservation_conservation",
        }.get(kind, "business_causality"),
        "business_causality_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": severity,
        "status": "needs_human_review",
        "confidence": round(min(0.98, confidence + float(contract.get("learning_bonus") or 0.0)), 3),
        "expected": expected,
        "actual": actual,
        "evidence": _redact(evidence),
        "learning_matches": contract.get("learning_matches") or [],
    }


def _audit_formula(contract: dict[str, Any], source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    if not source.get("complete"):
        return findings, [{"result": "skipped_source_incomplete", "source_total": source.get("total"), "fetched_rows": len(source.get("records") or [])}]
    formula = dict(contract.get("formula") or {})
    expected_field = str(formula.get("expected_field") or contract.get("source_amount_field") or "")
    positives = [str(item) for item in (formula.get("positive_fields") or [])]
    negatives = [str(item) for item in (formula.get("negative_fields") or [])]
    mappings = dict(contract.get("field_mappings") or {})
    tolerance = abs(float(contract.get("tolerance") or 0.01))
    identity_fields = list(contract.get("source_identity_fields") or [])
    sample: list[dict[str, Any]] = []
    checked = 0
    for row in source.get("records") or []:
        actual = _numeric(_field_value(row, expected_field, mappings))
        values_plus = [_numeric(_field_value(row, field, mappings)) for field in positives]
        values_minus = [_numeric(_field_value(row, field, mappings)) for field in negatives]
        if actual is None or any(value is None for value in values_plus) or any(value is None for value in values_minus):
            continue
        checked += 1
        expected = round(sum(values_plus) - sum(values_minus), 6)
        delta = round(float(actual) - expected, 6)
        if abs(delta) > tolerance:
            identifier = _identity(row, identity_fields, mappings) or _hash(row)
            sample.append({"identity_hash": _short(identifier), "actual": actual, "expected": expected, "delta": delta})
    observations.append({"result": "executed", "checked_rows": checked, "mismatch_rows": len(sample), "formula": {"expected_field": expected_field, "positive_fields": positives, "negative_fields": negatives}, "tolerance": tolerance})
    if sample:
        findings.append(_finding(contract, "record_conservation_mismatch", f"业务金额守恒失败：{contract.get('resource')} 单据合计与组成字段不一致", "每条完整单据的合计应等于组成金额之和减去优惠/抵扣。", f"在 {len(sample)} 条可计算记录中发现合计偏差，样例最大偏差为 {max(abs(float(row['delta'])) for row in sample):.6g}。", {"source_request": {"method": "GET", "path": source.get("request_path"), "query": source.get("query")}, "formula": {"expected_field": expected_field, "positive_fields": positives, "negative_fields": negatives, "tolerance": tolerance}, "mismatch_count": len(sample), "samples": sample[:12], "source_coverage": {"complete": source.get("complete"), "total": source.get("total"), "fetched_rows": len(source.get("records") or [])}}, confidence=0.94, key=[row["identity_hash"] for row in sample]))
    return findings, observations



def _audit_journal_balance(contract: dict[str, Any], source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    if not source.get("complete"):
        return findings, [{"result": "skipped_source_incomplete", "source_total": source.get("total"), "fetched_rows": len(source.get("records") or [])}]
    mappings = dict(contract.get("field_mappings") or {})
    group_fields = [str(item) for item in (contract.get("journal_group_fields") or []) if str(item)]
    debit_field = str(contract.get("journal_debit_field") or "")
    credit_field = str(contract.get("journal_credit_field") or "")
    currency_field = str(contract.get("currency_field") or "")
    if not group_fields or not debit_field or not credit_field:
        return findings, [{"result": "skipped_missing_journal_mapping", "journal_group_fields": group_fields, "debit_field": debit_field, "credit_field": credit_field}]
    tolerance = Decimal(str(abs(float(contract.get("tolerance") or 0.01))))
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    skipped = 0
    for row in source.get("records") or []:
        group = _identity(row, group_fields, mappings)
        debit = _decimal(_field_value(row, debit_field, mappings))
        credit = _decimal(_field_value(row, credit_field, mappings))
        if not group or debit is None or credit is None:
            skipped += 1
            continue
        currency = _canon(_field_value(row, currency_field, mappings)) if currency_field else ""
        key = (group, currency)
        bucket = buckets.setdefault(key, {"debit": Decimal("0"), "credit": Decimal("0"), "line_count": 0})
        bucket["debit"] += debit
        bucket["credit"] += credit
        bucket["line_count"] += 1
    mismatches: list[dict[str, Any]] = []
    for (group, currency), bucket in buckets.items():
        delta = bucket["debit"] - bucket["credit"]
        if abs(delta) > tolerance:
            mismatches.append({
                "journal_hash": _short(group),
                "currency": currency or None,
                "debit_total": _decimal_text(bucket["debit"]),
                "credit_total": _decimal_text(bucket["credit"]),
                "delta": _decimal_text(delta),
                "line_count": bucket["line_count"],
            })
    observations.append({"result": "executed", "checked_journal_count": len(buckets), "unbalanced_journal_count": len(mismatches), "skipped_line_count": skipped, "journal_group_fields": group_fields, "debit_field": debit_field, "credit_field": credit_field, "currency_field": currency_field or None, "tolerance": str(tolerance)})
    if mismatches:
        findings.append(_finding(
            contract,
            "journal_double_entry_unbalanced",
            f"账务双分录不平衡：{contract.get('resource')} 存在借贷不相等凭证",
            "同一凭证、币种下借方金额之和必须等于贷方金额之和。",
            f"发现 {len(mismatches)} 张凭证的借贷汇总不平衡。",
            {"source_request": {"method": "GET", "path": source.get("request_path"), "query": source.get("query")}, "journal_group_fields": group_fields, "debit_field": debit_field, "credit_field": credit_field, "currency_field": currency_field or None, "tolerance": str(tolerance), "mismatch_count": len(mismatches), "mismatches": mismatches[:20], "source_coverage": {"complete": source.get("complete"), "total": source.get("total"), "fetched_rows": len(source.get("records") or [])}},
            severity="P0",
            confidence=0.97,
            key=[item["journal_hash"] for item in mismatches],
        ))
    return findings, observations


def _audit_period_rollforward(contract: dict[str, Any], source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    if not source.get("complete"):
        return findings, [{"result": "skipped_source_incomplete", "source_total": source.get("total"), "fetched_rows": len(source.get("records") or [])}]
    mappings = dict(contract.get("field_mappings") or {})
    account_fields = [str(item) for item in (contract.get("account_fields") or []) if str(item)]
    period_field = str(contract.get("period_field") or "")
    opening_field = str(contract.get("opening_balance_field") or "")
    closing_field = str(contract.get("closing_balance_field") or "")
    debit_field = str(contract.get("journal_debit_field") or "")
    credit_field = str(contract.get("journal_credit_field") or "")
    currency_field = str(contract.get("currency_field") or "")
    periods = [str(item) for item in (contract.get("period_sequence") or []) if str(item)]
    if not account_fields or not period_field or not opening_field or not closing_field or len(periods) < 2:
        return findings, [{"result": "skipped_missing_rollforward_mapping", "account_fields": account_fields, "period_field": period_field, "opening_balance_field": opening_field, "closing_balance_field": closing_field, "period_sequence": periods}]
    tolerance = Decimal(str(abs(float(contract.get("tolerance") or 0.01))))
    allowed_periods = set(periods)
    buckets: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    duplicate_keys: set[tuple[str, str, str]] = set()
    skipped = 0
    for row in source.get("records") or []:
        account = _identity(row, account_fields, mappings)
        period = _canon(_field_value(row, period_field, mappings))
        if not account or period not in allowed_periods:
            skipped += 1
            continue
        currency = _canon(_field_value(row, currency_field, mappings)) if currency_field else ""
        key = (account, currency)
        values = buckets.setdefault(key, {})
        if period in values:
            duplicate_keys.add((account, currency, period))
            continue
        values[period] = row
    continuity: list[dict[str, Any]] = []
    formula_mismatches: list[dict[str, Any]] = []
    formula_checked = 0
    movement_sign = str(contract.get("movement_sign") or "debit_minus_credit").lower()
    for (account, currency), by_period in buckets.items():
        if any((account, currency, period) in duplicate_keys for period in by_period):
            continue
        for previous, following in zip(periods, periods[1:]):
            left = by_period.get(previous)
            right = by_period.get(following)
            if not left or not right:
                continue
            closing = _decimal(_field_value(left, closing_field, mappings))
            opening = _decimal(_field_value(right, opening_field, mappings))
            if closing is None or opening is None:
                continue
            delta = opening - closing
            if abs(delta) > tolerance:
                continuity.append({"account_hash": _short(account), "currency": currency or None, "previous_period": previous, "next_period": following, "previous_closing": _decimal_text(closing), "next_opening": _decimal_text(opening), "delta": _decimal_text(delta)})
        if not contract.get("verify_movement_formula") or not debit_field or not credit_field:
            continue
        for period, row in by_period.items():
            opening = _decimal(_field_value(row, opening_field, mappings))
            closing = _decimal(_field_value(row, closing_field, mappings))
            debit = _decimal(_field_value(row, debit_field, mappings))
            credit = _decimal(_field_value(row, credit_field, mappings))
            if None in {opening, closing, debit, credit}:
                continue
            formula_checked += 1
            expected = opening + debit - credit if movement_sign != "credit_minus_debit" else opening - debit + credit
            delta = closing - expected
            if abs(delta) > tolerance:
                formula_mismatches.append({"account_hash": _short(account), "currency": currency or None, "period": period, "opening": _decimal_text(opening), "debit": _decimal_text(debit), "credit": _decimal_text(credit), "closing": _decimal_text(closing), "expected_closing": _decimal_text(expected), "delta": _decimal_text(delta)})
    observations.append({"result": "executed", "checked_account_currency_count": len(buckets), "continuity_mismatch_count": len(continuity), "formula_checked_row_count": formula_checked, "formula_mismatch_count": len(formula_mismatches), "duplicate_account_period_count": len(duplicate_keys), "skipped_row_count": skipped, "period_sequence": periods, "movement_sign": movement_sign, "tolerance": str(tolerance)})
    common = {"source_request": {"method": "GET", "path": source.get("request_path"), "query": source.get("query")}, "account_fields": account_fields, "period_field": period_field, "opening_balance_field": opening_field, "closing_balance_field": closing_field, "currency_field": currency_field or None, "period_sequence": periods, "tolerance": str(tolerance), "source_coverage": {"complete": source.get("complete"), "total": source.get("total"), "fetched_rows": len(source.get("records") or [])}}
    if continuity:
        findings.append(_finding(contract, "period_opening_balance_mismatch", f"账期余额断档：{contract.get('resource')} 期末与下期期初不连续", "相邻已声明账期下，同一科目与币种的期末余额必须等于下一账期期初余额。", f"发现 {len(continuity)} 处跨账期余额不连续。", {**common, "mismatch_count": len(continuity), "mismatches": continuity[:20]}, severity="P0", confidence=0.97, key=[(item["account_hash"], item["previous_period"], item["next_period"]) for item in continuity]))
    if formula_mismatches:
        findings.append(_finding(contract, "period_balance_formula_mismatch", f"账期余额滚动错误：{contract.get('resource')} 期初与本期发生额未正确滚动到期末", "期末余额必须符合已声明的期初余额与借贷发生额滚动公式。", f"发现 {len(formula_mismatches)} 条账期余额滚动不一致。", {**common, "debit_field": debit_field, "credit_field": credit_field, "movement_sign": movement_sign, "mismatch_count": len(formula_mismatches), "mismatches": formula_mismatches[:20]}, severity="P0", confidence=0.96, key=[(item["account_hash"], item["period"]) for item in formula_mismatches]))
    return findings, observations


def _audit_inventory_reservation_balance(contract: dict[str, Any], source: dict[str, Any], dependent: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate an explicitly configured stock/reservation conservation contract.

    Inventory semantics vary by enterprise: some systems permit backorders,
    others model safety stock separately, and some reservation rows represent
    historical releases.  For that reason this audit never guesses field names
    or active states.  It executes only against complete, explicit snapshots.
    """

    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    if not source.get("complete") or not dependent.get("complete"):
        return findings, [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "dependent_complete": dependent.get("complete")}]

    mappings = dict(contract.get("field_mappings") or {})
    stock_fields = [str(item) for item in (contract.get("inventory_identity_fields") or []) if str(item)]
    reservation_fields = [str(item) for item in (contract.get("reservation_identity_fields") or []) if str(item)]
    on_hand_field = str(contract.get("inventory_on_hand_field") or "")
    reserved_field = str(contract.get("inventory_reserved_field") or "")
    available_field = str(contract.get("inventory_available_field") or "")
    reservation_quantity_field = str(contract.get("reservation_quantity_field") or "")
    reservation_status_field = str(contract.get("reservation_status_field") or "")
    active_states = set(_normal_states(contract.get("active_reservation_states") or []))
    if not stock_fields or not reservation_fields or not on_hand_field or not reserved_field or not available_field or not reservation_quantity_field or not reservation_status_field or not active_states:
        return findings, [{
            "result": "skipped_missing_inventory_mapping",
            "inventory_identity_fields": stock_fields,
            "reservation_identity_fields": reservation_fields,
            "inventory_on_hand_field": on_hand_field,
            "inventory_reserved_field": reserved_field,
            "inventory_available_field": available_field,
            "reservation_quantity_field": reservation_quantity_field,
            "reservation_status_field": reservation_status_field,
            "active_reservation_states": sorted(active_states),
        }]

    tolerance = Decimal(str(abs(float(contract.get("tolerance") or 0.01))))
    stock_by_key: dict[str, dict[str, Any]] = {}
    duplicate_stock_keys: set[str] = set()
    skipped_stock_rows = 0
    for row in source.get("records") or []:
        key = _identity(row, stock_fields, mappings)
        if not key:
            skipped_stock_rows += 1
            continue
        if key in stock_by_key:
            duplicate_stock_keys.add(key)
            continue
        stock_by_key[key] = row

    active_reserved_by_key: dict[str, Decimal] = defaultdict(Decimal)
    skipped_reservation_rows = 0
    active_reservation_count = 0
    for row in dependent.get("records") or []:
        key = _identity(row, reservation_fields, mappings)
        state = _norm(_field_value(row, reservation_status_field, mappings))
        if not key or not state:
            skipped_reservation_rows += 1
            continue
        if state not in active_states:
            continue
        quantity = _decimal(_field_value(row, reservation_quantity_field, mappings))
        if quantity is None:
            skipped_reservation_rows += 1
            continue
        active_reserved_by_key[key] += quantity
        active_reservation_count += 1

    reservation_mismatches: list[dict[str, Any]] = []
    available_mismatches: list[dict[str, Any]] = []
    negative_available: list[dict[str, Any]] = []
    invalid_stock_rows = 0
    for key, row in stock_by_key.items():
        if key in duplicate_stock_keys:
            continue
        on_hand = _decimal(_field_value(row, on_hand_field, mappings))
        reserved = _decimal(_field_value(row, reserved_field, mappings))
        available = _decimal(_field_value(row, available_field, mappings))
        if on_hand is None or reserved is None or available is None:
            invalid_stock_rows += 1
            continue
        key_hash = _short(key)
        active_reserved = active_reserved_by_key.get(key, Decimal("0"))
        reservation_delta = reserved - active_reserved
        if abs(reservation_delta) > tolerance:
            reservation_mismatches.append({
                "stock_identity_hash": key_hash,
                "snapshot_reserved": _decimal_text(reserved),
                "active_reservation_sum": _decimal_text(active_reserved),
                "delta": _decimal_text(reservation_delta),
            })
        expected_available = on_hand - reserved
        available_delta = available - expected_available
        if abs(available_delta) > tolerance:
            available_mismatches.append({
                "stock_identity_hash": key_hash,
                "on_hand": _decimal_text(on_hand),
                "reserved": _decimal_text(reserved),
                "available": _decimal_text(available),
                "expected_available": _decimal_text(expected_available),
                "delta": _decimal_text(available_delta),
            })
        if not contract.get("allow_negative_available") and available < -tolerance:
            negative_available.append({
                "stock_identity_hash": key_hash,
                "on_hand": _decimal_text(on_hand),
                "reserved": _decimal_text(reserved),
                "available": _decimal_text(available),
            })

    reservations_without_stock = [
        {"stock_identity_hash": _short(key), "active_reservation_sum": _decimal_text(quantity)}
        for key, quantity in active_reserved_by_key.items()
        if key not in stock_by_key
    ]
    observations.append({
        "result": "executed",
        "checked_stock_key_count": len(stock_by_key) - len(duplicate_stock_keys),
        "active_reservation_count": active_reservation_count,
        "reservation_quantity_mismatch_count": len(reservation_mismatches),
        "available_balance_mismatch_count": len(available_mismatches),
        "negative_available_count": len(negative_available),
        "reservation_without_stock_count": len(reservations_without_stock),
        "duplicate_stock_key_count": len(duplicate_stock_keys),
        "skipped_stock_row_count": skipped_stock_rows + invalid_stock_rows,
        "skipped_reservation_row_count": skipped_reservation_rows,
        "active_reservation_states": sorted(active_states),
        "tolerance": str(tolerance),
    })
    common = {
        "source_request": {"method": "GET", "path": source.get("request_path"), "query": source.get("query")},
        "dependent_request": {"method": "GET", "path": dependent.get("request_path"), "query": dependent.get("query")},
        "inventory_identity_fields": stock_fields,
        "reservation_identity_fields": reservation_fields,
        "on_hand_field": on_hand_field,
        "reserved_field": reserved_field,
        "available_field": available_field,
        "reservation_quantity_field": reservation_quantity_field,
        "reservation_status_field": reservation_status_field,
        "active_reservation_states": sorted(active_states),
        "allow_negative_available": bool(contract.get("allow_negative_available")),
        "tolerance": str(tolerance),
        "source_coverage": {"complete": source.get("complete"), "total": source.get("total"), "fetched_rows": len(source.get("records") or [])},
        "dependent_coverage": {"complete": dependent.get("complete"), "total": dependent.get("total"), "fetched_rows": len(dependent.get("records") or [])},
    }
    if reservation_mismatches:
        findings.append(_finding(
            contract,
            "inventory_reservation_quantity_mismatch",
            f"库存预占守恒失败：{contract.get('resource')} 库存预占与有效预占明细不一致",
            "同一显式库存键下，库存快照 reserved 数量必须等于有效预占记录数量之和。",
            f"发现 {len(reservation_mismatches)} 个库存键的快照预占与有效预占明细不一致。",
            {**common, "mismatch_count": len(reservation_mismatches), "mismatches": reservation_mismatches[:20]},
            severity="P1",
            confidence=0.97,
            key=[item["stock_identity_hash"] for item in reservation_mismatches],
        ))
    if available_mismatches:
        findings.append(_finding(
            contract,
            "inventory_available_balance_mismatch",
            f"可用库存守恒失败：{contract.get('resource')} 在库、预占与可用数量不一致",
            "同一库存快照中 available 必须等于 on_hand 减 reserved，误差不得超过配置容差。",
            f"发现 {len(available_mismatches)} 个库存键的可用库存公式不成立。",
            {**common, "mismatch_count": len(available_mismatches), "mismatches": available_mismatches[:20]},
            severity="P1",
            confidence=0.97,
            key=[item["stock_identity_hash"] for item in available_mismatches],
        ))
    if negative_available:
        findings.append(_finding(
            contract,
            "inventory_negative_available_stock",
            f"库存超卖风险：{contract.get('resource')} 存在负可用库存",
            "未显式允许负可用库存时，任何完整库存快照的 available 均不得小于零。",
            f"发现 {len(negative_available)} 个库存键可用库存为负，可能已发生超卖或库存扣减重复。",
            {**common, "negative_count": len(negative_available), "samples": negative_available[:20]},
            severity="P0",
            confidence=0.98,
            key=[item["stock_identity_hash"] for item in negative_available],
        ))
    if reservations_without_stock:
        findings.append(_finding(
            contract,
            "inventory_reservation_without_stock",
            f"库存预占孤儿数据：{contract.get('resource')} 存在无法对应库存快照的有效预占",
            "每条有效预占都必须能对应完整库存快照中的显式库存键。",
            f"发现 {len(reservations_without_stock)} 个有效预占键没有对应库存快照。",
            {**common, "orphan_count": len(reservations_without_stock), "orphans": reservations_without_stock[:20]},
            severity="P1",
            confidence=0.96,
            key=[item["stock_identity_hash"] for item in reservations_without_stock],
        ))
    return findings, observations

def _audit_referential(contract: dict[str, Any], source: dict[str, Any], dependent: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    if not source.get("complete") or not dependent.get("complete"):
        return findings, [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "dependent_complete": dependent.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    ids = {_identity(row, list(contract.get("source_identity_fields") or []), mappings) for row in (source.get("records") or [])}
    ids.discard(None)
    fk = str(contract.get("dependent_foreign_key") or "")
    missing: list[str] = []
    checked = 0
    for row in dependent.get("records") or []:
        value = _canon(_field_value(row, fk, mappings))
        if not value:
            continue
        checked += 1
        if value not in ids:
            missing.append(_short(value))
    observations.append({"result": "executed", "checked_dependent_rows": checked, "orphan_count": len(missing), "source_identity_count": len(ids), "foreign_key": fk})
    if missing:
        findings.append(_finding(contract, "orphan_side_effect", f"副作用孤儿数据：{contract.get('resource')} 存在无法回溯的关联记录", "每条副作用记录的主单外键都必须对应当前完整事实集合中的业务主键。", f"发现 {len(missing)} 条副作用记录关联不到主业务实体。", {"source_request": {"method": "GET", "path": source.get("request_path"), "query": source.get("query")}, "dependent_request": {"method": "GET", "path": dependent.get("request_path"), "query": dependent.get("query")}, "foreign_key": fk, "orphan_identity_hashes": missing[:20], "source_coverage": {"complete": source.get("complete"), "total": source.get("total")}, "dependent_coverage": {"complete": dependent.get("complete"), "total": dependent.get("total")}}, confidence=0.93, key=missing))
    return findings, observations


def _audit_side_effect(contract: dict[str, Any], source: dict[str, Any], dependent: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    if not source.get("complete") or not dependent.get("complete"):
        return findings, [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "dependent_complete": dependent.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    identity_fields = list(contract.get("source_identity_fields") or [])
    state_field = str(contract.get("state_field") or "")
    states = set(_normal_states(contract.get("required_states") or []))
    if not identity_fields or not state_field or not states:
        return findings, [{"result": "skipped_missing_causal_mapping", "identity_fields": identity_fields, "state_field": state_field, "required_states": sorted(states)}]
    fk = str(contract.get("dependent_foreign_key") or "")
    related = _index_rows(list(dependent.get("records") or []), fk, mappings)
    eligible: list[tuple[str, dict[str, Any]]] = []
    for row in source.get("records") or []:
        identity = _identity(row, identity_fields, mappings)
        state = _norm(_field_value(row, state_field, mappings))
        if identity and state in states:
            eligible.append((identity, row))
    min_count = int(contract.get("min_count") or 0)
    max_count = contract.get("max_count")
    max_count = int(max_count) if max_count is not None else None
    missing: list[str] = []
    duplicates: list[dict[str, Any]] = []
    amount_mismatches: list[dict[str, Any]] = []
    for identity, row in eligible:
        children = related.get(identity, [])
        if len(children) < min_count:
            missing.append(_short(identity))
        if max_count is not None and len(children) > max_count:
            duplicates.append({"identity_hash": _short(identity), "actual_count": len(children), "max_count": max_count})
        if str(contract.get("amount_relation") or "") in {"sum_equal", "equal"} and contract.get("source_amount_field") and contract.get("dependent_amount_field") and children:
            left = _numeric(_field_value(row, str(contract.get("source_amount_field")), mappings))
            right_values = [_numeric(_field_value(child, str(contract.get("dependent_amount_field")), mappings)) for child in children]
            if left is not None and all(value is not None for value in right_values):
                right = round(sum(float(value) for value in right_values if value is not None), 6)
                delta = round(float(left) - right, 6)
                if abs(delta) > abs(float(contract.get("tolerance") or 0.01)):
                    amount_mismatches.append({"identity_hash": _short(identity), "source_amount": left, "dependent_amount_sum": right, "delta": delta, "dependent_count": len(children)})
    observations.append({"result": "executed", "eligible_source_count": len(eligible), "missing_side_effect_count": len(missing), "duplicate_side_effect_count": len(duplicates), "amount_mismatch_count": len(amount_mismatches), "required_states": sorted(states), "min_count": min_count, "max_count": max_count})
    common = {"source_request": {"method": "GET", "path": source.get("request_path"), "query": source.get("query")}, "dependent_request": {"method": "GET", "path": dependent.get("request_path"), "query": dependent.get("query")}, "state_field": state_field, "required_states": sorted(states), "foreign_key": fk, "source_coverage": {"complete": source.get("complete"), "total": source.get("total")}, "dependent_coverage": {"complete": dependent.get("complete"), "total": dependent.get("total")}}
    if missing:
        findings.append(_finding(contract, "missing_side_effect", f"业务副作用缺失：{contract.get('resource')} 达成状态后未产生必要记录", f"状态属于 {sorted(states)} 的业务主单至少应有 {min_count} 条关联副作用记录。", f"在 {len(eligible)} 条符合状态条件的主单中，发现 {len(missing)} 条缺少关联副作用。", {**common, "min_count": min_count, "missing_identity_hashes": missing[:20]}, confidence=0.94, key=missing))
    if duplicates:
        findings.append(_finding(contract, "duplicate_side_effect", f"业务副作用重复：{contract.get('resource')} 同一业务主单产生重复结果", f"同一主单的关联副作用记录数量不得超过 {max_count}。", f"发现 {len(duplicates)} 个主单产生超量关联副作用记录。", {**common, "max_count": max_count, "duplicates": duplicates[:20]}, confidence=0.95, key=duplicates))
    if amount_mismatches:
        findings.append(_finding(contract, "side_effect_amount_mismatch", f"跨实体金额不守恒：{contract.get('resource')} 主单与副作用金额不一致", "主业务金额应等于关联副作用金额之和，且误差不得超过配置容差。", f"发现 {len(amount_mismatches)} 个主单的金额守恒关系被破坏。", {**common, "source_amount_field": contract.get("source_amount_field"), "dependent_amount_field": contract.get("dependent_amount_field"), "tolerance": contract.get("tolerance"), "mismatches": amount_mismatches[:20]}, confidence=0.95, key=amount_mismatches))
    return findings, observations


def run_business_causality_conservation(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_business_causality_profile(project, root, options)
    section = _section(cfg)
    mode = str(options.get("execution_mode") or cfg.get("business_causality_execution_mode") or cfg.get("business_causality_conservation_execution_mode") or "plan_only").lower()
    if mode not in {"plan_only", "safe_live"}:
        mode = "plan_only"
    base_url = str(cfg.get("base_url", ""))
    # Fallback: try environment config if runtime_config has no base_url
    if not base_url:
        try:
            from .enterprise_testops_control_plane import load_environment_config, _environment_by_name
            env_cfg = load_environment_config(project, root)
            target = _environment_by_name(env_cfg, cfg.get("environment", "test"))
            base_url = str(target.get("base_url", ""))
        except Exception:
            pass
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_pages") or section.get("max_pages") or 12), 100))
    max_records = max(10, min(int(options.get("max_records") or section.get("max_records") or 2000), 10000))
    accounts = _load_json(config_paths(project, root)["input_dir"] / "test_accounts.json", {})
    safety = execution_safety_verdict(project, cfg, accounts)
    live_execution_allowed = mode == "safe_live" and bool(safety.get("safe_to_proceed"))
    token = _normal_token(cfg, project, root, timeout) if live_execution_allowed and base_url else None
    cache: dict[str, dict[str, Any]] = {}
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    readiness_findings: list[dict[str, Any]] = []
    if mode == "safe_live" and not live_execution_allowed:
        readiness_findings.append({"kind": "safety_boundary", "status": "blocked", "message": "在线因果/账务执行已被安全边界阻断；这不是产品缺陷。", "violations": safety.get("violations") or []})

    def fetch(collection: dict[str, Any], query: dict[str, Any], pagination: dict[str, Any]) -> dict[str, Any]:
        key = _hash({"path": collection.get("path"), "query": query, "pagination": pagination})
        if key not in cache:
            cache[key] = _collection_context(base_url, collection, query, pagination, token, timeout, max_bytes, max_pages, max_records)
        return cache[key]

    for contract in profile.get("contracts") or []:
        contract_id = str(contract.get("contract_id") or "")
        if mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract_id, "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        if not live_execution_allowed:
            executions.append({"contract_id": contract_id, "status": "blocked_by_safety_boundary", "reason": "unsafe_or_undeclared_target"})
            continue
        source = contract.get("source") or {}
        if not source.get("path"):
            executions.append({"contract_id": contract_id, "status": "skipped", "reason": "source_path_missing"})
            continue
        source_context = fetch(source, dict(contract.get("source_query") or {}), dict((contract.get("pagination") or {}).get("source") or {}))
        if not (source_context.get("responses") or [{}])[0].get("status_code"):
            executions.append({"contract_id": contract_id, "status": "error", "reason": "source_fetch_failed", "source_responses": source_context.get("responses")})
            continue
        dependent_context: dict[str, Any] | None = None
        if contract.get("contract_kind") not in {"record_conservation", "journal_balance", "period_rollforward"}:
            dependent = contract.get("dependent") or {}
            if not dependent.get("path"):
                executions.append({"contract_id": contract_id, "status": "skipped", "reason": "dependent_path_missing"})
                continue
            dependent_context = fetch(dependent, dict(contract.get("dependent_query") or {}), dict((contract.get("pagination") or {}).get("dependent") or {}))
            if not (dependent_context.get("responses") or [{}])[0].get("status_code"):
                executions.append({"contract_id": contract_id, "status": "error", "reason": "dependent_fetch_failed", "dependent_responses": dependent_context.get("responses")})
                continue
        if contract.get("contract_kind") == "record_conservation":
            emitted, observations = _audit_formula(contract, source_context)
        elif contract.get("contract_kind") == "journal_balance":
            emitted, observations = _audit_journal_balance(contract, source_context)
        elif contract.get("contract_kind") == "period_rollforward":
            emitted, observations = _audit_period_rollforward(contract, source_context)
        elif contract.get("contract_kind") == "inventory_reservation_balance":
            emitted, observations = _audit_inventory_reservation_balance(contract, source_context, dependent_context or {})
        elif contract.get("contract_kind") == "referential_integrity":
            emitted, observations = _audit_referential(contract, source_context, dependent_context or {})
        else:
            emitted, observations = _audit_side_effect(contract, source_context, dependent_context or {})
        findings.extend(emitted)
        executions.append({"contract_id": contract_id, "status": "executed", "contract_kind": contract.get("contract_kind"), "source_complete": source_context.get("complete"), "source_total": source_context.get("total"), "fetched_source_rows": len(source_context.get("records") or []), "dependent_complete": dependent_context.get("complete") if dependent_context else None, "dependent_total": dependent_context.get("total") if dependent_context else None, "fetched_dependent_rows": len((dependent_context or {}).get("records") or []), "finding_count": len(emitted), "observations": observations})

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    # Runs AFTER all heuristic contracts to find cross-contract, semantic bugs
    # that regex field matching cannot detect (e.g. "refund amount exceeds
    # payment amount even though both passed individual validation").
    llm_findings_count = 0
    if mode == "safe_live" and findings:
        try:
            # Build context for LLM: PRD text, API schema, heuristic findings,
            # and redacted data samples (never raw business rows).
            project_paths = config_paths(project, root)
            prd_text = _read_text(project_paths["input_dir"] / "prd.md")
            api_spec_path = project_paths["workspace_dir"] / "normalized_openapi.json"
            if not api_spec_path.exists():
                api_spec_path = project_paths["input_dir"] / "openapi.json"
            api_schema = _read_text(api_spec_path)

            # Redact observed data — only field names, types, and value ranges
            observed_samples: list[dict[str, Any]] = []
            seen_paths: set[str] = set()
            for ex in executions:
                cid = ex.get("contract_id", "")
                # Find matching profile contract to get the source path
                for pc in (profile.get("contracts") or []):
                    if pc.get("contract_id") == cid:
                        source_path = str((pc.get("source") or {}).get("path") or "")
                        if source_path and source_path not in seen_paths:
                            seen_paths.add(source_path)
                            # Look up cached context for this path
                            for cache_key, ctx in cache.items():
                                if source_path in str(cache_key):
                                    sample = {
                                        "path": source_path,
                                        "record_count": len(ctx.get("records") or []),
                                        "field_names": sorted(list((ctx.get("records") or [{}])[0].keys())[:15]) if ctx.get("records") else [],
                                    }
                                    observed_samples.append(sample)
                                    break
                        break

            llm_context = {
                "prd_text": prd_text[:6000],
                "api_schema": api_schema[:8000],
                "observed_data": json.dumps(observed_samples, ensure_ascii=False, default=str)[:6000],
                "heuristic_findings": json.dumps(findings[:30], ensure_ascii=False, default=str)[:6000],
            }

            llm_result = _llm_reason("causality", llm_context)
            if llm_result and llm_result.get("findings"):
                for llm_finding in llm_result["findings"]:
                    if not isinstance(llm_finding, dict):
                        continue
                    # Convert LLM finding to QualiBug finding format
                    hypothesis = {
                        "severity": str(llm_finding.get("severity", "P1"))[:4],
                        "business_causality_type": "llm_semantic_" + str(llm_finding.get("rule", "unknown")),
                        "title": str(llm_finding.get("title", ""))[:300],
                        "expected": str(llm_finding.get("expected", ""))[:500],
                        "observed": str(llm_finding.get("observed", ""))[:500],
                        "confidence": float(llm_finding.get("confidence", 0.5)),
                        "source": "llm_reasoning",
                        "engine_version": "phase65_evidence_first",
                        "status": "unverified_hypothesis",
                        "requires_deterministic_replay": True,
                        "false_positive_risk": str(llm_finding.get("false_positive_risk", ""))[:300],
                    }
                    semantic_hypotheses.append(_redact(hypothesis))
                    llm_findings_count += 1
                observations.append({"llm_reasoning": {"status": "completed", "semantic_hypotheses_added": llm_findings_count}})
        except Exception:
            # LLM reasoning is best-effort — never block the heuristic path
            observations.append({"llm_reasoning": {"status": "unavailable", "note": "LLM not configured or call failed"}})

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase51_business_causality_conservation",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "capability_version": "phase70_inventory_reservation_conservation",
        "summary": {**(profile.get("summary") or {}), "execution_mode": mode, "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"), "blocked_contract_count": sum(1 for item in executions if item.get("status") == "blocked_by_safety_boundary"), "business_causality_finding_count": len(findings), "semantic_hypothesis_count": len(semantic_hypotheses), "persistent_business_causality_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")), "memory_fingerprint_count": len((registry or {}).get("entries") or {})},
        "profile": profile,
        "executions": executions,
        "findings": findings,
        "semantic_hypotheses": semantic_hypotheses,
        "readiness_findings": readiness_findings,
        "safety": safety,
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "同一副作用/守恒反例跨运行持续出现时提高置信度；仍需要人工确认后才能进入企业知识回灌。"},
        "governance": {"execution_mode": mode, "live_requests_limited_to_get": True, "shared_safety_boundary_required_for_live_execution": True, "write_execution_disabled": True, "side_effect_replay_is_sandbox_required": True, "evidence_uses_hashed_identities": True, "raw_business_rows_not_persisted": True, "llm_output_is_unverified_hypothesis_only": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "business_causality_run.json", result)
    _write_json(output["workspace"] / "business_causality_run.json", result)
    (output["out"] / "business_causality_run_report.html").write_text(render_business_causality_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#07111d;color:#eaf2ff;margin:0;padding:28px}}.hero,.panel{{background:#101d2c;border:1px solid #2b4260;border-radius:16px;padding:20px;margin-bottom:16px}}.badge{{display:inline-block;background:#174e52;color:#b6fff4;border-radius:999px;padding:4px 10px;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{background:#132638;border:1px solid #2b4260;border-radius:12px;padding:12px}}.card b{{display:block;font-size:24px;margin-top:5px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #2b4260;text-align:left;vertical-align:top;word-break:break-word}}th{{color:#9dc4ee}}</style><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></html>"""


def render_business_causality_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [("因果契约", summary.get("causality_contract_count", 0)), ("副作用", summary.get("side_effect_contract_count", 0)), ("可追溯关系", summary.get("referential_contract_count", 0)), ("金额守恒", summary.get("conservation_contract_count", 0)), ("库存预占", summary.get("inventory_reservation_contract_count", 0))])
    rows = "".join(f"<tr><td>{_html_escape(item.get('contract_id'))}</td><td>{_html_escape(item.get('contract_kind'))}</td><td>{_html_escape(item.get('resource'))}</td><td>{_html_escape((item.get('source') or {}).get('path'))}</td><td>{_html_escape((item.get('dependent') or {}).get('path') or '-')}</td><td>{_html_escape(item.get('discovery'))}</td></tr>" for item in (data.get("contracts") or [])[:160])
    return _render_html("Phase51 业务副作用与守恒画像", "GET-only · 关系取证", "从 PRD、接口和真实数据关系中推导“动作必须产生什么结果、金额必须如何守恒”的只读 Oracle。", cards, f"<h2>可执行契约</h2><table><thead><tr><th>ID</th><th>类型</th><th>资源</th><th>主实体</th><th>副作用</th><th>推导来源</th></tr></thead><tbody>{rows or '<tr><td colspan=6>暂无可推导契约；可在 business_causality_conservation.contracts 显式配置。</td></tr>'}</tbody></table>")


def render_business_causality_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [("已执行", summary.get("executed_contract_count", 0)), ("发现问题", summary.get("business_causality_finding_count", 0)), ("稳定复现", summary.get("persistent_business_causality_count", 0)), ("证据指纹", summary.get("memory_fingerprint_count", 0))])
    rows = "".join(f"<tr><td>{_html_escape(item.get('severity'))}</td><td>{_html_escape(item.get('business_causality_type'))}</td><td>{_html_escape(item.get('title'))}</td><td>{_html_escape(item.get('actual'))}</td><td>{_html_escape((item.get('evidence_stability') or {}).get('observations', 1))}</td></tr>" for item in (data.get("findings") or [])[:160])
    return _render_html("Phase51 业务副作用与守恒运行报告", str(summary.get("execution_mode") or "plan_only"), "只读验证主单、支付、库存预占、审批、台账等副作用的完整性、幂等性、可追溯性和守恒关系。", cards, f"<h2>已证伪关系</h2><table><thead><tr><th>级别</th><th>类型</th><th>问题</th><th>实际</th><th>观测次数</th></tr></thead><tbody>{rows or '<tr><td colspan=5>未发现已证伪的业务因果关系</td></tr>'}</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase51 business causality and conservation reasoning")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", default="plan_only", choices=["plan_only", "safe_live"])
    args = parser.parse_args(argv)
    result = run_business_causality_conservation(args.project, options={"execution_mode": args.mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
