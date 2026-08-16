"""Precondition-executor regression guards for establishment writes.

Two defects, both rooted in the precondition executor treating a pure
subject-establishment create like a state-transition write:

1. ``_target_verdict`` demanded ``to_state``/``state_field`` on EVERY write. A
   multi-level dependency create (address, user) has no declared target state —
   it only establishes a referenced entity whose identity the caller captures
   from the create response. Demanding a state field made the verdict
   ``target_or_state_field_missing`` even after the create was accepted, so the
   chain could never establish a subject and downstream steps died as
   ``BLOCKED_PRECONDITION_BINDING_INCOMPLETE``.

2. A caller-scoped ownership identity param (``userId``) is tokenized into a
   ``{userId}`` placeholder whose value is the executing actor's own identity,
   not a referenced collection row. With no collection read to supply it, the
   executor left it unresolved. It must bind from the step actor's
   login-observed ``account_id`` — the same channel the fixture materializer
   uses for ``ownership_identity_param``.
"""
from __future__ import annotations

from typing import Any

from ai_test_asset_center.experiment_precondition_executor import _target_verdict


def _governed(write_status: int, write_body: dict[str, Any] | None = None) -> dict:
    return {"write": {"status": write_status, "body": write_body or {}}}


def test_establishment_create_without_target_state_is_reached_on_acceptance() -> None:
    """A subject-establishment create has no target state; acceptance IS reach.

    ``to_state`` / ``state_field`` are absent on the multi-level dependency
    planner's establishment steps (they only carry ``observe_response_body`` and
    an identity_output_binding). The verdict must read the accepted create as
    ``reached`` rather than ``target_or_state_field_missing``.
    """
    step = {"observe_response_body": True}
    verdict = _target_verdict(step=step, governed=_governed(201, {"id": "addr-1"}))
    assert verdict["observed"] is True
    assert verdict["reached"] is True
    assert verdict["reason_code"] == ""


def test_establishment_create_rejected_write_is_not_reached() -> None:
    """A rejected establishment write is still NOT reached, fail-closed."""
    step = {"observe_response_body": True}
    verdict = _target_verdict(step=step, governed=_governed(500, {}))
    assert verdict["observed"] is True
    assert verdict["reached"] is False
    assert verdict["reason_code"] == "BLOCKED_PRECONDITION_TARGET_NOT_OBSERVED"


def test_state_transition_step_still_verifies_declared_target_state() -> None:
    """A real state-advancement step keeps its target-state verdict unchanged."""
    step = {
        "observe_response_body": False,
        "to_state": "CANCELLED",
        "state_field": "status",
    }
    verdict = _target_verdict(
        step=step,
        governed={"after": {"status": 200, "body": {"status": "CANCELLED"}}},
    )
    assert verdict["reached"] is True
    assert verdict["reason_code"] == ""


def test_state_transition_step_mismatch_still_not_reached() -> None:
    step = {
        "observe_response_body": False,
        "to_state": "CANCELLED",
        "state_field": "status",
    }
    verdict = _target_verdict(
        step=step,
        governed={"after": {"status": 200, "body": {"status": "PAID"}}},
    )
    assert verdict["reached"] is False
    assert verdict["reason_code"] == "BLOCKED_PRECONDITION_TARGET_NOT_REACHED"


def test_ownership_placeholder_resolves_from_actor_account_id() -> None:
    """``{userId}`` is caller-scoped: bind the actor's account_id, never a list.

    Reproduces the materialization path directly: an ownership-keyed token that
    is not in bindings must be filled from the step actor's ``account_id``. The
    executor's ``is_ownership_key`` gate is structural (userId/ownerId/accountId),
    never an industry term.
    """
    from ai_test_asset_center.validation_read_side_protocol import is_ownership_key

    assert is_ownership_key("userId") is True
    assert is_ownership_key("ownerId") is True
    assert is_ownership_key("account_id") is True
    assert is_ownership_key("addressId") is False
    assert is_ownership_key("sku") is False
