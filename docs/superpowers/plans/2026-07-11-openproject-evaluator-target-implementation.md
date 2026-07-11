# OpenProject Evaluator Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the D-drive WSL2/Docker foundation, deploy only the pinned OpenProject target, produce immutable readiness and cleanup receipts, and stop it cleanly without creating Ground Truth or claiming Gate D.

**Architecture:** This is the first self-contained target plan from the approved serial-deployment design. A small generic readiness contract and CLI machine-enforce the one-target-at-a-time state machine; infrastructure and target artifacts live outside the repository under `D:\QualiBug-Evaluator-Targets`, while the pinned upstream OpenProject deployment remains unchanged. OpenProject may reach `RUNTIME_READY` only if health, login, API, database observation, governed fixture, and cleanup checks all pass; otherwise the run emits an explicit blocker and still converges to `STOPPED_CLEAN`.

**Tech Stack:** Python 3.12, pytest, PowerShell 5.1, WSL2 Ubuntu, Docker Desktop WSL2 backend, Docker Compose, OpenProject 17.6.0 / `17-slim`, PostgreSQL 17.

## Global Constraints

- Preserve the existing dirty worktree; stage and commit only paths created by this plan.
- Do not reset, overwrite, clean, or delete existing repository work or unrelated Docker/WSL resources.
- Run the required AST parse immediately after every Python edit.
- Keep QualiBug frontend `5174` and backend `8088` online and reserved.
- Run at most one system under test: the benchmark mall and OpenProject must never overlap.
- Target ports are configuration data. This plan allocates `127.0.0.1:18080` only in the external OpenProject deployment configuration, never in active QualiBug product code.
- Environment type is explicitly `sandbox`; localhost never establishes safety.
- Every write must use the existing Target Policy and governed sandbox executor. Missing actor credentials or governance evidence blocks fixture execution before the request is sent.
- Never place a password, token, cookie, API key, or private evaluator file in the repository, command-line arguments, logs, receipts, or prompts.
- Runtime artifacts are persisted only through `artifact_redactor.write_json_redacted`.
- Do not create Ground Truth from JSON. This plan does not classify OpenProject as held-out measured, clean, or evaluator-ready.
- Gate D and controlled pilot remain `NOT_MEASURED` until external evaluator receipts satisfy the authoritative goal contract.
- Use only the exact Compose project name `qualibug-eval-openproject`; never run global Docker prune commands.
- WSL and Docker data must be confirmed on drive D before OpenProject starts.
- Stop OpenProject and reach `STOPPED_CLEAN` before beginning any ERPNext work.

## File Structure

- Create `ai_test_asset_center/evaluator_target_readiness.py`: generic serial-target state validation, deterministic fingerprints, and readiness receipt construction.
- Create `tools/evaluator_target_readiness.py`: CLI for emitting receipts and checking serial admission; all persistence goes through the artifact redactor.
- Create `tests/test_evaluator_target_readiness.py`: state-machine, serial-admission, transition, redaction, and fail-closed contract tests.
- Create `tests/test_evaluator_target_readiness_cli.py`: CLI persistence and exit-code tests.
- Modify `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`: link the serial target deployment contract and define readiness as deployment evidence, not quality measurement.
- Create external runtime artifacts under `D:\QualiBug-Evaluator-Targets\receipts` and `D:\QualiBug-Evaluator-Targets\openproject`; these are not Git inputs.

---

### Task 1: Serial Target Readiness Contract

**Files:**
- Create: `ai_test_asset_center/evaluator_target_readiness.py`
- Test: `tests/test_evaluator_target_readiness.py`

**Interfaces:**
- Consumes: `ai_test_asset_center.target_policy.build_target_policy_decision(...)`.
- Produces: `validate_target_transition(previous_state: str, new_state: str) -> None`.
- Produces: `assess_serial_target_admission(receipts: list[dict[str, Any]], requested_target_id: str) -> dict[str, Any]`.
- Produces: `build_target_readiness_receipt(...keyword arguments...) -> dict[str, Any]` using schema `qualibug.evaluator-target-readiness.v1`.

- [ ] **Step 1: Write the failing contract tests**

Create `tests/test_evaluator_target_readiness.py` with tests covering these exact cases:

```python
from __future__ import annotations

import pytest

from ai_test_asset_center.evaluator_target_readiness import (
    EvaluatorTargetReadinessError,
    assess_serial_target_admission,
    build_target_readiness_receipt,
    validate_target_transition,
)


def _receipt(target_id: str, state: str) -> dict:
    return {"target_id": target_id, "state": state}


def test_serial_admission_uses_latest_receipt_for_each_target() -> None:
    receipts = [
        _receipt("benchmark-mall-131", "RUNTIME_READY"),
        _receipt("benchmark-mall-131", "STOPPED_CLEAN"),
    ]
    decision = assess_serial_target_admission(receipts, "openproject-17.6.0")
    assert decision["allowed"] is True
    assert decision["blocking_codes"] == []


def test_serial_admission_blocks_another_non_stopped_target() -> None:
    decision = assess_serial_target_admission(
        [_receipt("benchmark-mall-131", "RUNTIME_READY")],
        "openproject-17.6.0",
    )
    assert decision["allowed"] is False
    assert decision["blocking_codes"] == ["BLOCKED_ANOTHER_TARGET_ACTIVE"]
    assert decision["active_target_ids"] == ["benchmark-mall-131"]


def test_failed_target_still_requires_stopped_clean_receipt() -> None:
    decision = assess_serial_target_admission(
        [_receipt("benchmark-mall-131", "FAILED_SAFE")],
        "openproject-17.6.0",
    )
    assert decision["allowed"] is False


def test_transition_rejects_skipping_runtime_ready() -> None:
    with pytest.raises(EvaluatorTargetReadinessError, match="invalid target transition"):
        validate_target_transition("DEPLOYABLE", "EVALUATOR_READY")


def test_first_receipt_may_enter_asset_valid() -> None:
    validate_target_transition("NOT_STARTED", "ASSET_VALID")


def test_runtime_ready_requires_all_runtime_checks() -> None:
    with pytest.raises(EvaluatorTargetReadinessError, match="missing required checks"):
        build_target_readiness_receipt(
            target_id="openproject-17.6.0",
            target_role="held_out_candidate",
            state="RUNTIME_READY",
            previous_state="DEPLOYABLE",
            environment_type="sandbox",
            environment_ref="openproject-local-sandbox",
            requested_base_url="http://127.0.0.1:18080",
            approved_base_url="http://127.0.0.1:18080",
            checks={"health": "passed"},
            fingerprints={"source_sha256": "a" * 64},
        )


def test_receipt_is_not_measurement_and_has_immutable_fingerprint() -> None:
    checks = {
        "health": "passed",
        "login": "passed",
        "api": "passed",
        "database_observation": "passed",
        "fixture_prepare": "passed",
        "fixture_cleanup": "passed",
    }
    receipt = build_target_readiness_receipt(
        target_id="openproject-17.6.0",
        target_role="held_out_candidate",
        state="RUNTIME_READY",
        previous_state="DEPLOYABLE",
        environment_type="sandbox",
        environment_ref="openproject-local-sandbox",
        requested_base_url="http://127.0.0.1:18080",
        approved_base_url="http://127.0.0.1:18080",
        checks=checks,
        fingerprints={"source_sha256": "a" * 64},
    )
    assert receipt["schema_version"] == "qualibug.evaluator-target-readiness.v1"
    assert receipt["measurement_status"] == "NOT_MEASURED"
    assert receipt["commercial_promotion_evidence"] is False
    assert receipt["target_policy_decision"]["write_allowed"] is True
    assert receipt["receipt_fingerprint"].startswith("sha256:")
```

