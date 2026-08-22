"""Focused coordinator for one immutable discovery authority."""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .discovery_mainline_contract import (
    MainlineContractError,
    MainlineRunContract,
    build_mainline_run_contract,
    validate_mainline_run_contract,
)
from .implicit_rule_runtime_evolution import (
    project_implicit_rule_runtime_evolution,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryMainlineInputs:
    project: str
    root: Path
    prd_text: str
    api_spec_text: str
    db_schema_text: str
    approved_base_url: str
    campaign_context: dict[str, Any]
    existing_findings: Sequence[dict[str, Any]] = ()


@dataclass(frozen=True)
class DiscoveryPlanningBundle:
    mainline_run: MainlineRunContract
    behavior_ir: dict[str, Any]
    obligations: dict[str, Any]
    experiments: dict[str, Any]


CampaignBuilder = Callable[[DiscoveryMainlineInputs], Any]
PlanBuilder = Callable[[DiscoveryMainlineInputs, Any], Any]
MainlineRunner = Callable[[DiscoveryMainlineInputs, Any, Any], dict[str, Any]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source_snapshot_hash(context: dict[str, Any]) -> str:
    manifest = context.get("source_manifest")
    if not isinstance(manifest, dict):
        return ""
    return _text(manifest.get("source_hash"))


def _campaign_id(campaign: Any) -> str:
    value = (
        campaign.get("campaign_id")
        if isinstance(campaign, dict)
        else getattr(campaign, "campaign_id", "")
    )
    campaign_id = _text(value)
    if not campaign_id:
        raise MainlineContractError("mainline_campaign_identity_missing")
    return campaign_id


def _plan_contract(plan: Any) -> MainlineRunContract:
    value = (
        plan.get("mainline_run")
        if isinstance(plan, dict)
        else getattr(plan, "mainline_run", None)
    )
    return validate_mainline_run_contract(value)


def _public_planning_pack(pack: Any) -> dict[str, Any]:
    """Project a planning pack for product output.

    Underscore-prefixed keys are internal planning context (knowledge asset,
    documented operations, runtime actors, budgets). They feed in-process
    consumers that read the plan bundle directly and must never travel with
    the serialized product result (scan_result.json / campaign artifacts).
    """

    if not isinstance(pack, dict):
        return {}
    return {
        key: value for key, value in pack.items() if not str(key).startswith("_")
    }


def _plan_value(plan: Any, field: str) -> Any:
    return plan.get(field) if isinstance(plan, dict) else getattr(plan, field, None)


def _exception_stage_identity(
    contract: dict[str, Any],
    obligation_id: str,
) -> dict[str, str]:
    return {
        "obligation_id": obligation_id,
        "run_id": _text(contract.get("run_id")),
        "campaign_id": _text(contract.get("campaign_id")),
        "target_id": _text(contract.get("target_id")),
        "environment_id": _text(contract.get("environment_id")),
        "policy_version": _text(contract.get("policy_version")),
        "evaluation_mode": _text(contract.get("evaluation_mode")),
        "source_snapshot_hash": _text(contract.get("source_snapshot_hash")),
        "mainline_contract_fingerprint": _text(
            contract.get("contract_fingerprint")
        ),
    }


def _selected_rows_from_plan(plan: Any) -> list[dict[str, Any]]:
    experiments = _plan_value(plan, "experiments")
    if not isinstance(experiments, dict):
        return []
    obligation_plan = experiments.get("obligation_plan")
    if not isinstance(obligation_plan, dict):
        return []
    selected = obligation_plan.get("selected")
    if not isinstance(selected, list):
        return []
    return [dict(row) for row in selected if isinstance(row, dict)]


def _accounting_rows_from_plan(plan: Any) -> list[dict[str, Any]]:
    """Return formal accounting rows when the planning bundle exposes them.

    Exception auditing also supports the narrow compatibility plan used by older
    callers.  That plan has no formal-obligation bundle, so the selected rows are
    the only identities it can prove and no unobserved rows are invented.
    """

    obligations = _plan_value(plan, "obligations")
    experiments = _plan_value(plan, "experiments")
    if not isinstance(obligations, dict) or not isinstance(experiments, dict):
        return _selected_rows_from_plan(plan)
    raw_obligations = obligations.get("obligations")
    if not isinstance(raw_obligations, list):
        return _selected_rows_from_plan(plan)
    obligation_rows = [
        dict(row)
        for row in raw_obligations
        if isinstance(row, dict)
    ]
    if not obligation_rows:
        return _selected_rows_from_plan(plan)
    from .discovery_funnel import _accounting_rows_for_parts

    by_obligation = experiments.get("by_obligation")
    agent_intent_plan = experiments.get("agent_intent_plan")
    obligation_plan = experiments.get("obligation_plan")
    if not isinstance(by_obligation, dict):
        by_obligation = {}
    if not isinstance(agent_intent_plan, dict):
        agent_intent_plan = {}
    if not isinstance(obligation_plan, dict):
        obligation_plan = {}
    return _accounting_rows_for_parts(
        obligations=obligation_rows,
        experiments=by_obligation,
        agent_intent_plan=agent_intent_plan,
        obligation_plan=obligation_plan,
        planning_round=1,
    )


def _build_runner_exception_ledger(
    plan: Any,
    exc: BaseException,
) -> dict[str, Any]:
    """Create terminal audit receipts while preserving runner exception semantics.

    This does not claim that a request was sent or that cleanup completed.  A
    compiled obligation is terminated at execution as HARNESS_FAILED; an
    obligation without a confirmed compile receipt is terminated at compile.
    The ledger is still the only funnel authority.
    """

    from .obligation_attempt_ledger import build_obligation_attempt_ledger

    contract = validate_mainline_run_contract(_plan_contract(plan))
    selected = _accounting_rows_from_plan(plan)
    experiments = _plan_value(plan, "experiments")
    by_obligation = (
        experiments.get("by_obligation")
        if isinstance(experiments, dict)
        else {}
    )
    by_obligation = by_obligation if isinstance(by_obligation, dict) else {}
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    exception_class = type(exc).__name__
    for row in selected:
        obligation_id = _text(row.get("obligation_id"))
        experiment = by_obligation.get(obligation_id)
        experiment = experiment if isinstance(experiment, dict) else {}
        source_compile = experiment.get("compile_receipt")
        source_compile = source_compile if isinstance(source_compile, dict) else {}
        compile_status = _text(source_compile.get("status")).upper()
        identity = _exception_stage_identity(contract, obligation_id)
        experiment_id = _text(
            experiment.get("experiment_id")
            or source_compile.get("experiment_id")
            or row.get("experiment_id")
        )
        selection_status = _text(row.get("selection_status")).upper() or "SELECTED"
        if selection_status != "SELECTED":
            if compile_status in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}:
                compile_results[obligation_id] = {
                    "status": compile_status,
                    "reason_code": _text(source_compile.get("reason_code"))
                    or _text(row.get("selection_reason_code"))
                    or (
                        "BLOCKED_COMPILE"
                        if compile_status == "BLOCKED"
                        else "DEFERRED"
                        if compile_status == "DEFERRED"
                        else "MAINLINE_RUNTIME_EXCEPTION"
                    ),
                    "reason_detail": _text(
                        source_compile.get("detail")
                        or source_compile.get("reason_detail")
                        or row.get("selection_reason_code")
                    ),
                    "experiment_id": experiment_id,
                    **identity,
                }
            elif compile_status == "COMPILED":
                compile_results[obligation_id] = {
                    "status": "COMPILED",
                    "reason_code": "",
                    "experiment_id": experiment_id,
                    **identity,
                }
                execution_results[obligation_id] = {
                    "status": "DEFERRED",
                    "reason_code": "OBLIGATION_NOT_IN_PLAN",
                    "reason_detail": "formal_obligation_not_selected",
                    "experiment_id": experiment_id,
                    **identity,
                }
            else:
                compile_results[obligation_id] = {
                    "status": "DEFERRED",
                    "reason_code": "OBLIGATION_NOT_IN_PLAN",
                    "reason_detail": "formal_obligation_not_selected",
                    "experiment_id": experiment_id,
                    **identity,
                }
            continue
        if compile_status in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}:
            compile_receipt = {
                "status": compile_status,
                "reason_code": _text(source_compile.get("reason_code"))
                or "MAINLINE_RUNTIME_EXCEPTION",
                "experiment_id": experiment_id,
                **identity,
            }
            for field in (
                "receipt_id",
                "input_fingerprint",
                "output_fingerprint",
                "elapsed_ms",
            ):
                value = source_compile.get(field)
                if value not in (None, ""):
                    compile_receipt[field] = value
            compile_results[obligation_id] = compile_receipt
            continue
        if compile_status != "COMPILED":
            compile_results[obligation_id] = {
                "status": "HARNESS_FAILED",
                "reason_code": "MAINLINE_RUNTIME_EXCEPTION",
                "reason_detail": (
                    f"compile_receipt_not_confirmed:{exception_class}"
                ),
                "experiment_id": experiment_id,
                **identity,
            }
            continue
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "reason_code": "",
            "experiment_id": experiment_id,
            **identity,
        }
        execution_results[obligation_id] = {
            "status": "HARNESS_FAILED",
            "reason_code": "MAINLINE_RUNTIME_EXCEPTION",
            "reason_detail": f"runner_exception:{exception_class}",
            "experiment_id": experiment_id,
            "cleanup_status": "UNKNOWN",
            "target_request_status": "NOT_PROVEN",
            **identity,
        }
    return build_obligation_attempt_ledger(
        mainline_run=contract,
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results={},
    )


