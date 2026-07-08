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
from .reasoner_quality_report import build_executable_quality_report

# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

OUTPUT_HARD_LIMITS = (
    "\nOUTPUT HARD LIMITS:\n"
    "- Return exactly one top-level JSON object with this shape: {\"hypotheses\":[{...}]}.\n"
    "- Return at most 15 hypotheses.\n"
    "- Each hypothesis must be concise and no longer than 500 characters.\n"
    "- Return JSON only.\n"
    "- Use double-quoted JSON strings only; never use Python repr, single quotes, comments, or trailing commas.\n"
    "- Do not include analysis, markdown, commentary, or code fences.\n"
    "- If evidence is insufficient, return fewer hypotheses rather than verbose explanations.\n"
)

MAX_HYPOTHESES = 15
MAX_HYPOTHESIS_CHARS = 500
MAX_REASONER_WORKERS = 4
MIN_REASONER_TIMEOUT_SECONDS = 300
MIN_REASONER_MAX_TOKENS = 32768
MAX_REASONER_MAX_TOKENS = 100000
MAX_HYPOTHESES_HARD_LIMIT = 15
EXECUTABLE_QUALITY_REPORT_FIELDS = (
    "executable_hypotheses",
    "non_executable_hypotheses",
    "executable_hypothesis_ratio",
    "per_engine_executable_hypotheses",
    "per_engine_non_executable_hypotheses",
    "per_engine_executable_ratio",
    "engines_with_no_executable_output",
)

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


def _effective_max_tokens(*values: Any) -> int:
    """Keep reasoner output budget within the supported enterprise range."""
    parsed = [MIN_REASONER_MAX_TOKENS]
    for value in values:
        try:
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    return max(MIN_REASONER_MAX_TOKENS, min(max(parsed), MAX_REASONER_MAX_TOKENS))


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