- [ ] **Step 2: Run the tests and confirm the module is absent**

Run:

```powershell
python -m pytest tests/test_evaluator_target_readiness.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'ai_test_asset_center.evaluator_target_readiness'`.

- [ ] **Step 3: Implement the generic readiness contract**

Create `ai_test_asset_center/evaluator_target_readiness.py` with this implementation:

```python
"""Industry-neutral evaluator target readiness and serial-admission receipts."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from .target_policy import WRITE_EXECUTION_MODE, build_target_policy_decision


READINESS_SCHEMA = "qualibug.evaluator-target-readiness.v1"
ADMISSION_SCHEMA = "qualibug.evaluator-target-admission.v1"
TARGET_STATES = frozenset({
    "ASSET_VALID",
    "DEPLOYABLE",
    "RUNTIME_READY",
    "EVALUATOR_READY",
    "STOPPED_CLEAN",
    "BLOCKED",
    "FAILED_SAFE",
})
TRANSITIONS = {
    "NOT_STARTED": frozenset({"ASSET_VALID", "BLOCKED", "FAILED_SAFE"}),
    "ASSET_VALID": frozenset({"DEPLOYABLE", "BLOCKED", "FAILED_SAFE"}),
    "DEPLOYABLE": frozenset({"RUNTIME_READY", "BLOCKED", "FAILED_SAFE", "STOPPED_CLEAN"}),
    "RUNTIME_READY": frozenset({"EVALUATOR_READY", "STOPPED_CLEAN", "BLOCKED", "FAILED_SAFE"}),
    "EVALUATOR_READY": frozenset({"STOPPED_CLEAN", "BLOCKED", "FAILED_SAFE"}),
    "BLOCKED": frozenset({"STOPPED_CLEAN"}),
    "FAILED_SAFE": frozenset({"STOPPED_CLEAN"}),
    "STOPPED_CLEAN": frozenset({"ASSET_VALID"}),
}
RUNTIME_REQUIRED_CHECKS = frozenset({
    "health",
    "login",
    "api",
    "database_observation",
    "fixture_prepare",
    "fixture_cleanup",
})
EVALUATOR_REQUIRED_CHECKS = RUNTIME_REQUIRED_CHECKS | frozenset({
    "reset",
    "evaluator_private_manifest",
    "ground_truth_or_clean_audit",
})
STOPPED_REQUIRED_CHECKS = frozenset({"target_stopped", "ports_released"})
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class EvaluatorTargetReadinessError(ValueError):
    """Raised when a target state or receipt would overstate readiness."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _state(value: Any) -> str:
    state = _text(value).upper()
    if state not in TARGET_STATES:
        raise EvaluatorTargetReadinessError(f"unsupported target state: {state!r}")
    return state


def _previous_state(value: Any) -> str:
    state = _text(value).upper()
    if state != "NOT_STARTED" and state not in TARGET_STATES:
        raise EvaluatorTargetReadinessError(f"unsupported previous target state: {state!r}")
    return state


def validate_target_transition(previous_state: str, new_state: str) -> None:
    previous = _previous_state(previous_state)
    current = _state(new_state)
    if current not in TRANSITIONS[previous]:
        raise EvaluatorTargetReadinessError(
            f"invalid target transition: {previous} -> {current}"
        )


def assess_serial_target_admission(
    receipts: list[dict[str, Any]],
    requested_target_id: str,
) -> dict[str, Any]:
    requested = _text(requested_target_id)
    if not requested:
        raise EvaluatorTargetReadinessError("requested target id is required")
    latest: dict[str, str] = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            raise EvaluatorTargetReadinessError(f"receipt[{index}] must be an object")
        target_id = _text(receipt.get("target_id"))
        if not target_id:
            raise EvaluatorTargetReadinessError(f"receipt[{index}] target_id is required")
        latest[target_id] = _state(receipt.get("state"))
    active = sorted(
        target_id
        for target_id, state in latest.items()
        if target_id != requested and state != "STOPPED_CLEAN"
    )
    blocking = ["BLOCKED_ANOTHER_TARGET_ACTIVE"] if active else []
    canonical = {
        "requested_target_id": requested,
        "allowed": not active,
        "active_target_ids": active,
        "latest_states": dict(sorted(latest.items())),
        "blocking_codes": blocking,
    }
    decision_id = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": ADMISSION_SCHEMA,
        "decision_id": f"sha256:{decision_id}",
        **canonical,
    }


def _required_checks(state: str) -> frozenset[str]:
    if state == "RUNTIME_READY":
        return RUNTIME_REQUIRED_CHECKS
    if state == "EVALUATOR_READY":
        return EVALUATOR_REQUIRED_CHECKS
    if state == "STOPPED_CLEAN":
        return STOPPED_REQUIRED_CHECKS
    return frozenset()


def build_target_readiness_receipt(
    *,
    target_id: str,
    target_role: str,
    state: str,
    previous_state: str,
    environment_type: str,
    environment_ref: str,
    requested_base_url: str,
    approved_base_url: str,
    checks: dict[str, str],
    fingerprints: dict[str, str],
    blocking_codes: list[str] | None = None,
    operator_action: str = "",
) -> dict[str, Any]:
    target = _text(target_id)
    role = _text(target_role)
    if not target or not role:
        raise EvaluatorTargetReadinessError("target id and role are required")
    current = _state(state)
    previous = _previous_state(previous_state)
    validate_target_transition(previous, current)
    normalized_checks = {
        _text(name): _text(result).lower()
        for name, result in checks.items()
        if _text(name)
    }
    required = _required_checks(current)
    missing = sorted(
        name for name in required if normalized_checks.get(name) != "passed"
    )
    if missing:
        raise EvaluatorTargetReadinessError(
            f"missing required checks for {current}: {', '.join(missing)}"
        )
    normalized_fingerprints: dict[str, str] = {}
    for name, value in fingerprints.items():
        key = _text(name)
        digest = _text(value).lower().removeprefix("sha256:")
        if not key or not SHA256_RE.fullmatch(digest):
            raise EvaluatorTargetReadinessError(
                f"malformed SHA-256 fingerprint: {key!r}"
            )
        normalized_fingerprints[key] = f"sha256:{digest}"
    blockers = sorted({_text(code) for code in (blocking_codes or []) if _text(code)})
    if current in {"BLOCKED", "FAILED_SAFE"} and not blockers:
        raise EvaluatorTargetReadinessError(f"{current} receipt requires a blocking code")
    policy = build_target_policy_decision(
        requested_base_url=requested_base_url,
        approved_base_url=approved_base_url,
        environment_type=environment_type,
        environment_ref=environment_ref,
        execution_mode=WRITE_EXECUTION_MODE,
        runtime_status="approved",
    )
    canonical = {
        "schema_version": READINESS_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_id": target,
        "target_role": role,
        "state": current,
        "previous_state": previous,
        "environment_type": _text(environment_type).lower(),
        "environment_ref": _text(environment_ref),
        "target_policy_decision": policy,
        "checks": dict(sorted(normalized_checks.items())),
        "blocking_codes": blockers,
        "operator_action": _text(operator_action),
        "fingerprints": dict(sorted(normalized_fingerprints.items())),
        "measurement_status": "NOT_MEASURED",
        "commercial_promotion_evidence": False,
        "gate_d_unlocked": False,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**canonical, "receipt_fingerprint": f"sha256:{digest}"}
```