def _persist_runner_exception_audit(
    campaign_handle: Any,
    ledger: dict[str, Any],
    exc: BaseException,
) -> bool:
    if not isinstance(campaign_handle, dict):
        logger.error(
            "mainline runner exception has no campaign persistence handle",
            extra={"exception_class": type(exc).__name__},
        )
        return False
    campaign = campaign_handle.get("campaign")
    store = campaign_handle.get("store")
    if not hasattr(campaign, "record_obligation_attempt_ledger") or not hasattr(
        store, "save"
    ):
        logger.error(
            "mainline runner exception persistence authority is incomplete",
            extra={"exception_class": type(exc).__name__},
        )
        return False
    campaign.record_obligation_attempt_ledger(ledger)
    campaign.audit_events.append({
        "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "mainline_runner_exception",
        "reason_code": "MAINLINE_RUNTIME_EXCEPTION",
        "exception_class": type(exc).__name__,
        "cleanup_status": "UNKNOWN",
        "target_request_status": "NOT_PROVEN",
        "obligation_attempt_ledger_fingerprint": _text(
            ledger.get("ledger_fingerprint")
        ),
    })
    campaign.audit_events = campaign.audit_events[-200:]
    store.save(campaign)
    return True


def _freeze_contract(inputs: DiscoveryMainlineInputs) -> MainlineRunContract:
    context = inputs.campaign_context
    return build_mainline_run_contract(
        mainline_authority=_text(context.get("mainline_authority")),
        run_id=_text(context.get("run_id")),
        campaign_id=_text(context.get("campaign_id")),
        target_id=_text(context.get("target_id")),
        environment_id=_text(context.get("environment_id")),
        policy_version=_text(context.get("policy_version")),
        evaluation_mode=_text(context.get("evaluation_mode")),
        source_snapshot_hash=_source_snapshot_hash(context),
    )


