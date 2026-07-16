from __future__ import annotations

"""Governed HTTP fixture reset controller for observed policy evaluation."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .discovery_policy_evaluation_runner import (
    FIXTURE_CLEANUP_SCHEMA,
    FIXTURE_PREPARE_SCHEMA,
    PolicyEvaluationRunnerError,
)
from .sandbox_write_executor import execute_governed_control_write


HTTP_FIXTURE_SCHEMA = "qualibug.evaluation-http-fixture.v1"


def _read_fixture(runtime_view: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    target = runtime_view.get("target") if isinstance(runtime_view.get("target"), dict) else {}
    runtime = target.get("runtime") if isinstance(target.get("runtime"), dict) else {}
    path = Path(str(runtime.get("fixture_snapshot_ref") or "")).resolve()
    if not path.is_file():
        raise PolicyEvaluationRunnerError(f"evaluation fixture snapshot not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyEvaluationRunnerError(f"evaluation fixture snapshot is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != HTTP_FIXTURE_SCHEMA:
        raise PolicyEvaluationRunnerError("evaluation fixture snapshot uses an unsupported schema")
    return target, payload


def _json_path(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise PolicyEvaluationRunnerError(f"fixture observation missing declared JSON path: {path}")
    return current


def _assert_clean_state(receipt: dict[str, Any], fixture: dict[str, Any]) -> None:
    after = receipt.get("after") if isinstance(receipt.get("after"), dict) else {}
    if int(after.get("status") or 0) < 200 or int(after.get("status") or 0) >= 300:
        raise PolicyEvaluationRunnerError("fixture observation did not return a successful HTTP status")
    body = after.get("body")
    assertions = fixture.get("clean_state_assertions")
    if not isinstance(assertions, dict) or not assertions:
        raise PolicyEvaluationRunnerError("fixture snapshot requires clean_state_assertions")
    mismatches = []
    for path, expected in assertions.items():
        actual = _json_path(body, str(path))
        if actual != expected:
            mismatches.append({"path": str(path), "expected": expected, "actual": actual})
    if mismatches:
        raise PolicyEvaluationRunnerError(f"fixture reset did not establish declared clean state: {mismatches}")


def _audit_receipt_id(receipt: dict[str, Any]) -> str:
    record = receipt.get("audit_record") if isinstance(receipt.get("audit_record"), dict) else {}
    if not str(receipt.get("audit_path") or "").strip() or not record:
        raise PolicyEvaluationRunnerError("governed fixture write did not emit an audit receipt")
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class GovernedHttpResetFixtureController:
    """Reset an explicitly declared non-production target before and after scans."""

    def __init__(self, *, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def prepare(self, **kwargs: Any) -> dict[str, Any]:
        return self._reset(phase="prepare", **kwargs)

    def cleanup(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("preparation_receipt", None)
        kwargs.pop("scan_output", None)
        return self._reset(phase="cleanup", expected_fixture_fingerprint="", **kwargs)

    def _reset(
        self,
        *,
        phase: str,
        runtime_view: dict[str, Any],
        campaign_id: str,
        policy_id: str,
        evaluation_mode: str,
        expected_fixture_fingerprint: str,
    ) -> dict[str, Any]:
        target, fixture = _read_fixture(runtime_view)
        runtime = target.get("runtime") if isinstance(target.get("runtime"), dict) else {}
        fixture_path = Path(str(runtime.get("fixture_snapshot_ref") or "")).resolve()
        actual_fixture_fingerprint = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        if expected_fixture_fingerprint and actual_fixture_fingerprint != expected_fixture_fingerprint:
            raise PolicyEvaluationRunnerError("fixture snapshot fingerprint drifted before reset")
        environment_ref = str(runtime.get("environment_ref") or "").strip()
        environment_type = str(runtime.get("environment_type") or "").strip().lower()
        configured_base_url = str(fixture.get("base_url") or "").strip().rstrip("/")
        if configured_base_url != environment_ref.rstrip("/"):
            raise PolicyEvaluationRunnerError("fixture base_url must exactly match manifest environment_ref")
        reset = fixture.get("reset") if isinstance(fixture.get("reset"), dict) else {}
        actor_identity = str(fixture.get("actor_identity") or "evaluation-fixture-controller").strip()
        actor_token_env = str(fixture.get("actor_token_env") or "").strip()
        actor_token = os.environ.get(actor_token_env, "") if actor_token_env else ""
        runtime_contract = {
            "status": "approved",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": configured_base_url,
            "environment_kind": environment_type,
            "environment_ref": environment_ref,
        }
        governed = execute_governed_control_write(
            root=self.workspace_root,
            project=str(target.get("project_id") or ""),
            base_url=configured_base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase=f"evaluation_fixture_{phase}",
            actor_identity=actor_identity,
            actor_token=actor_token,
            method=str(reset.get("method") or ""),
            path=str(reset.get("path") or ""),
            body=reset.get("body"),
            observation_path=str(fixture.get("observation_path") or ""),
        )
        if governed.get("accepted") is not True:
            write = governed.get("write") if isinstance(governed.get("write"), dict) else {}
            before = governed.get("before") if isinstance(governed.get("before"), dict) else {}
            after = governed.get("after") if isinstance(governed.get("after"), dict) else {}
            raise PolicyEvaluationRunnerError(
                f"governed fixture {phase} reset failed: {governed.get('reason')}; "
                f"write_status={write.get('status')}; "
                f"write_error={write.get('error') or ''}; "
                f"before_status={before.get('status')}; "
                f"after_status={after.get('status')}; "
                f"audit_path={governed.get('audit_path') or ''}"
            )
        _assert_clean_state(governed, fixture)
        common = {
            "target_id": str(target.get("target_id") or ""),
            "campaign_id": campaign_id,
            "policy_id": policy_id,
            "evaluation_mode": evaluation_mode,
            "environment_ref": environment_ref,
            "environment_type": environment_type,
            "fixture_fingerprint": actual_fixture_fingerprint,
            "audit_receipt_id": _audit_receipt_id(governed),
            "production_http_requests": int(governed.get("production_http_requests") or 0),
        }
        if phase == "prepare":
            return {
                "schema_version": FIXTURE_PREPARE_SCHEMA,
                **common,
                "status": "READY",
                "governed_sandbox_executor": True,
                "before_observation_ref": str(governed.get("before_ref") or ""),
                "after_observation_ref": str(governed.get("after_ref") or ""),
            }
        return {
            "schema_version": FIXTURE_CLEANUP_SCHEMA,
            **common,
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "after_cleanup_observation_ref": str(governed.get("after_ref") or ""),
        }
