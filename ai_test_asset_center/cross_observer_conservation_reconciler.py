from __future__ import annotations

"""Phase92S: cross-observer conservation reconciler.

Phase92R creates a joined before/after observer graph.  Phase92S reconciles the
numeric deltas visible in that graph: inventory/account state changes should be
explained by ledger/transaction/history projections when those observers are
available.  This is not a static rule oracle; it is a runtime evidence pack built
from observed before/after state.
"""

import re
from typing import Any

INVENTORY_STATE_FIELD_RE = re.compile(r"(?:stock|inventory|available|on_hand|remaining|库存|可用)", re.I)
ACCOUNT_STATE_FIELD_RE = re.compile(r"(?:balance|wallet|quota|credit|limit|points|余额|额度|积分)", re.I)
AMOUNT_STATE_FIELD_RE = re.compile(r"(?:amount|total|paid|payable|refund|金额|总额|支付|退款)", re.I)
LEDGER_KIND_RE = re.compile(r"(?:ledger|journal|transaction|流水|账|明细)", re.I)
HISTORY_KIND_RE = re.compile(r"(?:history|audit|event|workflow|approval|历史|审计|事件|审批)", re.I)
QUANTITY_DELTA_FIELD_RE = re.compile(r"(?:quantity|qty|stock|inventory|delta|change|count|数量|库存)", re.I)
AMOUNT_DELTA_FIELD_RE = re.compile(r"(?:amount|balance|money|fee|refund|paid|total|delta|change|金额|余额|退款|支付)", re.I)
VOLATILE_PATH_RE = re.compile(r"(?:trace|request|duration|timestamp|time|date|nonce|random|签名|日志|log)", re.I)


