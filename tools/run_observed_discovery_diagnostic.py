from __future__ import annotations

"""Run one evaluator-authenticated target diagnostic without promotion."""

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_test_asset_center.discovery_policy_evaluation_runner import (  # noqa: E402
    DiscoveryPolicyEvaluationRunner,
    TrustedObservationStore,
)
from ai_test_asset_center.evaluation_fixture_controller import (  # noqa: E402
    GovernedHttpResetFixtureController,
)
from ai_test_asset_center.evaluator_receipt_auth import (  # noqa: E402
    resolve_evaluator_hmac_key,
)
from ai_test_asset_center.observed_product_scan_executor import (  # noqa: E402
    ObservedProductScanExecutor,
)
from ai_test_asset_center.policy_registry import PolicyRegistry  # noqa: E402
from ai_test_asset_center.scan_operational_metrics import (  # noqa: E402
    collect_observed_scan_operational_metrics,
)
from benchmark_evaluator.http_observation_gateway import (  # noqa: E402
    EvaluatorHttpObservationGateway,
)
from benchmark_evaluator.windows_fixture_controller import (  # noqa: E402
    WindowsBenchmarkFixtureController,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--trusted-observation-root", required=True)
    parser.add_argument("--workspace-root", default=str(REPO_ROOT))
    parser.add_argument("--evaluation-mode", choices=("replay", "shadow"), default="replay")
    parser.add_argument("--evaluation-id")
    parser.add_argument("--hmac-key-file")
    parser.add_argument(
        "--fixture-controller",
        choices=("http", "windows-benchmark"),
        default="http",
    )
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    observation_root = Path(args.trusted_observation_root).resolve()
    key_path = (
        Path(args.hmac_key_file).resolve()
        if args.hmac_key_file
        else None
    )
    if key_path is not None:
        if key_path == workspace_root or workspace_root in key_path.parents:
            raise RuntimeError("evaluator HMAC key file must be outside product workspace")
        if not key_path.is_file():
            raise FileNotFoundError(f"evaluator HMAC key file not found: {key_path}")
        signing_key = resolve_evaluator_hmac_key(key_path.read_bytes())
    else:
        signing_key = resolve_evaluator_hmac_key()
    policy = PolicyRegistry(Path(args.registry).resolve()).get_active()
    if policy is None:
        raise RuntimeError("policy registry has no active policy")
    store = TrustedObservationStore(
        observation_root,
        product_workspace_root=workspace_root,
        verification_key=signing_key,
    )
    gateway = EvaluatorHttpObservationGateway(
        observation_root=observation_root,
        signing_key=signing_key,
    )
    fixture_controller = (
        WindowsBenchmarkFixtureController(workspace_root=workspace_root)
        if args.fixture_controller == "windows-benchmark"
        else GovernedHttpResetFixtureController(workspace_root=workspace_root)
    )
    runner = DiscoveryPolicyEvaluationRunner(
        Path(args.manifest).resolve(),
        output_root=Path(args.output_root).resolve(),
        fixture_controller=fixture_controller,
        scan_executor=ObservedProductScanExecutor(
            workspace_root=workspace_root,
            operational_metrics_collector=(
                collect_observed_scan_operational_metrics
            ),
        ),
        trusted_observation_gateway=gateway,
        trusted_observation_store=store,
        receipt_signing_key=signing_key,
        require_commercial_shape=False,
    )
    report = runner.run_target_diagnostic(
        policy=policy,
        target_id=args.target_id,
        evaluation_mode=args.evaluation_mode,
        evaluation_id=args.evaluation_id,
    )
    print(json.dumps({
        "diagnostic_only": True,
        "schema_version": report.get("schema_version"),
        "dataset_id": report.get("dataset_id"),
        "policy_id": report.get("policy_id"),
        "evaluation_mode": report.get("evaluation_mode"),
        "claim_status": report.get("claim_status"),
        "commercial_promotion_evidence_ready": report.get(
            "commercial_promotion_evidence_ready"
        ),
        "evaluated_target_count": report.get("evaluated_target_count"),
        "not_measured_targets": report.get("not_measured_targets"),
        "held_in": report.get("held_in"),
        "held_out": report.get("held_out"),
        "pipeline_degraded_target_count": report.get(
            "pipeline_degraded_target_count"
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
