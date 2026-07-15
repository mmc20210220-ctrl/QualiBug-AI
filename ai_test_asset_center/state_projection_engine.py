from __future__ import annotations

"""
State Projection Engine — JSONPath-like Field Extraction from Nested Dicts

Pure-Python extraction of deeply nested values from dict/list structures
using a dot-and-bracket path syntax.  Drop-in utility for projection and
comparison pipelines inside the AI Test Asset Center.

Supported path syntax
---------------------
- Simple dot-path         "data.status"            → nested dict key
- Bracket notation        "data[0].name"           → list index
- JSONPath `$` prefix     "$.data.status"          → identical to dot-path
- Numeric array indices   "items[0].quantity"      → list index then dict key
- Mixed nesting           "a.b[2].c[0].d"          → arbitrary depth

Design
------
- extract() returns None for any missing key/index — no exceptions raised
- to_number() coerces numeric strings, ints, and floats; returns None for
  non-numeric values (safe for threshold comparisons)
- Zero external dependencies; standard library only

Author: QualiBug AI Enterprise Edition — Phase 61+
"""

import re
from typing import Any


# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"(?:[\w-]+)|(?:\[-?\d+\])")


def _tokenise(path: str) -> list[str]:
    """Split a path string into a list of key / index tokens."""
    # Strip leading `$.` or `$` prefix (JSONPath style)
    work = path.strip()
    if work.startswith("$."):
        work = work[2:]
    elif work.startswith("$"):
        work = work[1:]

    tokens = _TOKEN_RE.findall(work)
    return tokens


def _resolve_index(token: str) -> int | None:
    """Return the integer index from a bracket token like '[3]', or None."""
    inner = token.strip("[]")
    try:
        return int(inner)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# StateProjectionEngine
# ---------------------------------------------------------------------------


class StateProjectionEngine:
    """Extract values from nested dict/list structures by path expression.

    Usage::

        engine = StateProjectionEngine()
        value = engine.extract(response, "data.items[0].price")
        num   = engine.to_number(value)
    """

    # ------------------------------------------------------------------
    # extract()
    # ------------------------------------------------------------------

    def extract(self, data: Any, path: str) -> Any:
        """Walk *data* along *path*, returning the resolved value or None.

        Parameters
        ----------
        data : Any
            The root dict or list to traverse.
        path : str
            Dot-and-bracket path expression (e.g. ``"data.results[2].id"``).

        Returns
        -------
        Any or ``None``
            The value at *path*, or ``None`` when the path does not exist.
            No exception is ever raised by this method.
        """
        tokens = _tokenise(path)
        if not tokens:
            return None

        current: Any = data
        for token in tokens:
            current = self._step(current, token)
            if current is None:
                return None
        return current

    # ------------------------------------------------------------------
    # Single-step resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _step(current: Any, token: str) -> Any:
        """Resolve a single key or index token against *current*."""
        index = _resolve_index(token)

        # --- list path ---
        if index is not None:
            if not isinstance(current, list):
                return None
            try:
                return current[index]
            except (IndexError, TypeError):
                return None

        # --- dict path ---
        if isinstance(current, dict):
            return current.get(token)

        # --- fallback for objects that support getattr ---
        if hasattr(current, token):
            return getattr(current, token)

        return None

    # ------------------------------------------------------------------
    # to_number()
    # ------------------------------------------------------------------

    @staticmethod
    def to_number(value: Any) -> float | None:
        """Coerce *value* to a float, returning ``None`` for non-numeric input.

        Handles ``int``, ``float``, ``Decimal``, and numeric strings.
        ``bool`` is intentionally treated as non-numeric — ``True``/``False``
        are usually categorical, not quantitative.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)

        # numeric string (supports hex / scientific notation)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return float(stripped)
            except (ValueError, OverflowError):
                return None

        # Decimal and other numeric types
        if hasattr(value, "__float__"):
            try:
                return float(value)
            except (ValueError, TypeError, OverflowError):
                return None

        return None


# ---------------------------------------------------------------------------
# Quick inline helpers (module-level, for users who prefer function calls)
# ---------------------------------------------------------------------------

_ENGINE = StateProjectionEngine()


def extract(data: Any, path: str) -> Any:
    """Module-level convenience wrapper around ``StateProjectionEngine.extract``."""
    return _ENGINE.extract(data, path)


def to_number(value: Any) -> float | None:
    """Module-level convenience wrapper around ``StateProjectionEngine.to_number``."""
    return _ENGINE.to_number(value)


# ---------------------------------------------------------------------------
# Self-test (run ``python state_projection_engine.py`` to verify)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    engine = StateProjectionEngine()

    payload = {
        "data": {
            "status": "ok",
            "items": [
                {"name": "alpha", "quantity": 10},
                {"name": "beta", "quantity": 5},
            ],
            "meta": {"version": 2, "nested": {"deep": 99}},
        },
        "empty_list": [],
    }

    # ---- extract() tests ----
    assert engine.extract(payload, "data.status") == "ok"
    assert engine.extract(payload, "$.data.status") == "ok"
    assert engine.extract(payload, "data.items[0].name") == "alpha"
    assert engine.extract(payload, "data.items[1].quantity") == 5
    assert engine.extract(payload, "data.meta.nested.deep") == 99
    assert engine.extract(payload, "data.meta.version") == 2
    assert engine.extract(payload, "data.items[0].name") == "alpha"
    assert engine.extract(payload, "data[0]") is None  # data is dict, not list
    assert engine.extract(payload, "nonexistent.path") is None
    assert engine.extract(payload, "data.items[99]") is None
    assert engine.extract(payload, "empty_list[0]") is None
    assert engine.extract(payload, "") is None

    # ---- to_number() tests ----
    assert engine.to_number(42) == 42.0
    assert engine.to_number(3.14) == 3.14
    assert engine.to_number("  123  ") == 123.0
    assert engine.to_number("-5.5") == -5.5
    assert engine.to_number("1e3") == 1000.0
    assert engine.to_number(None) is None
    assert engine.to_number(True) is None
    assert engine.to_number(False) is None
    assert engine.to_number("hello") is None
    assert engine.to_number("") is None
    assert engine.to_number({}) is None
    assert engine.to_number([]) is None

    # ---- module-level helpers ----
    assert extract(payload, "data.status") == "ok"
    assert to_number("42") == 42.0

    print("All self-tests passed.")
