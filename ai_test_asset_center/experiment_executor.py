"""Public experiment execution facade.

Governance, graph-proof, account-identity and authorization-comparison adapters
live in ``experiment_executor_governance``. This module keeps the established
public identities and monkeypatch surface while delegating one execution call.
The final public result additionally applies the authorization causal-evidence
gate and SPEC Oracle Validity Gates (with Effect Observation Graph) so an
Oracle candidate cannot leave the execution boundary without the existing
control/treatment/observer/binding receipt chain and non-vacuous
identity/contrast/evidence proof. Passed authorization findings embed that
complete receipt and exact binding proofs before Gate v2 fingerprints the
customer-facing payload.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import experiment_executor_governance as _governance
from .authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
    attach_authorization_delivery_evidence,
)
from .authorization_oracle_causality import (
    enforce_authorization_oracle_causality,
)
from .oracle_validity_gates import enforce_oracle_validity_gates
from .binding_materialization_identity_receipt import (
    BindingMaterializationIdentityError,
    binding_identity_proofs_for_targets,
    seal_binding_materialization_receipts,
)
from .experiment_runtime_support import (
    load_actor_tokens as _runtime_load_actor_tokens,
)


for _name in dir(_governance):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_governance, _name)


_execute_one_governed = _governance.execute_one_experiment
_governed_load_actor_tokens = _governance._identity_safe_load_actor_tokens

# Preserve the historical public identity required by architecture contracts.
# The governed delegate still uses its account-safe loader by default.
load_actor_tokens = _runtime_load_actor_tokens

_HOOK_NAMES = (
    "_http_request",
    "_run_http_step",
    "_resolve_token",
    "execute_governed_control_write",
    "sandbox_write_allowed",
    "materialize_experiment_fixtures",
    "execute_barrier_plans",
    "execute_non_barrier_plans",
    "execute_experiment_cleanup_compensation",
    "execute_database_observer_phase",
    "finalize_experiment_execution",
    "validate_cleanup_plan",
)


def _sync_governance_hooks() -> None:
    """Propagate explicit public injection points without weakening defaults."""
    for name in _HOOK_NAMES:
        value = globals().get(name)
        if value is not None and hasattr(_governance, name):
            setattr(_governance, name, value)
    public_loader = globals().get("load_actor_tokens")
    if public_loader is _runtime_load_actor_tokens:
        _governance.load_actor_tokens = _governed_load_actor_tokens
    elif public_loader is not None:
        _governance.load_actor_tokens = public_loader


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _authorization_binding_targets(
    experiment: dict[str, Any],
) -> list[str]:
    contract = _dict(experiment.get("authorization_comparison_contract"))
    return [
        _text(value)
        for value in _list(contract.get("resource_identity_binding_targets"))
        if _text(value)
    ]


def _verify_authorization_compile_identity(
    result: dict[str, Any],
    experiment: dict[str, Any],
) -> None:
    """Prove the causal receipt was built against the current compiled contract."""
    receipt = _dict(result.get("authorization_causality_receipt"))
    contract = _dict(experiment.get("authorization_comparison_contract"))
    if not receipt or not contract or _text(receipt.get("status")).upper() != "PASSED":
        return
    expected_contract_fingerprint = hashlib.sha256(
        _canonical(contract).encode("utf-8")
    ).hexdigest()
    if _text(receipt.get("comparison_contract_fingerprint")) != expected_contract_fingerprint:
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_comparison_contract_fingerprint_mismatch"
        )
    expected_binding_graph_fingerprint = _text(
        contract.get("shared_binding_graph_fingerprint")
    )
    if (
        not expected_binding_graph_fingerprint
        or _text(receipt.get("compile_binding_graph_fingerprint"))
        != expected_binding_graph_fingerprint
    ):
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_binding_graph_fingerprint_mismatch"
        )


def _seal_authorization_finding_lineage(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Bind the finding to the exact causal campaign/experiment execution."""
    receipt = _dict(result.get("authorization_causality_receipt"))
    finding = _dict(result.get("finding"))
    if _text(receipt.get("status")).upper() != "PASSED" or not finding:
        return result
    output = dict(result)
    sealed = dict(finding)
    for field in (
        "campaign_id",
        "obligation_id",
        "experiment_id",
        "execution_id",
    ):
        expected = _text(receipt.get(field))
        current = _text(sealed.get(field))
        if not expected:
            raise AuthorizationDeliveryGateError(
                f"authorization_delivery_finding_lineage_missing:{field}"
            )
        if current and current != expected:
            raise AuthorizationDeliveryGateError(
                f"authorization_delivery_finding_lineage_mismatch:{field}"
            )
        sealed[field] = expected
    output["finding"] = sealed
    return output


