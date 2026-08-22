"""Bind doc-less authorization contracts into executable obligations.

Consumes ``authorization_formal_contracts`` (emitted by
``runtime_probe_contract_derivation`` for an unfamiliar, doc-less system) and
produces single-arm ``authorization`` obligations on the
``runtime_auth_decision_consistency`` protocol.

The obligation RE-VERIFIES the observed non-deterministic auth decision under a
controlled repeat of the anonymous read — it never carries the probe's raw
samples as the verdict (the re-issue is the Oracle), and it never asserts what
the endpoint "should" be (原则6). For a stranger system with an empty IR, the
binder synthesizes the read-only operation and the ``anonymous`` actor so the
obligation compiles and executes (原则 10: wired into the main chain, no fork).
"""
from __future__ import annotations

import hashlib
from typing import Any

from .runtime_auth_decision_surface import (
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
)
from .test_obligation import make_obligation

_SAFE_METHODS = frozenset({"GET", "HEAD"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _operation_id(method: str, path: str) -> str:
    raw = "|".join(["runtime_auth_decision", method.upper(), path])
    return "rtauth_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _ensure_operation(behavior_ir: dict[str, Any], method: str, path: str) -> str:
    """Return an operation id for (method, path), creating the op if absent.

    For a stranger system the IR may have no operation at all; the binder owns
    the synthetic, read-only op so the obligation compiles and the protocol can
    address it. No business/industry vocabulary is added (原则6).
    """
    ir = _dict(behavior_ir)
    ops = _list(ir.get("operations"))
    op_id = _operation_id(method, path)
    for op in ops:
        if not isinstance(op, dict):
            continue
        if _text(op.get("id")) == op_id:
            return op_id
    ir["operations"] = [
        *ops,
        {
            "id": op_id,
            "method": method.upper(),
            "path": path,
            "raw_path": path,
            "summary": f"doc-less auth decision probe: {method.upper()} {path}",
            "read_only": True,
            "source": "runtime_probe_contract_derivation",
        },
    ]
    return op_id


def _ensure_anonymous_actor(behavior_ir: dict[str, Any]) -> None:
    ir = _dict(behavior_ir)
    actors = _list(ir.get("actors"))
    if any(
        isinstance(actor, dict) and _text(actor.get("id")) == "anonymous"
        for actor in actors
    ):
        return
    ir["actors"] = [
        *actors,
        {
            "id": "anonymous",
            "name": "anonymous",
            "role": "anonymous",
            "account_status": "active",
            "credential_secret_ref": "",
        },
    ]


def build_runtime_auth_decision_obligations(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any] | None = None,
    *,
    max_obligations: int = 20,
) -> list[dict[str, Any]]:
    """Generate single-arm authorization obligations from doc-less contracts.

    Reads ``authorization_formal_contracts`` from ``asset``; mutates
    ``behavior_ir`` to ensure the read-only operation and the ``anonymous`` actor
    exist. Returns obligation dicts (may be empty).

    Honesty guard (原则 7): only contracts already emitted by the derivation
    layer — which required >=5 repeated anonymous samples showing BOTH 2xx and
    401/403 — become obligations. No synthetic contract is ever invented here.
    """
    contracts = _list(_dict(asset).get("authorization_formal_contracts"))
    obligations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        method = _text(contract.get("method")).upper()
        path = _text(contract.get("operation_path"))
        if method not in _SAFE_METHODS or not path.startswith("/"):
            continue
        inconsistency = _text(contract.get("inconsistency"))
        if inconsistency != "auth_decision_non_deterministic":
            # Only the honest doc-less signal is actionable; anything else is
            # skipped, never guessed.
            continue
        op_id = _ensure_operation(behavior_ir, method, path)
        _ensure_anonymous_actor(behavior_ir)
        obligation_id = "obl_rtauth_" + _text(contract.get("contract_id") or "")
        if not obligation_id or obligation_id in seen:
            obligation_id = "obl_rtauth_" + _operation_id(method, path)
        if obligation_id in seen:
            continue
        seen.add(obligation_id)
        if len(obligations) >= max_obligations:
            break
        sample_count = int(contract.get("sample_count") or 0)
        obligations.append(make_obligation(
            risk_family=RISK_FAMILY,
            subject_refs=[op_id, "anonymous"],
            property_spec={
                "template": PROTOCOL_TEMPLATE,
                "operation_ref": op_id,
                "operation_path_prefix": path.rsplit("/", 1)[0] or "/",
                "expression": {
                    "kind": "authorization",
                    "operator": "must_hold",
                    "operands": [],
                    "raw": (
                        "匿名重复读取的鉴权决策必须一致：同一只读端点在匿名下既返回 "
                        "2xx 又返回 401/403 视为鉴权执行非确定性缺陷"
                    ),
                },
                "description": (
                    "匿名重复读取的鉴权决策必须一致（doc-less 派生信号，仅断言不一致，"
                    "不断言该端点应受保护或公开）"
                ),
                "auth_contract": {
                    "contract_id": _text(contract.get("contract_id")),
                    "method": method,
                    "operation_path": path,
                    "observed_statuses": list(_list(contract.get("observed_statuses"))),
                    "sample_count": sample_count,
                },
                "rejection_expected": False,
                "doc_less_derived": True,
            },
            required_actors=["anonymous"],
            required_operations=[op_id],
            required_observers=[OBSERVER_ID],
            cleanup_requirement={"required": False, "mode": "not_required_read"},
            source_refs=[
                dict(row)
                for row in _list(contract.get("source_refs"))
                if isinstance(row, dict)
            ] or [{
                "source_id": "runtime_probe",
                "locator": f"{method}:{path}",
                "kind": "runtime_probe_observation",
            }],
            confidence=0.8,
            obligation_id=obligation_id,
        ))
    return obligations
