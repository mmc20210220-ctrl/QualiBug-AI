# -*- coding: utf-8 -*-
"""Phase-6b: authenticated observed diagnostic for Fact→Experiment code.

Runs ``tools/run_observed_discovery_diagnostic.py`` through the evaluator-owned
HTTP observation gateway so the receipt can carry a verified
``qualibug.evaluator-execution-attestation.v1``.

Compared to the local HMAC-only reeval, this path can produce
``measurement_status=MEASURED`` with TP/FP/FN. It remains a one-target
diagnostic (``require_commercial_shape=False``) and is not commercial
promotion evidence by itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL_ROOT = Path(r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716")
MANIFEST = EVAL_ROOT / "manifest" / "evaluation_manifest.json"
REGISTRY = REPO / "platform_outputs" / "policy_registry.json"
HMAC_KEY = EVAL_ROOT / "evaluator-hmac.key"
OBSERVATION_ROOT = EVAL_ROOT / "observations"
TARGET_ID = "benchmark-mall-held-in-131"
DEFAULT_BENCHMARK_TARGET_ROOT = Path(
    r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
    r"\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
)


def main() -> int:
    for path, label in (
        (MANIFEST, "manifest"),
        (REGISTRY, "policy registry"),
        (HMAC_KEY, "HMAC key"),
        (OBSERVATION_ROOT, "observation root"),
    ):
        if not path.exists():
            raise SystemExit(f"missing {label}: {path}")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    evaluation_id = f"fact-to-experiment-observed-{stamp}"
    output_root = (
        REPO
        / "_funnel_runs"
        / f"20260802_fact_to_experiment_observed_{stamp}"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.setdefault(
        "QUALIBUG_BENCHMARK_TARGET_ROOT",
        str(DEFAULT_BENCHMARK_TARGET_ROOT),
    )
    env.setdefault(
        "QUALIBUG_DB_DSN",
        "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall",
    )
    env.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
    env.setdefault("QUALIBUG_TARGET_BASE_URL", "http://localhost:8080")
    env.setdefault("QUALIBUG_SSRF_ALLOW_INTERNAL", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    start_manifest = {
        "schema_version": "qualibug.fact-to-experiment-observed-diagnostic.v1",
        "evaluation_id": evaluation_id,
        "target_id": TARGET_ID,
        "manifest": str(MANIFEST),
        "registry": str(REGISTRY),
        "observation_root": str(OBSERVATION_ROOT),
        "output_root": str(output_root),
        "fixture_controller": "windows-benchmark",
        "notes": (
            "Authenticated one-target diagnostic after Fact→Experiment phases "
            "1–5. MEASURED TP/FP/FN require verified execution attestation; "
            "still not commercial promotion evidence alone."
        ),
    }
    (output_root / "start_manifest.json").write_text(
        json.dumps(start_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "wrapper_pid.txt").write_text(
        f"{os.getpid()}\n", encoding="utf-8"
    )
    (output_root / "status.json").write_text(
        json.dumps(
            {
                "phase": "starting_diagnostic",
                "pid": os.getpid(),
                "started_at_utc": stamp,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(start_manifest, ensure_ascii=False, indent=2), flush=True)

    stdout_path = output_root / "diagnostic_stdout.log"
    stderr_path = output_root / "diagnostic_stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout_fh, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_fh:
        (output_root / "status.json").write_text(
            json.dumps(
                {
                    "phase": "running_diagnostic",
                    "pid": os.getpid(),
                    "started_at_utc": stamp,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO / "tools" / "run_observed_discovery_diagnostic.py"),
                "--manifest",
                str(MANIFEST),
                "--target-id",
                TARGET_ID,
                "--output-root",
                str(output_root),
                "--registry",
                str(REGISTRY),
                "--trusted-observation-root",
                str(OBSERVATION_ROOT),
                "--workspace-root",
                str(REPO),
                "--hmac-key-file",
                str(HMAC_KEY),
                "--fixture-controller",
                "windows-benchmark",
                "--evaluation-mode",
                "replay",
                "--evaluation-id",
                evaluation_id,
            ],
            cwd=str(REPO),
            env=env,
            check=False,
            stdout=stdout_fh,
            stderr=stderr_fh,
        )
    (output_root / "diagnostic_exit_code.txt").write_text(
        str(completed.returncode) + "\n", encoding="utf-8"
    )
    (output_root / "status.json").write_text(
        json.dumps(
            {
                "phase": "diagnostic_finished",
                "pid": os.getpid(),
                "exit_code": int(completed.returncode),
                "started_at_utc": stamp,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Mirror logs to wrapper stdout for interactive runs.
    for label, path in (("STDOUT", stdout_path), ("STDERR", stderr_path)):
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            print(f"===== diagnostic {label} =====", flush=True)
            print(text[-8000:], flush=True)
    if completed.returncode != 0:
        return int(completed.returncode)

    # Extract TP/FP/FN from the newest receipt under this output root.
    # Deep Windows receipt trees exceed MAX_PATH; walk via \\?\ and never
    # pathlib.stat on the long relative form.
    def _extended(path: Path | str) -> str:
        text = str(path)
        if text.startswith("\\\\?\\"):
            return text
        return "\\\\?\\" + text

    receipt_paths: list[tuple[float, str]] = []
    for dirpath, _dirnames, filenames in os.walk(_extended(output_root)):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            full = os.path.join(dirpath, name)
            try:
                receipt_paths.append((os.path.getmtime(full), full))
            except OSError:
                continue
    receipt_paths.sort(key=lambda item: item[0])
    for _mtime, path in reversed(receipt_paths):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        measurement = str(payload.get("measurement_status") or "").strip()
        metrics = (
            payload.get("metrics")
            if isinstance(payload.get("metrics"), dict)
            else {}
        )
        display_path = path[4:] if path.startswith("\\\\?\\") else path
        if not measurement and "true_positives" not in metrics:
            # Aggregate report shape.
            held_in = (
                payload.get("held_in")
                if isinstance(payload.get("held_in"), dict)
                else {}
            )
            if held_in:
                extract = {
                    "report_path": display_path,
                    "claim_status": payload.get("claim_status"),
                    "held_in": {
                        "true_positives": held_in.get("true_positives"),
                        "false_positives": held_in.get("false_positives"),
                        "false_negatives": held_in.get("false_negatives"),
                        "micro_recall": held_in.get("micro_recall"),
                        "micro_precision": held_in.get("micro_precision"),
                        "measured_seeded_target_count": held_in.get(
                            "measured_seeded_target_count"
                        ),
                    },
                    "commercial_promotion_evidence_ready": payload.get(
                        "commercial_promotion_evidence_ready"
                    ),
                    "honesty": (
                        "one-target authenticated diagnostic; not commercial "
                        "promotion evidence by itself"
                    ),
                }
                (output_root / "evaluation_score_extract.json").write_text(
                    json.dumps(extract, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(json.dumps(extract, ensure_ascii=False, indent=2), flush=True)
                break
        if measurement:
            extract = {
                "receipt_path": display_path,
                "measurement_status": measurement,
                "not_measured_reason": payload.get("not_measured_reason") or "",
                "true_positives": metrics.get("true_positives"),
                "false_positives": metrics.get("false_positives"),
                "false_negatives": metrics.get("false_negatives"),
                "precision": metrics.get("precision"),
                "recall": metrics.get("recall"),
                "execution_attestation_status": (
                    (payload.get("execution_attestation") or {}).get("status")
                    if isinstance(payload.get("execution_attestation"), dict)
                    else None
                ),
                "pipeline_health_status": (
                    (payload.get("pipeline_health") or {}).get("status")
                    if isinstance(payload.get("pipeline_health"), dict)
                    else None
                ),
                "honesty": (
                    "one-target authenticated diagnostic; not commercial "
                    "promotion evidence by itself"
                ),
            }
            (output_root / "evaluation_score_extract.json").write_text(
                json.dumps(extract, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(extract, ensure_ascii=False, indent=2), flush=True)
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
