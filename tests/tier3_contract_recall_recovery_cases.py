"""Tier-3 contract rejection must remain strict while exposing recall misses."""

from __future__ import annotations


def classify_interface_rejection(*, interface_exists: bool, is_candidate: bool) -> str:
    """Classify without weakening the contract validator."""
    if not interface_exists:
        return "UNKNOWN_INTERFACE_ID"
    if not is_candidate:
        return "CANDIDATE_RECALL_MISS"
    return "ACCEPT"


def test_unknown_interface_is_not_a_recall_miss() -> None:
    assert classify_interface_rejection(
        interface_exists=False, is_candidate=False
    ) == "UNKNOWN_INTERFACE_ID"


def test_real_interface_outside_candidate_set_is_recall_miss() -> None:
    assert classify_interface_rejection(
        interface_exists=True, is_candidate=False
    ) == "CANDIDATE_RECALL_MISS"


def test_candidate_interface_remains_acceptable() -> None:
    assert classify_interface_rejection(
        interface_exists=True, is_candidate=True
    ) == "ACCEPT"
