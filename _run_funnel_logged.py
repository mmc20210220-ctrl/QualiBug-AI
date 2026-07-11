"""Run _funnel_benchmark with tee to a log file (avoids PowerShell redirect quirks)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).resolve().parent
mode = sys.argv[1] if len(sys.argv) > 1 else "llm_throughput"
log_path = ROOT / "_funnel_runs" / f"_{mode}_wrapper.log"
log_path.parent.mkdir(parents=True, exist_ok=True)

cmd = [sys.executable, "-u", str(ROOT / "_funnel_benchmark.py"), mode]
print(f"START {cmd} -> {log_path}", flush=True)

# Binary pipe + manual decode: child may emit mixed GBK/UTF-8 on Windows.
with log_path.open("wb") as log:
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.readline()
        if not chunk:
            break
        log.write(chunk)
        log.flush()
        text = chunk.decode("utf-8", errors="replace")
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except UnicodeEncodeError:
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
    code = proc.wait()
    footer = f"\nWRAPPER_EXIT={code}\n".encode("utf-8")
    log.write(footer)
    try:
        sys.stdout.write(footer.decode("utf-8"))
        sys.stdout.flush()
    except Exception:
        sys.stdout.buffer.write(footer)
sys.exit(code)
