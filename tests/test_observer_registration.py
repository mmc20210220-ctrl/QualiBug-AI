"""Observers must be registrable, including on non-HTTP surfaces.

All 13 built-in entries in OBSERVER_REGISTRY declare adapter "http_api", the registry
was a literal dict with no registration function, and the dispatch in
observe_experiment_requirements was a hardcoded if/elif whose else branch returned
UNSUPPORTED. That combination is the product's hardest breadth ceiling: a defect whose
evidence lives in a database, a message queue, a rendered view or a timing series has
no observer that can measure it, so it is unreachable regardless of comprehension
depth.

``register_observer`` is the extension point, deliberately additive -- the built-in
dispatch keeps working and registered handlers are consulted where it would otherwise
return UNSUPPORTED. Rewriting a working dispatch was not needed to remove the ceiling
and would have risked the paths that produce every real receipt today.

Registration is PROCESS-GLOBAL, so each test here cleans up after itself; a leaked
registration would silently change what other tests compile.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from ai_test_asset_center import observer_contracts_base as ocb
from ai_test_asset_center.observer_contracts_base import (
    OBSERVER_REGISTRY,
    _receipt,
    compile_observer_requirements,
    observe_experiment_requirements,
    register_observer,
    registered_observer_ids,
    validate_observer_receipt,
)


@pytest.fixture()
def cleanup_registrations() -> Iterator[list[str]]:
    """Remove anything registered during a test, pass or fail."""
    registered: list[str] = []
    yield registered
    for observer_id in registered:
        OBSERVER_REGISTRY.pop(observer_id, None)
        ocb._REGISTERED_OBSERVER_HANDLERS.pop(observer_id, None)


def _ok_handler(observer_id: str, **evidence: Any) -> Any:
    def handler(_envelope: dict[str, Any]) -> dict[str, Any]:
        return _receipt(
            observer_id=observer_id, status="OBSERVED", reason_code="", evidence=evidence
        )
    return handler


def _experiment(*observer_ids: str) -> dict[str, Any]:
    return {
        "observers": [{"observer_id": observer_id} for observer_id in observer_ids],
        "assertions": [{"kind": "state_transition", "property": {}}],
    }


def test_non_http_observer_can_be_registered_and_dispatched(cleanup_registrations) -> None:
    """The ceiling: before this, no observer on any surface but http_api could exist."""
    observer_id = "test_only_persistence_reader"
    register_observer(
        observer_id,
        surface="persistence_state",
        adapter="db_sql",
        handler=_ok_handler(observer_id, rows_seen=3),
        evidence_keys=("db_row_state",),
    )
    cleanup_registrations.append(observer_id)

    assert observer_id in registered_observer_ids()
    assert OBSERVER_REGISTRY[observer_id]["adapter"] == "db_sql"

    receipts = observe_experiment_requirements(
        _experiment(observer_id), observations={}, campaign_id="C1", execution_id="E1"
    )
    assert len(receipts) == 1
    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["rows_seen"] == 3
    # Must go through the existing content-addressed receipt layer, not beside it.
    assert validate_observer_receipt(receipts[0])


def test_undeclared_adapter_is_still_fail_closed(cleanup_registrations) -> None:
    """Registering an adapter does not make it available on an arbitrary target."""
    observer_id = "test_only_queue_reader"
    register_observer(
        observer_id,
        surface="message_stream",
        adapter="queue_admin_api",
        handler=_ok_handler(observer_id),
    )
    cleanup_registrations.append(observer_id)

    _compiled, reason_code, detail = compile_observer_requirements(
        [observer_id], risk_family="state", available_adapters={"http_api"}
    )
    assert reason_code == "BLOCKED_UNSUPPORTED_ADAPTER"
    assert detail == "queue_admin_api"

    compiled, reason_code, _detail = compile_observer_requirements(
        [observer_id], risk_family="state",
        available_adapters={"http_api", "queue_admin_api"},
    )
    assert not reason_code
    assert compiled[0]["adapter"] == "queue_admin_api"


def test_failing_handler_does_not_erase_other_receipts(cleanup_registrations) -> None:
    """One broken extension must not destroy evidence the rest of the chain collected."""
    good_id = "test_only_good_observer"
    bad_id = "test_only_raising_observer"

    def raising_handler(_envelope: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("handler exploded")

    register_observer(good_id, surface="s", adapter="http_api", handler=_ok_handler(good_id))
    register_observer(bad_id, surface="s", adapter="http_api", handler=raising_handler)
    cleanup_registrations.extend([good_id, bad_id])

    receipts = observe_experiment_requirements(
        _experiment(bad_id, good_id), observations={}, campaign_id="C1", execution_id="E1"
    )
    by_id = {receipt["observer_id"]: receipt for receipt in receipts}

    assert by_id[bad_id]["status"] == "INDETERMINATE"
    assert by_id[bad_id]["reason_code"] == "OBSERVER_HANDLER_FAILED"
    assert "handler exploded" in by_id[bad_id]["evidence"]["error"]
    # The failure is reported, never swallowed, and the good observer still reported.
    assert by_id[good_id]["status"] == "OBSERVED"


def test_handler_returning_a_non_receipt_is_reported(cleanup_registrations) -> None:
    observer_id = "test_only_bad_return_observer"
    register_observer(
        observer_id, surface="s", adapter="http_api", handler=lambda _envelope: "not a dict"
    )
    cleanup_registrations.append(observer_id)

    receipts = observe_experiment_requirements(
        _experiment(observer_id), observations={}, campaign_id="C1", execution_id="E1"
    )
    assert receipts[0]["reason_code"] == "OBSERVER_HANDLER_RETURNED_NON_RECEIPT"


def test_handler_receives_the_observation_envelope(cleanup_registrations) -> None:
    """A non-http handler needs its own evidence; positional shapes could not deliver it."""
    observer_id = "test_only_envelope_probe"
    seen: dict[str, Any] = {}

    def capture(envelope: dict[str, Any]) -> dict[str, Any]:
        seen.update(envelope)
        return _receipt(observer_id=observer_id, status="OBSERVED", reason_code="", evidence={})

    register_observer(observer_id, surface="s", adapter="http_api", handler=capture)
    cleanup_registrations.append(observer_id)

    observations = {
        "control_observation": {"status": 200},
        "treatment_observation": {"status": 403},
        "execution_steps": [{"phase": "treatment"}],
    }
    observe_experiment_requirements(
        _experiment(observer_id), observations=observations,
        campaign_id="CMP_1", execution_id="EXE_1",
    )

    assert seen["observer_id"] == observer_id
    assert seen["control_observation"] == {"status": 200}
    assert seen["treatment_observation"] == {"status": 403}
    assert seen["execution_steps"] == [{"phase": "treatment"}]
    assert seen["campaign_id"] == "CMP_1"
    assert seen["execution_id"] == "EXE_1"
    assert "experiment" in seen and "assertion" in seen


@pytest.mark.parametrize(
    "kwargs",
    [
        {"surface": "", "adapter": "http_api", "handler": lambda e: {}},
        {"surface": "s", "adapter": "", "handler": lambda e: {}},
        {"surface": "s", "adapter": "http_api", "handler": None},
        {"surface": "s", "adapter": "http_api", "handler": "not callable"},
    ],
)
def test_unusable_registration_is_rejected(kwargs: dict) -> None:
    """compile_observer_requirements only checks implemented is True.

    So an entry without a usable handler would compile, spend real target requests, and
    then fall through as UNSUPPORTED -- exactly how write_observer shipped.
    """
    with pytest.raises(ValueError):
        register_observer("test_only_unusable", **kwargs)


def test_empty_observer_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        register_observer("", surface="s", adapter="http_api", handler=lambda e: {})


def test_built_in_observers_cannot_be_shadowed() -> None:
    """A built-in has its own dispatch branch; shadowing it would be ambiguous."""
    with pytest.raises(ValueError):
        register_observer(
            "http_response", surface="s", adapter="http_api", handler=lambda e: {}
        )


def test_registration_does_not_leak_between_tests() -> None:
    """Guards the fixture itself: a leaked id changes what other tests compile."""
    leaked = [
        observer_id
        for observer_id in registered_observer_ids()
        if observer_id.startswith("test_only_")
    ]
    assert not leaked, f"test-only observers left registered: {leaked}"
