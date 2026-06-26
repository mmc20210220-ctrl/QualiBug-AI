#!/usr/bin/env python
"""Deprecated compatibility entry point.

All loop launches now run through the single supervised worker.  A durable
project lease prevents duplicate execution when an old scheduler still invokes
this script.
"""
from run_loop_worker import main

if __name__ == "__main__":
    raise SystemExit(main())
