# QualiBug-AI Product Roadmap

Version: 1.1  
Status: Active  
Product Boundary: Discover → Prove → Report → Regression Validate

---

## Product Mission

QualiBug-AI is an enterprise behavior validation platform.

It exists to:

- Discover behavior violations
- Prove violations with runtime evidence
- Generate customer-grade reports
- Validate regressions after customer fixes

It does **not**:

- Recommend fixes
- Generate fixes
- Modify customer code
- Create repair strategies
- Produce architecture remediation advice

Permanent rule:

> QualiBug-AI discovers, proves, reports, and validates. Customers fix.

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

Status: `MINIMUM COMPLETE / ENTERPRISE IN PROGRESS`

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
- [x] guardrail test preventing fix/recommendation/repair language

Enterprise completion criteria:

- [ ] validation history
- [ ] risk trend by behavior
- [ ] regression history
- [ ] behavior status lifecycle

---

## P0-2 Evidence Package

Status: `NOT STARTED`

Purpose:

Generate customer-grade defect evidence packages.

Target module:

```text
ai_test_asset_center/evidence_package.py
```

Minimum completion criteria:

- [ ] violation metadata
- [ ] runtime evidence
- [ ] reproduction steps
- [ ] severity/risk context

Enterprise completion criteria:

- [ ] request/response evidence
- [ ] traceability
- [ ] risk assessment
- [ ] complete audit package
- [ ] customer-ready export format

---

## P0-3 Regression Asset Library

Status: `NOT STARTED`

Purpose:

Convert every confirmed violation into a reusable regression asset.

Target module:

```text
ai_test_asset_center/regression_asset_library.py
```

Minimum completion criteria:

- [ ] regression asset generation
- [ ] confirmed violation linkage
- [ ] behavior linkage

Enterprise completion criteria:

- [ ] automatic execution
- [ ] automatic comparison
- [ ] automatic validation report
- [ ] proof that a customer fix resolved the behavior violation

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

### Behavior Drift Detection

Status: `PLANNED`

Purpose:

Detect behavior changes across releases.

### Behavior Assurance Report

Status: `PLANNED`

Purpose:

Generate executive-level behavior assurance reporting.

---

## P2 Roadmap

Only begin after P1.

### Rule Registry

Status: `PLANNED`

Purpose:

Manage business/system rules that define expected behavior.

### Rule Coverage

Status: `PLANNED`

Purpose:

Report validation coverage at the rule level.

---

## P3 Roadmap

Future exploration only. Not approved for implementation yet.

Includes:

- Intent Registry
- Claim Registry
- Knowledge Graph
- Ontology Systems

---

## Explicitly Rejected Capabilities

These are outside product strategy:

- bug fix recommendation
- auto fix
- code repair
- pull request generation
- architecture recommendation

Reason:

Every customer system is different. QualiBug-AI must not assume responsibility for remediation decisions.

---

## Official Execution Order

```text
P0-1 Behavior Registry
P0-2 Evidence Package
P0-3 Regression Asset Library
P0-4 Behavior Traceability
P1 Behavior Coverage
P1 Behavior Drift Detection
P1 Behavior Assurance Report
P2 Rule Registry
P2 Rule Coverage
P3 Intent / Claim / Knowledge Graph exploration
```

---

## Enterprise Success Definition

A commercially ready QualiBug-AI platform must provide:

```text
Behavior
→ Validation
→ Evidence
→ Violation
→ Report
→ Regression Validation
```

with:

- high confirmed violation rate
- low false-positive rate
- reproducible evidence
- reusable regression assets
- no repair recommendation responsibility
