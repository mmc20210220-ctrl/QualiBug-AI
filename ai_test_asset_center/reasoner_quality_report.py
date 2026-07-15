from __future__ import annotations

from typing import Any

EXECUTABLE_VERIFICATION_KEYS = ("path", "step1", "step2", "step3")


def has_executable_verification_method(hypothesis: dict[str, Any]) -> bool:
    """Return true when a hypothesis contains an executable probe binding."""
    if not isinstance(hypothesis, dict):
        return False
    verification_method = hypothesis.get("verification_method", {})
    if not isinstance(verification_method, dict):
        return False
    return any(str(verification_method.get(key, "") or "").strip() for key in EXECUTABLE_VERIFICATION_KEYS)


def build_executable_quality_report(
    results_by_engine: dict[str, dict[str, Any]],
    engine_names: list[str],
    final_hypotheses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize executable hypothesis quality per engine and overall.

    This is intentionally pure and side-effect free so CI can lock the reporting
    contract without calling LLM providers or real target APIs.
    """
    per_engine_executable: dict[str, int] = {}
    per_engine_non_executable: dict[str, int] = {}
    per_engine_ratio: dict[str, float] = {}
    engines_with_no_executable_output: list[str] = []

    for engine_name in engine_names:
        hypotheses = results_by_engine.get(engine_name, {}).get("hypotheses", [])
        if not isinstance(hypotheses, list):
            hypotheses = []
        executable_count = sum(
            1 for hypothesis in hypotheses
            if isinstance(hypothesis, dict) and has_executable_verification_method(hypothesis)
        )
        total_count = len([hypothesis for hypothesis in hypotheses if isinstance(hypothesis, dict)])
        non_executable_count = max(0, total_count - executable_count)
        per_engine_executable[engine_name] = executable_count
        per_engine_non_executable[engine_name] = non_executable_count
        per_engine_ratio[engine_name] = round(executable_count / total_count, 4) if total_count else 0.0
        if total_count and executable_count == 0:
            engines_with_no_executable_output.append(engine_name)

    final_items = final_hypotheses if isinstance(final_hypotheses, list) else []
    final_executable = sum(
        1 for hypothesis in final_items
        if isinstance(hypothesis, dict) and has_executable_verification_method(hypothesis)
    )
    final_total = len([hypothesis for hypothesis in final_items if isinstance(hypothesis, dict)])

    return {
        "executable_hypotheses": final_executable,
        "non_executable_hypotheses": max(0, final_total - final_executable),
        "executable_hypothesis_ratio": round(final_executable / final_total, 4) if final_total else 0.0,
        "per_engine_executable_hypotheses": per_engine_executable,
        "per_engine_non_executable_hypotheses": per_engine_non_executable,
        "per_engine_executable_ratio": per_engine_ratio,
        "engines_with_no_executable_output": engines_with_no_executable_output,
    }
