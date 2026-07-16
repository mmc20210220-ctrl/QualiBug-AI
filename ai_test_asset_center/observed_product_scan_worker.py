from __future__ import annotations

"""Isolated product worker for evaluator-owned observed scan runs."""

import argparse
import importlib
import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any

from .observed_product_scan_executor import ObservedProductScanExecutor
from .observed_product_scan_protocol import PRODUCT_SCAN_WORKER_REQUEST_SCHEMA
from .policy_registry import (
    DiscoveryPolicy,
    ExecutionPolicy,
    ReasonerPolicy,
    StrategyBundle,
    VerificationPolicy,
)
from .policy_wiring import policy_strategy_override


def _allowed(cls: type[Any], value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    names = {item.name for item in fields(cls)}
    return {key: raw[key] for key in raw if key in names}


def _strategy(value: Any) -> StrategyBundle:
    raw = value if isinstance(value, dict) else {}
    return StrategyBundle(
        reasoner=ReasonerPolicy(**_allowed(ReasonerPolicy, raw.get("reasoner"))),
        discovery=DiscoveryPolicy(**_allowed(DiscoveryPolicy, raw.get("discovery"))),
        verification=VerificationPolicy(
            **_allowed(VerificationPolicy, raw.get("verification"))
        ),
        execution=ExecutionPolicy(**_allowed(ExecutionPolicy, raw.get("execution"))),
    )


def _callable(value: Any) -> Any:
    ref = value if isinstance(value, dict) else {}
    module_name = str(ref.get("module") or "").strip()
    qualname = str(ref.get("qualname") or "").strip()
    if not module_name or not qualname or "<locals>" in qualname:
        raise ValueError("operational metrics collector reference is invalid")
    target: Any = importlib.import_module(module_name)
    for segment in qualname.split("."):
        target = getattr(target, segment)
    if not callable(target):
        raise TypeError("operational metrics collector reference is not callable")
    return target


def execute_request(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("schema_version") != PRODUCT_SCAN_WORKER_REQUEST_SCHEMA:
        raise ValueError("observed product worker request schema is invalid")
    workspace_root = Path(str(request.get("workspace_root") or "")).resolve()
    if not workspace_root.is_dir():
        raise ValueError(f"observed product workspace not found: {workspace_root}")
    invocation = request.get("invocation")
    if not isinstance(invocation, dict):
        raise TypeError("observed product worker invocation must be an object")
    executor = ObservedProductScanExecutor(
        workspace_root=workspace_root,
        operational_metrics_collector=_callable(
            request.get("operational_metrics_collector")
        ),
    )
    with policy_strategy_override(_strategy(request.get("strategy"))):
        return executor._execute_in_process(**invocation)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).resolve()
    output_path = Path(args.output).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise TypeError("observed product worker request must be an object")
    result = execute_request(request)
    temporary = output_path.with_suffix(output_path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
