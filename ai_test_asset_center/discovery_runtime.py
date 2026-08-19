"""Single-authority discovery planning and experiment-candidate runtime.

Planning lives in ``discovery_runtime_planning``; execution lives in
``discovery_runtime_execution``. This module re-exports the public surface for
compatibility with ``v12_pipeline`` and existing tests. The public planning
entry installs exact accepted rule/interface identity binding before any plan
is compiled. The public execution entry projects receipt-backed evidence and an
honest loss funnel without creating findings or inventing quality metrics.

The formal UI surface is installed on the same experiment mainline as API and
persistence obligations. It registers a source-declared browser protocol,
typed observer and assertion kind. The read-only guard blocks click/fill/select
plans until browser-side cleanup equivalence exists. Importing this module
registers capability only; it opens no browser and performs no target I/O.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .formal_ui_surface import install_formal_ui_surface
from .formal_ui_surface_guard import install_formal_ui_read_only_guard
from .reasoner_graph_context import (
    install_reasoner_graph_context_bridge,
    reasoner_graph_context_scope,
)
from .scan_stage_progress import (
    begin_scan_stage_progress,
    mark_scan_stage,
)

install_formal_ui_surface()
install_formal_ui_read_only_guard()
install_reasoner_graph_context_bridge()

from .discovery_runtime_execution import (  # noqa: E402,F401
    RUNTIME_SCHEMA,
    _authority_findings,
    _legacy_execution_terminal,
    _manual_terminal_receipts,
    _project_gate_results_for_authority,
)
from .discovery_runtime_planning import (  # noqa: E402,F401
    _api_operations,
    _campaign_object,
    _campaign_store,
    _contract,
    _runtime_actors,
)
from .recall_execution_variant_authority import (  # noqa: E402
    install_exact_execution_variant_authority,
)

# Coverage-unit planning may fan one semantic unit into several compiler input
# variants and actor arms. Install the exact execution-face identity authority
# before the semantic-binding facade captures the planning entrypoint, so every
# compiled face reaches transport without a lossy obligation-id dict collision.
install_exact_execution_variant_authority()

from .discovery_runtime_semantic_binding import (  # noqa: E402
    build_discovery_plan as _build_discovery_plan,
)
from .formal_event_execution_outcome_bridge import (  # noqa: E402
    install_formal_event_execution_outcome_bridge,
)
from .formal_event_binding_receipt_bridge import (  # noqa: E402
    install_formal_event_binding_receipt_bridge,
)
from .formal_event_observation_count_bridge import (  # noqa: E402
    install_formal_event_observation_count_bridge,
)
from .formal_event_verdict_reason_bridge import (  # noqa: E402
    install_formal_event_verdict_reason_bridge,
)

# Semantic binding registers the event observer, assertion and pre-cleanup wrapper first.
# Write-state observation remains governed by the existing HTTP/DB authority; the Event
# observer is an additional effect assertion, never a substitute for cleanup equivalence.
# Receipt wrappers preserve durable identity and privacy-safe total cardinality; the verdict
# wrapper explains violations, and the outcome wrapper keeps a measured timeout EXECUTED
# without promoting it to a Bug.
install_formal_event_binding_receipt_bridge()
install_formal_event_observation_count_bridge()
install_formal_event_verdict_reason_bridge()
install_formal_event_execution_outcome_bridge()

from .discovery_runtime_quality_projection import (  # noqa: E402
    run_experiment_candidate as _run_experiment_candidate,
)


def _progress_scope(inputs: Any) -> tuple[Path, str]:
    return Path(getattr(inputs, "root")), str(getattr(inputs, "project", "")).strip()


def _reasoner_scope(inputs: Any, campaign_handle: Any) -> dict[str, str]:
    context = getattr(inputs, "campaign_context", {})
    context = context if isinstance(context, dict) else {}
    run_id = str(
        context.get("run_id")
        or getattr(campaign_handle, "run_id", "")
        or ""
    ).strip()
    return {
        "environment_id": str(
            context.get("environment_id")
            or context.get("environment_type")
            or "test"
        ).strip() or "test",
        "run_id": run_id,
        "policy_version": str(context.get("policy_version") or "").strip(),
    }


def build_discovery_plan(inputs: Any, campaign_handle: Any) -> Any:
    """Run the real semantic/planning authority and publish only observed boundaries."""

    root, project = _progress_scope(inputs)
    begin_scan_stage_progress(root, project)
    mark_scan_stage(
        root,
        project,
        "enterprise_understanding",
        "active",
        detail="企业资料正在进入统一语义与行为 IR 规划主链",
    )
    # Semantic binding and obligation/scenario compilation occur inside the same
    # governed planning call. They overlap in one function boundary, so both are
    # active together instead of inventing a false sequential percentage.
    mark_scan_stage(
        root,
        project,
        "scenario_planning",
        "active",
        detail="行为义务、场景与实验计划正在同一规划主链编译",
    )
    graph_scope = _reasoner_scope(inputs, campaign_handle)
    try:
        with reasoner_graph_context_scope(
            project_id=project,
            environment_id=graph_scope["environment_id"],
            root=root,
            run_id=graph_scope["run_id"],
            policy_version=graph_scope["policy_version"],
        ):
            plan = _build_discovery_plan(inputs, campaign_handle)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:180]}"
        mark_scan_stage(root, project, "enterprise_understanding", "failed", detail=detail)
        mark_scan_stage(root, project, "scenario_planning", "failed", detail=detail)
        raise
    mark_scan_stage(
        root,
        project,
        "enterprise_understanding",
        "completed",
        detail="企业资料已完成本轮统一规划输入解析",
    )
    mark_scan_stage(
        root,
        project,
        "scenario_planning",
        "completed",
        detail="本轮义务、场景与实验计划已编译",
    )
    return plan


def run_experiment_candidate(inputs: Any, campaign_handle: Any, plan: Any) -> dict[str, Any]:
    """Run the real experiment authority and expose execution/evidence activity."""

    root, project = _progress_scope(inputs)
    mark_scan_stage(
        root,
        project,
        "runtime_execution",
        "active",
        detail="正式实验 runner 正在执行真实探针与受控操作",
    )
    mark_scan_stage(
        root,
        project,
        "evidence_collection",
        "active",
        detail="Observer、断言与运行回执随真实实验同步采集",
    )
    try:
        result = _run_experiment_candidate(inputs, campaign_handle, plan)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:180]}"
        mark_scan_stage(root, project, "runtime_execution", "failed", detail=detail)
        mark_scan_stage(root, project, "evidence_collection", "failed", detail=detail)
        raise
    mark_scan_stage(
        root,
        project,
        "runtime_execution",
        "completed",
        detail="正式实验 runner 已返回真实执行回执",
    )
    # Evidence remains ACTIVE here because the caller still performs UI execution,
    # evidence graph normalization and final evidence persistence after the runner.
    return result


__all__ = [
    "RUNTIME_SCHEMA",
    "build_discovery_plan",
    "run_experiment_candidate",
]
