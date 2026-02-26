"""Kafka event publishing adapter for the Vendor Intelligence service.

Wraps aumos-common's EventPublisher with vendor-intelligence-domain-specific
topic constants and structured event payloads.
"""

from aumos_common.events import EventPublisher
from aumos_common.observability import get_logger

logger = get_logger(__name__)


class VendorIntelligenceEventPublisher(EventPublisher):
    """Event publisher specialised for vendor intelligence domain events.

    Extends EventPublisher from aumos-common, adding vendor-specific
    helpers. Topic names follow the vendor.* convention.

    Topics published:
        vendor.registered            — new vendor registered for evaluation
        vendor.evaluated             — vendor evaluation completed with new score
        vendor.lock_in_assessed      — lock-in risk assessment completed
        contract.analyzed            — contract risk analysis completed
        insurance.gap_detected       — insurance coverage gap identified
        insurance.gap_updated        — insurance gap status updated
    """

    pass
