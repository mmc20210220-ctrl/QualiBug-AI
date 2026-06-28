"""
Phase79: Reasoner Engine Stability Refactor — stage_reason_all v2

Drop-in replacement for discovery_engine.py AutonomousDiscoveryEngine.stage_reason_all().
11 engines, max 4 parallel workers, independent ReasoningClient per engine,
2 attempts max, JSON truncation recovery via raw_decode.
"""

import ast, copy, json, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Any

from .console_output import safe_print as print

# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

OUTPUT_HARD_LIMITS = (
    "\nOUTPUT HARD LIMITS:\n"
    "- Return at most 15 hypotheses.\n"
    "- Each hypothesis must be concise and no longer than 500 characters.\n"
    "- Return JSON only.\n"
    "- Do not include analysis, markdown, commentary, or code fences.\n"
    "- If evidence is insufficient, return fewer hypotheses rather than verbose explanations.\n"
)

MAX_HYPOTHESES = 15
MAX_HYPOTHESIS_CHARS = 500
MAX_REASONER_WORKERS = 4
MIN_REASONER_TIMEOUT_SECONDS = 300

SIDE_PATH_REASONER_ENGINES = (
    ("business_outcome", "outcome"),
    ("business_reconciliation", "reconciliation"),
    ("business_invariant", "invariant"),
    ("multi_source_reasoning", "consistency"),
    ("business_lifecycle", "temporal"),
    ("consistency_isolation", "consistency"),
)

# Errors that warrant one retry
RETRYABLE_ERRORS = (
    "empty", "ValueError", "JSONDecodeError", "Unterminated string",
    "Expecting delimiter", "timeout", "Remote end closed",
    "URLError", "Connection", "connection", "502", "503", "504",
    "Expecting value", "not_list_or_dict", "failed", "corrupted",
    "reset", "refused", "timed out", "Read timed out",
)


def _effective_timeout_seconds(*values: Any) -> int:
    """Preserve configured timeouts while enforcing the DeepSeek safety floor."""
    parsed = [MIN_REASONER_TIMEOUT_SECONDS]
    for value in values:
        try:
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    return max(parsed)


def _effective_max_workers(value: Any, engine_count: int) -> int:
    """Keep reasoner concurrency between one and the hard rate-limit cap."""
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = MAX_REASONER_WORKERS
    return max(1, min(requested, MAX_REASONER_WORKERS, max(1, int(engine_count))))


# ═══════════════════════════════════════════════════════════════════
# JSON Truncation Recovery
# ═══════════════════════════════════════════════════════════════════

def _salvage_truncated_json(text: str) -> list[dict] | None:
    """Attempt to recover complete hypothesis dicts from truncated JSON content.

    Uses json.JSONDecoder().raw_decode() to parse complete objects,
    stopping before the first incomplete one.

    Returns a list of successfully parsed dicts, or None if none could be saved.
    """
    text = text.strip()
    if not text:
        return None

    # Remove code fences and leading label
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    if text.startswith("```json"):
        text = text[5:].strip()

    saved: list[dict] = []

    # Try to fix common truncation patterns
    # If it looks like an array: [{...}, {...}, {incomplete...
    if text.startswith("["):
        # Count opening/closing brackets and braces
        depth = 0
        obj_start = -1
        brace_depth = 0
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "{" and depth <= 1 and brace_depth == 0:
                obj_start = i
                brace_depth = 1
            elif ch == "{" and brace_depth > 0:
                brace_depth += 1
            elif ch == "}" and brace_depth > 0:
                brace_depth -= 1
                if brace_depth == 0 and obj_start >= 0:
                    # Try to parse this complete object
                    try:
                        obj = json.loads(text[obj_start:i+1])
                        if isinstance(obj, dict):
                            saved.append(obj)
                    except json.JSONDecodeError:
                        # Not actually complete — stop
                        break
                    obj_start = -1
            elif ch == "\\":
                i += 1  # Skip escaped char
            i += 1

    # If it looks like an object with hypotheses key
    elif text.startswith("{"):
        # Try to find the hypotheses array
        match = re.search(r'"hypotheses"\s*:\s*\[', text)
        if match:
            arr_start = match.end()  # After [
            # Now try the brace counting approach inside the array
            brace_depth = 0
            obj_start = -1
            i = arr_start
            while i < len(text):
                ch = text[i]
                if ch == "{" and brace_depth == 0:
                    obj_start = i
                    brace_depth = 1
                elif ch == "{" and brace_depth > 0:
                    brace_depth += 1
                elif ch == "}" and brace_depth > 0:
                    brace_depth -= 1
                    if brace_depth == 0 and obj_start >= 0:
                        try:
                            obj = json.loads(text[obj_start:i+1])
                            if isinstance(obj, dict):
                                saved.append(obj)
                        except json.JSONDecodeError:
                            break
                        obj_start = -1
                elif ch == "\\":
                    i += 1
                i += 1

    return saved if saved else None


