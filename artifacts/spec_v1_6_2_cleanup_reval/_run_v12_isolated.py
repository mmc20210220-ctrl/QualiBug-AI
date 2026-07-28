"""Isolated backend+scan launcher for cleanup-equivalence reval V12."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8088/health", timeout=2) as resp:
            return int(resp.status) == 200
    except Exception:
        return False


def _kill_port(port: int) -> None:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, errors="replace"
        )
    except Exception:
        return
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            pid = parts[-1]
            if pid.isdigit() and int(pid) > 0:
                subprocess.run(
                    ["taskkill", "/PID", pid, "/F"],
                    capture_output=True,
                    text=True,
                )


def main() -> int:
    os.chdir(ROOT)
    _kill_port(8088)
    time.sleep(1)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["QUALIBUG_LOCAL_DEV_ACTOR"] = "1"
    env["QUALIBUG_PORT"] = "8088"

    out_path = OUT / "_backend.out"
    err_path = OUT / "_backend.err"
    out_f = out_path.open("w", encoding="utf-8", errors="replace")
    err_f = err_path.open("w", encoding="utf-8", errors="replace")
    backend = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "ai_test_asset_center.private_pilot_entrypoint",
            "--host",
            "127.0.0.1",
            "--port",
            "8088",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=out_f,
        stderr=err_f,
    )
    (OUT / "_scan_pid.txt").write_text(str(backend.pid), encoding="utf-8")
    print(f"backend_pid={backend.pid}", flush=True)
    try:
        ready = False
        for _ in range(90):
            if backend.poll() is not None:
                print(f"backend_exited_early code={backend.returncode}", flush=True)
                return 2
            if _health_ok():
                ready = True
                break
            time.sleep(1)
        if not ready:
            print("backend_not_ready", flush=True)
            return 3
        print("backend_ready", flush=True)

        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "from ai_test_asset_center.experiment_cleanup_executor import _project_database_dsn; "
                    "dsn, err = _project_database_dsn(Path('.'), 'benchmark_mall_131'); "
                    "print('dsn_ok' if dsn and not err else f'dsn_fail err={err}'); "
                    "raise SystemExit(0 if dsn and not err else 4)"
                ),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        print(probe.stdout or "", end="", flush=True)
        if probe.returncode != 0:
            print(probe.stderr or "", end="", file=sys.stderr, flush=True)
            print(f"dsn_probe_exit={probe.returncode}", flush=True)
            return int(probe.returncode)

        scan = subprocess.run(
            [sys.executable, "-u", str(OUT / "_formal_product_scan.py")],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        (OUT / "_formal_scan_console_V12.log").write_text(
            scan.stdout or "", encoding="utf-8"
        )
        (OUT / "_formal_scan_console_V12.err").write_text(
            scan.stderr or "", encoding="utf-8"
        )
        print(scan.stdout or "", end="", flush=True)
        if scan.stderr:
            print(scan.stderr, end="", file=sys.stderr, flush=True)
        print(f"scan_exit={scan.returncode}", flush=True)
        return int(scan.returncode)
    finally:
        out_f.close()
        err_f.close()


if __name__ == "__main__":
    raise SystemExit(main())
