from __future__ import annotations

"""Evaluator fixture governance for the supplied Windows-native benchmark."""

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from _funnel_benchmark_prep import prepare_funnel_benchmark_target
from ai_test_asset_center.benchmark_target_cleanliness import (
    assert_benchmark_target_clean,
)
from ai_test_asset_center.discovery_policy_evaluation_runner import (
    FIXTURE_CLEANUP_SCHEMA,
    FIXTURE_PREPARE_SCHEMA,
    PolicyEvaluationRunnerError,
)
from ai_test_asset_center.target_policy import is_nonproduction_environment


WINDOWS_BENCHMARK_FIXTURE_SCHEMA = (
    "qualibug.evaluation-windows-benchmark-fixture.v1"
)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _observe_target(base_url: str) -> str:
    request = urllib.request.Request(base_url.rstrip("/") + "/", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = int(response.status)
            body = response.read(4096)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read(4096)
    except Exception as exc:
        raise PolicyEvaluationRunnerError(
            f"windows benchmark target observation failed: {type(exc).__name__}:{exc}"
        ) from exc
    return "target-observation-" + _fingerprint({
        "url": base_url.rstrip("/"),
        "status": status,
        "body_sha256": hashlib.sha256(body).hexdigest(),
    })[:24]


class WindowsBenchmarkFixtureController:
    """Reset, refresh actors, and prove cleanliness before and after a scan."""

    def __init__(self, *, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def prepare(self, **kwargs: Any) -> dict[str, Any]:
        return self._reset(phase="prepare", **kwargs)

    def cleanup(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("preparation_receipt", None)
        kwargs.pop("scan_output", None)
        return self._reset(
            phase="cleanup",
            expected_fixture_fingerprint="",
            **kwargs,
        )

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
        target = (
            runtime_view.get("target")
            if isinstance(runtime_view.get("target"), dict)
            else {}
        )
        runtime = (
            target.get("runtime")
            if isinstance(target.get("runtime"), dict)
            else {}
        )
        fixture_path = Path(
            str(runtime.get("fixture_snapshot_ref") or "")
        ).resolve()
        if not fixture_path.is_file():
            raise PolicyEvaluationRunnerError(
                f"windows benchmark fixture not found: {fixture_path}"
            )
        try:
            fixture = json.loads(fixture_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyEvaluationRunnerError(
                f"windows benchmark fixture is invalid: {fixture_path}:{exc}"
            ) from exc
        if (
            not isinstance(fixture, dict)
            or fixture.get("schema_version")
            != WINDOWS_BENCHMARK_FIXTURE_SCHEMA
        ):
            raise PolicyEvaluationRunnerError(
                "windows benchmark fixture schema is unsupported"
            )
        actual_fingerprint = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        if (
            expected_fixture_fingerprint
            and expected_fixture_fingerprint != actual_fingerprint
        ):
            raise PolicyEvaluationRunnerError(
                "windows benchmark fixture fingerprint drifted"
            )
        project = str(fixture.get("project") or "").strip()
        base_url = str(fixture.get("base_url") or "").strip().rstrip("/")
        target_root = Path(str(fixture.get("target_root") or "")).resolve()
        environment_ref = str(runtime.get("environment_ref") or "").strip().rstrip("/")
        environment_type = str(runtime.get("environment_type") or "").strip()
        if project != str(target.get("project_id") or "").strip():
            raise PolicyEvaluationRunnerError(
                "windows benchmark fixture project mismatch"
            )
        if not target_root.is_dir():
            raise PolicyEvaluationRunnerError(
                f"windows benchmark target root not found: {target_root}"
            )
        if not base_url or base_url != environment_ref:
            raise PolicyEvaluationRunnerError(
                "windows benchmark fixture base_url mismatch"
            )
        if not is_nonproduction_environment(environment_type):
            raise PolicyEvaluationRunnerError(
                "windows benchmark fixture requires non-production environment"
            )
        db_dsn = str(os.environ.get("QUALIBUG_DB_DSN") or "").strip()
        jwt_secret = str(os.environ.get("QUALIBUG_JWT_SECRET") or "").strip()
        if not db_dsn or not jwt_secret:
            raise PolicyEvaluationRunnerError(
                "windows benchmark fixture requires evaluator DB and JWT configuration"
            )
        before_ref = _observe_target(base_url)
        evaluator_environment = dict(os.environ)
        evaluator_environment.update({
            "QUALIBUG_BENCHMARK_TARGET_ROOT": str(target_root),
            "QUALIBUG_BENCHMARK_PROJECT": project,
            "QUALIBUG_TARGET_BASE_URL": base_url,
            "QUALIBUG_DB_DSN": db_dsn,
            "QUALIBUG_JWT_SECRET": jwt_secret,
        })
        reset = prepare_funnel_benchmark_target(
            root=self.workspace_root,
            env=evaluator_environment,
            project=project,
            target_base_url=base_url,
        )
        cleanliness = assert_benchmark_target_clean(
            root=self.workspace_root,
            project=project,
            target_base_url=base_url,
            reset_receipt_path=str(reset.get("reset_receipt_path") or ""),
        )
        if str(cleanliness.get("status") or "") not in {
            "clean_no_prior_write_audit",
            "clean_all_prior_writes_cleaned",
            "clean_reset_receipt_verified",
        }:
            raise PolicyEvaluationRunnerError(
                f"windows benchmark cleanliness proof failed: {cleanliness}"
            )
        reset_receipt = (
            reset.get("reset_receipt")
            if isinstance(reset.get("reset_receipt"), dict)
            else {}
        )
        if reset_receipt.get("status") != "completed":
            raise PolicyEvaluationRunnerError(
                "windows benchmark reset receipt is incomplete"
            )
        after_ref = str(
            cleanliness.get("archived_receipt")
            or reset.get("reset_receipt_path")
            or ""
        ).strip()
        if not before_ref or not after_ref:
            raise PolicyEvaluationRunnerError(
                "windows benchmark fixture observation receipt missing"
            )
        common = {
            "target_id": str(target.get("target_id") or ""),
            "campaign_id": campaign_id,
            "policy_id": policy_id,
            "evaluation_mode": evaluation_mode,
            "environment_ref": environment_ref,
            "environment_type": environment_type,
            "fixture_fingerprint": actual_fingerprint,
            "audit_receipt_id": _fingerprint(reset_receipt),
            "production_http_requests": 0,
            "governance_kind": "evaluator_windows_native_target_reset",
        }
        if phase == "prepare":
            return {
                "schema_version": FIXTURE_PREPARE_SCHEMA,
                **common,
                "status": "READY",
                "governed_sandbox_executor": True,
                "before_observation_ref": before_ref,
                "after_observation_ref": after_ref,
            }
        return {
            "schema_version": FIXTURE_CLEANUP_SCHEMA,
            **common,
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "after_cleanup_observation_ref": after_ref,
        }
