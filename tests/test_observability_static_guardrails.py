"""Static guardrails for exception observability.

An ``except`` handler that swallows an exception without logging or re-raising
destroys the only evidence that the failure ever happened.  In this codebase
that failure mode is not theoretical: 2026-08-31 measured **1284 of 2784**
``except`` handlers (46%) discarding the exception silently, which is the direct
cause of the recurring "green tests, broken run" and "cannot localise the root
cause" symptoms the project kept paying for.

The gate is a **ratchet**, not a clean-up order.  A rule of "zero silent
handlers" would open with 1284 failures, nobody could pay that debt in one
sitting, and the gate would be bypassed or deleted within a week — an unenforced
guardrail is worse than none because it keeps lying about coverage.  So:

* the current count is frozen as ``MAX_SILENT_EXCEPTION_HANDLERS``;
* **adding** a silent handler fails the build immediately (new debt is blocked);
* **removing** one passes, but warns that the baseline constant must be lowered
  so the gain cannot silently unwind later.

Detection is AST-based rather than textual, matching
``test_reasoner_static_guardrails``: substring rules pin formatting instead of
meaning and break the moment the code is reflowed.
"""

from __future__ import annotations

import ast
import warnings
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_PACKAGE = REPO_ROOT / "ai_test_asset_center"

# Ratchet baseline, measured 2026-08-31 against 1238 product files.
# Lower this constant whenever a silent handler is fixed.  Never raise it.
# 1284 -> 1266: discovery_engine/_engine.py cleared — all 18 handlers there now
# log. Expected-by-design failures (non-numeric business fields, non-JSON error
# bodies, the truncated-parse fallback) use logger.debug so they cannot drown
# real signals; actual degradations (cache store failure, a rule failing to
# evaluate, salvage exhausted) use logger.warning.
# 1266 -> 1083: two separate moves, keep them straight when reading the number.
#   (a) 1266 -> 1092 — the detector stopped over-reporting. Handlers that read
#       the caught exception into their return value (e.g. `return {"ok": False,
#       "error": str(exc)}`) are not silent; the caller receives structured
#       evidence it can act on. 174 handlers were false positives. An inflated
#       baseline is its own lie about coverage.
#   (b) 1092 -> 1083 — oracle_engine.py cleared (9 handlers). The one that
#       mattered: `_contract_activation_for_business_oracle` returned False on
#       failure, silently switching the business oracle off with no findings and
#       a clean-looking run (invisible breadth loss, AGENTS.md principle 14).
#   (c) 1083 -> 1065 — business_assurance_coverage.py cleared (18 handlers).
#       `_load_generators` had 17 copy-pasted try/except blocks, each silently
#       skipping a probe generator: a single import defect was indistinguishable
#       from "engine not packaged", so coverage shrank while the report still
#       read as complete. Rewritten table-driven with a per-skip warning and an
#       error when zero generators load.
MAX_SILENT_EXCEPTION_HANDLERS = 1065

# A handler is credited as "observable" if its body calls something whose name
# contains one of these fragments, or re-raises.  Matching on the callee name
# keeps the rule independent of how the logger object is obtained.
SIGNAL_FRAGMENTS = (
    "log", "warn", "error", "exception", "critical", "debug", "info",
    "trace", "print", "record", "emit", "report", "notify", "raise",
)

# Statements that carry no diagnostic signal on their own.
FLOW_ONLY = (ast.Pass, ast.Return, ast.Continue, ast.Break)