def _authorization_delivery_failure(
    result: dict[str, Any],
    exc: Exception,
) -> dict[str, Any]:
    """Preserve execution fact while removing an unpublishable finding."""
    blocked = dict(result)
    blocked["finding"] = None
    if _text(blocked.get("status")).upper() not in {
        "BLOCKED",
        "HARNESS_FAILURE",
    }:
        blocked["status"] = "EXECUTED"
    blocked["reason_code"] = "AUTHORIZATION_DELIVERY_EVIDENCE_INVALID"
    blocked["detail"] = str(exc)
    verdict = dict(
        blocked.get("oracle_verdict")
        if isinstance(blocked.get("oracle_verdict"), dict)
        else {}
    )
    verdict.update({
        "status": "INDETERMINATE",
        "verdict": "blocked_experiment",
        "customer_deliverable_candidate": False,
        "authorization_delivery_gate": "INDETERMINATE",
        "authorization_delivery_reason": str(exc),
    })
    blocked["oracle_verdict"] = verdict
    return blocked


def execute_one_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    execution_id: str,
    actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute through governance, causal validation, then delivery packaging."""
    _sync_governance_hooks()
    result = _execute_one_governed(
        experiment,
        behavior_ir=behavior_ir,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        campaign_id=campaign_id,
        execution_id=execution_id,
        actor_tokens=actor_tokens,
    )
    try:
        targets = _authorization_binding_targets(experiment)
        prepared = (
            seal_binding_materialization_receipts(result)
            if _dict(experiment.get("authorization_comparison_contract"))
            else result
        )
        governed = enforce_authorization_oracle_causality(
            result=prepared,
            experiment=experiment,
            behavior_ir=behavior_ir,
            account_rows=_governance._test_account_rows(root, project),
        )
        # SPEC §7.6–7.7: Effect Observation Graph + Oracle Validity Gates demote
        # PROPERTY_HELD/VIOLATION when identity/contrast/preconditions/causal/
        # evidence are incomplete. Never upgrades a verdict.
        governed = enforce_oracle_validity_gates(
            result=governed,
            experiment=experiment,
        )
        causal_passed = (
            _text(
                _dict(governed.get("authorization_causality_receipt")).get(
                    "status"
                )
            ).upper()
            == "PASSED"
        )
        if causal_passed and targets:
            # V1.7: When the authorization_comparison observer has already proven
            # same_resource_proven=True, runtime binding identity receipts are
            # redundant — the observer is the authoritative same-resource proof.
            _observer_proved = any(
                isinstance(_obs, dict)
                and _text(_obs.get("observer_id")) == "authorization_comparison"
                and _dict(_obs.get("evidence")).get("same_resource_proven") is True
                for _obs in _list(governed.get("observer_receipts"))
            )
            if not _observer_proved:
                binding_identity_proofs_for_targets(
                    _list(governed.get("binding_materialization_receipts")),
                    targets,
                )
        _verify_authorization_compile_identity(governed, experiment)
        packaged = attach_authorization_delivery_evidence(
            governed,
            experiment=experiment,
        )
        return _seal_authorization_finding_lineage(packaged)
    except (
        AuthorizationDeliveryGateError,
        BindingMaterializationIdentityError,
    ) as exc:
        return _authorization_delivery_failure(result, exc)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_governance",
        "_name",
        "_execute_one_governed",
        "_governed_load_actor_tokens",
        "_runtime_load_actor_tokens",
    }
)