"""Compatibility facade for the canonical atomic archive-ingestion authority.

The implementation lives in :mod:`archive_ingestion_core`. Keeping this module as the stable
public import path prevents a second archive parser or transaction authority from emerging.
"""
from .archive_ingestion_core import *  # noqa: F401,F403
from .archive_ingestion_core import __all__  # noqa: F401
