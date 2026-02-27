"""SLAMonitor adapter for AI vendor SLA compliance tracking and alerting.

Tracks availability, latency percentiles, and error rates per vendor.
Detects SLA breaches, maintains historical trend data, and generates
compliance reports with degradation alerts.
"""

import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Default SLA thresholds
DEFAULT_UPTIME_SLA_PERCENT: float = 99.9
DEFAULT_LATENCY_P95_SLA_MS: float = 2000.0
DEFAULT_ERROR_RATE_SLA_PERCENT: float = 0.5
DEFAULT_OBSERVATION_WINDOW_HOURS: int = 24
DEFAULT_TREND_BUCKETS: int = 30  # 30-day trend

# Breach severity thresholds relative to SLA
BREACH_CRITICAL_MULTIPLE: float = 2.0   # 2x over SLA = critical
BREACH_HIGH_MULTIPLE: float = 1.5       # 1.5x over SLA = high


class SLAMonitor:
    """AI vendor SLA compliance tracking and alerting engine.

    Maintains per-vendor metrics windows for availability (uptime %),
    latency percentiles (P50/P95/P99), and error rates. Automatically
    detects breaches against configured SLA thresholds and records
    compliance history for trend analysis and reporting.
    """

    def __init__(
        self,
        uptime_sla_percent: float = DEFAULT_UPTIME_SLA_PERCENT,
        latency_p95_sla_ms: float = DEFAULT_LATENCY_P95_SLA_MS,
        error_rate_sla_percent: float = DEFAULT_ERROR_RATE_SLA_PERCENT,
        observation_window_hours: int = DEFAULT_OBSERVATION_WINDOW_HOURS,
        trend_buckets: int = DEFAULT_TREND_BUCKETS,
    ) -> None:
        """Initialise the SLAMonitor.

        Args:
            uptime_sla_percent: Target availability percent (e.g., 99.9).
            latency_p95_sla_ms: Target P95 latency in milliseconds.
            error_rate_sla_percent: Target error rate percent (e.g., 0.5).
            observation_window_hours: Rolling window for real-time metrics.
            trend_buckets: Number of daily buckets for historical trends.
        """
        self._uptime_sla = uptime_sla_percent
        self._latency_p95_sla = latency_p95_sla_ms
        self._error_rate_sla = error_rate_sla_percent
        self._window_hours = observation_window_hours
        self._trend_buckets = trend_buckets

        # Per-vendor time-stamped data points
        self._availability_events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=10000)
        )
        self._latency_observations: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=10000)
        )
        self._error_events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=10000)
        )
        self._daily_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._breach_log: list[dict[str, Any]] = []
        self._vendor_metadata: dict[str, dict[str, Any]] = {}

    def register_vendor(
        self,
        vendor_id: str,
        vendor_name: str,
        custom_uptime_sla: float | None = None,
        custom_latency_p95_sla_ms: float | None = None,
        custom_error_rate_sla_percent: float | None = None,
    ) -> None:
        """Register a vendor for SLA monitoring with optional custom thresholds.

        Args:
            vendor_id: Unique vendor identifier.
            vendor_name: Human-readable vendor name.
            custom_uptime_sla: Override default uptime SLA for this vendor.
            custom_latency_p95_sla_ms: Override default P95 latency SLA.
            custom_error_rate_sla_percent: Override default error rate SLA.
        """
        self._vendor_metadata[vendor_id] = {
            "vendor_name": vendor_name,
            "uptime_sla": custom_uptime_sla or self._uptime_sla,
            "latency_p95_sla_ms": custom_latency_p95_sla_ms or self._latency_p95_sla,
            "error_rate_sla_percent": custom_error_rate_sla_percent or self._error_rate_sla,
            "registered_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Vendor registered for SLA monitoring",
            vendor_id=vendor_id,
            vendor_name=vendor_name,
        )

    def record_health_check(
        self,
        vendor_id: str,
        is_available: bool,
        latency_ms: float,
        is_error: bool,
        response_code: int | None = None,
        observed_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Record a health check observation for a vendor.

        Args:
            vendor_id: Vendor identifier.
            is_available: True if vendor responded successfully.
            latency_ms: Response time in milliseconds.
            is_error: True if the response was an error (5xx).
            response_code: HTTP response code.
            observed_at: Observation timestamp (defaults to now).

        Returns:
            List of newly triggered breach alerts (empty if SLA met).
        """
        timestamp = observed_at or datetime.now(tz=timezone.utc)

        event: dict[str, Any] = {
            "observed_at": timestamp.isoformat(),
            "is_available": is_available,
            "latency_ms": latency_ms,
            "is_error": is_error,
            "response_code": response_code,
        }

        self._availability_events[vendor_id].append(event)
        self._latency_observations[vendor_id].append({
            "observed_at": timestamp.isoformat(),
            "latency_ms": latency_ms,
        })
        if is_error:
            self._error_events[vendor_id].append({
                "observed_at": timestamp.isoformat(),
                "response_code": response_code,
            })

        # Check for breaches and return any new alerts
        return self._check_breaches(vendor_id, timestamp)

    def get_uptime_percent(
        self,
        vendor_id: str,
        window_hours: int | None = None,
    ) -> float:
        """Calculate uptime percentage for a vendor within a time window.

        Args:
            vendor_id: Vendor identifier.
            window_hours: Lookback window in hours (defaults to configured window).

        Returns:
            Uptime percentage (0.0–100.0).
        """
        effective_window = window_hours or self._window_hours
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=effective_window)

        events = [
            e for e in self._availability_events.get(vendor_id, [])
            if datetime.fromisoformat(e["observed_at"]) >= cutoff
        ]

        if not events:
            return 100.0  # No data = assume available

        available_count = sum(1 for e in events if e["is_available"])
        return round((available_count / len(events)) * 100.0, 4)

    def get_latency_percentiles(
        self,
        vendor_id: str,
        window_hours: int | None = None,
    ) -> dict[str, float]:
        """Compute P50, P95, and P99 latency percentiles for a vendor.

        Args:
            vendor_id: Vendor identifier.
            window_hours: Lookback window in hours.

        Returns:
            Dict with p50_ms, p95_ms, p99_ms, mean_ms, and observation_count.
        """
        effective_window = window_hours or self._window_hours
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=effective_window)

        observations = [
            o["latency_ms"]
            for o in self._latency_observations.get(vendor_id, [])
            if datetime.fromisoformat(o["observed_at"]) >= cutoff
        ]

        if not observations:
            return {
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "mean_ms": 0.0,
                "observation_count": 0.0,
            }

        sorted_obs = sorted(observations)
        n = len(sorted_obs)

        def pct(data: list[float], p: float) -> float:
            idx = int(p / 100 * n)
            return data[min(idx, n - 1)]

        return {
            "p50_ms": round(pct(sorted_obs, 50), 2),
            "p95_ms": round(pct(sorted_obs, 95), 2),
            "p99_ms": round(pct(sorted_obs, 99), 2),
            "mean_ms": round(sum(observations) / n, 2),
            "observation_count": float(n),
        }

    def get_error_rate_percent(
        self,
        vendor_id: str,
        window_hours: int | None = None,
    ) -> float:
        """Compute error rate percentage for a vendor within a time window.

        Args:
            vendor_id: Vendor identifier.
            window_hours: Lookback window in hours.

        Returns:
            Error rate percentage (0.0–100.0).
        """
        effective_window = window_hours or self._window_hours
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=effective_window)

        all_events = [
            e for e in self._availability_events.get(vendor_id, [])
            if datetime.fromisoformat(e["observed_at"]) >= cutoff
        ]
        error_events = [e for e in all_events if e["is_error"]]

        if not all_events:
            return 0.0

        return round((len(error_events) / len(all_events)) * 100.0, 4)

    def _check_breaches(
        self,
        vendor_id: str,
        observed_at: datetime,
    ) -> list[dict[str, Any]]:
        """Check if current metrics constitute an SLA breach.

        Args:
            vendor_id: Vendor identifier.
            observed_at: Timestamp of the triggering observation.

        Returns:
            List of newly created breach alert dicts.
        """
        vendor_meta = self._vendor_metadata.get(vendor_id, {})
        uptime_sla = vendor_meta.get("uptime_sla", self._uptime_sla)
        latency_sla = vendor_meta.get("latency_p95_sla_ms", self._latency_p95_sla)
        error_sla = vendor_meta.get("error_rate_sla_percent", self._error_rate_sla)

        current_uptime = self.get_uptime_percent(vendor_id)
        latency_percentiles = self.get_latency_percentiles(vendor_id)
        current_error_rate = self.get_error_rate_percent(vendor_id)

        breaches: list[dict[str, Any]] = []

        # Uptime breach
        if current_uptime < uptime_sla:
            uptime_deficit = uptime_sla - current_uptime
            severity = (
                "critical" if uptime_deficit > (100.0 - uptime_sla) * BREACH_CRITICAL_MULTIPLE
                else "high"
            )
            breaches.append(self._create_breach_record(
                vendor_id=vendor_id,
                breach_type="uptime",
                observed_at=observed_at,
                current_value=current_uptime,
                sla_value=uptime_sla,
                severity=severity,
                message=(
                    f"Uptime {current_uptime:.3f}% is below SLA target {uptime_sla:.3f}%."
                ),
            ))

        # P95 latency breach
        p95_latency = latency_percentiles.get("p95_ms", 0.0)
        if p95_latency > latency_sla:
            overrun_factor = p95_latency / latency_sla if latency_sla > 0 else 1.0
            severity = (
                "critical" if overrun_factor >= BREACH_CRITICAL_MULTIPLE
                else "high" if overrun_factor >= BREACH_HIGH_MULTIPLE
                else "medium"
            )
            breaches.append(self._create_breach_record(
                vendor_id=vendor_id,
                breach_type="latency_p95",
                observed_at=observed_at,
                current_value=p95_latency,
                sla_value=latency_sla,
                severity=severity,
                message=(
                    f"P95 latency {p95_latency:.0f}ms exceeds SLA target {latency_sla:.0f}ms."
                ),
            ))

        # Error rate breach
        if current_error_rate > error_sla:
            overrun_factor = current_error_rate / error_sla if error_sla > 0 else 1.0
            severity = (
                "critical" if overrun_factor >= BREACH_CRITICAL_MULTIPLE
                else "high" if overrun_factor >= BREACH_HIGH_MULTIPLE
                else "medium"
            )
            breaches.append(self._create_breach_record(
                vendor_id=vendor_id,
                breach_type="error_rate",
                observed_at=observed_at,
                current_value=current_error_rate,
                sla_value=error_sla,
                severity=severity,
                message=(
                    f"Error rate {current_error_rate:.2f}% exceeds SLA target {error_sla:.2f}%."
                ),
            ))

        if breaches:
            self._breach_log.extend(breaches)
            for breach in breaches:
                logger.warning(
                    "SLA breach detected",
                    vendor_id=vendor_id,
                    breach_type=breach["breach_type"],
                    severity=breach["severity"],
                    current_value=breach["current_value"],
                    sla_value=breach["sla_value"],
                )

        return breaches

    def _create_breach_record(
        self,
        vendor_id: str,
        breach_type: str,
        observed_at: datetime,
        current_value: float,
        sla_value: float,
        severity: str,
        message: str,
    ) -> dict[str, Any]:
        """Create a structured breach record dict.

        Args:
            vendor_id: Vendor identifier.
            breach_type: Type of breach (uptime/latency_p95/error_rate).
            observed_at: Detection timestamp.
            current_value: Actual measured value.
            sla_value: SLA target value.
            severity: Breach severity (medium/high/critical).
            message: Human-readable breach description.

        Returns:
            Breach record dict.
        """
        return {
            "breach_id": str(uuid.uuid4()),
            "vendor_id": vendor_id,
            "vendor_name": self._vendor_metadata.get(vendor_id, {}).get("vendor_name", "Unknown"),
            "breach_type": breach_type,
            "severity": severity,
            "current_value": round(current_value, 4),
            "sla_value": sla_value,
            "message": message,
            "detected_at": observed_at.isoformat(),
        }

    def get_historical_trend(
        self,
        vendor_id: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Return daily aggregated metrics for trend analysis.

        Args:
            vendor_id: Vendor identifier.
            days: Number of days of history to return.

        Returns:
            List of daily bucket dicts with date, uptime_percent,
            p95_latency_ms, error_rate_percent, and breach_count.
        """
        today = datetime.now(tz=timezone.utc).date()
        trend: list[dict[str, Any]] = []

        for day_offset in range(days - 1, -1, -1):
            day = today - timedelta(days=day_offset)
            day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)

            day_availability = [
                e for e in self._availability_events.get(vendor_id, [])
                if day_start <= datetime.fromisoformat(e["observed_at"]) < day_end
            ]
            day_latencies = [
                o["latency_ms"]
                for o in self._latency_observations.get(vendor_id, [])
                if day_start <= datetime.fromisoformat(o["observed_at"]) < day_end
            ]
            day_breaches = [
                b for b in self._breach_log
                if b.get("vendor_id") == vendor_id
                and day_start <= datetime.fromisoformat(b["detected_at"]) < day_end
            ]

            uptime = 100.0
            if day_availability:
                available_count = sum(1 for e in day_availability if e["is_available"])
                uptime = round((available_count / len(day_availability)) * 100.0, 4)

            p95_ms = 0.0
            if day_latencies:
                sorted_lat = sorted(day_latencies)
                idx = int(0.95 * len(sorted_lat))
                p95_ms = round(sorted_lat[min(idx, len(sorted_lat) - 1)], 2)

            error_count = sum(1 for e in day_availability if e["is_error"])
            error_rate = round(error_count / len(day_availability) * 100.0, 4) if day_availability else 0.0

            trend.append({
                "date": day.isoformat(),
                "uptime_percent": uptime,
                "p95_latency_ms": p95_ms,
                "error_rate_percent": error_rate,
                "observation_count": len(day_availability),
                "breach_count": len(day_breaches),
                "sla_met": (
                    uptime >= self._uptime_sla
                    and p95_ms <= self._latency_p95_sla
                    and error_rate <= self._error_rate_sla
                ),
            })

        return trend

    def generate_sla_compliance_report(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        report_period_days: int = 30,
    ) -> dict[str, Any]:
        """Generate a structured SLA compliance report for a vendor.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            report_period_days: Number of days to include in the report.

        Returns:
            Dict with compliance_percent, breach_summary, trend_data,
            sla_targets, current_metrics, and recommendation fields.
        """
        vendor_meta = self._vendor_metadata.get(vendor_id, {})
        trend = self.get_historical_trend(vendor_id, days=report_period_days)
        days_sla_met = sum(1 for d in trend if d["sla_met"])
        compliance_percent = round((days_sla_met / len(trend)) * 100.0, 1) if trend else 100.0

        recent_breaches = [
            b for b in self._breach_log
            if b.get("vendor_id") == vendor_id
        ][-50:]

        breach_by_type: dict[str, int] = defaultdict(int)
        for breach in recent_breaches:
            breach_by_type[breach["breach_type"]] += 1

        current_metrics = {
            "uptime_percent": self.get_uptime_percent(vendor_id),
            "latency_percentiles": self.get_latency_percentiles(vendor_id),
            "error_rate_percent": self.get_error_rate_percent(vendor_id),
        }

        recommendation = "SLA targets are being met consistently."
        if compliance_percent < 90.0:
            recommendation = "SLA compliance is critically low — escalate to vendor account team immediately."
        elif compliance_percent < 99.0:
            recommendation = "SLA compliance is below target — schedule review with vendor."

        report: dict[str, Any] = {
            "report_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "vendor_id": vendor_id,
            "vendor_name": vendor_meta.get("vendor_name", "Unknown"),
            "report_period_days": report_period_days,
            "compliance_percent": compliance_percent,
            "days_sla_met": days_sla_met,
            "total_days": len(trend),
            "sla_targets": {
                "uptime_percent": vendor_meta.get("uptime_sla", self._uptime_sla),
                "latency_p95_ms": vendor_meta.get("latency_p95_sla_ms", self._latency_p95_sla),
                "error_rate_percent": vendor_meta.get("error_rate_sla_percent", self._error_rate_sla),
            },
            "current_metrics": current_metrics,
            "breach_summary": dict(breach_by_type),
            "total_breaches": len(recent_breaches),
            "trend_data": trend,
            "recommendation": recommendation,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "SLA compliance report generated",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
            compliance_percent=compliance_percent,
            total_breaches=len(recent_breaches),
        )

        return report