def _fingerprint_runner(runner: MainlineRunner) -> tuple[str, str]:
    identity = f"{runner.__module__}:{runner.__qualname__}"
    try:
        source = inspect.getsource(runner)
    except (OSError, TypeError) as exc:
        raise MainlineContractError(
            f"mainline_runner_fingerprint_unavailable:{identity}"
        ) from exc
    material = f"{identity}\n{source}".encode("utf-8")
    return identity, hashlib.sha256(material).hexdigest()


def _runner_receipt(
    *,
    inputs: DiscoveryMainlineInputs,
    contract: MainlineRunContract,
    runner: MainlineRunner,
) -> dict[str, Any]:
    context = inputs.campaign_context
    runner_identity, runner_fingerprint = _fingerprint_runner(runner)
    expected_fingerprint = _text(context.get("mainline_runner_fingerprint"))
    if expected_fingerprint and expected_fingerprint != runner_fingerprint:
        raise MainlineContractError(
            f"mainline_runner_fingerprint_mismatch:{contract['mainline_authority']}"
        )
    required_identity = {
        "policy_id": _text(context.get("policy_id")),
        "policy_version": _text(context.get("policy_version")),
        "strategy_fingerprint": _text(context.get("strategy_fingerprint")),
    }
    missing = [key for key, value in required_identity.items() if not value]
    if missing:
        raise MainlineContractError(
            "mainline_runner_policy_identity_missing:" + ",".join(missing)
        )
    strategy_fingerprint = required_identity["strategy_fingerprint"]
    if len(strategy_fingerprint) != 64 or any(
        character not in "0123456789abcdef"
        for character in strategy_fingerprint.lower()
    ):
        raise MainlineContractError("mainline_strategy_fingerprint_invalid")
    receipt: dict[str, Any] = {
        "schema_version": "qualibug.discovery-mainline-runner.v1",
        "status": "BOUND",
        "mainline_authority": contract["mainline_authority"],
        **required_identity,
        "mainline_contract_fingerprint": contract["contract_fingerprint"],
        "runner_identity": runner_identity,
        "runner_fingerprint": runner_fingerprint,
    }
    canonical = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    receipt["receipt_fingerprint"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    return receipt


def assert_result_matches_authority(
    result: dict[str, Any],
    contract: MainlineRunContract,
) -> None:
    if not isinstance(result, dict):
        raise MainlineContractError("mainline_result_not_object")
    observed = validate_mainline_run_contract(result.get("mainline_run"))
    if observed["contract_fingerprint"] != contract["contract_fingerprint"]:
        raise MainlineContractError("mainline_result_authority_mismatch")


def _existing_findings_fingerprints(items: Any) -> set[str]:
    """Fingerprint prior findings so the mainline can mark known discoveries.

    Cross-round stability comes from content (title + expected + actual), not a
    per-run id that changes every execution.  A stable finding_id is preferred
    when present.
    """
    if not items:
        return set()
    out: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        fid = it.get("finding_id") or it.get("id")
        if fid:
            out.add(f"id:{fid}")
            continue
        title = str(it.get("title") or "")
        expected = str(it.get("expected") or (it.get("evidence") or {}).get("expected", ""))
        actual = str(it.get("actual") or (it.get("evidence") or {}).get("actual", ""))
        digest = hashlib.sha256(f"{title}|{expected}|{actual}".encode("utf-8")).hexdigest()
        out.add(f"h:{digest}")
    return out


def _mark_prior_known(result: dict[str, Any], known: set[str]) -> None:
    """Mark delivery/candidate findings that match a prior round (closed loop).

    This never removes or downgrades a finding: a reproduced bug stays a bug.
    It only adds an observable prior_known flag plus a closed-loop count so the
    self-improving loop can tell new from already-known discoveries.
    """
    prior = 0
    new = 0
    for bucket in ("delivery_occurrences", "candidate_findings"):
        items = result.get(bucket)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            fid = it.get("finding_id") or it.get("id")
            if fid and f"id:{fid}" in known:
                it["prior_known"] = True
                prior += 1
                continue
            title = str(it.get("title") or "")
            expected = str(it.get("expected") or (it.get("evidence") or {}).get("expected", ""))
            actual = str(it.get("actual") or (it.get("evidence") or {}).get("actual", ""))
            digest = hashlib.sha256(f"{title}|{expected}|{actual}".encode("utf-8")).hexdigest()
            if f"h:{digest}" in known:
                it["prior_known"] = True
                prior += 1
            else:
                it["prior_known"] = False
                new += 1
    result["prior_known_count"] = prior
    result["new_finding_count"] = new


def run_discovery_mainline(
    inputs: DiscoveryMainlineInputs,
    *,
    build_campaign: CampaignBuilder,
    build_plan: PlanBuilder,
    legacy_runner: MainlineRunner | None = None,
    experiment_runner: MainlineRunner | None = None,
) -> dict[str, Any]:
    """Freeze authority, then build a campaign and invoke exactly one runner."""

    if not isinstance(inputs, DiscoveryMainlineInputs):
        raise MainlineContractError("mainline_inputs_invalid")
    input_authority = _text(inputs.campaign_context.get("mainline_authority"))
    if not input_authority:
        raise MainlineContractError("mainline_input_authority_missing")
    frozen_contract = _freeze_contract(inputs)
    runner = (
        legacy_runner
        if frozen_contract["mainline_authority"] == "legacy_champion"
        else experiment_runner
    )
    if runner is None:
        authority = frozen_contract["mainline_authority"]
        hint = (
            ":installed_runners=experiment_candidate_only"
            ":select_experiment_candidate_before_next_run"
            if authority == "legacy_champion"
            else ""
        )
        raise MainlineContractError(f"mainline_runner_unavailable:{authority}{hint}")
    receipt = _runner_receipt(
        inputs=inputs,
        contract=frozen_contract,
        runner=runner,
    )

    campaign = build_campaign(inputs)
    campaign_id = _campaign_id(campaign)
    plan = build_plan(inputs, campaign)
    contract = _plan_contract(plan)
    if contract["campaign_id"] != campaign_id:
        raise MainlineContractError("mainline_campaign_identity_mismatch")
    if contract["mainline_authority"] != input_authority:
        raise MainlineContractError("mainline_input_authority_mismatch")
    if contract["contract_fingerprint"] != frozen_contract["contract_fingerprint"]:
        raise MainlineContractError("mainline_plan_contract_mismatch")

    try:
        result = runner(inputs, campaign, plan)
    except Exception as exc:
        logger.exception(
            "discovery mainline runner failed for campaign %s",
            campaign_id,
        )
        try:
            exception_ledger = _build_runner_exception_ledger(plan, exc)
            _persist_runner_exception_audit(campaign, exception_ledger, exc)
        except Exception:
            logger.exception(
                "mainline runner exception audit failed for campaign %s",
                campaign_id,
            )
        raise
    assert_result_matches_authority(result, contract)
    result["implicit_rule_runtime_evolution"] = (
        project_implicit_rule_runtime_evolution(
            behavior_ir=_plan_value(plan, "behavior_ir"),
            obligations=_plan_value(plan, "obligations"),
            obligation_attempt_ledger=result.get("obligation_attempt_ledger"),
        )
    )
    # Promote fact experimentability ledger for GT-free first-loss reporting.
    experiments_pack = _plan_value(plan, "experiments")
    if isinstance(experiments_pack, dict):
        fact_ledger = experiments_pack.get("fact_experimentability_ledger")
        if isinstance(fact_ledger, dict) and fact_ledger:
            result["fact_experimentability_ledger"] = fact_ledger
        result.setdefault("experiments", _public_planning_pack(experiments_pack))
    obligations_pack = _plan_value(plan, "obligations")
    if isinstance(obligations_pack, dict) and obligations_pack:
        result.setdefault("obligations", _public_planning_pack(obligations_pack))
    result["mainline_runner_receipt"] = receipt
    known_set = _existing_findings_fingerprints(inputs.existing_findings)
    if known_set:
        _mark_prior_known(result, known_set)
    return result
