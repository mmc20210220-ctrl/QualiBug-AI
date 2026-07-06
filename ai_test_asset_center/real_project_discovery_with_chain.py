from __future__ import annotations

"""Chain-aware real project discovery runner.

The original discovery engine owns execution and issue generation. This module
keeps that engine intact, then immediately evaluates the enterprise evidence
chain contract so every chain-aware run answers:

- were enterprise inputs present?
- did parsing/knowledge materialize?
- was a test plan generated?
- did probes execute?
- did validated bugs exist?
- did evidence link back to issues?
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .main_chain_contract import evaluate_main_chain_contract
from .real_project_defect_discovery import run_real_project_discovery as _run_real_project_discovery
from .real_project_onboarding import ROOT, _safe_project_id


def run_real_project_discovery_with_chain(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    """Run real-project discovery and attach the main chain contract."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _run_real_project_discovery(project, root)
    contract = evaluate_main_chain_contract(project, root, persist=True)
    if isinstance(data, dict):
        data["main_chain_contract"] = contract
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        summary["main_chain_ready"] = bool(contract.get("chain_ready"))
        summary["main_chain_first_blocked_stage"] = str((contract.get("summary") or {}).get("first_blocked_stage") or "")
        summary["main_chain_first_blocked_next_action"] = str((contract.get("summary") or {}).get("first_blocked_next_action") or "")
        data["summary"] = summary
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run real-project discovery and evaluate the main evidence chain contract")
    parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")
    parser.add_argument("--json", action="store_true", help="Print the full discovery result as JSON")
    args = parser.parse_args(argv)

    result = run_real_project_discovery_with_chain(args.project_id)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        contract = result.get("main_chain_contract", {}) if isinstance(result, dict) else {}
        summary = contract.get("summary", {}) if isinstance(contract, dict) else {}
        print(json.dumps({
            "project_id": args.project_id,
            "status": result.get("status") if isinstance(result, dict) else "unknown",
            "chain_ready": bool(contract.get("chain_ready")) if isinstance(contract, dict) else False,
            "customer_defect_delivery_ready": bool(contract.get("customer_defect_delivery_ready")) if isinstance(contract, dict) else False,
            "first_blocked_stage": summary.get("first_blocked_stage"),
            "first_blocked_next_action": summary.get("first_blocked_next_action"),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
