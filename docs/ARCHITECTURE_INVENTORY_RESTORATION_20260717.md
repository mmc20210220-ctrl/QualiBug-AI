# Architecture inventory authority restoration — 2026-07-17

The implementation SSOT declared by `AGENTS.md`,
`docs/DISCOVERY_MODULE_STRANGLER.md`, and
`tools/architecture_inventory.py` was missing from the working tree. It has
been restored byte-for-byte from the last complete Git revision, then amended
only so a repository inventory follows Git's tracked and unignored source
view. Ignored scratch files are not architecture modules; malformed visible
Python sources still fail fast.

The restored command reports:

- 452 Python modules and 254,041 physical lines;
- 102 static retirement candidates;
- 24 oversized boundaries and 17 patch-authority modules;
- architecture budget `OVER_BUDGET`;
- runtime trace coverage `NOT_PROVIDED`;
- dynamic-import uncertainty present;
- `auto_delete_performed=false`;
- external discovery quality `NOT_MEASURED`.

These are architecture diagnostics only. No module is approved for deletion
until the complete runtime-trace, dynamic-import review, passing-test, and
manual-review gates in `docs/DISCOVERY_MODULE_STRANGLER.md` all pass.