def _product_files() -> list[Path]:
    return sorted(
        path
        for path in PRODUCT_PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _has_signal(statements: list[ast.stmt]) -> bool:
    """True if the handler body logs, reports, or re-raises."""

    wrapper = ast.Module(body=list(statements), type_ignores=[])
    for node in ast.walk(wrapper):
        if isinstance(node, ast.Call):
            name = _called_name(node).lower()
            if any(fragment in name for fragment in SIGNAL_FRAGMENTS):
                return True
    return False


def _uses_exception(handler: ast.ExceptHandler) -> bool:
    """True when the handler body actually reads the caught exception object.

    ``except Exception as exc: return {"ok": False, "error": str(exc)}`` is not
    a silent swallow: the failure is handed back to the caller as structured
    data the caller can act on, which is strictly better than a log line.
    Only a handler that binds the exception and then never reads it is really
    destroying the evidence.

    Without this rule the gate over-reports: ``runtime_connectivity_auth_preflight``
    was flagged for all 11 handlers while every one of them returned
    ``{"ok": False, "error": f"{type(exc).__name__}: {exc}"}``.  An inflated
    baseline is its own kind of lie — it claims coverage of debt that does not
    exist and hides the handlers that genuinely swallow.
    """

    name = handler.name
    if not name:
        return False
    wrapper = ast.Module(body=list(handler.body), type_ignores=[])
    return any(
        isinstance(node, ast.Name) and node.id == name for node in ast.walk(wrapper)
    )


def _classify(handler: ast.ExceptHandler) -> str | None:
    """Return the swallow style, or None when the handler is observable."""

    body = [node for node in handler.body if not isinstance(node, ast.Pass)]
    if not body:
        return "bare pass"
    if len(body) == 1 and isinstance(body[0], ast.Return):
        value = body[0].value
        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            return "bare return"
    if _uses_exception(handler):
        return None
    if _has_signal(handler.body):
        return None
    if all(isinstance(node, FLOW_ONLY) for node in body):
        return "control-flow only"
    return None


@lru_cache(maxsize=1)
def _collect_silent_handlers() -> tuple[tuple[tuple[str, int, str], ...], tuple[str, ...]]:
    """Scan the product package once per process; both gates share the result."""

    silent: list[tuple[str, int, str]] = []
    unparsable: list[str] = []

    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            # Never skip quietly: an unparsable product file is itself a defect
            # and would silently shrink the measured surface.
            unparsable.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            style = _classify(node)
            if style:
                relative = path.relative_to(REPO_ROOT).as_posix()
                silent.append((relative, node.lineno, style))

    # Tuples, not lists: the result is cached and must not be mutated by a caller.
    return tuple(silent), tuple(unparsable)


def _summarise(silent: tuple[tuple[str, int, str], ...], limit: int = 15) -> str:
    per_file: dict[str, int] = {}
    for path, _, _ in silent:
        per_file[path] = per_file.get(path, 0) + 1
    ranked = sorted(per_file.items(), key=lambda item: -item[1])[:limit]
    return "\n".join(f"  {count:4d}  {path}" for path, count in ranked)


def test_product_files_all_parse() -> None:
    """Every product file must be parsable for the measure below to mean anything."""

    _, unparsable = _collect_silent_handlers()
    assert not unparsable, "unparsable product files:\n" + "\n".join(unparsable)


def test_silent_exception_handlers_do_not_grow() -> None:
    """The ratchet: new silent swallows fail; fixed ones must tighten the baseline.

    To clear a failure, either make the handler observable (log the exception or
    re-raise) or — if the count legitimately dropped — lower
    ``MAX_SILENT_EXCEPTION_HANDLERS`` to the new measured value.  Raising the
    constant re-admits debt that was already paid for.
    """

    silent, _ = _collect_silent_handlers()
    count = len(silent)

    assert count <= MAX_SILENT_EXCEPTION_HANDLERS, (
        f"silent exception handlers rose to {count}, baseline is "
        f"{MAX_SILENT_EXCEPTION_HANDLERS} (+"
        f"{count - MAX_SILENT_EXCEPTION_HANDLERS}).\n"
        "Swallowing an exception without a log destroys the only evidence the "
        "failure occurred; this codebase already pays for 46% of handlers doing "
        "it. Log the exception or re-raise.\n"
        "Offenders by file:\n" + _summarise(silent)
    )

    if count < MAX_SILENT_EXCEPTION_HANDLERS:
        warnings.warn(
            f"silent exception handlers are down to {count} but the baseline "
            f"constant is still {MAX_SILENT_EXCEPTION_HANDLERS}; lower "
            "MAX_SILENT_EXCEPTION_HANDLERS to lock in the gain.",
            stacklevel=2,
        )
