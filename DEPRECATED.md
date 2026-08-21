# DEPRECATED.md — Zombie Module Registry

Architecture decisions for modules tagged `[DEPRECATED]`. Convention:
never delete — preserve code for future activation. A deprecated module may
fail loudly at call time (removed dependency), but must import cleanly.

## 🔴 Zombies (0 cross-references, broken dependency)

| File | Purpose | Broken dependency | Roadmap |
|------|---------|-------------------|---------|
| `ai_test_asset_center/sweep_loop.py` | Loop Library #010 full discovery sweep CLI | `ai_test_asset_center.db_verifier` (removed in dead-code cleanup) | Reuse the six-step feedback pattern inside the unified mainline (`python -m ai_test_asset_center scan ...`); do not revive the side-path CLI |
| `ai_test_asset_center/million_dataset_card.py` | Phase13 dataset-card generator | `enterprise_bug_factory.tools.generate_million_dataset` (removed) | Fold back into `enterprise_bug_factory` if the million-bug dataset tooling is revived |

## History

- 2026-08-21: Registered `sweep_loop.py` and `million_dataset_card.py` after
  the full-package import smoke found them failing with ModuleNotFoundError.
  Both were already unreferenced (0 cross-refs); mainline discovery is
  unaffected. Broken top-level imports were moved into call sites so the
  package imports cleanly; callers now get an explicit deprecation error.
