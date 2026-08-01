from __future__ import annotations

from ai_test_asset_center.process_graph_resume import (
    GRAPH_RESUME_STATE_INVALID,
    recover_process_graph_runtime,
)
from ai_test_asset_center.process_graph_resume_checkpoint import (
    GRAPH_RESUME_AUTHORITY,
    GRAPH_RESUME_SCHEMA,
)


def test_same_execution_with_different_graph_id_fails_closed() -> None:
    checkpoint = {
        "schema_version": GRAPH_RESUME_SCHEMA,
        "authority": GRAPH_RESUME_AUTHORITY,
        "experiment_id": "exp-order-resume",
        "obligation_id": "obl-order-resume",
        "campaign_id": "campaign-order-resume",
        "execution_id": "run-order-resume",
        "execution_graph_id": "graph-original",
    }
    graph = {
        "execution_graph_id": "graph-replacement",
        "process_id": "process-order-resume",
        "topological_order": ["replacement_write"],
        "nodes": [
            {
                "node_id": "replacement_write",
                "step_id": "replacement_write",
                "method": "POST",
            }
        ],
        "edges": [],
    }
    treatment_plan = [
        {
            "step_id": "replacement_write",
            "node_id": "replacement_write",
            "method": "POST",
            "_execution_graph": graph,
        }
    ]
    runtime = {
        "status": "READY",
        "execution_graph_id": "graph-replacement",
        "process_id": "process-order-resume",
        "topological_order": ["replacement_write"],
        "predecessors": {"replacement_write": []},
        "wave_by_node": {"replacement_write": 0},
        "nodes": {"replacement_write": graph["nodes"][0]},
        "target_contexts": {},
        "node_status": {"replacement_write": "PENDING"},
        "binding_ledger": {
            "schema_version": "qualibug.process-graph-binding-ledger.v1",
            "execution_graph_id": "graph-replacement",
            "outputs_by_node": {},
            "consumptions": [],
            "unresolved": [],
        },
    }

    recovered = recover_process_graph_runtime(
        graph=graph,
        treatment_plan=treatment_plan,
        runtime=runtime,
        observations={
            "process_graph_resume_checkpoint": checkpoint,
            "process_step_receipts": [],
        },
        experiment_id="exp-order-resume",
        obligation_id="obl-order-resume",
        campaign_id="campaign-order-resume",
        execution_id="run-order-resume",
    )

    assert recovered["reason_code"] == GRAPH_RESUME_STATE_INVALID
    assert recovered["detail"] == (
        "resume_checkpoint_execution_graph_id_mismatch"
    )
