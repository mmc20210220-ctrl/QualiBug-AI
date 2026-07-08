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


1. Fail Fast / Errors Never Pass Silently：不要在代码里藏兜底逻辑来吞掉错误、隐藏问题。出了问题就应该让它爆出来，否则你永远找不到真实问题。
2. Fix the Cause, Not the Symptom / Don't Paper Over Bugs：当一个问题出现时，不要用各种 small fix、针对性补丁来掩盖它。必须定位真实根因，彻底修复。在 bug 上糊纸只会让系统积累你不知道的危险暗病。
3. Make It Observable：即使问题很难定位，也绝不要偷懒做表面修复。应该给项目增加充分的日志和可观测性，保证下次问题再现时你有足够信息去定位。问题无法修复时，只需要诚实告诉我信息不足、需新增日志，不要假装修好了。
4. Design for Debugging / Traceability：始终注意在关键路径上给自己留足排查日志，确保每一个关键节点都是可追溯的。
5. Living Documentation / Single Source of Truth：当项目关键技术栈或产品方向发生变更时，同步更新 agents.md。文档必须随代码一起演进，不能让它变成过时的谎言。
6.所有产品前后端都不能有硬编码，要保持通用性，我做的是全行业适配的，绝对不能有硬编码
7.测试项目测试bug不能造假数据给我，没有执行找出的bug不要给我，不能给我假数据
8.首先我要的就是全行业不同软件系统都适用，只要违反这个原则都要优化
9.我的产品前端服务端口是5174，后端服务端口是8088，不要搞错了