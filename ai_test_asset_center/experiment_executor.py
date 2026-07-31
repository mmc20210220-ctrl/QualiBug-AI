"""Public experiment execution facade.

Governance, graph-proof, account-identity and authorization-comparison adapters
live in ``experiment_executor_governance``. This module keeps the established
public identities and monkeypatch surface while delegating one execution call.
The final public result additionally applies the authorization causal-evidence
gate so an Oracle candidate cannot leave the execution boundary without the
existing control/treatment/observer/binding receipt chain. Passed authorization
findings embed that complete receipt and exact binding proofs before Gate v2
fingerprints the customer-facing payload.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import experiment_executor_governance as _governance
from .authorization_delivery_gate import (
    attach_authorization_delivery_evidence,
)
from .authorization_oracle_causality import (
    enforce_authorization_oracle_causality,
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
    governed = enforce_authorization_oracle_causality(
        result=result,
        experiment=experiment,
        behavior_ir=behavior_ir,
        account_rows=_governance._test_account_rows(root, project),
    )
    return attach_authorization_delivery_evidence(
        governed,
        experiment=experiment,
    )


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