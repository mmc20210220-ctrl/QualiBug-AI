#!/usr/bin/env python
"""Backward-compatible entry point routed to the supervised worker."""
from run_loop_worker import main

if __name__ == "__main__":
    raise SystemExit(main())
