"""Canonical public entrypoint for Feishu enterprise-material synchronization.

Transport and export primitives remain in ``feishu_connector_adapter``. All product code that
needs to reconcile a Feishu snapshot must import synchronization from this module, which delegates
to the capability-aware application service. The legacy adapter-level sync function remains only
as a temporary compatibility surface and is not an authorized product entrypoint.
"""
from __future__ import annotations

from .feishu_connector_capability_sync import (
    FEISHU_MATERIALIZATION_CAPABILITY_VERSION,
    classify_feishu_resource,
    sync_feishu_connector,
)

FEISHU_SYNC_ENTRYPOINT_SCHEMA = "qualibug.feishu-sync-entrypoint.v1"

__all__ = [
    "FEISHU_MATERIALIZATION_CAPABILITY_VERSION",
    "FEISHU_SYNC_ENTRYPOINT_SCHEMA",
    "classify_feishu_resource",
    "sync_feishu_connector",
]
