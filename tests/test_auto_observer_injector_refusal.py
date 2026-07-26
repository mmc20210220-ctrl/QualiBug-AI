"""An injected observer must observe something, or refuse to exist.

``auto_observer_injector`` was dead code -- all five public functions had zero external
references, not even a test -- and the reason is written into the compiler it was meant
to serve:

    # A synthesized observer observes nothing. Without a source-declared
    # effect read there is no way to tell whether the write took effect.
    return blocked_experiment(oid, "BLOCKED_MISSING_OBSERVER", "write_observer")

That refusal is correct, and the module's own fallback was exactly what it refuses.
``build_http_state_observer`` returned an observer with
``observation_mode: "response_body_only"`` whenever no read endpoint was found, reading
only the write's own response. An assertion like "cancelling an order releases its
inventory" would then be checked against the cancel response, pass on a 200, and never
look at inventory -- the write's own claim standing in for evidence that it took effect.

So the fallback is removed rather than the gate weakened. A discovered observer is a
different thing from a synthesized one: the read is a GET the source specification
actually declares over the entity the write mutates, and that distinction is recorded.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.auto_observer_injector import (
    build_http_state_observer,
    find_read_endpoint_for_write,
    inject_observers_for_experiment,
    should_skip_observer_block,
)


def _op(op_id, method, path, read_write=""):
    node = {"id": op_id, "method": method, "path": path}
    if read_write:
        node["read_write"] = read_write
    return node


# ── refusal ─────────────────────────────────────────────────────────────────

def test_no_read_endpoint_yields_no_observer() -> None:
    """The whole point. An observer over nothing is worse than no observer: it turns a
    blocked obligation into a passing one without adding a single observation."""
    assert build_http_state_observer(_op("w1", "POST", "/api/orders"), None) is None


def test_a_read_op_without_a_path_yields_no_observer() -> None:
    assert build_http_state_observer(_op("w1", "POST", "/api/orders"), {"id": "r1"}) is None


def test_an_unidentified_write_yields_no_observer() -> None:
    """Evidence bound to a write nobody can name is untraceable."""
    observer = build_http_state_observer(
        {"method": "POST", "path": "/api/orders"},
        _op("r1", "GET", "/api/orders"),
    )
    assert observer is None


def test_response_body_only_mode_is_never_emitted() -> None:
    """Pinned behaviourally rather than by grepping for the word.

    The name still appears in the module docstring, which explains the defect that was
    removed -- that is documentation worth keeping. What must not come back is an
    observer carrying that mode, so this drives the function over every shape that used
    to produce one.
    """
    for read_op in (None, {}, {"id": "r1"}, {"id": "r1", "path": ""}):
        observer = build_http_state_observer(_op("w1", "POST", "/api/orders"), read_op)
        assert observer is None, read_op

    observer = build_http_state_observer(
        _op("w1", "POST", "/api/orders/{id}/cancel"),
        _op("r1", "GET", "/api/orders/{id}"),
    )
    assert observer["observation_mode"] == "before_after_comparison"


# ── a real discovered observer ──────────────────────────────────────────────

def test_declared_read_produces_a_before_after_observer() -> None:
    observer = build_http_state_observer(
        _op("w1", "POST", "/api/orders/{id}/cancel"),
        _op("r1", "GET", "/api/orders/{id}"),
        actor_ref="buyer",
    )
    assert observer is not None
    assert observer["observation_mode"] == "before_after_comparison"
    assert observer["observation_basis"] == "discovered_source_declared_read"
    assert observer["read_path"] == "/api/orders/{id}"
    assert observer["write_path"] == "/api/orders/{id}/cancel"
    assert observer["actor_ref"] == "buyer"


def test_read_discovery_prefers_the_same_entity_collection() -> None:
    ir = {"operations": [
        _op("r_products", "GET", "/api/products"),
        _op("r_orders", "GET", "/api/orders/{id}"),
        _op("r_orders_list", "GET", "/api/orders"),
    ]}
    read = find_read_endpoint_for_write(_op("w1", "POST", "/api/orders/{id}/cancel"), ir)
    assert read is not None
    assert "/api/orders" in read["path"], read
    assert read["id"] != "r_products", "a write on orders must not observe products"


def test_read_discovery_requires_a_shared_prefix() -> None:
    """No shared path prefix means no relationship the source declared."""
    ir = {"operations": [_op("r1", "GET", "/api/products")]}
    assert find_read_endpoint_for_write(_op("w1", "POST", "/api/refunds"), ir) is None


def test_a_read_operation_is_never_treated_as_a_write() -> None:
    ir = {"operations": [_op("r1", "GET", "/api/orders")]}
    assert find_read_endpoint_for_write(_op("w1", "GET", "/api/orders"), ir) is None


# ── injection into an experiment ────────────────────────────────────────────

def test_unobservable_writes_are_recorded_not_dropped() -> None:
    """The caller must be able to say WHICH write had no effect read."""
    ir = {"operations": [_op("w1", "POST", "/api/refunds")]}
    exp = {
        "treatment_plan": [{"operation_ref": "w1", "method": "POST", "actor_ref": "buyer"}],
    }
    result = inject_observers_for_experiment(exp, ir)
    assert not result.get("observers")
    assert result["_observer_injection_unobservable_writes"] == ["/api/refunds"]


def test_observers_are_injected_when_a_read_exists() -> None:
    ir = {"operations": [
        _op("w1", "POST", "/api/orders/{id}/cancel"),
        _op("r1", "GET", "/api/orders/{id}"),
    ]}
    exp = {
        "treatment_plan": [{"operation_ref": "w1", "method": "POST", "actor_ref": "buyer"}],
    }
    result = inject_observers_for_experiment(exp, ir)
    assert len(result["observers"]) == 1
    assert result["_auto_injected_observers"] is True
    assert result["observers"][0]["observation_basis"] == "discovered_source_declared_read"


def test_existing_observers_are_never_overwritten() -> None:
    """A source-declared observer outranks a discovered one."""
    ir = {"operations": [
        _op("w1", "POST", "/api/orders/{id}/cancel"),
        _op("r1", "GET", "/api/orders/{id}"),
    ]}
    exp = {
        "observers": [{"observer_type": "source_declared"}],
        "treatment_plan": [{"operation_ref": "w1", "method": "POST"}],
    }
    result = inject_observers_for_experiment(exp, ir)
    assert result["observers"] == [{"observer_type": "source_declared"}]
    assert not result.get("_auto_injected_observers")


def test_a_read_only_experiment_gets_no_injection() -> None:
    ir = {"operations": [_op("r1", "GET", "/api/orders")]}
    exp = {"treatment_plan": [{"operation_ref": "r1", "method": "GET"}]}
    assert not inject_observers_for_experiment(exp, ir).get("observers")


# ── the skip gate keeps its own refusal ─────────────────────────────────────

def test_skip_requires_actually_injected_observers() -> None:
    assert should_skip_observer_block({"treatment_plan": []}) is False


def test_skip_is_refused_for_a_potentially_irreversible_delete() -> None:
    """A discovered read proves the state changed; it does not make the write undoable."""
    exp = {
        "_auto_injected_observers": True,
        "treatment_plan": [{"method": "DELETE", "path": "/api/orders/{id}"}],
    }
    assert should_skip_observer_block(exp) is False


def test_skip_is_allowed_for_a_reversible_write() -> None:
    exp = {
        "_auto_injected_observers": True,
        "treatment_plan": [{"method": "POST", "path": "/api/orders/{id}/cancel"}],
    }
    assert should_skip_observer_block(exp) is True