def _parse_engine_content(raw: str) -> tuple[list[dict] | None, str, str]:
    """Parse DeepSeek API response → list of hypothesis dicts.

    Returns (hypotheses, status, degradation_reason).
    status: "success" | "degraded" | "failed"
    """
    if not raw or len(str(raw).strip()) < 20:
        return None, "failed", "empty_raw_response"

    try:
        outer = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None, "failed", "outer_json_corrupted"

    choices = outer.get("choices", [{}])
    if not choices:
        return None, "failed", "empty_choices"

    content = choices[0].get("message", {}).get("content", "")
    if not content or len(content.strip()) < 10:
        return None, "failed", "empty_content"

    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()

    # Try normal parse first
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        try:
            parsed = ast.literal_eval(cleaned)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            status_from_literal = "degraded"
            degradation_from_literal = "python_literal_json_salvaged"
        else:
            status_from_literal = ""
            degradation_from_literal = ""
        # Attempt truncation recovery
        if parsed is None and any(kw in str(e) for kw in (
            "Unterminated string", "Expecting delimiter", "Expecting value",
            "Expecting property name enclosed in double quotes",
            "Expecting ',' delimiter", "Extra data",
        )):
            salvaged = _salvage_truncated_json(cleaned)
            if salvaged and len(salvaged) >= 1:
                # Apply hard limits
                salvaged = salvaged[:MAX_HYPOTHESES]
                for h in salvaged:
                    for k in h:
                        if isinstance(h[k], str) and len(h[k]) > MAX_HYPOTHESIS_CHARS:
                            h[k] = h[k][:MAX_HYPOTHESIS_CHARS - 3] + "..."
                return salvaged[:MAX_HYPOTHESES], "degraded", "truncated_json_salvaged"
        if parsed is None:
            return None, "failed", f"parse_error: {str(e)[:100]}"
    else:
        status_from_literal = ""
        degradation_from_literal = ""

    # Normalize: extract hypotheses from various root shapes
    if isinstance(parsed, list):
        hypotheses = parsed
    elif isinstance(parsed, dict):
        hypotheses = parsed.get("hypotheses") or parsed.get("findings") or [parsed]
    else:
        return None, "failed", "content_not_list_or_dict"

    if not isinstance(hypotheses, list):
        return None, "failed", f"hypotheses_not_list: {type(hypotheses).__name__}"

    # Apply hard limits
    hypotheses = hypotheses[:MAX_HYPOTHESES]
    for h in hypotheses:
        if isinstance(h, dict):
            for k in h:
                if isinstance(h[k], str) and len(h[k]) > MAX_HYPOTHESIS_CHARS:
                    h[k] = h[k][:MAX_HYPOTHESIS_CHARS - 3] + "..."

    if not hypotheses:
        return None, "failed", "empty_hypotheses"

    return hypotheses, status_from_literal or "success", degradation_from_literal


# ═══════════════════════════════════════════════════════════════════
# Engine Worker
# ═══════════════════════════════════════════════════════════════════

