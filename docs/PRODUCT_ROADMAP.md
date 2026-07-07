# QualiBug-AI Product Roadmap

Version: 1.3  
Status: Active  
Product Boundary: Discover → Prove → Report → Regression Validate

---

## Product Mission

QualiBug-AI is an enterprise behavior validation platform.

It exists to:

- Discover behavior violations
- Prove violations with runtime evidence
- Generate customer-grade reports
- Validate regressions after customer changes

It does **not**:

- Recommend code changes
- Generate code changes
- Modify customer code
- Create repair strategies
- Produce architecture remediation advice

Permanent rule:

> QualiBug-AI discovers, proves, reports, and validates. Customers change their systems.

---

## North Star Metric

### Confirmed Violation Rate

```text
confirmed violations / total detected violations
```

The product must optimize for:

- higher confirmation rate
- lower false-positive rate
- stronger evidence packages
- reproducible validation
- reusable regression assets

---

## Completed Capabilities

### Discovery Engine

Status: `COMPLETED`

Current capability:

- hypothesis generation
- runtime verification path
- discovery execution chain

---

### Runtime Evidence

Status: `COMPLETED`

Current capability:

- runtime evidence collection
- execution evidence reporting
- evidence threshold enforcement

---

### Confirmed Violation Gate

Status: `COMPLETED`

Current capability:

- promotion requires confirmed bug/violation state
- promotion requires runtime evidence

---

### Risk Scoring

Status: `COMPLETED`

Current capability:

- P0/P1/P2/P3 severity classification
- deterministic risk scoring
- risk report rendering

---

## P0 Roadmap

These items are mandatory before expanding the product into higher abstraction layers.

---

## P0-1 Behavior Registry

Status: `COMPLETED`

Purpose:

Create a permanent system of record for validated business/system behaviors.

Target module:

```text
ai_test_asset_center/behavior_registry.py
```

Target renderer:

```text
tools/render_behavior_registry_report.py
```

Minimum completion criteria:

- [x] behavior ID
- [x] behavior name
- [x] behavior category
- [x] linked violations
- [x] linked evidence
- [x] deterministic registry report
- [x] CLI report renderer
- [x] guardrail test preventing out-of-bound advisory language

Enterprise completion criteria:

- [x] validation history
- [x] risk trend by behavior
- [x] regression history
- [x] behavior status lifecycle

---

## P0-2 Evidence Package

Status: `COMPLETED`

Purpose:

Generate customer-grade violation evidence packages.

Target module:

```text
ai_test_asset_center/evidence_package.py
```

Target renderer:

```text
tools/render_evidence_package_report.py
```

Minimum completion criteria:

- [x] violation metadata
- [x] runtime evidence
- [x] reproduction steps
- [x] severity/risk context
- [x] renderer tests

Enterprise completion criteria:

- [x] request/response evidence
- [x] traceability
- [x] risk assessment
- [x] complete audit package
- [x] customer-ready export format

---

## P0-3 Regression Asset Library

Status: `COMPLETED`

Purpose:

Convert every confirmed violation into a reusable regression validation asset.

Target module:

```text
ai_test_asset_center/regression_asset_library.py
```

Target renderer:

```text
tools/render_regression_asset_report.py
```

Minimum completion criteria:

- [x] regression asset generation
- [x] confirmed violation linkage
- [x] behavior linkage
- [x] CLI report renderer

Enterprise completion criteria:

- [x] evidence linkage
- [x] replay input capture
- [x] expected outcome capture
- [x] execution result comparison
- [x] validation state report

---

## P0-4 Behavior Traceability

Status: `NOT STARTED`

Purpose:

Create end-to-end traceability across behavior validation artifacts.

Target module:

```text
ai_test_asset_center/behavior_traceability.py
```

Required chain:

```text
Behavior
→ Validation Run
→ Evidence Package
→ Violation
→ Regression Asset
→ Regression Result
```

Minimum completion criteria:

- [ ] Behavior ↔ Violation mapping

Enterprise completion criteria:

- [ ] full chain report
- [ ] validation history
- [ ] regression history
- [ ] status lifecycle

---

## P1 Roadmap

Only begin after all P0 items are completed.

### Behavior Coverage

Status: `PLANNED`

Example output:

```json
{
  "total_behaviors": 100,
  "validated_behaviors": 80,
  "violated_behaviors": 10,
  "untested_behaviors": 10
}
```