def _flatten(value: Any, prefix: str = "", *, max_items: int = 50) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(child, path, max_items=max_items))
        return out
    if isinstance(value, list):
        out[prefix + ".__len__" if prefix else "__len__"] = len(value)
        for idx, child in enumerate(value[:max_items]):
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            out.update(_flatten(child, path, max_items=max_items))
        return out
    out[prefix or "$"] = value
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _records(joined_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(joined_payload, dict):
        return []
    return [r for r in (joined_payload.get("records") or []) if isinstance(r, dict)]


def _record_match_key(record: dict[str, Any]) -> str:
    return "|".join([
        str(record.get("entity_key") or ""),
        str(record.get("observer_kind") or ""),
        str(record.get("path") or ""),
        str(record.get("record_index") or 0),
    ])


def _state_category(path: str, kind: str) -> str:
    if LEDGER_KIND_RE.search(kind) or HISTORY_KIND_RE.search(kind):
        return ""
    if INVENTORY_STATE_FIELD_RE.search(path) or "inventory" in kind or "stock" in kind:
        return "inventory"
    if ACCOUNT_STATE_FIELD_RE.search(path) or "account" in kind or "balance" in kind:
        return "account"
    if AMOUNT_STATE_FIELD_RE.search(path):
        return "amount"
    return ""


def _ledger_category(path: str) -> str:
    if QUANTITY_DELTA_FIELD_RE.search(path):
        return "inventory"
    if AMOUNT_DELTA_FIELD_RE.search(path):
        return "account_or_amount"
    return ""


def _state_deltas(before_payload: dict[str, Any], after_payload: dict[str, Any]) -> list[dict[str, Any]]:
    before = {_record_match_key(r): r for r in _records(before_payload)}
    after = {_record_match_key(r): r for r in _records(after_payload)}
    deltas: list[dict[str, Any]] = []
    for key in sorted(set(before) & set(after)):
        b = before[key]
        a = after[key]
        kind = str(a.get("observer_kind") or b.get("observer_kind") or "")
        bf = b.get("fields") if isinstance(b.get("fields"), dict) else {}
        af = a.get("fields") if isinstance(a.get("fields"), dict) else {}
        fb = _flatten(bf)
        fa = _flatten(af)
        for path in sorted(set(fb) & set(fa)):
            if VOLATILE_PATH_RE.search(path):
                continue
            if not (_is_number(fb[path]) and _is_number(fa[path])):
                continue
            delta = float(fa[path]) - float(fb[path])
            if abs(delta) <= 1e-9:
                continue
            category = _state_category(path, kind)
            if not category:
                continue
            deltas.append({
                "category": category,
                "entity_key": a.get("entity_key") or b.get("entity_key"),
                "observer_kind": kind,
                "path": path,
                "before": fb[path],
                "after": fa[path],
                "delta": delta,
            })
    return deltas[:100]


def _ledger_added_deltas(before_payload: dict[str, Any], after_payload: dict[str, Any]) -> list[dict[str, Any]]:
    before_keys = {_record_match_key(r) for r in _records(before_payload)}
    added = [r for r in _records(after_payload) if _record_match_key(r) not in before_keys]
    deltas: list[dict[str, Any]] = []
    for record in added:
        kind = str(record.get("observer_kind") or "")
        if not (LEDGER_KIND_RE.search(kind) or HISTORY_KIND_RE.search(kind)):
            continue
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        for path, value in _flatten(fields).items():
            if not _is_number(value):
                continue
            category = _ledger_category(path)
            if not category:
                continue
            deltas.append({
                "category": category,
                "entity_key": record.get("entity_key"),
                "observer_kind": kind,
                "path": path,
                "delta": float(value),
                "value": value,
            })
    return deltas[:100]


def _sum_by_category(deltas: list[dict[str, Any]], category: str) -> float:
    return sum(float(d.get("delta") or 0.0) for d in deltas if d.get("category") == category)


def _sum_ledger_inventory(ledger_deltas: list[dict[str, Any]]) -> float:
    return sum(float(d.get("delta") or 0.0) for d in ledger_deltas if d.get("category") == "inventory")


def _sum_ledger_amount(ledger_deltas: list[dict[str, Any]]) -> float:
    return sum(float(d.get("delta") or 0.0) for d in ledger_deltas if d.get("category") in {"account_or_amount", "account", "amount"})


def _observer_kinds(payload: dict[str, Any]) -> set[str]:
    return {str(r.get("observer_kind") or "") for r in _records(payload) if r.get("observer_kind")}


def _snapshot_observer_kinds(snapshots: dict[str, Any]) -> set[str]:
    kinds: set[str] = set()
    for phase in ("before", "after"):
        raw = snapshots.get(phase) if isinstance(snapshots, dict) else []
        if isinstance(raw, dict):
            raw = [raw]
        for item in raw or []:
            if isinstance(item, dict) and item.get("observer_kind"):
                kinds.add(str(item.get("observer_kind")))
    return kinds


def reconcile_cross_observer_conservation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any], semantic_graph: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reconcile conservation evidence across the joined observer graph."""
    if str(probe.get("risk_type") or "") != "conservation_probe":
        return {"engine": "cross_observer_conservation_reconciler_v1_phase92s", "verdict": "not_applicable", "reason": "probe is not a conservation probe", "confidence": 0.0}

    graph = semantic_graph or {}
    if not graph or not isinstance(graph.get("joined_payloads"), dict):
        try:
            from .observer_response_semantic_joiner import join_snapshot_observer_responses

            graph = join_snapshot_observer_responses(snapshots)
        except Exception as exc:  # pragma: no cover
            return {"engine": "cross_observer_conservation_reconciler_v1_phase92s", "verdict": "undetermined", "reason": f"semantic graph unavailable: {type(exc).__name__}: {exc}", "confidence": 0.0}

    joined = graph.get("joined_payloads") if isinstance(graph.get("joined_payloads"), dict) else {}
    before_payload = joined.get("before") if isinstance(joined, dict) else {}
    after_payload = joined.get("after") if isinstance(joined, dict) else {}
    if not before_payload or not after_payload:
        return {"engine": "cross_observer_conservation_reconciler_v1_phase92s", "verdict": "undetermined", "reason": "before/after joined observer graph is unavailable", "confidence": 0.0, "semantic_graph_engine": graph.get("engine")}

    state_deltas = _state_deltas(before_payload, after_payload)
    ledger_deltas = _ledger_added_deltas(before_payload, after_payload)
    before_kinds = _observer_kinds(before_payload)
    after_kinds = _observer_kinds(after_payload)
    configured_kinds = set(graph.get("coverage") or []) | _snapshot_observer_kinds(snapshots)
    ledger_observed = any(LEDGER_KIND_RE.search(k) or HISTORY_KIND_RE.search(k) for k in before_kinds | after_kinds | configured_kinds)

    inventory_delta = _sum_by_category(state_deltas, "inventory")
    account_delta = _sum_by_category(state_deltas, "account") + _sum_by_category(state_deltas, "amount")
    ledger_inventory_delta = _sum_ledger_inventory(ledger_deltas)
    ledger_amount_delta = _sum_ledger_amount(ledger_deltas)
    failures: list[dict[str, Any]] = []
    passes: list[str] = []

    if abs(inventory_delta) > 1e-9 and abs(ledger_inventory_delta) > 1e-9:
        if abs(abs(inventory_delta) - abs(ledger_inventory_delta)) > 1e-6:
            failures.append({
                "kind": "inventory_delta_ledger_mismatch",
                "state_delta": inventory_delta,
                "ledger_delta": ledger_inventory_delta,
                "reason": "inventory/stock state delta is not reconciled by observed ledger quantity delta",
            })
        else:
            passes.append("inventory_delta_reconciled_by_ledger")
    elif abs(inventory_delta) > 1e-9 and ledger_observed:
        failures.append({
            "kind": "inventory_delta_without_ledger_entry",
            "state_delta": inventory_delta,
            "ledger_delta": ledger_inventory_delta,
            "reason": "inventory/stock changed while a ledger/history observer was available but no matching ledger delta was observed",
        })

    if abs(account_delta) > 1e-9 and abs(ledger_amount_delta) > 1e-9:
        if abs(abs(account_delta) - abs(ledger_amount_delta)) > 1e-6:
            failures.append({
                "kind": "account_amount_delta_ledger_mismatch",
                "state_delta": account_delta,
                "ledger_delta": ledger_amount_delta,
                "reason": "account/amount state delta is not reconciled by observed ledger amount delta",
            })
        else:
            passes.append("account_or_amount_delta_reconciled_by_ledger")
    elif abs(account_delta) > 1e-9 and ledger_observed:
        failures.append({
            "kind": "account_amount_delta_without_ledger_entry",
            "state_delta": account_delta,
            "ledger_delta": ledger_amount_delta,
            "reason": "account/amount changed while a ledger/history observer was available but no matching ledger delta was observed",
        })

    if failures:
        return {
            "engine": "cross_observer_conservation_reconciler_v1_phase92s",
            "verdict": "failed",
            "reason": failures[0]["reason"],
            "confidence": 0.88,
            "failures": failures,
            "state_deltas": state_deltas,
            "ledger_deltas": ledger_deltas,
            "semantic_graph_engine": graph.get("engine"),
        }
    if passes:
        return {
            "engine": "cross_observer_conservation_reconciler_v1_phase92s",
            "verdict": "passed",
            "reason": "observed cross-observer conservation deltas reconciled",
            "confidence": 0.72,
            "passes": passes,
            "state_deltas": state_deltas,
            "ledger_deltas": ledger_deltas,
            "semantic_graph_engine": graph.get("engine"),
        }
    if state_deltas or ledger_deltas:
        return {
            "engine": "cross_observer_conservation_reconciler_v1_phase92s",
            "verdict": "undetermined",
            "reason": "resource deltas were observed, but available observer coverage was insufficient for reconciliation",
            "confidence": 0.42,
            "state_deltas": state_deltas,
            "ledger_deltas": ledger_deltas,
            "semantic_graph_engine": graph.get("engine"),
        }
    return {
        "engine": "cross_observer_conservation_reconciler_v1_phase92s",
        "verdict": "no_observed_delta",
        "reason": "no cross-observer resource delta was observed",
        "confidence": 0.0,
        "state_deltas": [],
        "ledger_deltas": [],
        "semantic_graph_engine": graph.get("engine"),
    }