def _message_content_to_text(content: Any) -> str:
    """Normalize OpenAI-compatible message content variants into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                value = part.get("text") or part.get("content") or part.get("value")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return str(content or "")


def _strip_json_wrappers(text: str) -> tuple[str, str]:
    """Remove common presentation wrappers without changing JSON semantics."""
    cleaned = (text or "").strip().lstrip("\ufeff")
    degradations: list[str] = []
    if cleaned.startswith("```"):
        degradations.append("code_fence_removed")
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    stripped = re.sub(r"^\s*(?:json|JSON|output|Output|result|Result)\s*:\s*", "", cleaned).strip()
    if stripped != cleaned:
        degradations.append("label_prefix_removed")
    return stripped, ",".join(degradations)


def _extract_balanced_json(text: str) -> str | None:
    """Extract the first complete JSON object/array from surrounding text."""
    start = -1
    opener = ""
    for idx, ch in enumerate(text):
        if ch in "{[":
            start = idx
            opener = ch
            break
    if start < 0:
        return None

    closer_for = {"{": "}", "[": "]"}
    stack = [closer_for[opener]]
    in_string = False
    escape = False
    quote = ""
    for idx in range(start + 1, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            continue
        if ch in {'"', "'"}:
            in_string = True
            quote = ch
            continue
        if ch in "{[":
            stack.append(closer_for[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return text[start:idx + 1].strip()
    return None


def _parse_structured_content(cleaned: str) -> tuple[Any | None, str, str]:
    """Parse model content, preferring strict JSON and labeling deviations."""
    try:
        return json.loads(cleaned), "", ""
    except json.JSONDecodeError as first_error:
        balanced = _extract_balanced_json(cleaned)
        if balanced and balanced != cleaned:
            try:
                return json.loads(balanced), "degraded", "json_slice_extracted"
            except json.JSONDecodeError:
                pass
        # SECURITY: ast.literal_eval only evaluates Python literals (str, num,
        # tuple, list, dict, set, bool, None) — it does NOT execute code, so
        # this is safe even with adversarial model output. The isinstance check
        # below further restricts the result to dict/list.
        try:
            parsed = ast.literal_eval(balanced or cleaned)
        except (SyntaxError, ValueError):
            return None, "", f"parse_error: {str(first_error)[:100]}"
        if isinstance(parsed, (dict, list)):
            return parsed, "degraded", "python_literal_json_salvaged"
        return None, "", f"parse_error: {str(first_error)[:100]}"


def _extract_hypothesis_items(parsed: Any) -> tuple[list[Any] | None, str]:
    """Normalize common model root shapes into a hypothesis item list."""
    if isinstance(parsed, list):
        return parsed, "array_root"
    if not isinstance(parsed, dict):
        return None, f"content_not_list_or_dict: {type(parsed).__name__}"

    for key in ("hypotheses", "findings", "risks", "bugs", "issues", "items", "results"):
        value = parsed.get(key)
        if isinstance(value, list):
            return value, "" if key == "hypotheses" else f"alternate_root_key:{key}"

    for key in ("result", "data", "output", "response"):
        value = parsed.get(key)
        if isinstance(value, (dict, list)):
            nested, reason = _extract_hypothesis_items(value)
            if nested is not None:
                return nested, f"nested_root:{key}" + (f",{reason}" if reason else "")

    hypothesis_like_keys = {
        "hypothesis_id", "title", "severity", "expected_behavior",
        "verification_method", "why_this_matters", "symptoms_if_broken",
    }
    if hypothesis_like_keys.intersection(parsed):
        return [parsed], "single_hypothesis_object"
    return None, "missing_hypotheses_array"


def _normalize_hypothesis_items(items: list[Any], *, max_hypotheses: int = MAX_HYPOTHESES) -> list[dict]:
    normalized: list[dict] = []
    for item in items[:max(1, min(int(max_hypotheses or 1), MAX_HYPOTHESES))]:
        if isinstance(item, dict):
            normalized.append(dict(item))
        elif isinstance(item, str) and item.strip():
            normalized.append({"title": item.strip(), "source_format": "string_hypothesis"})
    for hypothesis in normalized:
        for key, value in list(hypothesis.items()):
            if isinstance(value, str) and len(value) > MAX_HYPOTHESIS_CHARS:
                hypothesis[key] = value[:MAX_HYPOTHESIS_CHARS - 3] + "..."
    return normalized


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

    content = _message_content_to_text(choices[0].get("message", {}).get("content", ""))
    if not content or len(content.strip()) < 10:
        return None, "failed", "empty_content"

    cleaned, wrapper_degradation = _strip_json_wrappers(content)
    parsed, parse_status, parse_degradation = _parse_structured_content(cleaned)
    if parsed is None:
        if any(kw in parse_degradation for kw in (
            "Unterminated string", "Expecting delimiter", "Expecting value",
            "Expecting property name enclosed in double quotes",
            "Expecting ',' delimiter", "Extra data",
        )):
            salvaged = _salvage_truncated_json(cleaned)
            if salvaged and len(salvaged) >= 1:
                return _normalize_hypothesis_items(salvaged), "degraded", "truncated_json_salvaged"
        return None, "failed", parse_degradation or "parse_error"

    items, shape_degradation = _extract_hypothesis_items(parsed)
    if items is None:
        return None, "failed", shape_degradation

    item_degradation = "string_hypothesis_items" if any(isinstance(item, str) for item in items) else ""
    hypotheses = _normalize_hypothesis_items(items)

    if not hypotheses:
        return None, "failed", "empty_hypotheses"

    degradation_parts = [
        part for part in (wrapper_degradation, parse_degradation, shape_degradation, item_degradation) if part
    ]
    status = parse_status or ("degraded" if degradation_parts else "success")
    return hypotheses, status, ",".join(degradation_parts)


def _hypothesis_identity(hypothesis: dict[str, Any]) -> tuple[str, str, str, str]:
    """Build a semantic cluster key across LLM and local analyzer hypotheses."""
    title = " ".join(str(hypothesis.get("title", "") or "").lower().split())
    expected = " ".join(str(hypothesis.get("expected_behavior", "") or "").lower().split())
    risk_type = str(hypothesis.get("risk_type") or hypothesis.get("category") or "").lower().strip()
    entity = str(hypothesis.get("entity") or hypothesis.get("source_entity") or "").lower().strip()
    return title[:180], risk_type[:120], expected[:180], entity[:120]


def _extract_entity_from_hypothesis(hypothesis: dict[str, Any]) -> str:
    """Extract an entity hint from title/description for cross-source dedup."""
    title = str(hypothesis.get("title", "") or "")
    vm = hypothesis.get("verification_method", {})
    if isinstance(vm, dict):
        path = str(vm.get("path", "") or vm.get("step1", "") or "")
    else:
        path = ""
    # Try to extract resource name from API path (e.g., /api/v1/orders/{id} -> orders)
    if path:
        parts = [p for p in path.split("/") if p and not p.startswith("{") and p.lower() not in ("api", "v1", "v2", "v3")]
        if parts:
            return parts[0].lower()
    # Fallback: first significant word from title
    words = [w for w in title.split() if len(w) > 2]
    return words[0].lower() if words else ""


def _filter_low_quality_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out hypotheses with no executable content and no evidence."""
    filtered = []
    dropped = 0
    for h in hypotheses:
        if not isinstance(h, dict):
            continue
        vm = h.get("verification_method", {})
        has_vm = isinstance(vm, dict) and any(
            str(vm.get(k, "") or "").strip() for k in ("path", "step1", "step2", "step3")
        )
        evidence = h.get("evidence", "")
        has_evidence = bool(evidence) and (not isinstance(evidence, (dict, list)) or len(str(evidence)) > 10)
        title = str(h.get("title", "") or "").strip()
        has_title = len(title) > 3

        if has_title and (has_vm or has_evidence):
            filtered.append(h)
        elif has_title and not str(h.get("description", "") or "").strip():
            h.setdefault("execution_block", "missing_executable_binding")
            h.setdefault("confirmation_status", "needs_executable_evidence")
            filtered.append(h)
        else:
            dropped += 1
    if dropped:
        print(f"  [OK] 质量门控过滤了 {dropped} 条低质量假设", flush=True)
    return filtered


