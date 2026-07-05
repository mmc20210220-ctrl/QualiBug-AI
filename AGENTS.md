## Product Health Checks

When evaluating product readiness or dogfooding bug-finding features, verify observable behavior from the running product and code before reporting status. Treat configured-but-unverified integrations as not online: for model providers, a saved key or endpoint only means "configured" until a real health check succeeds, and failures must be shown as failed/offline rather than healthy.

## Syntax Check After Every Edit

**After ANY file edit (patch, write, terminal sed), always verify syntax before concluding the change succeeded.** A missing parenthesis, bracket, or quote is invisible in the diff but makes the entire module unimportable. Silent import failures cause background processes, cron jobs, and tests to die with zero output — and their exit code still reports "ok" to the scheduler.

Run immediately after editing any Python file:
```
python -c "import ast; ast.parse(open('path/to/file.py').read()); print('OK')"
```

Never skip this step. A "syntax OK" check takes 0.1 seconds and prevents hours of silent failures.

## Critical Configuration Guardrails

These values MUST NOT be removed or lowered below their floor. Removing them causes silent failures that look like "process died" but are actually timeouts.

| File | Line | Value | Reason |
|---|---|---|---|
| `discovery_engine.py` | `__init__` | `timeout_seconds ≥ 300` | Reader prompt needs 150-200s on DeepSeek. Default 120s → silent timeout → loop appears "crashed". |
| `discovery_engine.py` | `__init__` | `max_tokens ≥ 32768` | Causality engine produces >41K chars JSON. Truncation at lower values causes engine failures. |
| `stage_reason_all_v2.py` | `MAX_HYPOTHESES` | `15` | Per-engine hypothesis cap. Higher values increase API cost disproportionately. |
| `stage_reason_all_v2.py` | `max_workers` | `4` | Default parallel engine workers. Higher → API rate limits. |

When refactoring configuration (e.g. Policy Registry migration), always verify these floors are preserved with:
```python
assert engine.client.config.timeout_seconds >= 300, "timeout too low"
assert engine.client.config.max_tokens >= 32768, "max_tokens too low"
```
所有产品前后端都不能有硬编码，要保持通用性，我做的是全行业适配的，绝对不能有硬编码
