"""Compatibility facade adding fail-closed Recall preservation to Business Behavior IR."""
from __future__ import annotations

from . import behavior_ir_base as _base
from .behavior_fact_recall_authority import is_behavior_worthy_fact

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_behavior_from_fact = _base._behavior_from_fact


def _behavior_from_fact(fact):
    """Preserve test-worthy accepted facts even when their operation is unresolved.

    The base builder already represents a missing operation as
    BEHAVIOR_OPERATION_UNRESOLVED / INCOMPLETE. Its historical early return,
    however, silently dropped most fact families before that fail-closed state
    could be emitted. We only bypass that early return for explicit test-worthy
    kinds and never invent an operation.
    """
    if not isinstance(fact, dict):
        return _original_behavior_from_fact(fact)
    action = _base.as_dict(fact.get("action"))
    operation = _base.text(action.get("canonical") or action.get("raw"))
    kind = str(fact.get("kind") or "").strip().upper()
    if (
        not operation
        and kind not in {"RULE", "STATE_TRANSITION"}
        and is_behavior_worthy_fact(fact)
    ):
        forwarded = dict(fact)
        # The base implementation uses kind only for its historical early gate.
        # RULE opens that gate without manufacturing operation/evidence.
        forwarded["kind"] = "RULE"
        return _original_behavior_from_fact(forwarded)
    return _original_behavior_from_fact(fact)


# Functions defined in behavior_ir_base resolve globals from that module, so
# patch its helper as well; public imports through this facade stay compatible.
_base._behavior_from_fact = _behavior_from_fact
globals()["_behavior_from_fact"] = _behavior_from_fact
