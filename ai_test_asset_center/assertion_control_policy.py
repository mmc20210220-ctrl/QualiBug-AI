from __future__ import annotations

"""Single semantic authority for assertions that require a control actor.

Control is evidence, not a blanket HTTP convention. Actor-bound properties
need a control/treatment comparison; state, validation, conservation, and other
single-path properties may be proven from source-grounded treatment evidence.
"""

from typing import Any


CONTROL_REQUIRED_ASSERTION_KINDS = frozenset({
    "access_control",
    "authorization",
    "idor",
    "isolation",
    "owner_tenant_visibility",
    "permission",
    "permission_boundary",
    "privacy",
    "resource_isolation",
    "tenant_isolation",
    "visibility",
})


def normalize_assertion_kind(value: Any) -> str:
    return "_".join(
        str(value or "").strip().casefold().replace("-", " ").split()
    )


def assertion_requires_control(
    assertion_kind: Any,
    *,
    explicitly_required: bool = False,
) -> bool:
    """Return whether the assertion semantically needs a control actor."""

    return bool(
        explicitly_required
        or normalize_assertion_kind(assertion_kind)
        in CONTROL_REQUIRED_ASSERTION_KINDS
    )