def _hypothesis_quality_score(hypothesis: dict[str, Any]) -> tuple[int, int, int, int]:
    """Prefer hypotheses with executable bindings and richer local context."""
    vm = hypothesis.get("verification_method", {})
    if not isinstance(vm, dict):
        vm = {}
    step_count = sum(1 for key in ("path", "step1", "step2", "step3") if str(vm.get(key, "") or "").strip())
    explicit_binding = 1 if step_count > 0 else 0
    local_source = 1 if hypothesis.get("_hypothesis_source") == "local_analyzer" else 0
    evidence_weight = len(str(hypothesis.get("evidence", "") or "")) + len(str(hypothesis.get("description", "") or ""))
    severity = str(hypothesis.get("severity", "") or "").upper()
    severity_weight = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(severity, 0)
    return explicit_binding, step_count, local_source + severity_weight, evidence_weight


def _merge_verification_method(primary: Any, secondary: Any) -> dict[str, Any]:
    """Keep the richer executable binding while filling missing probe steps."""
    left = dict(primary) if isinstance(primary, dict) else {}
    right = dict(secondary) if isinstance(secondary, dict) else {}
    merged = dict(left)
    for key in ("method", "path", "step1", "step2", "step3", "_before_observer", "_after_observer", "_cross_observer"):
        if not merged.get(key) and right.get(key):
            merged[key] = right[key]
    return merged


