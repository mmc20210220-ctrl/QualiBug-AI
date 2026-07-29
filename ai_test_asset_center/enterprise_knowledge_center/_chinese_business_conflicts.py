"""Compatibility facade for field-evidence-aware conflict construction."""
from __future__ import annotations

from . import _chinese_business_conflicts_legacy as _legacy

for _name in _legacy.__all__:
    globals()[_name] = getattr(_legacy, _name)

from ._field_dictionary_evidence import install_field_dictionary_evidence_contract

install_field_dictionary_evidence_contract()

__all__ = list(_legacy.__all__)
