"""Doc-less visibility signal: non-deterministic anonymous exposure.

同源 (same source) with ``runtime_auth_decision_surface``.  The read-only GET
probe's repeated anonymous samples that returned BOTH a 2xx (data exposed) and
a 401/403 (denied) are consumed here under the *visibility* risk lens: the
endpoint intermittently exposes data to unauthenticated callers. That is a
genuine visibility defect, and — exactly like the authorization signal — it
asserts ONLY the *inconsistency*, never what "should" be visible (原则6: no
invented business/industry semantics).

Because the underlying observation is identical to authorization's, this module
reuses the authorization observer (``runtime_auth_decision_reader``), assertion
evaluator (``runtime_auth_decision_consistency``) and protocol compiler — it
registers ONLY a new (family, template) pair on the existing ``visibility``
family.  No fork, no duplicated logic (原则 10).  The experiment re-issues the
anonymous GET a short window of times; the controlled re-issue is the Oracle,
never the probe's raw samples.

Discipline (identical to authorization and to AGENTS.md 原则 2/6/7/14):
- A consistently-closed endpoint (always 401/403) or consistently-open endpoint
  (always 2xx) is NOT a defect under this signal: we only ever assert the *mix*.
- Incomplete coverage (fewer than the required samples) is INDETERMINATE, never
  a silent PASS or a fabricated VIOLATION.
"""
from __future__ import annotations

from typing import Any

# 同源: reuse the authorization surface's observer, assertion evaluator and
# protocol compiler.  The visibility family is a distinct risk lens on the same
# runtime observation — no new observation machinery is needed.
from .runtime_auth_decision_surface import (  # noqa: F401
    ASSERTION_KIND,
    OBSERVER_ID,
    _compile_runtime_auth_protocol,
    install_runtime_auth_decision_surface,
)

RISK_FAMILY = "visibility"
PROTOCOL_TEMPLATE = "runtime_visibility_exposure_consistency"


def install_runtime_visibility_exposure_surface() -> dict[str, str]:
    """Install a visibility protocol idempotently (同源 with authorization).

    The ``visibility`` risk family is already registered (canonical); this adds
    ONLY a new protocol template that reuses the authorization observer +
    assertion evaluator + compiler — no fork (原则 10).
    """
    from .experiment_protocol_registry import (
        register_family_protocol,
        registered_family_protocols,
    )

    # 同源: this protocol reuses the authorization observer + assertion evaluator,
    # so ensure they are registered first (idempotent; mirrors the production
    # install order in discovery_runtime_semantic_binding but stays robust if
    # this module is imported/installed on its own).
    install_runtime_auth_decision_surface()

    protocol_id = f"{RISK_FAMILY}:{PROTOCOL_TEMPLATE}"
    if protocol_id not in set(registered_family_protocols()):
        register_family_protocol(
            RISK_FAMILY,
            PROTOCOL_TEMPLATE,
            compiler=_compile_runtime_auth_protocol,
            observers=(OBSERVER_ID,),
            assertion_kind=ASSERTION_KIND,
            emits_control=False,
            per_step_evidence=True,
        )
    return {
        "protocol": protocol_id,
        "observer": OBSERVER_ID,
        "assertion": ASSERTION_KIND,
    }


__all__ = [
    "PROTOCOL_TEMPLATE",
    "RISK_FAMILY",
    "install_runtime_visibility_exposure_surface",
]
