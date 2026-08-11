"""Compatibility bridge for strict cleanup resource identity.

This module is intentionally tiny so cleanup callers can adopt the strict
identity authority without depending on the historical ladder's deep nested-ID
fallback.
"""
from .cleanup_identity_authority import strict_observed_resource_identity

observed_resource_identity = strict_observed_resource_identity

__all__ = ["observed_resource_identity", "strict_observed_resource_identity"]
