# LLM Moat Activation Guide — Phase61

## What Changed

Phase61 moat upgrade adds **LLM-powered semantic reasoning** to the business
reasoning engines. Previously, all 17 engines used regex + hardcoded dictionaries
to find bugs. Now, when LLM is configured, they use semantic reasoning that
understands business meaning — not just field name matches.

### New Modules

| Module | Purpose |
|--------|---------|
| `ai_test_asset_center/llm_reasoning.py` | Shared LLM reasoning layer, 16 prompt templates |
| `ai_test_asset_center/bug_pattern_memory.py` | Embedding-based bug similarity search, TF-IDF + cosine |

### Engines Upgraded

| Engine | LLM Capability |
|--------|---------------|
| `business_causality_conservation` | Semantic causality + conservation analysis |
| `counterexample_discovery` | Semantic counterexample detection across API pairs |
| `confirmed_bug_flywheel` | Pattern memory learning + similarity classification |

## Activation (3 Environment Variables)

```bash
export LLM_BASE_URL="https://api.openai.com/v1"    # or any OpenAI-compatible endpoint
export LLM_API_KEY="sk-..."                          # your API key
export LLM_MODEL="gpt-4o"                            # or claude-3-opus, etc.

# Optional tuning
export LLM_TEMPERATURE="0.1"                         # default: 0.1
export LLM_TIMEOUT_SECONDS="120"                     # default: 120
export LLM_MAX_TOKENS="4096"                         # default: 4096
```

Verify activation:

```bash
python -m aitestops.cli doctor
# Should show: "llm_enabled": true
```

## What The LLM Finds That Regex Misses

### Causality Engine (financial bugs)

**Regex finds**: `order.status="paid"` but no `payment` record with matching `order_id`

**LLM also finds**:
- "order.total is `base_price * quantity - discount + tax`, but the sum of
  line_item.amounts doesn't equal order.total within rounding tolerance"
- "refund.amount exceeds original payment.amount because the refund was calculated
  on `gross_amount` instead of `net_amount`"
- "shipment.tracking_number exists but the order was never paid — fulfillment
  without payment"
- "invoice.tax_rate=0.13 but line_item.tax_amount doesn't equal line_item.price * 0.13"

### Counterexample Engine

**Regex finds**: `GET /orders` shows `total=100` but `GET /orders/123` shows `total=99.99`

**LLM also finds**:
- "List endpoint returns `status: 'shipped'` for order 456, but detail endpoint
  returns `status: 'processing'` — state vocabulary disagreement across endpoints"
- "The `customer` field in the list response is an integer ID, but in the detail
  response it's a nested object with `id`, `name`, `email` — schema drift"
- "Pagination token `next_page=3` returns page 3, but the results are a subset of
  page 2 — pagination overlap bug"

### Bug Flywheel

**Without memory**: Each finding is isolated, no cross-finding learning

**With pattern memory**:
- New finding "refund exceeds payment for order 789" → auto-matched to confirmed
  bug "payment amount mismatch for order 456" (cosine=0.82)
- Auto-suggests: category=`side_effect_amount_mismatch`, severity=`P0`
- After 2+ confirmations, extracts detection signal: `[refund, payment, amount,
  exceed, mismatch]` → permanent detection rule
- Future probes with these tokens get automatic priority boost

## Fallback Behavior

All LLM reasoning is **best-effort**. When LLM is unavailable:
- Engines use existing heuristic path (regex + dictionaries) — no regression
- `llm_reasoning.reason()` returns `None` → caller uses `if llm_result:` pattern
- `bug_pattern_memory` uses lightweight TF-IDF even without LLM
- Exception handling prevents any LLM failure from blocking the pipeline

## Performance

- LLM calls happen AFTER heuristic analysis, not instead of it
- One LLM call per engine per run (not per contract)
- Context is limited to 6000-8000 chars per field
- Raw business data is never sent — only field names, types, and value ranges

## Testing Without LLM

All 75 existing tests pass without LLM configured — the moat upgrade is purely
additive and doesn't break existing behavior:

```bash
python -m pytest -q    # 75 passed
```
