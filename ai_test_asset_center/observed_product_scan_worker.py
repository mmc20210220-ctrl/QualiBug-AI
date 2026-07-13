from __future__ import annotations

"""Secret-free subprocess entrypoint for a real product discovery scan."""

import json
import os
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

from .observed_product_scan_protocol import (
    PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
    is_evaluator_secret_environment_name,
)


def _assert_secret_free_environment() -> None:
    leaked = sorted(
        name
        for name in os.environ
        if is_evaluator_secret_environment_name(name)
    )
    if leaked:
        raise RuntimeError(
            "product scan worker received evaluator-owned environment variables: "
            + ",".join(leaked)
        )


def _load_request(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("product scan worker request must be an object")
    expected_fields = {
        "schema_version",
        "strategy",
        "strategy_fingerprint",
        "scan_kwargs",
    }
    if set(payload) != expected_fields:
        raise ValueError("product scan worker request fields are invalid")
    if payload.get("schema_version") != PRODUCT_SCAN_WORKER_REQUEST_SCHEMA:
        raise ValueError("product scan worker request schema is unsupported")
    return payload


def _policy_section(cls: type[Any], value: Any, section: str) -> Any:
    if not isinstance(value, dict):
        raise ValueError(f"product scan strategy {section} must be an object")
    allowed = {field.name for field in fields(cls)}
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(
            f"product scan strategy {section} contains unsupported fields: "
            + ",".join(unexpected)
        )
    return cls(**value)


def _strategy_from_request(payload: Any) -> Any:
    from .policy_registry import (
        DiscoveryPolicy,
        ExecutionPolicy,
        ReasonerPolicy,
        StrategyBundle,
        VerificationPolicy,
    )

    if not isinstance(payload, dict):
        raise ValueError("product scan strategy must be an object")
    if set(payload) != {"reasoner", "discovery", "verification", "execution"}:
        raise ValueError("product scan strategy sections are invalid")
    return StrategyBundle(
        reasoner=_policy_section(ReasonerPolicy, payload["reasoner"], "reasoner"),
        discovery=_policy_section(
            DiscoveryPolicy,
            payload["discovery"],
            "discovery",
        ),
        verification=_policy_section(
            VerificationPolicy,
            payload["verification"],
            "verification",
        ),
        execution=_policy_section(
            ExecutionPolicy,
            payload["execution"],
            "execution",
        ),
    )


def _scan_kwargs(value: Any) -> dict[str, Any]:
    from .discovery_mainline_contract import validate_mainline_run_contract

    if not isinstance(value, dict):
        raise ValueError("product scan kwargs must be an object")
    expected_fields = {
        "project",
        "root",
        "prd_text",
        "api_doc_path",
        "base_url",
        "ci_gate",
        "multi_layer",
        "output_dir",
        "save_report",
        "campaign_context",
    }
    if set(value) != expected_fields:
        raise ValueError("product scan kwargs fields are invalid")
    if value.get("ci_gate") is not False or value.get("save_report") is not False:
        raise ValueError("evaluator product scan must disable publication and CI gating")
    context = value.get("campaign_context")
    if not isinstance(context, dict):
        raise ValueError("product scan campaign_context must be an object")
    contract = validate_mainline_run_contract(context.get("mainline_run"))
    context_fields = {
        "run_id": "run_id",
        "campaign_id": "campaign_id",
        "target_id": "target_id",
        "environment_id": "environment_id",
        "policy_version": "policy_version",
        "mainline_authority": "mainline_authority",
        "evaluation_mode": "evaluation_mode",
    }
    for context_field, contract_field in context_fields.items():
        if context.get(context_field) != contract[contract_field]:
            raise ValueError(
                f"product scan campaign_context {context_field} does not match "
                "the preallocated mainline contract"
            )
    return {
        **value,
        "root": Path(str(value["root"])).resolve(),
        "output_dir": Path(str(value["output_dir"])).resolve(),
        "campaign_context": dict(context),
    }


def run_worker(request_path: Path, result_path: Path) -> None:
    _assert_secret_free_environment()
    request = _load_request(request_path)
    strategy = _strategy_from_request(request["strategy"])

    from .discovery_policy_evaluation_runner import strategy_fingerprint

    if strategy_fingerprint(strategy) != str(
        request.get("strategy_fingerprint") or ""
    ):
        raise ValueError("product scan strategy fingerprint mismatch")
    kwargs = _scan_kwargs(request["scan_kwargs"])

    from .__main__ import scan
    from .policy_wiring import policy_strategy_override

    with policy_strategy_override(strategy):
        result = scan(**kwargs)
    if not isinstance(result, dict):
        raise ValueError("product scan did not return an object")
    serialized = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix(result_path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(serialized, encoding="utf-8")
    os.replace(temporary, result_path)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        raise SystemExit(
            "usage: python -m ai_test_asset_center.observed_product_scan_worker "
            "<request.json> <result.json>"
        )
    run_worker(Path(args[0]).resolve(), Path(args[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