def _run_reasoner_engine(
    engine_name: str,
    template: str,
    prompt: str,
    system_prompt: str,
    client_config: Any,  # Deep copy of original client config
    *,
    retry_count: int = 1,
    retry_delay_seconds: float = 2.0,
    max_hypotheses: int = MAX_HYPOTHESES,
    max_hypothesis_chars: int = MAX_HYPOTHESIS_CHARS,
) -> dict:
    """Run a single Reasoner engine with an isolated bounded retry budget.

    The worker receives policy values as immutable arguments.  It never mutates
    the caller's client/config, and policy retries are intentionally capped at
    one retry to prevent an API outage from turning into an unbounded loop.
    """

    result = {
        "engine_name": engine_name,
        "hypotheses": [],
        "status": "failed",
        "attempts": 0,
        "retry_used": False,
        "raw_chars": 0,
        "content_chars": 0,
        "duration_seconds": 0.0,
        "error": "",
        "degradation_reason": "",
    }

    t0 = time.time()

    attempts_allowed = 1 + max(0, min(int(retry_count or 0), 1))
    for attempt in range(attempts_allowed):
        result["attempts"] = attempt + 1
        if attempt > 0:
            result["retry_used"] = True
            jitter = time.time() % 1.0
            time.sleep(max(0.0, float(retry_delay_seconds or 0.0)) + jitter)

        try:
            # Build a fresh independent client for each attempt
            from ai_test_asset_center.llm_reasoning import ReasoningClient
            worker_config = copy.deepcopy(client_config)
            worker_config.timeout_seconds = _effective_timeout_seconds(
                getattr(worker_config, "timeout_seconds", MIN_REASONER_TIMEOUT_SECONDS)
            )
            if (
                hasattr(worker_config, "response_format")
                and not getattr(worker_config, "response_format", "")
                and str(os.environ.get("QUALIBUG_DISABLE_REASONER_JSON_MODE", "")).lower()
                not in {"1", "true", "yes", "on"}
            ):
                worker_config.response_format = "json_object"
            worker_client = ReasoningClient(config=worker_config)

            raw = worker_client._chat(prompt, system_prompt=system_prompt)
            result["raw_chars"] = len(str(raw)) if raw else 0
            result["duration_seconds"] = time.time() - t0

            # Parse
            hypotheses, status, degradation = _parse_engine_content(str(raw) if raw else "")

            if hypotheses is not None:
                normalized = []
                for hypothesis in hypotheses[:max(1, min(int(max_hypotheses or 1), MAX_HYPOTHESES))]:
                    if not isinstance(hypothesis, dict):
                        continue
                    item = dict(hypothesis)
                    for key, value in list(item.items()):
                        if isinstance(value, str) and len(value) > max_hypothesis_chars:
                            item[key] = value[:max_hypothesis_chars - 3] + "..."
                    item.setdefault("_reasoner_engine", engine_name)
                    normalized.append(item)
                result["hypotheses"] = normalized
                result["status"] = status
                result["degradation_reason"] = degradation
                result["content_chars"] = sum(
                    len(str(h)) for h in hypotheses
                ) if hypotheses else 0
                return result

            # No hypotheses parsed — check if retryable
            error_hint = degradation or status
            if any(kw in error_hint for kw in RETRYABLE_ERRORS) and attempt == 0:
                result["error"] = error_hint[:200]
                continue
            else:
                result["error"] = error_hint[:200]
                return result

        except Exception as e:
            error_str = str(e)[:200]
            result["error"] = error_str
            result["duration_seconds"] = time.time() - t0

            if any(kw in error_str for kw in RETRYABLE_ERRORS) and attempt == 0:
                continue
            return result

    return result


# ═══════════════════════════════════════════════════════════════════
# Main: stage_reason_all v2
# ═══════════════════════════════════════════════════════════════════

