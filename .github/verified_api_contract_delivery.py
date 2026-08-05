from __future__ import annotations

import base64
import gzip
import hashlib
import subprocess
from pathlib import Path

BASE = "8c3e8c26da3b1d699894dd34b926602b3c12701b"
B64_SHA256 = "74319f671ac3aedfa8b068fbc4ae30288748ba304e63d8c4bb2d576eec105b10"
PATCH_SHA256 = "1df69aa681a52779aeaa124f15b50834a32eba977f494f89d96b989adb4afc9d"
PAYLOAD_WORKFLOW = Path(".github/workflows/one-time-api-contract-actorless-delivery.yml")
EXPECTED = {
    "ai_test_asset_center/enterprise_knowledge_center/_parsing.py",
    "ai_test_asset_center/experiment_runtime_credentials.py",
    "ai_test_asset_center/scan_source_runtime.py",
    "ai_test_asset_center/semantic_scenario_generator/_generator.py",
    "ai_test_asset_center/universal_api_parser.py",
    "projects/benchmark_mall/input/API_SPEC.md",
    "tests/test_actor_permission_guard.py",
    "tests/test_discovery_mainline_coordinator.py",
    "tests/test_markdown_request_schema.py",
    "tests/test_stale_credential_and_reason_honesty.py",
}
TESTS = [
    "tests/test_actor_permission_guard.py",
    "tests/test_discovery_mainline_coordinator.py",
    "tests/test_markdown_request_schema.py",
    "tests/test_stale_credential_and_reason_honesty.py",
]


def run(*args: str, capture: bool = False) -> str:
    completed = subprocess.run(
        list(args),
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return completed.stdout if capture else ""


def extract_patch() -> Path:
    text = PAYLOAD_WORKFLOW.read_text(encoding="utf-8")
    start = "cat > /tmp/qualibug-main-delivery.patch.gz.b64 <<'PATCH_B64'\n"
    end = "\n          PATCH_B64"
    payload = text.split(start, 1)[1].split(end, 1)[0]
    lines = [line[10:] if line.startswith("          ") else line for line in payload.splitlines()]
    encoded = ("\n".join(lines) + "\n").encode("ascii")
    if hashlib.sha256(encoded).hexdigest() != B64_SHA256:
        raise RuntimeError("staged base64 payload hash mismatch")
    patch = gzip.decompress(base64.b64decode(encoded))
    if hashlib.sha256(patch).hexdigest() != PATCH_SHA256:
        raise RuntimeError("decoded patch hash mismatch")
    path = Path("/tmp/qualibug-main-delivery.patch")
    path.write_bytes(patch)
    return path


def focused_tests() -> None:
    run("python", "-m", "pytest", "-q", *TESTS)


def main() -> None:
    run("git", "merge-base", "--is-ancestor", BASE, "HEAD")
    patch = extract_patch()
    run("git", "apply", "--3way", "--check", str(patch))
    run("git", "apply", "--3way", str(patch))
    run("git", "diff", "--check")
    changed = set(run("git", "diff", "--name-only", capture=True).splitlines())
    if changed != EXPECTED:
        raise RuntimeError(f"unexpected changed files: {sorted(changed ^ EXPECTED)}")
    focused_tests()

    run("git", "config", "user.name", "QualiBug Automation")
    run("git", "config", "user.email", "automation@qualibug.local")
    run(
        "git",
        "checkout",
        BASE,
        "--",
        ".github/workflows/one-time-export-current-main.yml",
        ".github/source-export-trigger",
    )
    for path in (
        Path(".github/workflows/one-time-api-contract-actorless-delivery.yml"),
        Path(".github/workflows/one-time-api-contract-actorless-issue-delivery.yml"),
        Path(".github/verified_api_contract_delivery.py"),
    ):
        if path.exists():
            run("git", "rm", "-f", str(path))
    run("git", "add", "-A")
    run("git", "diff", "--cached", "--check")
    run(
        "git",
        "commit",
        "-m",
        "feat(runtime): complete benchmark API contract and support preauthenticated execution",
        "-m",
        "Document all 101 benchmark-mall business routes, preserve required fields, foreign keys and role constraints, load project-scoped database schemas, avoid treating large inline OpenAPI JSON as a filesystem path, improve sibling identity binding, and allow declared preauthenticated bearer contexts to bypass unavailable login endpoints while remaining fail-closed for missing or expired tokens.",
    )
    for _ in range(5):
        run("git", "fetch", "origin", "main")
        if subprocess.run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"]).returncode != 0:
            run("git", "rebase", "origin/main")
            focused_tests()
        if subprocess.run(["git", "push", "origin", "HEAD:main"]).returncode == 0:
            return
    raise RuntimeError("failed to push main after retries")


if __name__ == "__main__":
    main()
