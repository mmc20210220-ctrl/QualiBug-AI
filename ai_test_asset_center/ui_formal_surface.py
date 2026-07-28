"""Source-bound formal UI observation and delivery chain.

This module turns explicit browser contracts into the same authority chain used by
API and persistence experiments:

    source contract -> browser execution fact -> typed observer receipt
    -> typed assertion -> Contract Oracle -> reproduction receipt -> Delivery Gate

It deliberately does not implement visual guessing. The first supported contracts
judge only facts the browser runtime already records deterministically:

* page reachability;
* exact/contained page title;
* maximum console error count;
* maximum failed-network count;
* maximum page duration.

Every expected value and every page path must be source-declared. Missing or
ambiguous evidence is INDETERMINATE, never a violation. Provider-produced finding
dictionaries are not consumed here and therefore cannot bypass formal authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
from .contract_oracles import build_contract_evidence_receipt, evaluate_contract_oracle
from .customer_delivery_gate_v2 import (
    DeliveryGateV2Error,
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
)
from .discovery_mainline_contract import validate_mainline_run_contract
from .observer_contracts_base import (
    _receipt,
    observe_experiment_requirements,
    register_observer,
    registered_observer_ids,
)
from .operational_receipts import build_execution_operational_receipt_from_counts
from .target_policy import is_nonproduction_environment


OBSERVER_ID = "ui_contract_observer"
ASSERTION_KIND = "ui_contract_expectation"
EVIDENCE_KEY = "ui_contract_observation"
FORMAL_UI_SCHEMA = "qualibug.formal-ui-contracts.v1"
_SUPPORTED_KINDS = frozenset({
    "page_reachable",
    "title_equals",
    "title_contains",
    "console_error_count_max",
    "network_error_count_max",
    "duration_ms_max",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_id(value: Any, default: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", _text(value)).strip("._")
    return token or default


def _sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_refs(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in _list(value) if isinstance(item, dict)]


def _criterion(contract: dict[str, Any]) -> dict[str, Any]:
    raw = _dict(contract.get("success_criteria") or contract.get("criterion"))
    kind = _text(raw.get("kind") or raw.get("type")).lower()
    if kind not in _SUPPORTED_KINDS:
        return {}
    if "expected" not in raw:
        return {}
    return {"kind": kind, "expected": raw.get("expected")}


def _contract_path(contract: dict[str, Any]) -> str:
    path = _text(contract.get("path") or contract.get("page_path"))
    if not path:
        start_url = _text(contract.get("start_url") or contract.get("url"))
        if start_url:
            parsed = urlparse(start_url)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
    if not path:
        return ""
    if not path.startswith("/"):
        path = "/" + path
    return path


def normalize_ui_formal_contracts(value: Any) -> list[dict[str, Any]]:
    """Validate the source-authoritative subset; invalid rows stay visible as blockers."""
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value), start=1):
        raw = _dict(item)
        contract_id = _safe_id(
            raw.get("contract_id") or raw.get("request_id") or raw.get("id"),
            f"ui_contract_{index}",
        )
        source_refs = _source_refs(raw.get("source_refs"))
        criterion = _criterion(raw)
        path = _contract_path(raw)
        actor_ref = _text(raw.get("actor_ref"))
        blockers: list[str] = []
        if not source_refs:
            blockers.append("UI_SOURCE_REFS_MISSING")
        if not criterion:
            blockers.append("UI_SUCCESS_CRITERION_INVALID")
        if not path:
            blockers.append("UI_PAGE_PATH_MISSING")
        if not actor_ref:
            blockers.append("UI_ACTOR_REF_MISSING")
        mode = _text(raw.get("execution_mode") or "safe_read_only")
        if mode != "safe_read_only":
            # Interactive UI writes need a cleanup/restoration contract before they can
            # become formal findings. The first UI surface is intentionally read-only.
            blockers.append("UI_FORMAL_WRITE_CLEANUP_NOT_IMPLEMENTED")
        rows.append({
            "contract_id": contract_id,
            "title": _text(raw.get("title") or contract_id)[:200],
            "path": path,
            "actor_ref": actor_ref,
            "severity": _text(raw.get("severity") or "P2")[:20],
            "source_refs": source_refs,
            "success_criteria": criterion,
            "execution_mode": mode,
            "blockers": sorted(set(blockers)),
        })
    return rows


def formal_ui_paths(value: Any) -> list[str]:
    """Unique source-declared paths for the browser smoke executor."""
    return list(dict.fromkeys(
        row["path"]
        for row in normalize_ui_formal_contracts(value)
        if row.get("path")
    ))


def _matching_page(report: dict[str, Any], path: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for item in _list(_dict(report).get("pages")):
        page = _dict(item)
        page_url = _text(page.get("url"))
        parsed = urlparse(page_url)
        observed_path = parsed.path or "/"
        if parsed.query:
            observed_path += "?" + parsed.query
        if observed_path == path:
            candidates.append(page)
    return dict(candidates[0]) if len(candidates) == 1 else {}


def _ui_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    observations = _dict(envelope.get("observations"))
    report = _dict(observations.get("browser_ui_report"))
    contract = _dict(observations.get("ui_formal_contract"))
    path = _text(contract.get("path"))
    base = {
        EVIDENCE_KEY: {
            "contract_id": _text(contract.get("contract_id")),
            "path": path,
            "criterion_kind": _text(
                _dict(contract.get("success_criteria")).get("kind")
            ),
            "report_schema": _text(report.get("schema_version")),
        }
    }
    if report.get("enabled") is not True:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=_text(report.get("reason_code")) or "UI_BROWSER_NOT_ENABLED",
            evidence=base,
        )
    if _text(report.get("status")) == "error":
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=_text(report.get("reason_code")) or "UI_BROWSER_EXECUTION_FAILED",
            evidence=base,
        )
    page = _matching_page(report, path)
    if not page:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="UI_PAGE_OBSERVATION_AMBIGUOUS_OR_MISSING",
            evidence=base,
        )
    try:
        status_code = int(page.get("status_code") or 0)
        duration_ms = int(page.get("duration_ms") or 0)
    except (TypeError, ValueError):
        return _receipt(
            observer_id=OBSERVER_ID,
            status="FAILED",
            reason_code="UI_PAGE_OBSERVATION_INVALID",
            evidence=base,
        )
    console_errors = [dict(row) for row in _list(page.get("console_errors")) if isinstance(row, dict)]
    network_errors = [dict(row) for row in _list(page.get("network_errors")) if isinstance(row, dict)]
    normalized = {
        "contract_id": _text(contract.get("contract_id")),
        "path": path,
        "criterion_kind": _text(
            _dict(contract.get("success_criteria")).get("kind")
        ),
        "reachable": page.get("reachable") is True,
        "status_code": status_code,
        "title": _text(page.get("title"))[:300],
        "duration_ms": max(0, duration_ms),
        "console_error_count": len(console_errors),
        "network_error_count": len(network_errors),
        "console_error_fingerprints": [_sha256(row)[:20] for row in console_errors],
        "network_error_fingerprints": [_sha256(row)[:20] for row in network_errors],
        "screenshot_present": bool(_text(page.get("screenshot_path"))),
        "page_error_present": bool(_text(page.get("error"))),
    }
    base[EVIDENCE_KEY] = normalized
    # A browser exception with no HTTP response is harness ambiguity, not proof that the
    # application violated a reachability contract.
    if status_code <= 0:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="UI_PAGE_TRANSPORT_UNOBSERVED",
            evidence=base,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        evidence=base,
    )


def _ui_assertion_evaluator(envelope: dict[str, Any]) -> dict[str, Any]:
    spec = _dict(envelope.get("spec"))
    criterion = _dict(spec.get("success_criteria") or spec.get("criterion"))
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    kind = _text(criterion.get("kind")).lower()
    expected = criterion.get("expected")
    if kind not in _SUPPORTED_KINDS or "expected" not in criterion:
        return {
            "passed": None,
            "reason_code": "UI_SUCCESS_CRITERION_INVALID",
            "expected": criterion,
            "actual": observation,
        }
    if not observation:
        return {
            "passed": None,
            "reason_code": "UI_CONTRACT_OBSERVATION_MISSING",
            "expected": criterion,
            "actual": {},
        }

    actual: Any
    passed: bool | None
    if kind == "page_reachable":
        if expected is not True and expected is not False:
            passed = None
            actual = observation.get("reachable")
        else:
            actual = observation.get("reachable") is True
            passed = actual is expected
    elif kind in {"title_equals", "title_contains"}:
        expected_text = _text(expected)
        actual = _text(observation.get("title"))
        if not expected_text or observation.get("reachable") is not True or not actual:
            passed = None
        elif kind == "title_equals":
            passed = actual == expected_text
        else:
            passed = expected_text in actual
    else:
        field = {
            "console_error_count_max": "console_error_count",
            "network_error_count_max": "network_error_count",
            "duration_ms_max": "duration_ms",
        }[kind]
        try:
            limit = int(expected)
            actual = int(observation.get(field))
        except (TypeError, ValueError):
            passed = None
            actual = observation.get(field)
        else:
            passed = limit >= 0 and actual <= limit

    return {
        "passed": passed,
        "reason_code": "" if passed is not None else "UI_CRITERION_EVIDENCE_INDETERMINATE",
        "expected": criterion,
        "actual": {
            "value": actual,
            "path": _text(observation.get("path")),
            "status_code": int(observation.get("status_code") or 0),
        },
    }


def install_ui_formal_surface() -> dict[str, str]:
    """Install observer first, then its evidence-consuming assertion kind."""
    installed: dict[str, str] = {}
    if OBSERVER_ID not in set(registered_observer_ids()):
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface="rendered_view",
            adapter="ui_browser",
            handler=_ui_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_ui_assertion_evaluator,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["assertion"] = ASSERTION_KIND
    return installed


def _find_mainline_run(result: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        _dict(result.get("mainline_run")),
        _dict(_dict(result.get("v12")).get("mainline_run")),
        _dict(_dict(result.get("discovery_runtime")).get("mainline_run")),
    ]
    for candidate in candidates:
        if candidate:
            try:
                return dict(validate_mainline_run_contract(candidate))
            except Exception:
                continue
    return {}


def _finding(
    *,
    contract: dict[str, Any],
    observation: dict[str, Any],
    mainline_run: dict[str, Any],
    candidate_id: str,
    slice_id: str,
    obligation_id: str,
    experiment_id: str,
    execution_id: str,
    evidence_id: str,
) -> dict[str, Any]:
    finding_id = "finding_ui_" + _sha256({
        "contract_id": contract["contract_id"],
        "path": contract["path"],
        "criterion": contract["success_criteria"],
        "actual": observation,
        "execution_id": execution_id,
    })[:24]
    return {
        "finding_id": finding_id,
        "campaign_id": _text(mainline_run.get("campaign_id")),
        "candidate_id": candidate_id,
        "slice_id": slice_id,
        "obligation_id": obligation_id,
        "experiment_id": experiment_id,
        "execution_id": execution_id,
        "evidence_id": evidence_id,
        "mainline_run": dict(mainline_run),
        "title": contract["title"],
        "category": "ui_contract_violation",
        "risk_family": "ui_contract",
        "surface": "UI",
        "severity": contract["severity"],
        "expected": dict(contract["success_criteria"]),
        "actual": {
            "path": observation.get("path"),
            "reachable": observation.get("reachable"),
            "status_code": observation.get("status_code"),
            "title": observation.get("title"),
            "console_error_count": observation.get("console_error_count"),
            "network_error_count": observation.get("network_error_count"),
            "duration_ms": observation.get("duration_ms"),
        },
        "source_refs": [dict(row) for row in contract["source_refs"]],
    }


def _blocked(contract: dict[str, Any], *reasons: str) -> dict[str, Any]:
    return {
        "contract_id": _text(contract.get("contract_id")),
        "status": "BLOCKED",
        "reason_codes": sorted(set(_text(reason) for reason in reasons if _text(reason))),
        "finding": None,
    }


def formalize_browser_ui_contracts(
    result: dict[str, Any],
    *,
    browser_ui_report: dict[str, Any],
    contracts: Any,
    runtime_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate explicit UI contracts and append only Delivery-Gate-approved findings."""
    install_ui_formal_surface()
    updated = dict(result or {})
    normalized = normalize_ui_formal_contracts(contracts)
    runtime = _dict(runtime_contract) or _dict(updated.get("runtime_contract"))
    declared_adapters = {_text(value) for value in _list(runtime.get("declared_adapters"))}
    mainline = _find_mainline_run(updated)
    outcomes: list[dict[str, Any]] = []
    deliverable_findings: list[dict[str, Any]] = []

    for contract in normalized:
        blockers = list(contract.get("blockers") or [])
        if "ui_browser" not in declared_adapters:
            blockers.append("UI_BROWSER_ADAPTER_NOT_DECLARED")
        if not mainline:
            blockers.append("UI_MAINLINE_RUN_MISSING_OR_INVALID")
        environment_id = _text(mainline.get("environment_id")) if mainline else ""
        if not environment_id or not is_nonproduction_environment(environment_id):
            blockers.append("UI_NONPRODUCTION_ENVIRONMENT_REQUIRED")
        if blockers:
            outcomes.append(_blocked(contract, *blockers))
            continue

        contract_id = contract["contract_id"]
        run_id = _text(mainline.get("run_id"))
        campaign_id = _text(mainline.get("campaign_id"))
        execution_id = "ui_exec_" + _sha256({"run": run_id, "contract": contract_id})[:20]
        obligation_id = "ui_obl_" + _sha256({"contract": contract_id, "sources": contract["source_refs"]})[:20]
        experiment_id = "ui_exp_" + _sha256({"obligation": obligation_id, "path": contract["path"]})[:20]
        candidate_id = "ui_candidate_" + _sha256(contract_id)[:16]
        slice_id = "ui_slice_" + _sha256({"path": contract["path"]})[:16]
        evidence_id = "ui_evidence_" + _sha256({"execution": execution_id})[:16]
        step_id = "treatment_ui_page"
        operation_ref = "ui_get_" + _sha256(contract["path"])[:16]
        criterion = dict(contract["success_criteria"])

        experiment = {
            "experiment_id": experiment_id,
            "obligation_id": obligation_id,
            "campaign_id": campaign_id,
            "execution_id": execution_id,
            "risk_family": "ui_contract",
            "source_refs": [dict(row) for row in contract["source_refs"]],
            "control_plan": [],
            "treatment_plan": [{
                "step_id": step_id,
                "actor_ref": contract["actor_ref"],
                "operation_ref": operation_ref,
                "intent": "source_declared_ui_observation",
                "protocol_step": "ui_page_read",
            }],
            "cleanup_plan": [],
            "observers": [{
                "observer_id": OBSERVER_ID,
                "surface": "rendered_view",
                "adapter": "ui_browser",
                "receipt_schema": "qualibug.observer-receipt.v1",
                "required_status": "OBSERVED",
            }],
            "assertions": [{
                "assertion_id": "ui_assert_" + _sha256(contract_id)[:20],
                "kind": ASSERTION_KIND,
                "success_criteria": criterion,
                "invariant_ref": contract_id,
            }],
        }
        observations: dict[str, Any] = {
            "campaign_id": campaign_id,
            "execution_id": execution_id,
            "browser_ui_report": dict(browser_ui_report or {}),
            "ui_formal_contract": dict(contract),
        }
        observer_receipts = observe_experiment_requirements(
            experiment,
            observations=observations,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
        ui_observer = next(
            (row for row in observer_receipts if _text(row.get("observer_id")) == OBSERVER_ID),
            {},
        )
        ui_observation = _dict(_dict(ui_observer.get("evidence")).get(EVIDENCE_KEY))
        status_code = int(ui_observation.get("status_code") or 0)

        request_body_fp = _sha256(None)
        mutation_class = "ui_observation"
        mutation_selector = _text(criterion.get("kind"))
        mutation_operator = "expect"
        request_semantics_fp = _sha256({
            "operation_ref": operation_ref,
            "method": "GET",
            "path_template": contract["path"],
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "request_body_fingerprint": request_body_fp,
        })
        treatment_receipt = build_contract_evidence_receipt(
            kind="treatment",
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id=campaign_id,
            execution_id=execution_id,
            subject_id=step_id,
            status="OBSERVED" if status_code > 0 else "BLOCKED",
            evidence={
                "response_observed": status_code > 0,
                "status_code": status_code,
                "path_template": contract["path"],
                "request_body_fingerprint": request_body_fp,
                "request_semantics_fingerprint": request_semantics_fp,
                "mutation_class": mutation_class,
                "mutation_selector": mutation_selector,
                "mutation_operator": mutation_operator,
            },
        )
        actor_receipt = build_contract_evidence_receipt(
            kind="actor",
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id=campaign_id,
            execution_id=execution_id,
            subject_id=contract["actor_ref"],
            status="OBSERVED",
            evidence={"actor_ref_fingerprint": _sha256(contract["actor_ref"])[:20]},
        )
        contract_receipts = [actor_receipt, treatment_receipt]
        oracle_evidence = {
            **observations,
            EVIDENCE_KEY: dict(ui_observation),
            "observer_receipts": [dict(row) for row in observer_receipts],
            "contract_evidence_receipts": [dict(row) for row in contract_receipts],
        }
        oracle_receipt = evaluate_contract_oracle(
            experiment=experiment,
            evidence=oracle_evidence,
        )
        operational_receipt = build_execution_operational_receipt_from_counts(
            receipt_id="operational_" + _sha256(execution_id)[:24],
            execution_status="EXECUTED" if status_code > 0 else "BLOCKED",
            scenario_attempt_count=1,
            http_request_attempt_count=1 if status_code > 0 else 0,
            write_request_attempt_count=0,
            production_http_request_count=0,
            accepted_non_cleanup_write_count=0,
            accepted_cleanup_write_count=0,
            cleanup_status="NOT_REQUIRED",
            cleanup_attempted_count=0,
            cleanup_completed_count=0,
            cleanup_failure_count=0,
        )
        observation_ids = [
            _text(row.get("receipt_id"))
            for row in [*contract_receipts, *observer_receipts]
            if _text(row.get("receipt_id"))
        ]
        try:
            execution_receipt = build_delivery_execution_receipt(
                mainline_run=mainline,
                candidate_id=candidate_id,
                slice_id=slice_id,
                obligation_id=obligation_id,
                experiment_id=experiment_id,
                execution_id=execution_id,
                evidence_id=evidence_id,
                operational_receipt=operational_receipt,
                observation_receipt_ids=observation_ids,
                oracle_receipt_id=_text(oracle_receipt.get("receipt_id")),
                elapsed_ms=int(ui_observation.get("duration_ms") or 0),
                cost_coverage_status="MEASURED",
            )
        except DeliveryGateV2Error as exc:
            outcomes.append(_blocked(contract, f"UI_DELIVERY_EXECUTION_RECEIPT_FAILED:{exc}"))
            continue

        reproduction_step = {
            "phase": "treatment",
            "step_id": step_id,
            "actor_ref": contract["actor_ref"],
            "operation_ref": operation_ref,
            "method": "GET",
            "path": contract["path"],
            "path_template": contract["path"],
            "status_code": status_code,
            "observation_receipt_id": _text(ui_observer.get("receipt_id")),
            "request_body_fingerprint": request_body_fp,
            "request_semantics_fingerprint": request_semantics_fp,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "body": {
                "reachable": ui_observation.get("reachable"),
                "title": ui_observation.get("title"),
                "console_error_count": ui_observation.get("console_error_count"),
                "network_error_count": ui_observation.get("network_error_count"),
                "duration_ms": ui_observation.get("duration_ms"),
            },
        }
        try:
            reproduction_receipt = build_reproduction_receipt(
                execution_receipt=execution_receipt,
                steps=[reproduction_step],
                oracle_receipt=oracle_receipt,
                source_refs=contract["source_refs"],
            )
        except DeliveryGateV2Error as exc:
            outcomes.append(_blocked(contract, f"UI_REPRODUCTION_RECEIPT_FAILED:{exc}"))
            continue

        finding = None
        if _text(oracle_receipt.get("status")) == "VIOLATION":
            finding = _finding(
                contract=contract,
                observation=ui_observation,
                mainline_run=mainline,
                candidate_id=candidate_id,
                slice_id=slice_id,
                obligation_id=obligation_id,
                experiment_id=experiment_id,
                execution_id=execution_id,
                evidence_id=evidence_id,
            )
        try:
            gate = build_customer_delivery_gate_receipt_v2(
                finding=finding,
                execution_receipt=execution_receipt,
                contract_evidence_receipts=contract_receipts,
                observer_receipts=observer_receipts,
                oracle_receipt=oracle_receipt,
                reproduction_receipt=reproduction_receipt,
            )
        except DeliveryGateV2Error as exc:
            outcomes.append(_blocked(contract, f"UI_DELIVERY_GATE_FAILED:{exc}"))
            continue

        deliverable = _text(gate.get("status")) == "DELIVERABLE" and isinstance(finding, dict)
        if deliverable:
            finding = {
                **finding,
                "gate_passed": True,
                "customer_delivery_status": "defect",
                "customer_visible": True,
                "delivery_gate_receipt_id": _text(gate.get("gate_receipt_id")),
                "observer_receipt_ids": [
                    _text(row.get("receipt_id")) for row in observer_receipts
                ],
                "oracle_receipt_id": _text(oracle_receipt.get("receipt_id")),
                "reproduction_receipt_id": _text(reproduction_receipt.get("receipt_id")),
            }
            deliverable_findings.append(finding)
        outcomes.append({
            "contract_id": contract_id,
            "status": _text(gate.get("status")),
            "reason_codes": list(gate.get("reason_codes") or []),
            "observer_receipts": [dict(row) for row in observer_receipts],
            "oracle_receipt": dict(oracle_receipt),
            "reproduction_receipt": dict(reproduction_receipt),
            "delivery_gate_receipt": dict(gate),
            "finding": finding if deliverable else None,
        })

    existing_findings = [
        dict(row) for row in _list(updated.get("findings")) if isinstance(row, dict)
    ]
    existing_ui = [
        dict(row) for row in _list(updated.get("ui_findings")) if isinstance(row, dict)
    ]
    known_ids = {_text(row.get("finding_id") or row.get("id")) for row in existing_findings}
    appended = [
        row for row in deliverable_findings
        if _text(row.get("finding_id")) not in known_ids
    ]
    updated["findings"] = [*existing_findings, *appended]
    updated["ui_findings"] = [
        *existing_ui,
        *[
            row for row in appended
            if _text(row.get("finding_id")) not in {
                _text(item.get("finding_id") or item.get("id")) for item in existing_ui
            }
        ],
    ]
    updated["formal_ui_contracts"] = {
        "schema_version": FORMAL_UI_SCHEMA,
        "requested": len(normalized),
        "evaluated": len(outcomes),
        "deliverable_count": len(appended),
        "blocked_count": sum(1 for row in outcomes if row.get("status") == "BLOCKED"),
        "rejected_count": sum(1 for row in outcomes if row.get("status") == "REJECTED"),
        "outcomes": outcomes,
        "provider_findings_promoted": 0,
    }
    return updated


__all__ = [
    "ASSERTION_KIND",
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "formal_ui_paths",
    "formalize_browser_ui_contracts",
    "install_ui_formal_surface",
    "normalize_ui_formal_contracts",
]
