"""Capture one already-finalized enterprise-understanding asset for evaluator scoring.

This command never loads Ground Truth and never invokes the product builder. It calls the
single explicit load authority, copies the persisted final asset into an immutable JSON snapshot,
and records the asset fingerprint used by later evaluator runs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from benchmark_evaluator.scored_run_comparison import _fingerprint

SNAPSHOT_RECEIPT_SCHEMA = "qualibug.enterprise-understanding-product-asset-snapshot.v1"


class ProductAssetCaptureError(RuntimeError):
    """A finalized enterprise-understanding asset could not be captured safely."""


def _default_loader(project_id: str, root: Path) -> dict[str, Any] | None:
    # Delayed import keeps evaluator package import declarative. Ground Truth is not loaded in
    # this process and the explicit load authority never enriches or rewrites persisted assets.
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        load_enterprise_business_knowledge_asset,
    )

    return load_enterprise_business_knowledge_asset(project_id, root)


def capture_finalized_product_asset(
    *,
    project_id: str,
    root: str | Path,
    output_path: str | Path,
    loader: Callable[[str, Path], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Write a canonical snapshot of one persisted final asset without rebuilding it."""
    project = str(project_id or "").strip()
    if not project:
        raise ProductAssetCaptureError("project_id_required")
    resolved_root = Path(root).resolve()
    if not resolved_root.exists():
        raise ProductAssetCaptureError(f"product_root_missing:{resolved_root}")
    load = loader or _default_loader
    asset = load(project, resolved_root)
    if not isinstance(asset, dict) or not asset:
        raise ProductAssetCaptureError(
            f"finalized_enterprise_understanding_asset_missing:{project}"
        )
    model = asset.get("enterprise_understanding_model")
    if not isinstance(model, dict) or not model:
        raise ProductAssetCaptureError(
            f"enterprise_understanding_model_missing:{project}"
        )

    # JSON round-trip proves the evaluator snapshot contains only serializable persisted data and
    # prevents accidental mutation of the loader-owned object.
    snapshot = json.loads(json.dumps(asset, ensure_ascii=False, sort_keys=True, default=str))
    receipt = {
        "schema_version": SNAPSHOT_RECEIPT_SCHEMA,
        "project_id": project,
        "asset_id": str(snapshot.get("asset_id") or ""),
        "model_id": str(model.get("model_id") or ""),
        "product_asset_fingerprint": _fingerprint(snapshot),
        "load_authority": (
            "ai_test_asset_center.enterprise_knowledge_center.composition."
            "load_enterprise_business_knowledge_asset"
        ),
        "build_invoked": False,
        "ground_truth_loaded": False,
        "product_asset_rewritten": False,
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    snapshot["_enterprise_understanding_evaluator_snapshot"] = receipt

    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture one persisted final enterprise-understanding asset for evaluation."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = capture_finalized_product_asset(
            project_id=args.project,
            root=args.root,
            output_path=args.output,
        )
    except ProductAssetCaptureError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "PASS", "receipt": receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SNAPSHOT_RECEIPT_SCHEMA",
    "ProductAssetCaptureError",
    "capture_finalized_product_asset",
]
