from __future__ import annotations

"""Run the real four-pass discovery policy evaluation on a frozen manifest."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_test_asset_center.autonomous_evolution_orchestrator import EvolutionOrchestrator  # noqa: E402
from ai_test_asset_center.discovery_harness_proposer import apply_bounded_harness_edit  # noqa: E402
from ai_test_asset_center.discovery_policy_evaluation_runner import (  # noqa: E402
    DiscoveryPolicyEvaluationRunner,
    TrustedObservationStore,
)
from ai_test_asset_center.evaluation_fixture_controller import GovernedHttpResetFixtureController  # noqa: E402
from ai_test_asset_center.evaluator_receipt_auth import resolve_evaluator_hmac_key  # noqa: E402
from ai_test_asset_center.observed_product_scan_executor import ObservedProductScanExecutor  # noqa: E402
from ai_test_asset_center.policy_registry import PolicyRecord, PolicyRegistry  # noqa: E402
from ai_test_asset_center.scan_operational_metrics import collect_observed_scan_operational_metrics  # noqa: E402
from benchmark_evaluator.http_observation_gateway import (  # noqa: E402
    EvaluatorHttpObservationGateway,
)


def _edit(args: argparse.Namespace) -> dict[str, Any]:
    value: Any = args.edit_value
    if args.edit_operation == "set_integer":
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("set_integer edit value must be an integer") from exc
    return {"path": args.edit_path, "operation": args.edit_operation, "value": value}


def _candidate(registry: PolicyRegistry, edit: dict[str, Any]) -> tuple[PolicyRecord, PolicyRecord]:
    champion = registry.get_active()
    if champion is None:
        raise RuntimeError("policy registry has no active champion")
    strategy = apply_bounded_harness_edit(champion.strategy, edit)
    signature = hashlib.sha256(
        json.dumps(edit, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    candidate_id = f"policy-eval-{signature}"
    existing = registry._policies.get(candidate_id)
    if existing is not None:
        if existing.parent_policy_version != champion.policy_version:
            raise RuntimeError("existing evaluation candidate belongs to a different champion")
        return champion, existing
    candidate = PolicyRecord(
        policy_id=candidate_id,
        policy_version=f"{champion.policy_version}+candidate.{signature}",
        parent_policy_version=champion.policy_version,
        project_scope="global",
        status="candidate",
        created_reason=f"bounded_observed_evaluation:{edit}",
        strategy=strategy,
    )
    registry.register(candidate)
    return champion, candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--trusted-observation-root", required=True)
    parser.add_argument("--workspace-root", default=str(REPO_ROOT))
    parser.add_argument("--evaluation-id")
    parser.add_argument("--edit-path", required=True)
    parser.add_argument("--edit-operation", choices=("append_unique", "set_integer"), required=True)
    parser.add_argument("--edit-value", required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    signing_key = resolve_evaluator_hmac_key()

    workspace_root = Path(args.workspace_root).resolve()
    trusted_observation_store = TrustedObservationStore(
        Path(args.trusted_observation_root).resolve(),
        product_workspace_root=workspace_root,
        verification_key=signing_key,
    )
    trusted_observation_gateway = EvaluatorHttpObservationGateway(
        observation_root=Path(args.trusted_observation_root).resolve(),
        signing_key=signing_key,
    )
    registry = PolicyRegistry(Path(args.registry).resolve())
    champion, challenger = _candidate(registry, _edit(args))
    controller = GovernedHttpResetFixtureController(workspace_root=workspace_root)
    executor = ObservedProductScanExecutor(
        workspace_root=workspace_root,
        operational_metrics_collector=collect_observed_scan_operational_metrics,
    )
    if args.activate:
        orchestrator = EvolutionOrchestrator.__new__(EvolutionOrchestrator)
        orchestrator.registry = registry
        result = orchestrator.evaluate_and_promote_observed_candidate(
            candidate_policy_id=challenger.policy_id,
            manifest_path=str(Path(args.manifest).resolve()),
            output_root=str(Path(args.output_root).resolve()),
            fixture_controller=controller,
            scan_executor=executor,
            trusted_observation_gateway=trusted_observation_gateway,
            trusted_observation_store=trusted_observation_store,
            receipt_signing_key=signing_key,
            evaluation_id=args.evaluation_id,
        )
    else:
        result = DiscoveryPolicyEvaluationRunner(
            Path(args.manifest).resolve(),
            output_root=Path(args.output_root).resolve(),
            fixture_controller=controller,
            scan_executor=executor,
            trusted_observation_gateway=trusted_observation_gateway,
            trusted_observation_store=trusted_observation_store,
            receipt_signing_key=signing_key,
        ).run(
            champion=champion,
            challenger=challenger,
            evaluation_id=args.evaluation_id,
        )
    summary = {
        "evaluation_id": result["evaluation_id"],
        "comparison_ref": result["comparison_ref"],
        "champion_policy_id": champion.policy_id,
        "challenger_policy_id": challenger.policy_id,
        "promotion_decision": result["promotion_decision"],
        "activation_performed": bool(result.get("activation_performed")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
