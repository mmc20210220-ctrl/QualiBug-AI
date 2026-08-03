"""CLI entry for ``python -m ai_test_asset_center``.

Extracted from ``__main__.py`` to keep the module under the architecture
extraction budget (tests/test_architecture_extraction_contracts.py). The
public wrapper ``scan`` stays in ``__main__`` (see AGENTS.md); this module
only owns argument parsing and CLI campaign-context construction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def build_cli_campaign_context(args: Any) -> dict[str, Any]:
    from .private_pilot_scan_context_contract import (
        default_scan_execution_mode,
        default_scan_test_data_contract,
    )

    body = {
        "base_url": getattr(args, "base_url", ""),
        "scope_id": getattr(args, "scope_id", ""),
        "environment_ref": getattr(args, "environment_ref", ""),
        "environment_type": getattr(args, "environment_type", ""),
        "execution_mode": getattr(args, "execution_mode", ""),
    }
    execution_mode = (
        str(getattr(args, "execution_mode", "") or "").strip()
        or default_scan_execution_mode(body)
    )
    body["execution_mode"] = execution_mode
    test_data_contract: dict[str, Any] = {}
    strategy = str(getattr(args, "test_data_strategy", "") or "").strip()
    if strategy:
        test_data_contract["strategy"] = strategy
        if strategy in {"create_disposable", "approved_fixture_setup"} and execution_mode == "approved_sandbox_write":
            test_data_contract["write_approved"] = True
            if strategy == "create_disposable":
                scope_ref = str(
                    getattr(args, "scope_id", "")
                    or getattr(args, "environment_ref", "")
                    or ""
                ).strip()
                if scope_ref:
                    test_data_contract["disposable_scope_ref"] = scope_ref
    else:
        test_data_contract = default_scan_test_data_contract(body)
    context = {
        "scope_id": getattr(args, "scope_id", ""),
        "environment_ref": getattr(args, "environment_ref", ""),
        "environment_type": getattr(args, "environment_type", ""),
        "source_manifest": {
            "source_id": getattr(args, "source_id", ""),
            "source_hash": getattr(args, "source_hash", ""),
            "source_version_id": getattr(args, "source_version_id", ""),
        },
        "execution_approval_id": getattr(args, "execution_approval_id", ""),
        "execution_mode": execution_mode,
    }
    if test_data_contract:
        context["test_data_contract"] = test_data_contract
    return context


def run_cli() -> None:
    from .credential_crypto import ensure_credential_key

    ensure_credential_key()

    parser = argparse.ArgumentParser(description="QualiBug enterprise source-grounded scanner")
    parser.add_argument("scan", nargs="?", default="scan")
    parser.add_argument("--project", required=True)
    parser.add_argument("--api-doc")
    parser.add_argument("--api-doc-text")
    parser.add_argument("--prd", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--scope-id", default="")
    parser.add_argument("--environment-ref", default="")
    parser.add_argument("--environment-type", default="")
    parser.add_argument("--source-id", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--source-version-id", default="")
    parser.add_argument("--execution-approval-id", default="")
    parser.add_argument("--execution-mode", default="")
    parser.add_argument("--test-data-strategy", default="")
    parser.add_argument("--ci-gate", action="store_true")
    parser.add_argument("--no-multi-layer", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    from .__main__ import scan

    context = build_cli_campaign_context(args)
    result = scan(project=args.project, api_doc_path=args.api_doc or "", api_doc_text=args.api_doc_text or "", prd_text=args.prd, base_url=args.base_url, ci_gate=args.ci_gate, multi_layer=not args.no_multi_layer, output_dir=Path(args.output_dir) if args.output_dir else None, save_report=not args.no_report, campaign_context=context)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif result.get("success"):
        campaign = result.get("campaign", {})
        print(f"QualiBug scan: {result['project']}")
        print(f"Confirmed: {result['total_findings']} | Candidates: {result['total_candidates']} | Execution: {result['execution_status']}")
        print(f"Release gate: {result.get('release_gate', {}).get('verdict', 'not_ready')}")
        print(f"Campaign: {campaign.get('campaign_id', 'n/a')} ({campaign.get('campaign_status', 'n/a')})")
    else:
        print(f"Error: {result.get('error', 'scan failed')}", file=sys.stderr)
    raise SystemExit(0 if result.get("success") else 1)
