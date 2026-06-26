# Phase73 Verification

## Product regression

```text
python -m pytest tests/test_deep_bug_mining.py \
  tests/test_bug_validation_queue.py \
  tests/test_product_ui.py -q --tb=short
11 passed
```

## Static adapter checks

```text
python -m py_compile \
  ai_test_asset_center/document_contract_fuzzing.py \
  ai_test_asset_center/mes_source_contract_audit.py
```

The verification harness also asserts that:

1. source comments are not included in the static evidence text;
2. a material-create numeric rule binds to `POST /master/materials` rather
   than `GET /master/materials`;
3. a document-backed manufacturing target produces at least one concrete
   route/function evidence record before any benchmark-scoring data is read.

## Safety checks

- Source audit: no network calls.
- Active document contract execution: disposable sandbox only, explicit
  `execute=true`, `approved_sandbox_execution=true`, and an approval id.
- No production target, credential, workspace output, cache, bytecode or
  benchmark ground truth is included in the release archive.

## Full regression

```text
python -m pytest -q --tb=short
95 passed in 38.04s
```
