# Cross-Industry Evaluator Target Deployment Design

Date: 2026-07-11

## Objective

Turn the existing 131-bug benchmark mall and five pinned open-source system bundles into auditable evaluator assets without claiming unverified runtime readiness, hidden-ground-truth quality, or Gate D success.

The deployment process is strictly serial: at most one system under test may run at any time. QualiBug itself may remain available on frontend port 5174 and backend port 8088, but the benchmark mall and the open-source targets must never run concurrently with one another.

## Asset Roles

- The 131-bug benchmark mall remains the frozen held-in diagnostic target.
- OpenProject is the first held-out deployment candidate.
- ERPNext is the second held-out deployment candidate.
- OpenMRS is the third held-out deployment candidate.
- Snipe-IT remains a clean-target candidate until an evaluator audit proves the selected snapshot and fixtures satisfy the clean-target contract.
- Medusa remains an additional commerce stress target and does not substitute for cross-industry coverage.

An asset role is not a measurement result. A target becomes evaluator-ready only after real deployment, governed execution, reset, cleanup, and evaluator-private ground-truth checks succeed.

## Host Architecture

1. Enable WSL2 and install an Ubuntu distribution.
2. Install Docker Desktop using the WSL2 backend.
3. Store WSL and Docker data on drive D rather than the space-constrained system drive.
4. Keep target-specific runtime state under a dedicated D-drive root.
5. Use one Compose project name and one target identity per system.
6. Never use global Docker prune operations. Remove only explicitly named disposable resources belonging to the current target.

Windows 10 build 19045 and firmware virtualization are suitable. Installation may require elevation and a reboot. A reboot is a planned boundary: work resumes by rechecking WSL, Docker, disk, memory, QualiBug ports, and the current target state.

## Serial Execution State Machine

Each target moves through these states:

1. `ASSET_VALID`: pinned source and deployment checksums are verified.
2. `DEPLOYABLE`: configuration renders successfully with target-specific ports and secrets.
3. `RUNTIME_READY`: health, login, API, database, fixture, and observation checks pass.
4. `EVALUATOR_READY`: a frozen evaluator-private manifest, real executable ground truth or audited clean designation, reset contract, cleanup contract, and receipts exist.
5. `STOPPED_CLEAN`: the target is stopped, disposable state is removed or restored, and cleanup receipts are preserved.

The next target cannot start until the current target reaches `STOPPED_CLEAN`. A failure produces `BLOCKED` or `FAILED_SAFE`; it never advances readiness by assumption.

## Deployment Order

### Phase 0: Infrastructure

- Record the host baseline: Windows version, virtualization, memory, C/D free space, active ports, and running QualiBug processes.
- Enable WSL2 and Ubuntu.
- Install Docker Desktop and relocate data to D.
- Verify Docker engine and Compose with a disposable, non-production smoke container.

### Phase 1: OpenProject

- Stop the benchmark mall before starting OpenProject.
- Create an ignored environment file from the upstream example with non-production secrets.
- Override the published host port without editing upstream deployment files.
- Start only OpenProject and its declared dependencies.
- Verify health, login, role behavior, API or documented HTTP surface, database observation, reset, and cleanup.
- Stop OpenProject and produce a `STOPPED_CLEAN` receipt.

### Phase 2: ERPNext

- Confirm OpenProject is stopped and clean.
- Start ERPNext using its pinned disposable deployment entry point and a configuration-only port override.
- Create explicit test actors and disposable business fixtures through documented product flows.
- Verify health, login, workflow/API behavior, database observation, reverse-order cleanup, and reset.
- Stop ERPNext and produce a `STOPPED_CLEAN` receipt.

### Phase 3: OpenMRS

- Confirm ERPNext is stopped and clean.
- Start the pinned OpenMRS distribution with isolated credentials and target ports.
- Model healthcare actors and fixtures explicitly; do not infer production-like safety from localhost.
- Verify health, login, documented workflows, database observation, cleanup, and reset.
- Stop OpenMRS and produce a `STOPPED_CLEAN` receipt.

Snipe-IT and Medusa are not started during these three phases.

## Runtime Input Boundary

QualiBug receives only:

- target identity and declared non-production environment type;
- public product documentation and API descriptions;
- exact frontend/API/database endpoints;
- test-account secret references;
- fixture and reset contracts;
- runtime observations.

QualiBug must not receive source archives, hidden ground truth, evaluator match rules, historical answer labels, or mutation metadata. Evaluator-private files live outside the runtime-readable target input tree and are opened only by the external evaluator.

## Ground Truth and Clean Target Rules

- A ground-truth item is accepted only when the deployed target contains the defect and the evaluator can execute its trigger.
- JSON-only answers are invalid.
- Historical defects require a pinned defective version and a pinned fixed counterpart.
- Fresh mutations require a real deployed mutation plus an independently executable trigger.
- The 131-bug mall stays held-in and cannot replace held-out industries.
- An upstream snapshot is not automatically clean. Snipe-IT can be classified as clean only after P0/P1 audit, fixture execution, and evaluator receipt show zero critical/high seeded defects in scope.
- The existing mall keyword scorer is diagnostic only; Gate D uses the repository's external evaluation contract and formal customer-delivery findings.

## Port and Resource Policy

- QualiBug frontend 5174 and backend 8088 are reserved.
- Target ports are configuration values, never product-code constants.
- Only the current target may own its allocated ports.
- Before each startup, record free memory and D-drive capacity. Insufficient resources produce `BLOCKED_RESOURCE_CAPACITY` before containers start.
- Do not start all five systems, and do not leave the benchmark mall running while an open-source target is active.

## Failure Handling

- Installation, Compose rendering, health, login, fixture, observer, cleanup, reset, or receipt failures are visible stage failures.
- Partially started targets are stopped using their exact Compose project identity.
- Partially successful writes are compensated in reverse order through governed hooks.
- No entire scenario is retried after a write may have been accepted.
- Original cleanup failures remain recorded even if a later global reset restores the environment.
- No unrelated process, Docker project, volume, or image is removed.

## Verification and Acceptance

Infrastructure acceptance requires:

- WSL2 and Ubuntu online;
- Docker engine and Compose online;
- Docker/WSL data confirmed on D;
- 5174 and 8088 still owned by QualiBug;
- no target running after the infrastructure smoke test.

Per-target acceptance requires:

- checksum and deployment fingerprint receipt;
- rendered configuration with no reserved-port collision;
- health and login receipts;
- public API or UI execution evidence;
- database or other declared observation evidence;
- fixture prepare/reset receipts;
- per-write governance and reverse-order cleanup receipts;
- target stopped and clean before the next target starts.

Gate D remains `NOT_MEASURED` until three held-out industries, a proven clean target, champion/challenger replay and shadow runs, frozen unit-cost baseline, and external evaluator receipts exist.

## Explicit Non-Goals

- Do not run multiple systems under test concurrently.
- Do not modify upstream application source merely to make deployment easier.
- Do not encode benchmark answers in QualiBug prompts, detectors, services, or UI.
- Do not claim a clean target, held-out result, Gate D pass, controlled pilot, or GA status from deployment readiness alone.
