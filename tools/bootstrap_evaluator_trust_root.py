from __future__ import annotations

"""Create an evaluator-owned trust root outside the product workspace."""

import argparse
import json
import os
import secrets
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--product-workspace", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    workspace = Path(args.product_workspace).resolve()
    if root == workspace or workspace in root.parents:
        raise RuntimeError("evaluator trust root must be outside product workspace")
    root.mkdir(parents=True, exist_ok=True)
    observations = root / "observations"
    outputs = root / "outputs"
    observations.mkdir(exist_ok=True)
    outputs.mkdir(exist_ok=True)
    key_path = root / "evaluator-hmac.key"
    created = False
    if not key_path.exists():
        descriptor = os.open(
            str(key_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, secrets.token_bytes(48))
        finally:
            os.close(descriptor)
        os.chmod(key_path, 0o600)
        created = True
    if not key_path.is_file() or key_path.stat().st_size < 32:
        raise RuntimeError("evaluator HMAC key file is invalid")
    print(json.dumps({
        "root": str(root),
        "observation_root": str(observations),
        "output_root": str(outputs),
        "hmac_key_file": str(key_path),
        "key_created": created,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
