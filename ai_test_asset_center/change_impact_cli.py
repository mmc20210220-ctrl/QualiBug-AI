"""CLI for source-version change impact planning.

Example:
  python -m ai_test_asset_center.change_impact_cli \
    --project <project> --base-hash <sha256> --head-hash <sha256> --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .change_impact import compare_source_versions
from .enterprise_source_registry import list_source_assets


def _manifest_for_hash(project: str, source_hash: str, root: Path) -> dict[str, str]:
    for asset in list_source_assets(project, root=root):
        if str(asset.get("latest_source_hash") or "") == source_hash:
            return {
                "source_id": str(asset.get("source_id") or ""),
                "source_hash": source_hash,
                "source_version_id": str(asset.get("latest_version_id") or ""),
            }
    return {"source_hash": source_hash}


def main() -> None:
    parser = argparse.ArgumentParser(description="QualiBug registered-source change impact planner")
    parser.add_argument("--project", required=True)
    parser.add_argument("--base-hash", required=True)
    parser.add_argument("--head-hash", required=True)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(args.workspace_root).resolve()
    result = compare_source_versions(
        args.project,
        root=root,
        base_manifest=_manifest_for_hash(args.project, args.base_hash, root),
        head_manifest=_manifest_for_hash(args.project, args.head_hash, root),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        print(f"Changed operations: {summary['changed_operation_count']}")
        for impact in result["impacts"]:
            print(f"- {impact['change_kind']}: {impact['method']} {impact['path']}")
        for gap in result["coverage_gaps"]:
            print(f"- review gap: {gap['code']}")


if __name__ == "__main__":
    main()
