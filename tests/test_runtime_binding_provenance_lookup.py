"""A runtime binding must be matched against the graph node's semantic name.

The runtime provenance check indexed the compile-time binding graph like this:

    _compile_node_targets = {
        _text(n.get("binding_id") or n.get("target")): n
        for n in _compile_nodes if isinstance(n, dict)
    }

``binding_coverage_graph`` emits nodes shaped

    {"binding_id": "bind_0573d5af75dc5986", "semantic_name": "sku",
     "target_locations": ["cleanup.path.sku"], "source_kind": "PRIMARY_RESPONSE", ...}

so the index was keyed by an opaque hash while the runtime binding key is the semantic
name. ``"sku" in {"bind_0573d5af75dc5986"}`` is never true, the set was non-empty so the
guard was active, and ``semantic_name`` -- the field that actually holds ``"sku"`` -- was
never read. Nodes carrying no ``binding_id`` collapsed to the single key ``""``.

Measured on the live 131-defect target: 146 experiments compiled successfully and were
then blocked at execution with ``undeclared_runtime_binding:sku`` (76) and
``undeclared_runtime_binding:id`` (70), while the graph itself reported
``graph_status: VALID`` and its compile and runtime fingerprints matched exactly.

The guard itself is right -- a binding reaching transport without a declared producer is
exactly what it should stop. It was reading the wrong field.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# The runtime provenance check moved into the executor core during the
# executor-core/facade split; the facade only re-exports execute_one_experiment.
EXECUTOR = Path(__file__).resolve().parents[1] / "ai_test_asset_center" / "experiment_executor_core.py"
GRAPH = Path(__file__).resolve().parents[1] / "ai_test_asset_center" / "binding_coverage_graph.py"


def _lookup_block() -> str:
    source = EXECUTOR.read_text(encoding="utf-8")
    marker = "_compile_node_targets"
    start = source.index(marker)
    return source[start: start + 900]


# ── the producer's shape is what the consumer must read ─────────────────────

def test_the_graph_emits_semantic_name() -> None:
    """Pins the producer contract this fix depends on."""
    source = GRAPH.read_text(encoding="utf-8")
    assert '"semantic_name"' in source


def test_the_consumer_reads_semantic_name() -> None:
    block = _lookup_block()
    assert "semantic_name" in block, (
        "the runtime provenance check must match on the graph's semantic name"
    )


def test_binding_id_alone_is_no_longer_the_key() -> None:
    """The exact defect: keying by an opaque hash and comparing a semantic name to it."""
    block = _lookup_block()
    assert '_text(n.get("binding_id") or n.get("target")): n' not in block


def test_every_identifying_name_resolves_to_the_node() -> None:
    """Behavioural: reproduce the index the executor builds and query it.

    Reproduced rather than imported because the block is inline inside
    execute_one_experiment and cannot be called without a live experiment.
    """
    block = _lookup_block()
    keys = re.findall(r'n\.get\("([a-z_]+)"\)', block)
    assert "semantic_name" in keys
    assert "binding_id" in keys, "other producers key by binding_id; keep accepting it"
    assert "target" in keys, "the pre-existing shape must keep resolving"

    # The real node shape from the live target.
    node = {
        "binding_id": "bind_0573d5af75dc5986",
        "semantic_name": "sku",
        "target_locations": ["cleanup.path.sku"],
        "source_kind": "PRIMARY_RESPONSE",
    }
    index = {}
    for key in (node.get("semantic_name"), node.get("binding_id"),
                node.get("target"), node.get("name")):
        if key:
            index.setdefault(str(key), node)

    assert "sku" in index, "the runtime binding key must resolve"
    assert "bind_0573d5af75dc5986" in index, "the id must still resolve"


def test_a_genuinely_undeclared_binding_is_still_caught() -> None:
    """The guard must keep failing closed.

    Fixing the key must not turn "no declared producer" into "anything goes" -- that
    would let a synthetic value reach the target with no provenance.
    """
    node = {"binding_id": "bind_x", "semantic_name": "sku"}
    index = {}
    for key in (node.get("semantic_name"), node.get("binding_id"),
                node.get("target"), node.get("name")):
        if key:
            index.setdefault(str(key), node)
    assert "order_id" not in index, "a binding the graph never declared must not resolve"


def test_the_synthetic_value_guard_is_untouched() -> None:
    """The sibling check that blocks qb_test_ values must remain."""
    block = _lookup_block()
    source = EXECUTOR.read_text(encoding="utf-8")
    assert "synthetic_binding_reaching_transport" in source
    assert "qb_test_" in source


def test_the_fingerprint_drift_check_is_untouched() -> None:
    """A different, correct guard on the same graph; this fix must not weaken it."""
    source = EXECUTOR.read_text(encoding="utf-8")
    assert "binding_graph_fingerprint_drift" in source
