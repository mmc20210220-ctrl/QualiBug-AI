from __future__ import annotations

import json
from copy import deepcopy

from ai_test_asset_center.process_graph_broker_delivery import (
    BROKER_CHECKPOINT_BEHIND_OBSERVED,
    BROKER_CHECKPOINT_REGRESSION,
    BROKER_DLQ_DELIVERY_UNEXPECTED,
    BROKER_PARTITION_OFFSET_CONFLICT,
    BROKER_RESTART_DEDUPLICATION_VIOLATION,
    BROKER_SEQUENCE_ORDER_VIOLATION,
    BROKER_TOPIC_MISMATCH,
)
from ai_test_asset_center.process_graph_event_transition import (
    STATUS_COMPILED,
    STATUS_CONVERGED,
    compile_event_transition_contract,
    execute_event_transition,
)


def _compiled_wait() -> dict:
    return {
        "wait_id": "wait_order_events",
        "source_node_id": "submit_order",
        "target_node_id": "consume_event",
        "observer_operation_ref": "op_event_observer",
        "method": "GET",
        "path_template": "/test-observers/events",
        "actor_ref": "actor_public",
        "system_ref": "broker_observer",
        "async_policy": {
            "enabled": True,
            "expected_max_delay_ms": 20,
            "poll_interval_ms": 1,
            "max_attempts": 2,
            "required_stable_observations": 1,
            "terminal_condition": "source_declared_event_delivery",
        },
    }


def _raw_wait() -> dict:
    return {
        "event_transition": {
            "delivery_kind": "message",
            "delivery_semantics": "at_least_once",
            "events_path": "$.items",
            "event_id_field": "$.event_id",
            "event_type_field": "$.event_type",
            "correlation_field": "$.order_id",
            "correlation_binding": "order_id",
            "correlation_query_parameter": "order_id",
            "expected_event_type": "OrderCreated",
            "expected_min_count": 1,
            "expected_max_count": 3,
            "delivery_attempt_field": "$.broker.delivery_attempt",
            "expected_max_delivery_attempt": 4,
            "source_refs": [
                {
                    "source_id": "event_contract",
                    "kind": "formal_event_contract",
                    "locator": "OrderCreated",
                }
            ],
            "broker_delivery": {
                "broker_model": "partitioned_log",
                "topic_field": "$.broker.topic",
                "expected_topic": "orders.events",
                "partition_field": "$.broker.partition",
                "offset_field": "$.broker.offset",
                "checkpoint_field": "$.broker.checkpoint",
                "consumer_group_field": "$.broker.consumer_group",
                "expected_consumer_group": "notification-service",
                "delivery_state_field": "$.broker.state",
                "dead_letter_topic_field": "$.broker.dead_letter_topic",
                "checkpoint_policy": "must_cover_observed",
                "dlq_policy": "forbidden",
                "dead_letter_states": ["DLQ", "DEAD_LETTERED"],
                "ordering_policy": "source_sequence",
                "ordering_key_field": "$.broker.ordering_key",
                "sequence_field": "$.broker.sequence",
                "restart_deduplication_required": True,
                "consumer_epoch_field": "$.broker.consumer_epoch",
                "deduplication_key_field": "$.broker.dedup_key",
                "effect_applied_field": "$.broker.effect_applied",
                "source_refs": [
                    {
                        "source_id": "broker_contract",
                        "kind": "formal_broker_contract",
                        "locator": "orders.events/notification-service",
                    }
                ],
            },
        }
    }


def _compile(raw_wait: dict | None = None) -> dict:
    contract, error = compile_event_transition_contract(
        raw_wait=raw_wait or _raw_wait(),
        compiled_wait=_compiled_wait(),
        relation_type="MESSAGE",
    )
    assert not error
    assert contract["status"] == STATUS_COMPILED
    return contract


