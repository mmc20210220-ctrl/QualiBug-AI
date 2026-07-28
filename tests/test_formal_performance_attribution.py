from __future__ import annotations

from ai_test_asset_center import formal_performance_surface as performance
from ai_test_asset_center.formal_performance_attribution_guard import (
    install_formal_performance_attribution_guard,
)


def _assertion() -> dict:
    return {
        "kind": performance.ASSERTION_KIND,
        "property": {
            "performance_contract": {
                "contract_id": "orders-p95",
                "sample_count": 3,
                "warmup_count": 0,
                "percentile": "p95",
                "max_latency_ms": 100,
                "max_error_rate": 0,
                "expected_status_class": 2,
            },
        },
    }


def _step(index: int, *, duration: int, status: int = 200, attempts: int = 1) -> dict:
    return {
        "step_id": f"performance_sample_{index}",
        "status_code": status,
        "duration_ms": duration,
        "raw": {"_attempts": attempts, "body": {"secret": "must-not-enter"}},
    }


def _envelope(steps: list[dict]) -> dict:
    return {
        "assertion": _assertion(),
        "execution_steps": steps,
    }


def test_retried_sample_is_indeterminate_not_slow_target_evidence() -> None:
    performance.install_formal_performance_surface()
    install_formal_performance_attribution_guard()

    receipt = performance._performance_observer_handler(_envelope([
        _step(1, duration=20),
        _step(2, duration=800, attempts=3),
        _step(3, duration=22),
    ]))

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERFORMANCE_RETRIED_TRANSPORT_UNTRUSTWORTHY"
    evidence = receipt["evidence"][performance.EVIDENCE_KEY]
    assert evidence["retried_sample_count"] == 1
    assert evidence["transport_backoff_included"] is False
    assert "must-not-enter" not in str(receipt)


def test_missing_attempt_count_is_indeterminate() -> None:
    performance.install_formal_performance_surface()
    install_formal_performance_attribution_guard()
    steps = [
        _step(1, duration=20),
        _step(2, duration=21),
        _step(3, duration=22),
    ]
    steps[1]["raw"].pop("_attempts")

    receipt = performance._performance_observer_handler(_envelope(steps))

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERFORMANCE_TRANSPORT_ATTEMPT_COUNT_MISSING"


def test_non_2xx_sample_suppresses_latency_verdict() -> None:
    performance.install_formal_performance_surface()
    install_formal_performance_attribution_guard()

    receipt = performance._performance_observer_handler(_envelope([
        _step(1, duration=20),
        _step(2, duration=21, status=500),
        _step(3, duration=22),
    ]))

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERFORMANCE_FUNCTIONAL_RESPONSE_INVALID"
    evidence = receipt["evidence"][performance.EVIDENCE_KEY]
    assert evidence["latency_verdict_suppressed"] is True
    assert evidence["non_success_sample_count"] == 1


def test_complete_single_attempt_2xx_series_can_be_judged() -> None:
    performance.install_formal_performance_surface()
    install_formal_performance_attribution_guard()

    receipt = performance._performance_observer_handler(_envelope([
        _step(1, duration=80),
        _step(2, duration=110),
        _step(3, duration=90),
    ]))
    assert receipt["status"] == "OBSERVED"
    evidence = receipt["evidence"][performance.EVIDENCE_KEY]
    assert evidence["observed_percentile_ms"] == 110
    assert evidence["latency_verdict_scope"] == "successful_2xx_reads_only"
    assert evidence["functional_error_verdict_included"] is False

    verdict = performance._evaluate_latency_budget({
        "spec": _assertion()["property"],
        "observations": {performance.EVIDENCE_KEY: evidence},
    })
    assert verdict["passed"] is False
    assert verdict["actual"]["latency_budget_exceeded"] is True
    assert "error_rate_budget_exceeded" not in verdict["actual"]


def test_first_increment_rejects_non_success_status_contract() -> None:
    performance.install_formal_performance_surface()
    install_formal_performance_attribution_guard()
    contract = _assertion()["property"]["performance_contract"]
    contract = {**contract, "expected_status_class": 4}

    normalized, reason = performance._validated_contract(contract)

    assert normalized is None
    assert reason == "PERFORMANCE_SUCCESS_STATUS_CLASS_REQUIRED"
