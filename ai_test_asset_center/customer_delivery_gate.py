"""Re-export shim — forward to customer_delivery_gate_v2."""
from .customer_delivery_gate_v2 import *  # noqa: F401,F403

LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA = "qualibug.customer-delivery-gate-receipt.v1"
CUSTOMER_READY_MIN_EVIDENCE_SCORE = 90