def _event(
    event_id: str,
    *,
    offset: int,
    checkpoint: int,
    sequence: int,
    topic: str = "orders.events",
    consumer_group: str = "notification-service",
    state: str = "PROCESSED",
    dead_letter_topic: str = "",
    consumer_epoch: str = "epoch-1",
    effect_applied: bool = True,
    delivery_attempt: int = 1,
    payload_status: str = "created",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "OrderCreated",
        "order_id": "ORD-42",
        "payload": {"status": payload_status},
        "broker": {
            "topic": topic,
            "partition": 2,
            "offset": offset,
            "checkpoint": checkpoint,
            "consumer_group": consumer_group,
            "state": state,
            "dead_letter_topic": dead_letter_topic,
            "ordering_key": "ORD-42",
            "sequence": sequence,
            "consumer_epoch": consumer_epoch,
            "dedup_key": "ORD-42:OrderCreated",
            "effect_applied": effect_applied,
            "delivery_attempt": delivery_attempt,
        },
    }


def _execute(contract: dict, polls: list[list[dict]]) -> dict:
    responses = iter(
        {"status_code": 200, "body": {"items": rows}}
        for rows in polls
    )
    ticks = iter(
        [
            0.0,
            0.0,
            0.001,
            0.002,
            0.003,
            0.004,
            0.005,
            0.006,
        ]
    )
    return execute_event_transition(
        contract=contract,
        context={
            "base_url": "https://observer.example.test",
            "bindings": {"order_id": "ORD-42"},
        },
        actors={"actor_public": {"role": "public"}},
        tokens={},
        read_once=lambda: next(responses),
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )


def test_broker_contract_requires_its_own_source_refs() -> None:
    raw = _raw_wait()
    raw["event_transition"]["broker_delivery"]["source_refs"] = []

    contract, error = compile_event_transition_contract(
        raw_wait=raw,
        compiled_wait=_compiled_wait(),
        relation_type="MESSAGE",
    )

    assert contract == {}
    assert "broker_source_refs_missing" in error


def test_checkpoint_and_retry_metadata_changes_are_not_event_id_reuse() -> None:
    contract = _compile()
    first = _event(
        "evt-1",
        offset=10,
        checkpoint=10,
        sequence=1,
        consumer_epoch="epoch-1",
        delivery_attempt=1,
    )
    second = deepcopy(first)
    second["broker"]["checkpoint"] = 11
    second["broker"]["delivery_attempt"] = 2
    second["broker"]["consumer_epoch"] = "epoch-2"

    receipt = _execute(contract, [[first], [second]])

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "PASS"
    assert receipt["event_id_reuse_conflict_count"] == 0
    assert receipt["poll_replay_count"] == 1


def test_restart_replay_passes_when_business_effect_is_applied_once() -> None:
    contract = _compile()
    event_1 = _event(
        "evt-1",
        offset=10,
        checkpoint=10,
        sequence=1,
        consumer_epoch="epoch-1",
        effect_applied=True,
    )
    replay = _event(
        "evt-2",
        offset=11,
        checkpoint=11,
        sequence=2,
        consumer_epoch="epoch-2",
        effect_applied=False,
        delivery_attempt=2,
    )
    advanced = deepcopy(event_1)
    advanced["broker"]["checkpoint"] = 11

    receipt = _execute(contract, [[event_1], [advanced, replay]])

    assert receipt["semantic_status"] == "PASS"
    broker = receipt["broker_evidence"]
    assert broker["restart_replay_count"] == 1
    assert broker["restart_duplicate_effect_count"] == 0
    assert broker["checkpoint_behind_observed_count"] == 0


def test_wrong_topic_is_a_measured_oracle_violation() -> None:
    contract = _compile()
    wrong = _event(
        "evt-1",
        offset=10,
        checkpoint=10,
        sequence=1,
        topic="payments.events",
    )

    receipt = _execute(contract, [[wrong], [wrong]])

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "VIOLATION"
    assert BROKER_TOPIC_MISMATCH in receipt["semantic_reason_codes"]
    assert receipt["broker_evidence"]["topic_mismatch_count"] == 1


