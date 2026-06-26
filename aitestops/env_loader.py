from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Set


ROOT = Path(__file__).resolve().parents[1]


def _load_one(env_path: Path, loaded: Dict[str, str], *, protected_keys: Set[str], override: bool = False) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        if key in protected_keys:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def load_dotenv(path: Path | None = None) -> Dict[str, str]:
    """Load a small .env file without external dependencies.

    Existing environment variables win over file values. This keeps CI secrets safe.
    By default, load `.env` first and then `.env.local` for machine-local secrets.
    """
    loaded: Dict[str, str] = {}
    protected_keys = set(os.environ)
    if path is not None:
        _load_one(path, loaded, protected_keys=protected_keys)
        return loaded

    _load_one(ROOT / ".env", loaded, protected_keys=protected_keys)
    _load_one(ROOT / ".env.local", loaded, protected_keys=protected_keys, override=True)
    return loaded