Do not add any industry, product route, hostname, or port constant to this module.

- [ ] **Step 4: Run the mandatory AST syntax check**

Run:

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/evaluator_target_readiness.py', encoding='utf-8').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 5: Run the focused tests**

Run:

```powershell
python -m pytest tests/test_evaluator_target_readiness.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit only the contract paths**

Run:

```powershell
git add -- ai_test_asset_center/evaluator_target_readiness.py tests/test_evaluator_target_readiness.py
git commit -m "feat: enforce serial evaluator target readiness"
```

Expected: one commit containing exactly two files.

### Task 2: Redacted Readiness CLI

**Files:**
- Create: `tools/evaluator_target_readiness.py`
- Create: `tests/test_evaluator_target_readiness_cli.py`

**Interfaces:**
- Consumes: `build_target_readiness_receipt` and `assess_serial_target_admission` from Task 1.
- Consumes: `artifact_redactor.write_json_redacted(path, payload)`.
- Produces CLI subcommands `emit` and `admit`.
- Receipt filenames follow `<zero-padded-sequence>-<target-id>-<state-lower>.json` so append order is stable.

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_evaluator_target_readiness_cli.py` that invokes `main([...])` directly and asserts:

```python
from __future__ import annotations

import json
from pathlib import Path

from tools.evaluator_target_readiness import main


def test_emit_persists_redacted_non_measurement_receipt(tmp_path: Path) -> None:
    code = main([
        "emit",
        "--receipts-root", str(tmp_path),
        "--sequence", "1",
        "--target-id", "benchmark-mall-131",
        "--target-role", "held_in_diagnostic",
        "--state", "STOPPED_CLEAN",
        "--previous-state", "RUNTIME_READY",
        "--environment-type", "sandbox",
        "--environment-ref", "benchmark-mall-local-sandbox",
        "--target-url", "http://127.0.0.1:8080",
        "--check", "target_stopped=passed",
        "--check", "ports_released=passed",
        "--fingerprint", f"source_sha256={'a' * 64}",
    ])
    assert code == 0
    path = tmp_path / "0001-benchmark-mall-131-stopped_clean.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["measurement_status"] == "NOT_MEASURED"
    assert payload["state"] == "STOPPED_CLEAN"


def test_admit_returns_two_when_another_target_is_active(tmp_path: Path) -> None:
    active = {
        "schema_version": "qualibug.evaluator-target-readiness.v1",
        "target_id": "benchmark-mall-131",
        "state": "RUNTIME_READY",
    }
    (tmp_path / "0001-benchmark-mall-131-runtime_ready.json").write_text(
        json.dumps(active), encoding="utf-8"
    )
    code = main([
        "admit",
        "--receipts-root", str(tmp_path),
        "--requested-target-id", "openproject-17.6.0",
    ])
    assert code == 2
```