def test_partition_offset_collision_is_not_hidden_by_distinct_event_ids() -> None:
    contract = _compile()
    event_1 = _event("evt-1", offset=10, checkpoint=10, sequence=1)
    event_2 = _event(
        "evt-2",
        offset=10,
        checkpoint=10,
        sequence=2,
        effect_applied=False,
        payload_status="duplicate",
    )

    receipt = _execute(contract, [[event_1, event_2], [event_1, event_2]])

    assert receipt["semantic_status"] == "VIOLATION"
    assert BROKER_PARTITION_OFFSET_CONFLICT in receipt["semantic_reason_codes"]
    assert receipt["broker_evidence"]["partition_offset_conflict_count"] == 1


def test_checkpoint_regression_and_behind_observed_are_distinct() -> None:
    contract = _compile()
    first = _event("evt-1", offset=10, checkpoint=11, sequence=1)
    second = deepcopy(first)
    second["broker"]["checkpoint"] = 9

    receipt = _execute(contract, [[first], [second]])

    assert receipt["semantic_status"] == "VIOLATION"
    assert BROKER_CHECKPOINT_REGRESSION in receipt["semantic_reason_codes"]
    assert BROKER_CHECKPOINT_BEHIND_OBSERVED in receipt["semantic_reason_codes"]
    broker = receipt["broker_evidence"]
    assert broker["checkpoint_regression_count"] == 1
    assert broker["checkpoint_behind_observed_count"] == 1


def test_dlq_sequence_and_restart_duplicate_effect_are_measured() -> None:
    contract = _compile()
    first = _event(
        "evt-1",
        offset=10,
        checkpoint=11,
        sequence=2,
        consumer_epoch="epoch-1",
        effect_applied=True,
    )
    second = _event(
        "evt-2",
        offset=11,
        checkpoint=11,
        sequence=1,
        state="DLQ",
        dead_letter_topic="orders.events.DLQ",
        consumer_epoch="epoch-2",
        effect_applied=True,
    )

    receipt = _execute(contract, [[first], [first, second]])

    assert receipt["semantic_status"] == "VIOLATION"
    assert BROKER_DLQ_DELIVERY_UNEXPECTED in receipt["semantic_reason_codes"]
    assert BROKER_SEQUENCE_ORDER_VIOLATION in receipt["semantic_reason_codes"]
    assert (
        BROKER_RESTART_DEDUPLICATION_VIOLATION
        in receipt["semantic_reason_codes"]
    )
    broker = receipt["broker_evidence"]
    assert broker["dlq_delivery_count"] == 1
    assert broker["sequence_order_violation_count"] == 1
    assert broker["restart_duplicate_effect_count"] == 1


def test_runtime_receipt_contains_only_broker_fingerprints_and_counts() -> None:
    raw = _raw_wait()
    raw["event_transition"]["broker_delivery"]["expected_topic"] = (
        "orders.secret.topic"
    )
    raw["event_transition"]["broker_delivery"]["expected_consumer_group"] = (
        "consumer-secret-group"
    )
    contract = _compile(raw)
    event = _event(
        "evt-secret",
        offset=987654,
        checkpoint=987654,
        sequence=1,
        topic="orders.secret.topic",
        consumer_group="consumer-secret-group",
    )

    receipt = _execute(contract, [[event], [event]])
    rendered = json.dumps(receipt, ensure_ascii=False, sort_keys=True)

    assert receipt["semantic_status"] == "PASS"
    assert "orders.secret.topic" not in rendered
    assert "consumer-secret-group" not in rendered
    assert "987654" not in rendered
    assert receipt["broker_evidence"]["topic_fingerprints"]
    assert receipt["broker_evidence"]["consumer_group_fingerprints"]
