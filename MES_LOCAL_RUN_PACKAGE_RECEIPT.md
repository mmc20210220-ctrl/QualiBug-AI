# MES Local Run Pack Receipt

This package contains the Phase91 QualiBug source required to evaluate business-bug discovery against the bundled MES BugLab target.

- No `.env` or `.env.local` is included.
- No generated runtime output, SQLite ledger, logs, caches or customer data is included.
- The two bundled `.db` files are local synthetic MES BugLab fixtures only: an active working copy and a reset seed copy.
- Run `RUN_MES_BUG_DISCOVERY_ONCE.ps1` after bootstrap and LLM connectivity validation.