- [ ] **Step 2: Run the CLI tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_evaluator_target_readiness_cli.py -q
```

Expected: collection fails because `tools.evaluator_target_readiness` is absent.

- [ ] **Step 3: Implement the CLI**

Create `tools/evaluator_target_readiness.py` with this implementation:

```python
"""Emit redacted evaluator target receipts and enforce serial admission."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_test_asset_center.artifact_redactor import write_json_redacted  # noqa: E402
from ai_test_asset_center.evaluator_target_readiness import (  # noqa: E402
    READINESS_SCHEMA,
    EvaluatorTargetReadinessError,
    assess_serial_target_admission,
    build_target_readiness_receipt,
)


PAIR_RE = re.compile(r"^[^=\r\n]+=[^\r\n]+$")


def _pairs(values: list[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if not PAIR_RE.fullmatch(value):
            raise EvaluatorTargetReadinessError(
                f"{label} must use non-empty NAME=VALUE syntax"
            )
        name, item = value.split("=", 1)
        name = name.strip()
        item = item.strip()
        if not name or not item or name in parsed:
            raise EvaluatorTargetReadinessError(f"invalid or duplicate {label}: {name!r}")
        parsed[name] = item
    return parsed


def _receipt_sequence(path: Path) -> int:
    prefix = path.name.split("-", 1)[0]
    if not prefix.isdigit():
        raise EvaluatorTargetReadinessError(
            f"readiness receipt filename lacks numeric prefix: {path.name}"
        )
    return int(prefix)


def _load_readiness_receipts(root: Path) -> list[dict[str, Any]]:
    receipts: list[tuple[int, dict[str, Any]]] = []
    if not root.exists():
        return []
    for path in root.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise EvaluatorTargetReadinessError(f"receipt must be an object: {path}")
        if payload.get("schema_version") != READINESS_SCHEMA:
            continue
        receipts.append((_receipt_sequence(path), payload))
    receipts.sort(key=lambda item: item[0])
    sequences = [sequence for sequence, _ in receipts]
    if len(sequences) != len(set(sequences)):
        raise EvaluatorTargetReadinessError("duplicate readiness receipt sequence")
    return [payload for _, payload in receipts]


def _safe_target_id(value: str) -> str:
    target = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", target):
        raise EvaluatorTargetReadinessError("target id contains unsupported characters")
    return target


def _emit(args: argparse.Namespace) -> int:
    root = Path(args.receipts_root)
    target = _safe_target_id(args.target_id)
    receipt = build_target_readiness_receipt(
        target_id=target,
        target_role=args.target_role,
        state=args.state,
        previous_state=args.previous_state,
        environment_type=args.environment_type,
        environment_ref=args.environment_ref,
        requested_base_url=args.target_url,
        approved_base_url=args.approved_url or args.target_url,
        checks=_pairs(args.check, "check"),
        fingerprints=_pairs(args.fingerprint, "fingerprint"),
        blocking_codes=args.blocker,
        operator_action=args.operator_action,
    )
    filename = f"{args.sequence:04d}-{target}-{receipt['state'].lower()}.json"
    path = root / filename
    if path.exists():
        raise EvaluatorTargetReadinessError(f"receipt already exists: {path}")
    write_json_redacted(path, receipt)
    print(json.dumps({"status": "written", "path": str(path), "receipt": receipt}, ensure_ascii=False))
    return 0


def _admit(args: argparse.Namespace) -> int:
    receipts = _load_readiness_receipts(Path(args.receipts_root))
    decision = assess_serial_target_admission(receipts, args.requested_target_id)
    print(json.dumps(decision, ensure_ascii=False))
    return 0 if decision["allowed"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    emit = sub.add_parser("emit")
    emit.add_argument("--receipts-root", required=True)
    emit.add_argument("--sequence", required=True, type=int)
    emit.add_argument("--target-id", required=True)
    emit.add_argument("--target-role", required=True)
    emit.add_argument("--state", required=True)
    emit.add_argument("--previous-state", required=True)
    emit.add_argument("--environment-type", required=True)
    emit.add_argument("--environment-ref", required=True)
    emit.add_argument("--target-url", required=True)
    emit.add_argument("--approved-url")
    emit.add_argument("--check", action="append", default=[])
    emit.add_argument("--fingerprint", action="append", default=[])
    emit.add_argument("--blocker", action="append", default=[])
    emit.add_argument("--operator-action", default="")
    emit.set_defaults(handler=_emit)

    admit = sub.add_parser("admit")
    admit.add_argument("--receipts-root", required=True)
    admit.add_argument("--requested-target-id", required=True)
    admit.set_defaults(handler=_admit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

No secret-bearing argument is supported by this CLI.

- [ ] **Step 4: Run the mandatory AST check immediately**

Run:

```powershell
python -c "import ast; ast.parse(open('tools/evaluator_target_readiness.py', encoding='utf-8').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 5: Run focused and neighboring safety tests**

Run:

```powershell
python -m pytest tests/test_evaluator_target_readiness.py tests/test_evaluator_target_readiness_cli.py tests/test_nonproduction_write_contract.py tests/test_phase0_trust_security_baseline.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit only the CLI paths**

Run:

```powershell
git add -- tools/evaluator_target_readiness.py tests/test_evaluator_target_readiness_cli.py
git commit -m "feat: emit evaluator target readiness receipts"
```

Expected: one commit containing exactly two files.

### Task 3: Capture the Host Baseline and Establish the Reboot Checkpoint

**Files:**
- External artifact: `D:\QualiBug-Evaluator-Targets\receipts\host-baseline-before-wsl.json`
- External artifact: `D:\QualiBug-Evaluator-Targets\receipts\REBOOT_CHECKPOINT.json`

**Interfaces:**
- Consumes: Windows host facts and currently listening ports.
- Produces: redacted immutable operational receipts; no target readiness state is advanced here.

- [ ] **Step 1: Recheck the host without changing state**

Run the following commands separately and retain their exit codes:

```powershell
Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,HyperVisorPresent
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory,NumberOfLogicalProcessors,HypervisorPresent
Get-PSDrive C,D | Select-Object Name,Free,Used
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 5174,8088,18080,3001,3002,8080,8001,8002,8003,8004,8005,8006,8007,8008,8009,8010 | Select-Object LocalAddress,LocalPort,OwningProcess
wsl --status
wsl -l -v
Get-Command docker -ErrorAction SilentlyContinue
```

Expected before installation: QualiBug owns `5174` and `8088`; `18080` is free; Ubuntu and Docker may be absent. Any difference is recorded, not normalized away.

- [ ] **Step 2: Persist the baseline through the redactor**

Run this exact collector; it imports the repository redactor and excludes environment variables and process command lines:

```powershell
@'
import json
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from ai_test_asset_center.artifact_redactor import write_json_redacted


def run(command: list[str]) -> dict:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    return {
        "exit_code": completed.returncode,
        "stdout_present": bool(completed.stdout.strip()),
        "stderr_present": bool(completed.stderr.strip()),
    }


listeners = {}
for conn in psutil.net_connections(kind="tcp"):
    if conn.status != psutil.CONN_LISTEN or not conn.laddr:
        continue
    port = int(conn.laddr.port)
    if port in {5174, 8088, 18080}:
        listeners[str(port)] = {"status": "listening", "pid": conn.pid}
for port in (5174, 8088, 18080):
    listeners.setdefault(str(port), {"status": "free", "pid": None})

wsl = run(["wsl.exe", "--status"])
docker_path = shutil.which("docker")
docker = run([docker_path, "version"]) if docker_path else {
    "exit_code": 127,
    "stdout_present": False,
    "stderr_present": False,
}
receipt = {
    "schema_version": "qualibug.evaluator-host-baseline.v1",
    "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "measurement_status": "NOT_MEASURED",
    "memory": {"total_bytes": psutil.virtual_memory().total, "available_bytes": psutil.virtual_memory().available},
    "disk": {
        "c_free_bytes": shutil.disk_usage("C:\\").free,
        "d_free_bytes": shutil.disk_usage("D:\\").free,
    },
    "listeners": listeners,
    "wsl": {**wsl, "status": "ready" if wsl["exit_code"] == 0 else "not_ready"},
    "docker": {**docker, "status": "ready" if docker["exit_code"] == 0 else "not_ready"},
    "operator_action": "enable WSL2 and reboot if required",
}
write_json_redacted(
    Path(r"D:\QualiBug-Evaluator-Targets\receipts\host-baseline-before-wsl.json"),
    receipt,
)
print(json.dumps({"status": "written", "listeners": listeners}, ensure_ascii=False))
'@ | python -
```

Expected: the output file parses; its WSL and Docker statuses are derived only from exit codes and are never upgraded from configuration alone.

- [ ] **Step 3: Enable the Windows WSL features from an elevated PowerShell**

Run:

```powershell
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

Expected: both commands exit `0` and report the operation completed successfully. If either fails, persist `BLOCKED_WSL_FEATURE_ENABLE_FAILED` and do not reboot automatically.

- [ ] **Step 4: Write the reboot checkpoint before restarting Windows**

Persist `REBOOT_CHECKPOINT.json` through `write_json_redacted` with:

```json
{
  "schema_version": "qualibug.evaluator-reboot-checkpoint.v1",
  "reason": "WSL2 feature enablement",
  "resume_task": 3,
  "resume_step": 5,
  "target_id": "openproject-17.6.0",
  "target_started": false,
  "qualibug_expected_ports": [5174, 8088],
  "required_next_checks": ["wsl_status", "disk_capacity", "memory", "qualibug_ports", "target_port_free"]
}
```

Verify the file parses and contains no secret-scanner findings.

- [ ] **Step 5: Announce the reboot boundary, then restart only after the checkpoint exists**

Run from the elevated shell:

```powershell
shutdown.exe /r /t 0
```

Expected: Windows restarts. This is an intentional session boundary, not plan completion.

- [ ] **Step 6: Resume by rerunning every Step 1 check**

Expected: no target is running, `5174` and `8088` are restored or explicitly reported offline, and `18080` remains free. Do not proceed while QualiBug port identity is ambiguous.

### Task 4: Install Ubuntu and Docker Desktop with Data on D

**Files:**
- Host config: `%USERPROFILE%\.wslconfig` (merge with existing content; never overwrite unrelated keys)
- External WSL root: `D:\WSL\Ubuntu-Evaluator`
- External Docker root: `D:\DockerDesktopWSL`
- External receipts: `D:\QualiBug-Evaluator-Targets\receipts\infrastructure-ready.json`

**Interfaces:**
- Produces WSL2 Ubuntu and Docker Compose runtime.
- Produces no system-under-test state.

- [ ] **Step 1: Update WSL and install Ubuntu**

Run from elevated PowerShell:

```powershell
wsl --update
wsl --set-default-version 2
wsl --install -d Ubuntu
```

Expected: commands succeed or the last command reports Ubuntu already installed. If `wsl --update` fails through Store delivery, run the documented fallback `wsl --update --web-download`; if the installed WSL binary does not support that flag, emit `BLOCKED_WSL_UPDATE_UNSUPPORTED` instead of guessing another installer.

- [ ] **Step 2: Initialize Ubuntu once and verify version 2**

Launch Ubuntu, create the local non-production Linux user interactively, then run:

```powershell
wsl -l -v
wsl -d Ubuntu -- uname -a
```

Expected: Ubuntu state is `Running` or `Stopped`, version is `2`, and `uname` reports Linux.

- [ ] **Step 3: Relocate Ubuntu to D with an export verification gate**

Run:

```powershell
New-Item -ItemType Directory -Force -Path D:\WSL\exports,D:\WSL\Ubuntu-Evaluator | Out-Null
wsl --terminate Ubuntu
wsl --export Ubuntu D:\WSL\exports\Ubuntu-Evaluator.tar
$archive = Get-Item -LiteralPath D:\WSL\exports\Ubuntu-Evaluator.tar
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $archive.FullName
if ($archive.Length -le 1048576 -or -not $hash.Hash) { throw 'WSL export verification failed' }
wsl --unregister Ubuntu
wsl --import Ubuntu-Evaluator D:\WSL\Ubuntu-Evaluator D:\WSL\exports\Ubuntu-Evaluator.tar --version 2
wsl --set-default Ubuntu-Evaluator
wsl -d Ubuntu-Evaluator -- uname -a
```

Expected: unregister occurs only after the archive is larger than 1 MiB and has a SHA-256; imported distribution is version 2 and starts successfully from `D:\WSL\Ubuntu-Evaluator`.

- [ ] **Step 4: Merge the WSL resource cap**

Preserve any existing `.wslconfig` sections and keys. Ensure its `[wsl2]` section contains exactly these minimum host-appropriate limits:

```ini
[wsl2]
memory=8GB
processors=4
swap=4GB
```

Then run:

```powershell
wsl --shutdown
```

Expected: the next WSL start uses the cap; existing unrelated settings remain present.

- [ ] **Step 5: Download the pinned Docker Desktop installer and verify its signature**

Run:

```powershell
New-Item -ItemType Directory -Force -Path D:\QualiBug-Evaluator-Targets\installers | Out-Null
winget download --exact --id Docker.DockerDesktop --version 4.81.0 --download-directory D:\QualiBug-Evaluator-Targets\installers --accept-package-agreements --accept-source-agreements
$installer = Get-ChildItem -LiteralPath D:\QualiBug-Evaluator-Targets\installers -Recurse -Filter '*Docker*Installer*.exe' | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if (-not $installer) { throw 'Docker Desktop installer not found' }
$signature = Get-AuthenticodeSignature -LiteralPath $installer.FullName
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Docker') { throw 'Docker installer signature invalid' }
```

Expected: exactly one selected installer, valid Authenticode signature, signer subject containing `Docker`.

- [ ] **Step 6: Install Docker Desktop using WSL2 and the D-drive data root**

Run from elevated PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path D:\DockerDesktopWSL | Out-Null
Start-Process -Wait -FilePath $installer.FullName -ArgumentList @('install','--accept-license','--backend=wsl-2','--wsl-default-data-root=D:\DockerDesktopWSL')
```

Expected: installer exits successfully. Launch Docker Desktop, wait for engine readiness, and verify the disk image/data directories exist under `D:\DockerDesktopWSL`, not the default C-drive WSL data root.

- [ ] **Step 7: Verify Docker and remove only the disposable smoke container**

Run:

```powershell
docker version
docker compose version
docker run --name qualibug-evaluator-infra-smoke hello-world
docker rm qualibug-evaluator-infra-smoke
docker ps -a --filter name=qualibug-evaluator-infra-smoke --format '{{.Names}}'
```

Expected: client and server versions print, Compose prints a version, hello-world exits `0`, explicit removal succeeds, and the final command prints nothing.

- [ ] **Step 8: Persist infrastructure readiness and recheck reserved ports**

Persist a redacted receipt with WSL version, distro identity, Docker Desktop version, Docker server version, Compose version, D-drive data paths, free disk bytes, free memory bytes, smoke exit code, and current listeners on `5174`, `8088`, and `18080`.

Expected: infrastructure status is `ready`, both D-drive roots exist, QualiBug ports retain their intended owners, `18080` is free, and no target container is running.

### Task 5: Stop the Mall, Admit OpenProject, and Render Its Configuration

**Files:**
- External config: `D:\QualiBug-Evaluator-Targets\openproject\runtime\.env`
- External state: `D:\QualiBug-Evaluator-Targets\openproject\pgdata`
- External state: `D:\QualiBug-Evaluator-Targets\openproject\opdata`
- External receipts: `D:\QualiBug-Evaluator-Targets\receipts\0001-benchmark-mall-131-stopped_clean.json`
- External receipts: `D:\QualiBug-Evaluator-Targets\receipts\0002-openproject-17.6.0-asset_valid.json`
- External receipts: `D:\QualiBug-Evaluator-Targets\receipts\0003-openproject-17.6.0-deployable.json`

**Interfaces:**
- Consumes pinned source SHA-256 `AC2C393C823F31D5CCFA3D8CC15205B19E7F2D61FC52F855E224D4D61640718C` and deployment commit `56ec06d73df236080f169198a54ab428fc47c3f7` from the bundle manifest.
- Consumes Compose project name `qualibug-eval-openproject` and external `.env`.
- Produces a rendered Compose configuration without starting containers.

- [ ] **Step 1: Stop the benchmark mall with its own stop script**

Run only:

```powershell
& 'C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\qualibug_enterprise_benchmark_v0_5_windows_native_stable\scripts\stop_all_windows.ps1'
```

Expected: its managed services stop. Do not terminate processes by broad name matching.

- [ ] **Step 2: Verify the mall ports are released and QualiBug remains online**

Run:

```powershell
$mallPorts = 3001,3002,8080,8001,8002,8003,8004,8005,8006,8007,8008,8009,8010
$mallListeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -in $mallPorts
if ($mallListeners) { $mallListeners | Format-Table; throw 'benchmark mall still listening' }
$qualibug = Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -in 5174,8088
if (($qualibug.LocalPort | Sort-Object -Unique) -join ',' -ne '5174,8088') { throw 'QualiBug ports are not both online' }
```

Expected: no mall listener and both QualiBug listeners present.

- [ ] **Step 3: Emit the mall `STOPPED_CLEAN` receipt and check admission**

Run:

```powershell
python tools/evaluator_target_readiness.py emit --receipts-root D:\QualiBug-Evaluator-Targets\receipts --sequence 1 --target-id benchmark-mall-131 --target-role held_in_diagnostic --state STOPPED_CLEAN --previous-state RUNTIME_READY --environment-type sandbox --environment-ref benchmark-mall-local-sandbox --target-url http://127.0.0.1:8080 --check target_stopped=passed --check ports_released=passed --fingerprint benchmark_manifest_sha256=F0E7B477F3AE806B8377EC484E1D365B116291A0F82444B7786BAD73D783FC99
python tools/evaluator_target_readiness.py admit --receipts-root D:\QualiBug-Evaluator-Targets\receipts --requested-target-id openproject-17.6.0
```

Immediately before emitting, recompute SHA-256 for `BENCHMARK_MANIFEST.json` and require the exact value `F0E7B477F3AE806B8377EC484E1D365B116291A0F82444B7786BAD73D783FC99`; a mismatch emits `BLOCKED_MALL_FINGERPRINT_MISMATCH` and prevents OpenProject admission.

Expected: receipt persists and admission exits `0` with `allowed=true`.

- [ ] **Step 4: Recompute and verify the pinned OpenProject source archive**

Run:

```powershell
$source = 'C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\open-source-test-systems-completed-20260711\02-openproject\source-v17.6.0.tar.gz'
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
if ($actual -ne 'AC2C393C823F31D5CCFA3D8CC15205B19E7F2D61FC52F855E224D4D61640718C') { throw 'OpenProject source SHA-256 mismatch' }
```

Expected: exact SHA-256 match.

- [ ] **Step 5: Create the external runtime directories and secret-bearing `.env`**

Run this exact local materializer. It generates secrets inside Python, writes only the external functional config, and emits only irreversible secret fingerprints through the repository redactor:

```powershell
@'
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from ai_test_asset_center.artifact_redactor import write_json_redacted


runtime = Path(r"D:\QualiBug-Evaluator-Targets\openproject\runtime")
runtime.mkdir(parents=True, exist_ok=True)
Path(r"D:\QualiBug-Evaluator-Targets\openproject\pgdata").mkdir(parents=True, exist_ok=True)
Path(r"D:\QualiBug-Evaluator-Targets\openproject\opdata").mkdir(parents=True, exist_ok=True)

secret_key_base = secrets.token_urlsafe(48)
collaboration_secret = secrets.token_urlsafe(48)
postgres_password = secrets.token_urlsafe(32)
values = {
    "TAG": "17-slim",
    "OPENPROJECT_HTTPS": "false",
    "SECRET_KEY_BASE": secret_key_base,
    "OPENPROJECT_HOST__NAME": "127.0.0.1:18080",
    "PORT": "127.0.0.1:18080",
    "OPENPROJECT_RAILS__RELATIVE__URL__ROOT": "",
    "IMAP_ENABLED": "false",
    "DATABASE_URL": f"postgres://postgres:{postgres_password}@db/openproject?pool=20&encoding=unicode&reconnect=true",
    "POSTGRES_PASSWORD": postgres_password,
    "RAILS_MIN_THREADS": "2",
    "RAILS_MAX_THREADS": "8",
    "PGDATA": "D:/QualiBug-Evaluator-Targets/openproject/pgdata",
    "OPDATA": "D:/QualiBug-Evaluator-Targets/openproject/opdata",
    "COLLABORATIVE_SERVER_URL": "ws://127.0.0.1:18080/hocuspocus",
    "COLLABORATIVE_SERVER_SECRET": collaboration_secret,
    "POSTGRES_VERSION": "17",
}
for key, value in values.items():
    if "\r" in value or "\n" in value:
        raise RuntimeError(f"newline rejected in environment value: {key}")
target = runtime / ".env"
temporary = runtime / ".env.tmp"
temporary.write_text(
    "".join(f"{key}={value}\n" for key, value in values.items()),
    encoding="utf-8",
)
os.replace(temporary, target)
subprocess.run(
    [
        "icacls.exe",
        str(target),
        "/inheritance:r",
        "/grant:r",
        f"{os.environ['USERNAME']}:(R,W)",
        "SYSTEM:(F)",
    ],
    check=True,
    capture_output=True,
    text=True,
)
receipt = {
    "schema_version": "qualibug.evaluator-secret-config-receipt.v1",
    "target_id": "openproject-17.6.0",
    "config_path": str(target),
    "config_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    "secret_records": [
        {"name": "SECRET_KEY_BASE", "secret_present": True, "fingerprint": hashlib.sha256(secret_key_base.encode()).hexdigest()},
        {"name": "COLLABORATIVE_SERVER_SECRET", "secret_present": True, "fingerprint": hashlib.sha256(collaboration_secret.encode()).hexdigest()},
        {"name": "POSTGRES_PASSWORD", "secret_present": True, "fingerprint": hashlib.sha256(postgres_password.encode()).hexdigest()},
    ],
    "raw_secret_persisted_in_receipt": False,
}
write_json_redacted(
    Path(r"D:\QualiBug-Evaluator-Targets\receipts\openproject-secret-config.json"),
    receipt,
)
print(json.dumps({"status": "written", "path": str(target)}, ensure_ascii=False))
'@ | python -
```

Expected: `.env` exists, its ACL contains only the current user and SYSTEM, no generated value appears in stdout or repository files, and the receipt contains secret presence plus irreversible fingerprints only.

- [ ] **Step 6: Render Compose without starting OpenProject**

Run:

```powershell
$deployment = 'C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\open-source-test-systems-completed-20260711\02-openproject\deployment\docker-compose.yml'
$envFile = 'D:\QualiBug-Evaluator-Targets\openproject\runtime\.env'
docker compose --project-name qualibug-eval-openproject --env-file $envFile -f $deployment config --quiet
docker compose --project-name qualibug-eval-openproject --env-file $envFile -f $deployment config --services
```

Expected: render exits `0`; services are `db`, `cache`, `proxy`, `web`, `autoheal`, `worker`, `cron`, `seeder`, and `hocuspocus`; only proxy publishes `127.0.0.1:18080`.

- [ ] **Step 7: Emit `ASSET_VALID` and `DEPLOYABLE` receipts**

Include the verified source SHA-256, a SHA-256 of the pinned deployment directory content manifest, a redacted `.env` fingerprint, Compose rendered-config fingerprint, exact target policy decision, and checks for checksum, reserved-port collision, secret presence, and Compose rendering.

Expected: sequence 2 transitions from `NOT_STARTED` to `ASSET_VALID`; sequence 3 transitions to `DEPLOYABLE`; both remain `NOT_MEASURED`.

### Task 6: Start OpenProject and Assess Runtime Readiness

**Files:**
- External receipts under `D:\QualiBug-Evaluator-Targets\receipts`
- Existing governed write audit under the configured QualiBug project workspace if fixture execution is authorized.

**Interfaces:**
- Consumes: exact Compose project and external config from Task 5.
- Consumes: OpenProject documented health endpoint `/health_checks/default`, API root `/api/v3`, and API documentation `/api/docs`.
- Produces: `RUNTIME_READY` only if all required checks pass; otherwise a `BLOCKED` receipt with exact operator action.

- [ ] **Step 1: Recheck serial admission and resource capacity immediately before startup**

Run:

```powershell
python tools/evaluator_target_readiness.py admit --receipts-root D:\QualiBug-Evaluator-Targets\receipts --requested-target-id openproject-17.6.0
Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory
Get-PSDrive D | Select-Object Free
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 18080
```

Expected: admission allowed, at least 4 GiB free physical memory, at least 20 GiB free on D, and no listener on `18080`. Otherwise emit `BLOCKED_RESOURCE_CAPACITY` or `BLOCKED_TARGET_PORT_IN_USE` before Compose starts.

- [ ] **Step 2: Start exactly one Compose project**

Run:

```powershell
docker compose --project-name qualibug-eval-openproject --env-file D:\QualiBug-Evaluator-Targets\openproject\runtime\.env -f 'C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\open-source-test-systems-completed-20260711\02-openproject\deployment\docker-compose.yml' up -d --build --pull always
```

Expected: only containers labeled with Compose project `qualibug-eval-openproject` start. If startup partially fails, capture `docker compose ... ps --all` and `docker compose ... logs --no-color`, redact them, then run Task 7 cleanup.

- [ ] **Step 3: Poll health with a bounded deadline**

Poll `http://127.0.0.1:18080/health_checks/default` every 5 seconds for at most 10 minutes. Success requires HTTP 200 and a healthy `web` container. Do not convert timeout to success.

Also run:

```powershell
docker compose --project-name qualibug-eval-openproject --env-file D:\QualiBug-Evaluator-Targets\openproject\runtime\.env -f 'C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\open-source-test-systems-completed-20260711\02-openproject\deployment\docker-compose.yml' ps --all
```

Expected: declared long-running services are running and `web` is healthy; `seeder` may have exited successfully after initialization.

- [ ] **Step 4: Verify browser rendering and login without claiming a write result**

Open `http://127.0.0.1:18080` in the in-app browser, verify the OpenProject login page renders, and authenticate with the upstream documented initial administrator credentials only if the login does not force an ungoverned password change. Capture the visible result and HTTP evidence.

If authentication requires changing a password, creating a token, accepting terms, or any other write outside the governed executor, stop the login step and record `BLOCKED_GOVERNED_ACTOR_PROVISIONING`. Do not bypass the boundary through Rails console, direct SQL, seed scripts, or browser form submission.

- [ ] **Step 5: Verify read-only API and database observation**

Run:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18080/api/v3 -Method Get
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18080/api/docs -Method Get
docker compose --project-name qualibug-eval-openproject --env-file D:\QualiBug-Evaluator-Targets\openproject\runtime\.env -f 'C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\open-source-test-systems-completed-20260711\02-openproject\deployment\docker-compose.yml' exec -T db psql -U postgres -d openproject -c 'SELECT current_database(), current_user;'
```

Expected: API root and docs return HTTP 200; database observation reports database `openproject` and user `postgres`. Persist only schema/table counts and query status, never row contents containing user data.

- [ ] **Step 6: Execute one governed fixture lifecycle only when a valid API token secret reference already exists**

Use the existing Target Policy and `execute_governed_control_write` with a token resolved at runtime from the secret reference. The documented operations are:

- prepare: `POST /api/v3/projects` with a unique disposable project identifier;
- observe: `GET /api/v3/projects/{returned_id}`;
- cleanup: `DELETE /api/v3/projects/{returned_id}`;
- cleanup verification: repeated bounded `GET` until HTTP 404 because OpenProject deletion is asynchronous.

Each write must have its own audit receipt and exact concrete path. Do not retry the prepare request after any response that might indicate acceptance. If no token reference exists, emit `BLOCKED_ACTOR_SECRET_REF_MISSING` before the POST and leave `fixture_prepare`/`fixture_cleanup` failed.

- [ ] **Step 7: Emit the honest readiness result**

Emit `RUNTIME_READY` only when health, login, API, database observation, fixture prepare, and fixture cleanup all equal `passed`. If any check is absent or failed, emit `BLOCKED` with the exact blocking code and operator action, and keep `measurement_status=NOT_MEASURED`.

Do not emit `EVALUATOR_READY`: no frozen evaluator-private OpenProject Ground Truth or audited clean designation exists in this plan.

### Task 7: Stop and Clean OpenProject by Exact Project Identity

**Files:**
- External receipt: next sequence `openproject-17.6.0-stopped_clean.json`
- Redacted logs retained under `D:\QualiBug-Evaluator-Targets\openproject\artifacts`

**Interfaces:**
- Consumes: exact Compose project identity and runtime `.env`.
- Produces: `STOPPED_CLEAN` before any later target may start.

- [ ] **Step 1: Capture final runtime state and unresolved cleanup failures**

Run `docker compose ... ps --all` and capture only the named project logs. Preserve every original fixture cleanup failure in the receipt even if target teardown later succeeds.

- [ ] **Step 2: Stop only the OpenProject Compose project**

Run:

```powershell
docker compose --project-name qualibug-eval-openproject --env-file D:\QualiBug-Evaluator-Targets\openproject\runtime\.env -f 'C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\open-source-test-systems-completed-20260711\02-openproject\deployment\docker-compose.yml' down --remove-orphans
```

Expected: containers and network for `qualibug-eval-openproject` stop. Do not use `down -v`, `docker system prune`, `docker volume prune`, or broad container deletion.

- [ ] **Step 3: Verify cleanup and port release**

Run:

```powershell
$remaining = docker ps -a --filter label=com.docker.compose.project=qualibug-eval-openproject --format '{{.ID}}'
if ($remaining) { throw 'OpenProject project containers remain' }
$listener = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 18080
if ($listener) { throw 'OpenProject port remains bound' }
```

Expected: no matching container and no `18080` listener. D-drive pgdata/opdata remain for auditable reset analysis; they are not silently deleted.

- [ ] **Step 4: Emit `STOPPED_CLEAN` and prove the next target is serially admissible**

The receipt must include `target_stopped=passed`, `ports_released=passed`, any original cleanup failures, retained-state paths, Compose project fingerprint, and operator action. Then run:

```powershell
python tools/evaluator_target_readiness.py admit --receipts-root D:\QualiBug-Evaluator-Targets\receipts --requested-target-id erpnext-16.26.2
```

Expected: admission is allowed only because the latest OpenProject receipt is `STOPPED_CLEAN`. This command does not start ERPNext.

### Task 8: Documentation, Regression, and Gate Status

**Files:**
- Modify: `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`

**Interfaces:**
- Consumes: actual receipts and observed blockers from Tasks 3-7.
- Produces: living documentation and verified non-measurement status.

- [ ] **Step 1: Update the architecture SSOT**

Add a concise section linking:

- the approved design `docs/superpowers/specs/2026-07-11-cross-industry-evaluator-target-deployment-design.md`;
- readiness schema `qualibug.evaluator-target-readiness.v1`;
- receipt root policy (external artifacts, not Git history);
- strict serial rule and `STOPPED_CLEAN` gate;
- the distinction between deployment readiness and external quality measurement.

Do not add OpenProject routes, ports, or credentials to active product architecture.

- [ ] **Step 2: Run syntax and focused regression tests**

Run:

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/evaluator_target_readiness.py', encoding='utf-8').read()); ast.parse(open('tools/evaluator_target_readiness.py', encoding='utf-8').read()); print('OK')"
python -m pytest tests/test_evaluator_target_readiness.py tests/test_evaluator_target_readiness_cli.py tests/test_nonproduction_write_contract.py tests/test_evaluation_fixture_controller.py tests/test_discovery_evaluation_contract.py -q
```

Expected: AST `OK`; all focused tests pass.

- [ ] **Step 3: Run the full Python regression suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass with only documented skips. Any new failure blocks completion.

- [ ] **Step 4: Verify the running QualiBug product and Gate status**

Run:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5174
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8088/api/v1/health
python tools/discovery_evaluation.py goal-status
```

Expected: QualiBug endpoints respond from current code; Gate A/B/C remain based on actual output; Gate D and controlled pilot remain `NOT_MEASURED` with explicit missing external evaluation evidence.

- [ ] **Step 5: Inspect the final diff and commit only documentation paths**

Run:

```powershell
git diff -- docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md
git add -- docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md
git commit -m "docs: define serial evaluator target readiness"
```

Expected: the commit contains only the intended documentation changes. External receipts and secret-bearing runtime configuration remain outside Git.

## Acceptance Boundary

This plan is complete only when:

1. WSL2 Ubuntu, Docker engine, and Compose are verified online with data roots on D.
2. The benchmark mall is stopped before OpenProject starts.
3. The pinned OpenProject source and deployment fingerprints are verified.
4. OpenProject starts alone, is observed honestly, and never advances past the checks it actually passed.
5. Every governed fixture write, if any, has a before/after and cleanup receipt.
6. OpenProject is stopped by exact Compose identity and the final `STOPPED_CLEAN` receipt exists.
7. ERPNext has not been started.
8. Gate D remains `NOT_MEASURED`; no held-out recall, precision, F1, clean-target result, or controlled-pilot claim is produced.

## Authoritative External References

- Microsoft WSL installation: https://learn.microsoft.com/windows/wsl/install
- Microsoft WSL commands: https://learn.microsoft.com/windows/wsl/basic-commands
- Docker Desktop Windows install flags: https://docs.docker.com/desktop/setup/install/windows-install/
- Docker Desktop WSL2 backend: https://docs.docker.com/desktop/features/wsl/
- Docker Desktop WSL best practices: https://docs.docker.com/desktop/features/wsl/best-practices/
- OpenProject Compose README: pinned local bundle `02-openproject/deployment/README.md`
- OpenProject API authentication and docs: https://www.openproject.org/docs/api/introduction/
- OpenProject project create/delete contract: https://www.openproject.org/docs/api/endpoints/projects/