def _merge_unique_lists(*values: Any) -> list[Any]:
    """Merge small evidence lists while keeping order stable."""
    merged: list[Any] = []
    seen: set[str] = set()
    for value in values:
        items = value if isinstance(value, list) else ([value] if value not in (None, "", [], {}) else [])
        for item in items:
            marker = json.dumps(item, ensure_ascii=False, default=str, sort_keys=True)
            if marker in seen:
                continue
            merged.append(item)
            seen.add(marker)
    return merged


def _prefer_text(primary: Any, secondary: Any) -> str:
    """Choose the more informative non-empty textual field."""
    left = str(primary or "").strip()
    right = str(secondary or "").strip()
    if not left:
        return right
    if not right:
        return left
    return right if len(right) > len(left) else left


def _merge_hypothesis_pair(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge a duplicate hypothesis cluster into a stronger executable hypothesis."""
    current_score = _hypothesis_quality_score(current)
    incoming_score = _hypothesis_quality_score(incoming)
    base, extra = (incoming, current) if incoming_score > current_score else (current, incoming)

    merged = dict(base)
    merged["verification_method"] = _merge_verification_method(
        base.get("verification_method"),
        extra.get("verification_method"),
    )

    for field in (
        "title",
        "description",
        "expected_behavior",
        "actual_behavior",
        "why_this_matters",
        "symptoms_if_broken",
        "entity",
        "source_entity",
        "risk_type",
        "category",
    ):
        merged[field] = _prefer_text(base.get(field), extra.get(field))

    base_evidence = base.get("evidence")
    extra_evidence = extra.get("evidence")
    if isinstance(base_evidence, dict) or isinstance(extra_evidence, dict):
        merged_evidence = dict(base_evidence) if isinstance(base_evidence, dict) else {}
        if isinstance(extra_evidence, dict):
            for key, value in extra_evidence.items():
                merged_evidence.setdefault(key, value)
        if merged_evidence:
            merged["evidence"] = merged_evidence

    for field in ("reproduction_steps", "related_endpoints", "evidence_refs"):
        values = _merge_unique_lists(base.get(field, []), extra.get(field, []))
        if values:
            merged[field] = values

    merged_sources = _merge_unique_lists(
        base.get("_merged_sources", []),
        extra.get("_merged_sources", []),
        base.get("_reasoner_engine"),
        extra.get("_reasoner_engine"),
        base.get("_hypothesis_source"),
        extra.get("_hypothesis_source"),
    )
    if merged_sources:
        merged["_merged_sources"] = merged_sources

    merged_ids = _merge_unique_lists(
        base.get("_merged_hypothesis_ids", []),
        extra.get("_merged_hypothesis_ids", []),
        base.get("hypothesis_id"),
        extra.get("hypothesis_id"),
    )
    if merged_ids:
        merged["_merged_hypothesis_ids"] = merged_ids

    merged["_merge_count"] = max(
        int(base.get("_merge_count", 1) or 1),
        int(extra.get("_merge_count", 1) or 1),
    ) + 1
    return merged


def _dedupe_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate hypotheses while preserving cross-source evidence."""
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        key = _hypothesis_identity(hypothesis)
        if key not in deduped:
            deduped[key] = hypothesis
            order.append(key)
            continue
        deduped[key] = _merge_hypothesis_pair(deduped[key], hypothesis)
    return [deduped[key] for key in order]


def _execution_priority_score(hypothesis: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Rank stronger hypotheses first without changing their executable shape."""
    vm = hypothesis.get("verification_method", {})
    if not isinstance(vm, dict):
        vm = {}
    step_count = sum(1 for key in ("path", "step1", "step2", "step3") if str(vm.get(key, "") or "").strip())
    merged_sources = hypothesis.get("_merged_sources", [])
    source_count = len(merged_sources) if isinstance(merged_sources, list) and merged_sources else 1
    has_cross_source = 1 if source_count >= 2 else 0
    severity = str(hypothesis.get("severity", "") or "").upper()
    severity_weight = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(severity, 0)
    evidence_weight = len(str(hypothesis.get("evidence", "") or "")) + len(str(hypothesis.get("description", "") or ""))
    merge_count = int(hypothesis.get("_merge_count", 1) or 1)
    return has_cross_source, source_count, step_count, severity_weight + merge_count, evidence_weight


def _prioritize_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort hypotheses so dual-source executable candidates run earlier."""
    enumerated = list(enumerate(hypotheses))
    ordered = sorted(
        enumerated,
        key=lambda item: (_execution_priority_score(item[1]), -item[0]),
        reverse=True,
    )
    return [hypothesis for _, hypothesis in ordered]


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
            if hasattr(worker_config, "max_tokens"):
                worker_config.max_tokens = _effective_max_tokens(
                    getattr(worker_config, "max_tokens", MIN_REASONER_MAX_TOKENS)
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
                # Sort by quality before truncating to avoid dropping good hypotheses
                sorted_hypotheses = sorted(
                    hypotheses,
                    key=lambda h: _hypothesis_quality_score(h) if isinstance(h, dict) else (0, 0, 0, 0),
                    reverse=True,
                )
                for hypothesis in sorted_hypotheses[:max(1, min(int(max_hypotheses or 1), MAX_HYPOTHESES))]:
                    if not isinstance(hypothesis, dict):
                        continue
                    item = dict(hypothesis)
                    for key, value in list(item.items()):
                        if isinstance(value, str) and len(value) > max_hypothesis_chars:
                            item[key] = value[:max_hypothesis_chars - 3] + "..."
                    item.setdefault("_reasoner_engine", engine_name)
                    # Extract entity from title/description for cross-source dedup
                    if not item.get("entity") and not item.get("source_entity"):
                        extracted_entity = _extract_entity_from_hypothesis(item)
                        if extracted_entity:
                            item["entity"] = extracted_entity
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
    """11 engines + 8 analyzers, max 4 parallel workers, independent clients, 2 attempts max."""

    from .reasoner_prompt import REASONER_PROMPTS, REASONER_SYSTEM_PROMPT, REAL_BUG_EXAMPLES, REASONER_PRE_PROMPT
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
        ("saga", REASONER_PROMPTS["saga"]),
        ("event_chain", REASONER_PROMPTS["event_chain"]),
        ("metamorphic", REASONER_PROMPTS["metamorphic"]),
        *[
            (name, REASONER_PROMPTS.get(prompt_key, REASONER_PROMPTS["consistency"]))
            for name, prompt_key in SIDE_PATH_REASONER_ENGINES
        ],
    ]
    # Deduplicate engines that share identical prompt templates (same prompt =
    # same output = wasted API cost).  Keep the first occurrence of each
    # canonical prompt.
    _seen_templates: dict[str, bool] = {}
    _deduped_engines: list[tuple[str, str]] = []
    _dedup_count = 0
    for ename, etemplate in engines:
        # Use the template text as the identity key (template object may differ
        # but content is what matters)
        template_key = etemplate.strip() if isinstance(etemplate, str) else str(etemplate)
        if template_key in _seen_templates:
            _dedup_count += 1
            continue
        _seen_templates[template_key] = True
        _deduped_engines.append((ename, etemplate))
    if _dedup_count:
        print(f"  [INFO] Deduplicated {_dedup_count} engines with identical prompt templates "
              f"({len(engines)} -> {len(_deduped_engines)})", flush=True)
    engines = _deduped_engines

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
    raw_max_tokens = os.environ.get(
        "QUALIBUG_REASONER_MAX_TOKENS",
        str(get_policy_value("reasoner", "max_tokens", MIN_REASONER_MAX_TOKENS)),
    )
    max_tokens = _effective_max_tokens(raw_max_tokens)
    retry_delay = get_policy_value("reasoner", "retry_delay_seconds", 2.0)
    truncation_map = get_policy_value("reasoner", "prompt_truncation_chars",
        {"prd_text": 45000, "api_schema": 50000, "observed_data": 12000,
         "heuristic_findings": 12000, "reader_json": 20000, "lifecycle_definition": 12000,
         "requirement_context": 45000, "api_context": 50000,
         "database_context": 25000, "bug_history_context": 25000})
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
        local_executable_quality_report = build_executable_quality_report(
            {"local_bootstrap": {"hypotheses": local_hypotheses}},
            ["local_bootstrap"],
            local_hypotheses,
        )
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
            "max_tokens": 0,
            "max_hypotheses_per_engine": max_hypotheses_per_engine,
            "max_hypothesis_chars": max_hypothesis_chars,
        }
        self._last_engine_report.update(local_executable_quality_report)
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
            event_chain=reader_json[:_limit("lifecycle_definition", 2000)],
            events=prompt_api[:_limit("api_schema", 2000)],
            relations=reader_json[:_limit("reader_json", 2000)],
            test_data=observed_json[:_limit("observed_data", 2000)],
            REAL_BUG_EXAMPLES=REAL_BUG_EXAMPLES,
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
    original_config.max_tokens = _effective_max_tokens(
        getattr(original_config, "max_tokens", MIN_REASONER_MAX_TOKENS),
        max_tokens,
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
                REASONER_SYSTEM_PROMPT + "\n\n" + REASONER_PRE_PROMPT, original_config,
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

    # ── 新增：运行分析器（可选，通过环境变量控制）──
    use_analyzers = str(os.environ.get("QUALIBUG_USE_ANALYZERS", "1")).lower() in {"1", "true", "yes"}
    if use_analyzers:
        try:
            from .analyzers_adapter import build_analyzer_hypotheses, get_analyzer_engine_names

            analyzer_engine_names = get_analyzer_engine_names()
            print(f"\n[Stage 2] Analyzers — 运行 {len(analyzer_engine_names)} 个分析器...", flush=True)

            analyzer_hypotheses_by_engine = build_analyzer_hypotheses(
                prd_text=prd_text,
                api_spec=api_spec,
                max_hypotheses_per_analyzer=max_hypotheses_per_engine
            )

            analyzer_hypotheses_count = 0
            for engine_name in analyzer_engine_names:
                hypotheses = analyzer_hypotheses_by_engine.get(engine_name, [])
                status = "success" if hypotheses or engine_name in analyzer_hypotheses_by_engine else "degraded"
                results_by_engine[engine_name] = {
                    "engine_name": engine_name,
                    "hypotheses": hypotheses,
                    "status": status,
                    "attempts": 1,
                    "retry_used": False,
                    "raw_chars": 0,
                    "content_chars": sum(len(str(h)) for h in hypotheses),
                    "duration_seconds": 0.0,
                    "error": "",
                    "degradation_reason": "" if hypotheses else "analyzer_no_bindable_hypotheses",
                }
                all_hypotheses.extend(hypotheses)
                analyzer_hypotheses_count += len(hypotheses)
                status_label = "[OK]" if hypotheses else "[WARN]"
                print(f"    {status_label} [{engine_name}] {len(hypotheses)} hypotheses", flush=True)
            engine_names_for_report.extend(analyzer_engine_names)

            print(f"  [OK] 分析器生成了 {analyzer_hypotheses_count} 条假设", flush=True)

        except Exception as e:
            print(f"  [WARN] 分析器集成失败: {e}", flush=True)

    # ── P3: Bug Ontology-driven hypothesis generation ────────────────
    # Generate ontology-guided behavior slices as additional hypotheses.
    ontology_hypotheses: list[dict[str, Any]] = []
    try:
        from .context_extractor import extract_context
        from .bug_ontology_registry import get_ontology_registry
        from .behavior_slice_gen import BehaviorSliceGenerator

        ctx = extract_context(prd_text, api_spec)
        registry = get_ontology_registry()
        gen = BehaviorSliceGenerator(ctx, registry)
        slices = gen.generate()

        # Convert slices to hypothesis format
        for sl in slices:
            d = sl.to_dict()
            ontology_hypotheses.append({
                "hypothesis_id": f"onto_{d['slice_id']}",
                "title": d["invariant"],
                "severity": d.get("severity", "P2"),
                "category": d.get("risk_family", "ontology"),
                "risk_type": d.get("risk_family", "ontology"),
                "expected_behavior": d.get("expected_result", ""),
                "verification_method": {"path": d.get("target", ""), "method": d.get("action", "").split()[0] if d.get("action") else "GET"},
                "entity": d.get("source_entity", ""),
                "source_entity": d.get("source_entity", ""),
                "evidence": {"_slice_id": d["slice_id"], "ontology_subtype": d.get("ontology_subtype", "")},
                "reproduction_steps": d.get("execution_plan", [])[:5],
                "related_endpoints": [d.get("source_endpoint", "")],
                "why_this_matters": d.get("invariant", ""),
                "symptoms_if_broken": d.get("expected_result", ""),
                "_reasoner_engine": "bug_ontology",
                "_hypothesis_source": "ontology_slice",
                "_ontology": {
                    "family_id": d.get("risk_family", ""),
                    "subtype": d.get("ontology_subtype", ""),
                    "invariant_type": d.get("invariant_type", ""),
                    "slice_id": d["slice_id"],
                },
            })

        if ontology_hypotheses:
            print(f"  [OK] Ontology: {len(ontology_hypotheses)} ontology-driven hypotheses generated", flush=True)
            results_by_engine["bug_ontology"] = {
                "engine_name": "bug_ontology",
                "hypotheses": ontology_hypotheses,
                "status": "success",
                "attempts": 1,
                "retry_used": False,
                "raw_chars": 0,
                "content_chars": sum(len(str(h)) for h in ontology_hypotheses),
                "duration_seconds": 0.0,
                "error": "",
                "degradation_reason": "",
            }
            engine_names_for_report.append("bug_ontology")
    except Exception as e:
        print(f"  [WARN] Ontology integration degraded: {e}", flush=True)

    # Merge ontology hypotheses into the main pool (with dedup weighting)
    all_hypotheses.extend(ontology_hypotheses)

    # ── Quality gate: filter out low-quality hypotheses ──
    pre_filter_total = len(all_hypotheses)
    all_hypotheses = _filter_low_quality_hypotheses(all_hypotheses)

    # ── Dedupe hypotheses across LLM and local analyzers ──
    pre_dedupe_total = len(all_hypotheses)
    all_hypotheses = _dedupe_hypotheses(all_hypotheses)
    deduped_count = pre_dedupe_total - len(all_hypotheses)
    if deduped_count > 0:
        print(f"  [OK] 去重移除了 {deduped_count} 条重复假设", flush=True)
    all_hypotheses = _prioritize_hypotheses(all_hypotheses)
    if all_hypotheses:
        top = all_hypotheses[0]
        print(
            f"  [OK] 最高优先级假设: [{top.get('severity','?')}] {str(top.get('title','?'))[:80]}",
            flush=True,
        )

    # ── Build engine health report ──
    executable_quality_report = build_executable_quality_report(
        results_by_engine,
        engine_names_for_report,
        all_hypotheses,
    )
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
        "max_tokens": _effective_max_tokens(max_tokens),
        "max_hypotheses_per_engine": max_hypotheses_per_engine,
        "max_hypothesis_chars": max_hypothesis_chars,
    }
    self._last_engine_report.update(executable_quality_report)

    print(f"  [OK] 生成了 {len(all_hypotheses)} 条假设 (across {len(engines)} engines)", flush=True)
    return all_hypotheses
