"""FallbackRouter adapter for automatic AI vendor failover and routing.

Monitors per-vendor health metrics and applies circuit-breaker logic to
route requests to the highest-priority healthy vendor. Records all routing
decisions for audit and dashboard consumption.
"""

import asyncio
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Circuit breaker state constants
STATE_CLOSED = "closed"        # Normal operation — requests pass through
STATE_OPEN = "open"            # Tripped — requests are blocked, failover active
STATE_HALF_OPEN = "half_open"  # Recovery probe in progress

# Default thresholds
DEFAULT_LATENCY_THRESHOLD_MS: float = 5000.0
DEFAULT_ERROR_RATE_THRESHOLD: float = 0.10
DEFAULT_WINDOW_SIZE: int = 100
DEFAULT_OPEN_DURATION_SECONDS: float = 60.0
DEFAULT_HALF_OPEN_PROBE_COUNT: int = 5


class VendorCircuitBreaker:
    """Per-vendor circuit breaker tracking health state.

    Maintains a sliding window of request outcomes and transitions between
    CLOSED, OPEN, and HALF_OPEN states based on configurable thresholds.
    """

    def __init__(
        self,
        vendor_id: str,
        error_rate_threshold: float,
        latency_threshold_ms: float,
        window_size: int,
        open_duration_seconds: float,
        probe_count: int,
    ) -> None:
        """Initialise the circuit breaker for a single vendor.

        Args:
            vendor_id: Unique vendor identifier.
            error_rate_threshold: Error fraction that trips the breaker.
            latency_threshold_ms: P95 latency (ms) that trips the breaker.
            window_size: Number of recent requests tracked.
            open_duration_seconds: Time to wait before entering HALF_OPEN.
            probe_count: Number of successful probes to close the circuit.
        """
        self.vendor_id = vendor_id
        self._error_rate_threshold = error_rate_threshold
        self._latency_threshold_ms = latency_threshold_ms
        self._window_size = window_size
        self._open_duration = open_duration_seconds
        self._probe_count = probe_count

        self.state: str = STATE_CLOSED
        self._outcomes: deque[bool] = deque(maxlen=window_size)  # True = success
        self._latencies_ms: deque[float] = deque(maxlen=window_size)
        self._tripped_at: datetime | None = None
        self._successful_probes: int = 0

    def record_outcome(self, success: bool, latency_ms: float) -> None:
        """Record the outcome of a request attempt.

        Args:
            success: True if the request succeeded.
            latency_ms: Response latency in milliseconds.
        """
        self._outcomes.append(success)
        self._latencies_ms.append(latency_ms)
        self._evaluate_state()

    def _evaluate_state(self) -> None:
        """Re-evaluate circuit breaker state after a new outcome."""
        if self.state == STATE_HALF_OPEN:
            if self._outcomes and self._outcomes[-1]:
                self._successful_probes += 1
                if self._successful_probes >= self._probe_count:
                    self._close()
            else:
                self._trip()
            return

        if len(self._outcomes) < max(10, self._window_size // 5):
            return  # Insufficient data

        error_rate = 1.0 - (sum(self._outcomes) / len(self._outcomes))
        latency_p95 = self._compute_p95_latency()

        if error_rate >= self._error_rate_threshold or latency_p95 >= self._latency_threshold_ms:
            self._trip()

    def _trip(self) -> None:
        """Transition to OPEN state."""
        if self.state != STATE_OPEN:
            logger.warning(
                "Circuit breaker tripped",
                vendor_id=self.vendor_id,
                error_rate=self._current_error_rate(),
                p95_latency_ms=self._compute_p95_latency(),
            )
        self.state = STATE_OPEN
        self._tripped_at = datetime.now(tz=timezone.utc)
        self._successful_probes = 0

    def _close(self) -> None:
        """Transition to CLOSED state (recovered)."""
        logger.info(
            "Circuit breaker recovered",
            vendor_id=self.vendor_id,
        )
        self.state = STATE_CLOSED
        self._tripped_at = None
        self._successful_probes = 0
        self._outcomes.clear()
        self._latencies_ms.clear()

    def should_allow_request(self) -> bool:
        """Determine whether a request should be allowed through.

        Returns:
            True if the request should proceed, False if blocked.
        """
        if self.state == STATE_CLOSED:
            return True

        if self.state == STATE_OPEN:
            if self._tripped_at is not None:
                elapsed = (datetime.now(tz=timezone.utc) - self._tripped_at).total_seconds()
                if elapsed >= self._open_duration:
                    self.state = STATE_HALF_OPEN
                    logger.info(
                        "Circuit breaker entering half-open",
                        vendor_id=self.vendor_id,
                    )
                    return True  # Allow probe request
            return False

        # HALF_OPEN — allow limited probes
        return True

    def _compute_p95_latency(self) -> float:
        """Compute P95 latency from the sliding window."""
        if not self._latencies_ms:
            return 0.0
        sorted_latencies = sorted(self._latencies_ms)
        index = int(0.95 * len(sorted_latencies))
        return sorted_latencies[min(index, len(sorted_latencies) - 1)]

    def _current_error_rate(self) -> float:
        """Compute current error rate from the sliding window."""
        if not self._outcomes:
            return 0.0
        return 1.0 - (sum(self._outcomes) / len(self._outcomes))

    def get_status(self) -> dict[str, Any]:
        """Return current circuit breaker status for dashboard consumption.

        Returns:
            Dict with state, error_rate, p95_latency_ms, and tripped_at fields.
        """
        return {
            "vendor_id": self.vendor_id,
            "state": self.state,
            "error_rate": round(self._current_error_rate(), 4),
            "p95_latency_ms": round(self._compute_p95_latency(), 2),
            "tripped_at": self._tripped_at.isoformat() if self._tripped_at else None,
            "successful_probes": self._successful_probes,
        }


class FallbackRouter:
    """Automatic AI vendor failover router with circuit breaker per vendor.

    Routes requests to the highest-priority healthy vendor. Monitors latency
    and error rate per vendor and triggers failover when thresholds are
    breached. Logs all routing decisions for auditability.

    Vendors are supplied with explicit priority ordering (1 = highest priority).
    On failback, the circuit breaker's HALF_OPEN state allows probe requests
    to verify recovery before restoring full traffic.
    """

    def __init__(
        self,
        latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
        error_rate_threshold: float = DEFAULT_ERROR_RATE_THRESHOLD,
        window_size: int = DEFAULT_WINDOW_SIZE,
        open_duration_seconds: float = DEFAULT_OPEN_DURATION_SECONDS,
        probe_count: int = DEFAULT_HALF_OPEN_PROBE_COUNT,
    ) -> None:
        """Initialise the FallbackRouter.

        Args:
            latency_threshold_ms: P95 latency above which a vendor is tripped.
            error_rate_threshold: Error fraction above which a vendor is tripped.
            window_size: Sliding window size for health computation.
            open_duration_seconds: Duration circuit stays open before probe.
            probe_count: Successful probes required to restore vendor.
        """
        self._latency_threshold = latency_threshold_ms
        self._error_rate_threshold = error_rate_threshold
        self._window_size = window_size
        self._open_duration = open_duration_seconds
        self._probe_count = probe_count

        self._breakers: dict[str, VendorCircuitBreaker] = {}
        self._vendor_priorities: dict[str, int] = {}
        self._routing_log: list[dict[str, Any]] = []
        self._health_store: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def register_vendor(
        self,
        vendor_id: str,
        vendor_name: str,
        priority: int,
    ) -> None:
        """Register a vendor with its routing priority.

        Args:
            vendor_id: Unique vendor identifier.
            vendor_name: Human-readable vendor name.
            priority: Routing priority (1 = highest, higher number = lower priority).
        """
        self._vendor_priorities[vendor_id] = priority
        self._breakers[vendor_id] = VendorCircuitBreaker(
            vendor_id=vendor_id,
            error_rate_threshold=self._error_rate_threshold,
            latency_threshold_ms=self._latency_threshold,
            window_size=self._window_size,
            open_duration_seconds=self._open_duration,
            probe_count=self._probe_count,
        )

        logger.info(
            "Vendor registered with fallback router",
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            priority=priority,
        )

    def select_vendor(self, tenant_id: uuid.UUID, request_context: dict[str, Any]) -> str | None:
        """Select the highest-priority healthy vendor for a request.

        Args:
            tenant_id: Requesting tenant UUID.
            request_context: Contextual information about the request.

        Returns:
            Selected vendor_id, or None if no healthy vendors are available.
        """
        ordered = sorted(
            self._vendor_priorities.items(),
            key=lambda item: item[1],
        )

        selected_vendor: str | None = None
        for vendor_id, priority in ordered:
            breaker = self._breakers.get(vendor_id)
            if breaker and breaker.should_allow_request():
                selected_vendor = vendor_id
                break

        routing_decision: dict[str, Any] = {
            "decision_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "selected_vendor": selected_vendor,
            "request_context": request_context,
            "vendor_states": {
                vid: self._breakers[vid].state
                for vid in self._breakers
            },
            "decided_at": datetime.now(tz=timezone.utc).isoformat(),
            "is_failover": (
                selected_vendor is not None
                and self._vendor_priorities.get(selected_vendor, 1) > 1
            ),
        }
        self._routing_log.append(routing_decision)

        if selected_vendor:
            logger.info(
                "Vendor selected for routing",
                tenant_id=str(tenant_id),
                selected_vendor=selected_vendor,
                is_failover=routing_decision["is_failover"],
            )
        else:
            logger.error(
                "No healthy vendors available",
                tenant_id=str(tenant_id),
                total_vendors=len(self._breakers),
            )

        return selected_vendor

    def record_vendor_outcome(
        self,
        vendor_id: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record the outcome of a request to a vendor.

        Args:
            vendor_id: Vendor identifier.
            success: True if request succeeded.
            latency_ms: Response latency in milliseconds.
        """
        breaker = self._breakers.get(vendor_id)
        if breaker:
            breaker.record_outcome(success=success, latency_ms=latency_ms)

    def get_vendor_health(self, vendor_id: str) -> dict[str, Any]:
        """Get the health status for a specific vendor.

        Args:
            vendor_id: Vendor identifier.

        Returns:
            Health status dict with circuit breaker state and metrics.
        """
        breaker = self._breakers.get(vendor_id)
        if not breaker:
            return {"vendor_id": vendor_id, "error": "Vendor not registered"}
        return breaker.get_status()

    def get_all_vendor_health(self) -> list[dict[str, Any]]:
        """Get health status for all registered vendors.

        Returns:
            List of health status dicts, ordered by priority.
        """
        ordered = sorted(
            self._vendor_priorities.items(),
            key=lambda item: item[1],
        )
        return [
            {
                **self._breakers[vid].get_status(),
                "priority": priority,
                "is_primary": priority == 1,
            }
            for vid, priority in ordered
            if vid in self._breakers
        ]

    def get_routing_log(
        self,
        tenant_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve recent routing decisions.

        Args:
            tenant_id: Optional tenant filter.
            limit: Maximum number of records to return.

        Returns:
            List of routing decision dicts, most recent first.
        """
        log = list(reversed(self._routing_log))
        if tenant_id is not None:
            log = [d for d in log if d.get("tenant_id") == str(tenant_id)]
        return log[:limit]

    def get_vendor_status_dashboard(self) -> dict[str, Any]:
        """Get a structured dashboard data payload for vendor health.

        Returns:
            Dict with healthy_vendor_count, degraded_vendors, unavailable_vendors,
            overall_health_status, primary_vendor_state, and vendor_details.
        """
        all_health = self.get_all_vendor_health()
        healthy = [v for v in all_health if v["state"] == STATE_CLOSED]
        degraded = [v for v in all_health if v["state"] == STATE_HALF_OPEN]
        unavailable = [v for v in all_health if v["state"] == STATE_OPEN]

        primary_vendors = [v for v in all_health if v.get("is_primary")]
        primary_state = primary_vendors[0]["state"] if primary_vendors else "unknown"

        overall_health = (
            "healthy" if not unavailable and not degraded
            else "degraded" if not unavailable
            else "critical" if not healthy
            else "partial"
        )

        recent_failovers = [
            d for d in self._routing_log[-50:]
            if d.get("is_failover")
        ]

        return {
            "dashboard_generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "overall_health_status": overall_health,
            "primary_vendor_state": primary_state,
            "healthy_vendor_count": len(healthy),
            "degraded_vendor_count": len(degraded),
            "unavailable_vendor_count": len(unavailable),
            "total_vendor_count": len(all_health),
            "recent_failover_count": len(recent_failovers),
            "vendor_details": all_health,
        }