def _stage_reason_all_v2(self, prd_text: str, api_spec: str,
                         reader_output: dict, prior_findings=None) -> list[dict]:
    """11 engines, max 4 parallel workers, independent clients, 2 attempts max."""

    from .reasoner_prompt import REASONER_PROMPTS, REASONER_SYSTEM_PROMPT
    from ai_test_asset_center.llm_reasoning import ReasoningClient

    all_hypotheses: list[dict] = []

    # ── Engines ──
    engines = [
        ("causality", REASONER_PROMPTS["causality"]),
        ("invariant", REASONER_PROMPTS["invariant"]),
        ("reconciliation", REASONER_PROMPTS["reconciliation"]),
        ("counterexample", REASONER_PROMPTS["counterexample"]),
        ("consistency", REASONER_PROMPTS["consistency"]),
        ("population", REASONER_PROMPTS["population"]),
        ("outcome", REASONER_PROMPTS["outcome"]),
        ("temporal", REASONER_PROMPTS["temporal"]),
        ("saga", REASONER_PROMPTS.get("saga", REASONER_PROMPTS["causality"])),
        ("event_chain", REASONER_PROMPTS.get("event_chain", REASONER_PROMPTS["causality"])),
        ("metamorphic", REASONER_PROMPTS.get("metamorphic", REASONER_PROMPTS["consistency"])),
        *[
            (name, REASONER_PROMPTS.get(prompt_key, REASONER_PROMPTS["consistency"]))
            for name, prompt_key in SIDE_PATH_REASONER_ENGINES
        ],
    ]

    # ── Concurrency (Policy Registry + env var fallback) ──
    from .policy_wiring import get_policy_value
    raw_max = os.environ.get("QUALIBUG_REASONER_MAX_WORKERS",
                             str(get_policy_value("reasoner", "max_workers", 4)))
    max_hypotheses_per_engine = get_policy_value("reasoner", "max_hypotheses_per_engine", 15)
    max_hypothesis_chars = get_policy_value("reasoner", "max_hypothesis_chars", 500)
    retry_count = get_policy_value("reasoner", "retry_count", 1)
    timeout_seconds = get_policy_value(
        "reasoner", "timeout_seconds", MIN_REASONER_TIMEOUT_SECONDS
    )
    retry_delay = get_policy_value("reasoner", "retry_delay_seconds", 2.0)
    truncation_map = get_policy_value("reasoner", "prompt_truncation_chars",
        {"prd_text": 2000, "api_schema": 3000, "observed_data": 2000,
         "heuristic_findings": 2000, "reader_json": 3000, "lifecycle_definition": 2000})
    enabled_engines = get_policy_value("reasoner", "enabled_engines", [name for name, _ in engines])
    engine_weights = get_policy_value("reasoner", "engine_weights", {})
    enabled_set = {str(name) for name in (enabled_engines or [])}
    if enabled_set:
        engines = [(name, template) for name, template in engines if name in enabled_set]
    # A malformed or overly restrictive policy may never silently disable the
    # entire discovery surface.  Fall back to the canonical set and emit health.
    if not engines:
        engines = [
            ("causality", REASONER_PROMPTS["causality"]),
            ("invariant", REASONER_PROMPTS["invariant"]),
            ("reconciliation", REASONER_PROMPTS["reconciliation"]),
            ("counterexample", REASONER_PROMPTS["counterexample"]),
            ("consistency", REASONER_PROMPTS["consistency"]),
            ("population", REASONER_PROMPTS["population"]),
            ("outcome", REASONER_PROMPTS["outcome"]),
            ("temporal", REASONER_PROMPTS["temporal"]),
            ("saga", REASONER_PROMPTS.get("saga", REASONER_PROMPTS["causality"])),
            ("event_chain", REASONER_PROMPTS.get("event_chain", REASONER_PROMPTS["causality"])),
            ("metamorphic", REASONER_PROMPTS.get("metamorphic", REASONER_PROMPTS["consistency"])),
            *[
                (name, REASONER_PROMPTS.get(prompt_key, REASONER_PROMPTS["consistency"]))
                for name, prompt_key in SIDE_PATH_REASONER_ENGINES
            ],
        ]

    if str(os.environ.get("QUALIBUG_LOCAL_BOOTSTRAP_ONLY", "")).lower() in {"1", "true", "yes", "on"}:
        try:
            from .local_reasoner_bootstrap import build_local_bootstrap_hypotheses
            local_hypotheses = build_local_bootstrap_hypotheses(
                prd_text=prd_text,
                api_spec=api_spec,
                reader_output=reader_output if isinstance(reader_output, dict) else {},
                prior_findings=prior_findings or [],
                max_hypotheses=max_hypotheses_per_engine,
            )
        except Exception as exc:
            local_hypotheses = []
            bootstrap_error = str(exc)[:200]
        else:
            bootstrap_error = ""
        self._last_engine_report = {
            "total_engines": 1,
            "successful_engines": [],
            "degraded_engines": ["local_bootstrap"] if (local_hypotheses or prior_findings) else [],
            "failed_engines": [] if (local_hypotheses or prior_findings) else ["local_bootstrap"],
            "local_bootstrap_engaged": bool(local_hypotheses),
            "local_bootstrap_only": True,
            "local_bootstrap_exhausted_by_prior_findings": bool((prior_findings or []) and not local_hypotheses),
            "local_bootstrap_hypotheses": len(local_hypotheses),
            "graph_context_mode": "local_bootstrap_only",
            "graph_context_ready": False,
            "graph_context_active": False,
            "graph_context_chars": 0,
            "retried_engines": [],
            "engines_with_low_output": [],
            "engine_outputs": {"local_bootstrap": len(local_hypotheses)},
            "engine_attempts": {"local_bootstrap": 1},
            "engine_durations_seconds": {"local_bootstrap": 0.0},
            "engine_errors": {"local_bootstrap": bootstrap_error} if bootstrap_error else {},
            "total_hypotheses": len(local_hypotheses),
            "max_workers": 0,
            "enabled_engines": ["local_bootstrap"],
            "engine_weights": {},
            "retry_count": 0,
            "timeout_seconds": 0,
            "max_hypotheses_per_engine": max_hypotheses_per_engine,
            "max_hypothesis_chars": max_hypothesis_chars,
        }
        print(f"    [WARN] [local_bootstrap_only] {len(local_hypotheses)} read-only hypotheses", flush=True)
        return local_hypotheses

    max_workers = _effective_max_workers(raw_max, len(engines))
    def _limit(name: str, fallback: int) -> int:
        try:
            return max(64, int(truncation_map.get(name, fallback)))
        except Exception:
            return fallback

    # ── Context truncation / Phase91 Graph Context ──
    # The GraphContextComposer is created in the Discovery mainline.  In
    # ``shadow`` it is measured but Phase90 prompts remain unchanged; in
    # ``active`` it replaces raw PRD/API slices with a bounded, traceable local
    # evidence pack.  Markdown is never read here.
    graph_pack = reader_output.get("_graph_evidence_pack") if isinstance(reader_output, dict) else None
    graph_mode = str((graph_pack or {}).get("graph_mode") or reader_output.get("_graph_mode") if isinstance(reader_output, dict) else "off").lower()
    graph_ready = bool((graph_pack or {}).get("graph_ready"))
    use_graph_context = graph_ready and graph_mode == "active"
    graph_rendered = str((graph_pack or {}).get("rendered_context") or "")[:4500]

    reader_json = json.dumps(reader_output, ensure_ascii=False, default=str)[:3000]
    heuristic_json = "[]"
    observed_json = reader_json
    if prior_findings:
        heuristic_json = json.dumps({
            "previously_confirmed_bugs": [
                {"title": f.get("title", ""), "verdict": f.get("verdict", ""),
                 "severity": f.get("severity", "P1")}
                for f in prior_findings[:15]
            ],
            "instruction": "DO NOT repeat the above confirmed bugs. Build on them to find DEEPER or RELATED bugs."
        }, ensure_ascii=False, default=str)[:3000]
        observed_json = json.dumps({
            "business_world": reader_output,
            "prior_discoveries": [
                {"title": f.get("title", ""), "severity": f.get("severity", "P1")}
                for f in prior_findings[:10]
            ]
        }, ensure_ascii=False, default=str)[:4000]

    # Extract real API paths for the Phase90 fallback only.  Active graph mode
    # avoids sending the full PRD/API documents to every Reasoner invocation.
    path_matches = re.findall(r'"(/api/[\w\-\/{}]+)"', api_spec + prd_text)
    api_with_paths = api_spec
    if path_matches:
        paths_hint = "EXACT API PATHS AVAILABLE:\n" + "\n".join(sorted(set(path_matches))[:50])
        api_with_paths = paths_hint + "\n\n" + api_spec
    prompt_prd = graph_rendered if use_graph_context else prd_text
    prompt_api = graph_rendered if use_graph_context else api_with_paths
    if use_graph_context:
        reader_json = json.dumps({
            "graph_evidence_pack": {key: value for key, value in (graph_pack or {}).items() if key != "rendered_context"},
            "risk_frontier": reader_output.get("_risk_frontier", {}),
        }, ensure_ascii=False, default=str)[:3500]
        observed_json = graph_rendered

    # ── Build prompts ──
    engine_prompts: dict[str, str] = {}
    for engine_name, template in engines:
        prompt = self._fill_template(template,
            prd_text=prompt_prd[:_limit("prd_text", 2000)], api_schema=prompt_api[:_limit("api_schema", 3000)],
            observed_data=observed_json[:_limit("observed_data", 2000)], heuristic_findings=heuristic_json[:_limit("heuristic_findings", 2000)],
            primary_view="{}", secondary_view="{}", schema_context=prompt_api[:_limit("api_schema", 1000)],
            resource_a="{}", resource_b="{}", relationship_context="",
            lifecycle_definition=reader_json[:_limit("lifecycle_definition", 2000)], business_context=prompt_prd[:_limit("prd_text", 1000)],
            tenant_context=reader_json[:_limit("reader_json", 1000)], model_comparison="{}",
            constraints=reader_json[:_limit("reader_json", 1000)], business_process=reader_json[:_limit("reader_json", 1000)],
            expected_outcomes="{}", observed_results="{}",
            snapshot_t1="{}", snapshot_t2="{}",
        )
        # Not every legacy reasoner template references every placeholder.
        # Append the same bounded evidence pack explicitly in active mode so all
        # 11 engines operate on the selected local graph neighborhood.
        if use_graph_context:
            prompt += "\n\n[PHASE91 GRAPH EVIDENCE PACK]\n" + graph_rendered
        engine_prompts[engine_name] = prompt + OUTPUT_HARD_LIMITS

    # ── Parallel execution ──
    # Deep copy client config for each worker (prevents thread contamination)
    original_config = copy.deepcopy(self.client.config)
    original_config.timeout_seconds = _effective_timeout_seconds(
        getattr(original_config, "timeout_seconds", MIN_REASONER_TIMEOUT_SECONDS),
        timeout_seconds,
    )

    results_by_engine: dict[str, dict] = {}

    print(f"\n[Stage 2] Reasoner — {len(engines)} engines, {max_workers} parallel workers...", flush=True)

    # Schedule high-weight lenses first while aggregating in canonical order.
    scheduled_engines = sorted(
        engines,
        key=lambda item: (-float((engine_weights or {}).get(item[0], 1.0) or 0.0), item[0]),
    )
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map: dict[Future, str] = {
            executor.submit(
                _run_reasoner_engine,
                engine_name, template, engine_prompts[engine_name],
                REASONER_SYSTEM_PROMPT, original_config,
                retry_count=retry_count,
                retry_delay_seconds=retry_delay,
                max_hypotheses=max_hypotheses_per_engine,
                max_hypothesis_chars=max_hypothesis_chars,
            ): engine_name
            for engine_name, template in scheduled_engines
        }

        for future in as_completed(future_map):
            engine_name = future_map[future]
            try:
                results_by_engine[engine_name] = future.result()
            except Exception as exc:
                results_by_engine[engine_name] = {
                    "engine_name": engine_name, "hypotheses": [],
                    "status": "failed", "attempts": 0, "retry_used": False,
                    "raw_chars": 0, "content_chars": 0, "duration_seconds": 0.0,
                    "error": str(exc)[:200], "degradation_reason": "",
                }

    # ── Aggregate in original engine order ──
    for engine_name, _ in engines:
        r = results_by_engine.get(engine_name, {})
        hyps = r.get("hypotheses", [])
        if hyps:
            all_hypotheses.extend(hyps)
        status_label = "[OK]" if r.get("status") == "success" else ("[WARN]" if r.get("status") == "degraded" else "[FAIL]")
        print(f"    {status_label} [{engine_name}] {len(hyps)} hypotheses ({r.get('status', '?')})", flush=True)

    # ── Local bootstrap fallback ──
    # If every live LLM lens fails or the model is not configured, keep the
    # discovery/self-evolution loop alive with a small, read-only, contract-derived
    # hypothesis set.  This never lowers the evidence gate and never uses hidden
    # oracle/known-bug seed files; it only creates executable GET probes so the
    # runtime can collect evidence and diagnose why semantic reasoning is down.
    local_bootstrap_hypotheses: list[dict[str, Any]] = []
    if not all_hypotheses and str(os.environ.get("QUALIBUG_DISABLE_LOCAL_BOOTSTRAP", "")).lower() not in {"1", "true", "yes", "on"}:
        try:
            from .local_reasoner_bootstrap import build_local_bootstrap_hypotheses
            local_bootstrap_hypotheses = build_local_bootstrap_hypotheses(
                prd_text=prd_text,
                api_spec=api_spec,
                reader_output=reader_output if isinstance(reader_output, dict) else {},
                prior_findings=prior_findings or [],
                max_hypotheses=max_hypotheses_per_engine,
            )
        except Exception as exc:
            local_bootstrap_hypotheses = []
            results_by_engine["local_bootstrap"] = {
                "engine_name": "local_bootstrap", "hypotheses": [], "status": "failed",
                "attempts": 1, "retry_used": False, "raw_chars": 0, "content_chars": 0,
                "duration_seconds": 0.0, "error": str(exc)[:200], "degradation_reason": "bootstrap_failed",
            }
        if local_bootstrap_hypotheses:
            all_hypotheses.extend(local_bootstrap_hypotheses)
            results_by_engine["local_bootstrap"] = {
                "engine_name": "local_bootstrap",
                "hypotheses": local_bootstrap_hypotheses,
                "status": "degraded",
                "attempts": 1,
                "retry_used": False,
                "raw_chars": 0,
                "content_chars": sum(len(str(h)) for h in local_bootstrap_hypotheses),
                "duration_seconds": 0.0,
                "error": "",
                "degradation_reason": "live_reasoners_unavailable_read_only_local_bootstrap",
            }
            print(f"    [WARN] [local_bootstrap] {len(local_bootstrap_hypotheses)} read-only hypotheses (LLM reasoners unavailable)", flush=True)

    engine_names_for_report = [name for name, _ in engines] + (["local_bootstrap"] if "local_bootstrap" in results_by_engine else [])

    # ── Build engine health report ──
    self._last_engine_report = {
        "total_engines": len(engine_names_for_report),
        "successful_engines": [e for e in engine_names_for_report if results_by_engine.get(e, {}).get("status") == "success"],
        "degraded_engines": [e for e in engine_names_for_report if results_by_engine.get(e, {}).get("status") == "degraded"],
        "failed_engines": [e for e in engine_names_for_report if results_by_engine.get(e, {}).get("status") == "failed"],
        "local_bootstrap_engaged": bool(local_bootstrap_hypotheses),
        "local_bootstrap_hypotheses": len(local_bootstrap_hypotheses),
        "graph_context_mode": graph_mode,
        "graph_context_ready": graph_ready,
        "graph_context_active": use_graph_context,
        "graph_context_chars": len(graph_rendered),
        "retried_engines": [e for e in engine_names_for_report if results_by_engine.get(e, {}).get("retry_used")],
        "engines_with_low_output": [e for e in engine_names_for_report if len(results_by_engine.get(e, {}).get("hypotheses", [])) < 3 and e != "local_bootstrap"],
        "engine_outputs": {e: len(results_by_engine.get(e, {}).get("hypotheses", [])) for e in engine_names_for_report},
        "engine_attempts": {e: results_by_engine.get(e, {}).get("attempts", 0) for e in engine_names_for_report},
        "engine_durations_seconds": {e: round(results_by_engine.get(e, {}).get("duration_seconds", 0), 1) for e in engine_names_for_report},
        "engine_errors": {e: results_by_engine.get(e, {}).get("error", "")[:100] for e in engine_names_for_report
                         if results_by_engine.get(e, {}).get("error")},
        "total_hypotheses": len(all_hypotheses),
        "max_workers": max_workers,
        "enabled_engines": engine_names_for_report,
        "engine_weights": {name: float((engine_weights or {}).get(name, 1.0) or 0.0) for name, _ in engines},
        "retry_count": retry_count,
        "timeout_seconds": _effective_timeout_seconds(timeout_seconds),
        "max_hypotheses_per_engine": max_hypotheses_per_engine,
        "max_hypothesis_chars": max_hypothesis_chars,
    }

    print(f"  [OK] 生成了 {len(all_hypotheses)} 条假设 (across {len(engines)} engines)", flush=True)
    return all_hypotheses
