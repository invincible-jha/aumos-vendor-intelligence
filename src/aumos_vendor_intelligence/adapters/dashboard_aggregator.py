"""VendorDashboardAggregator adapter for vendor intelligence trend analytics.

Aggregates cross-vendor performance, cost, quality, and usage data into
structured dashboard payloads suitable for executive reporting and real-time
monitoring dashboards.
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Dashboard time window presets
WINDOW_7D = 7
WINDOW_30D = 30
WINDOW_90D = 90


class VendorDashboardAggregator:
    """Vendor performance trend analytics and dashboard data aggregator.

    Consumes time-series metric data from SLAMonitor, BenchmarkingRunner,
    ArbitrageDetector, and CostRecords to produce structured JSON payloads
    ready for executive dashboards, Grafana panels, or API responses.

    All aggregation operates over tenant-scoped data. The aggregator is
    stateless with respect to persistence — it receives raw metric series
    and produces computed summaries on demand.
    """

    def __init__(
        self,
        default_trend_days: int = WINDOW_30D,
    ) -> None:
        """Initialise the VendorDashboardAggregator.

        Args:
            default_trend_days: Default number of days for trend windows.
        """
        self._default_trend_days = default_trend_days

    async def compute_vendor_performance_trends(
        self,
        tenant_id: uuid.UUID,
        vendor_metrics: list[dict[str, Any]],
        window_days: int | None = None,
    ) -> dict[str, Any]:
        """Compute per-vendor performance trend lines from time-series data.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_metrics: List of daily metric snapshots, each with:
                - vendor_id: str
                - vendor_name: str
                - date: str (ISO date)
                - uptime_percent: float
                - p95_latency_ms: float
                - error_rate_percent: float
                - cost_usd: float
            window_days: Days of history to include (defaults to configured default).

        Returns:
            Dict with per_vendor_trends (keyed by vendor_id), trend_period,
            and overall_performance_index for the period.
        """
        effective_window = window_days or self._default_trend_days
        cutoff_date = (datetime.now(tz=timezone.utc) - timedelta(days=effective_window)).date()

        logger.info(
            "Computing vendor performance trends",
            tenant_id=str(tenant_id),
            window_days=effective_window,
            data_points=len(vendor_metrics),
        )

        # Group by vendor
        by_vendor: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for metric in vendor_metrics:
            try:
                metric_date_str = metric.get("date", "")
                from datetime import date
                metric_date = date.fromisoformat(metric_date_str)
                if metric_date >= cutoff_date:
                    by_vendor[metric["vendor_id"]].append(metric)
            except ValueError:
                continue

        per_vendor_trends: dict[str, dict[str, Any]] = {}
        all_performance_scores: list[float] = []

        for vendor_id, daily_metrics in by_vendor.items():
            daily_metrics.sort(key=lambda d: d.get("date", ""))
            vendor_name = daily_metrics[0].get("vendor_name", "Unknown") if daily_metrics else "Unknown"

            uptime_series = [m.get("uptime_percent", 100.0) for m in daily_metrics]
            latency_series = [m.get("p95_latency_ms", 0.0) for m in daily_metrics]
            error_series = [m.get("error_rate_percent", 0.0) for m in daily_metrics]
            cost_series = [m.get("cost_usd", 0.0) for m in daily_metrics]

            avg_uptime = sum(uptime_series) / len(uptime_series) if uptime_series else 100.0
            avg_latency = sum(latency_series) / len(latency_series) if latency_series else 0.0
            avg_error_rate = sum(error_series) / len(error_series) if error_series else 0.0
            total_cost = sum(cost_series)

            uptime_trend = self._compute_trend_direction(uptime_series)
            latency_trend = self._compute_trend_direction(latency_series, invert=True)

            performance_score = (
                (avg_uptime / 100.0) * 0.40
                + max(0.0, 1.0 - avg_latency / 5000.0) * 0.35
                + max(0.0, 1.0 - avg_error_rate / 10.0) * 0.25
            )
            all_performance_scores.append(performance_score)

            per_vendor_trends[vendor_id] = {
                "vendor_name": vendor_name,
                "data_points": len(daily_metrics),
                "averages": {
                    "uptime_percent": round(avg_uptime, 3),
                    "p95_latency_ms": round(avg_latency, 2),
                    "error_rate_percent": round(avg_error_rate, 4),
                    "daily_cost_usd": round(total_cost / len(cost_series), 2) if cost_series else 0.0,
                },
                "totals": {
                    "total_cost_usd": round(total_cost, 2),
                },
                "trend_directions": {
                    "uptime": uptime_trend,
                    "latency": latency_trend,
                },
                "performance_score": round(performance_score, 4),
                "daily_series": {
                    "dates": [m.get("date") for m in daily_metrics],
                    "uptime_percent": uptime_series,
                    "p95_latency_ms": latency_series,
                    "error_rate_percent": error_series,
                    "cost_usd": cost_series,
                },
            }

        overall_index = round(
            sum(all_performance_scores) / len(all_performance_scores), 4
        ) if all_performance_scores else 0.0

        result: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "trend_period_days": effective_window,
            "vendor_count": len(per_vendor_trends),
            "per_vendor_trends": per_vendor_trends,
            "overall_performance_index": overall_index,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Vendor performance trends computed",
            tenant_id=str(tenant_id),
            vendor_count=len(per_vendor_trends),
            overall_performance_index=overall_index,
        )

        return result

    async def compute_cost_trends(
        self,
        tenant_id: uuid.UUID,
        cost_records: list[dict[str, Any]],
        window_days: int | None = None,
        group_by: str = "vendor",
    ) -> dict[str, Any]:
        """Compute cost trend visualization data across vendors.

        Args:
            tenant_id: Requesting tenant UUID.
            cost_records: List of cost record dicts with:
                - vendor_id: str
                - vendor_name: str
                - date: str (ISO date)
                - cost_usd: float
                - resource_type: str
            window_days: Days of history to include.
            group_by: Grouping dimension: "vendor" | "resource_type" | "day".

        Returns:
            Dict with cost_by_group, daily_total_series, cost_growth_rate,
            and highest_cost_group fields.
        """
        effective_window = window_days or self._default_trend_days
        cutoff_date = (datetime.now(tz=timezone.utc) - timedelta(days=effective_window)).date()

        logger.info(
            "Computing cost trends",
            tenant_id=str(tenant_id),
            window_days=effective_window,
            group_by=group_by,
            record_count=len(cost_records),
        )

        filtered: list[dict[str, Any]] = []
        for record in cost_records:
            try:
                from datetime import date
                record_date = date.fromisoformat(record.get("date", ""))
                if record_date >= cutoff_date:
                    filtered.append(record)
            except ValueError:
                continue

        cost_by_group: dict[str, float] = defaultdict(float)
        daily_totals: dict[str, float] = defaultdict(float)

        for record in filtered:
            cost = record.get("cost_usd", 0.0)
            date_key = record.get("date", "unknown")
            daily_totals[date_key] += cost

            if group_by == "vendor":
                group_key = record.get("vendor_name", record.get("vendor_id", "Unknown"))
            elif group_by == "resource_type":
                group_key = record.get("resource_type", "unknown")
            else:
                group_key = date_key
            cost_by_group[group_key] += cost

        sorted_dates = sorted(daily_totals.keys())
        daily_series = [
            {"date": d, "total_cost_usd": round(daily_totals[d], 2)}
            for d in sorted_dates
        ]

        cost_growth_rate = 0.0
        if len(daily_series) >= 2:
            first_half = sum(d["total_cost_usd"] for d in daily_series[:len(daily_series) // 2])
            second_half = sum(d["total_cost_usd"] for d in daily_series[len(daily_series) // 2:])
            if first_half > 0:
                cost_growth_rate = round((second_half - first_half) / first_half * 100.0, 2)

        sorted_groups = sorted(cost_by_group.items(), key=lambda item: item[1], reverse=True)
        highest_cost_group = sorted_groups[0][0] if sorted_groups else "none"

        result: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "window_days": effective_window,
            "group_by": group_by,
            "cost_by_group": {k: round(v, 2) for k, v in sorted_groups},
            "daily_total_series": daily_series,
            "total_cost_usd": round(sum(cost_by_group.values()), 2),
            "cost_growth_rate_percent": cost_growth_rate,
            "highest_cost_group": highest_cost_group,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Cost trends computed",
            tenant_id=str(tenant_id),
            total_cost_usd=result["total_cost_usd"],
            cost_growth_rate=cost_growth_rate,
        )

        return result

    async def compute_quality_trends(
        self,
        tenant_id: uuid.UUID,
        quality_metrics: list[dict[str, Any]],
        window_days: int | None = None,
    ) -> dict[str, Any]:
        """Compute quality trend data across vendors and model versions.

        Args:
            tenant_id: Requesting tenant UUID.
            quality_metrics: List of quality measurement dicts with:
                - vendor_id: str
                - vendor_name: str
                - model_name: str
                - date: str (ISO date)
                - bleu_score: float
                - semantic_similarity: float
                - composite_quality_score: float
            window_days: Days of history to include.

        Returns:
            Dict with per_vendor_quality_trends, quality_ranking, and
            overall_quality_index fields.
        """
        effective_window = window_days or self._default_trend_days
        cutoff_date = (datetime.now(tz=timezone.utc) - timedelta(days=effective_window)).date()

        logger.info(
            "Computing quality trends",
            tenant_id=str(tenant_id),
            window_days=effective_window,
            metric_count=len(quality_metrics),
        )

        filtered: list[dict[str, Any]] = []
        for metric in quality_metrics:
            try:
                from datetime import date
                metric_date = date.fromisoformat(metric.get("date", ""))
                if metric_date >= cutoff_date:
                    filtered.append(metric)
            except ValueError:
                continue

        by_vendor: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for metric in filtered:
            by_vendor[metric["vendor_id"]].append(metric)

        vendor_quality: dict[str, dict[str, Any]] = {}
        for vendor_id, metrics in by_vendor.items():
            metrics.sort(key=lambda m: m.get("date", ""))
            vendor_name = metrics[0].get("vendor_name", "Unknown") if metrics else "Unknown"
            model_name = metrics[0].get("model_name", "Unknown") if metrics else "Unknown"

            composite_scores = [m.get("composite_quality_score", 0.0) for m in metrics]
            bleu_scores = [m.get("bleu_score", 0.0) for m in metrics]
            semantic_scores = [m.get("semantic_similarity", 0.0) for m in metrics]

            avg_composite = sum(composite_scores) / len(composite_scores) if composite_scores else 0.0
            quality_trend = self._compute_trend_direction(composite_scores)

            vendor_quality[vendor_id] = {
                "vendor_name": vendor_name,
                "model_name": model_name,
                "avg_composite_quality_score": round(avg_composite, 4),
                "avg_bleu_score": round(sum(bleu_scores) / len(bleu_scores), 4) if bleu_scores else 0.0,
                "avg_semantic_similarity": round(sum(semantic_scores) / len(semantic_scores), 4) if semantic_scores else 0.0,
                "quality_trend": quality_trend,
                "data_points": len(metrics),
                "quality_series": {
                    "dates": [m.get("date") for m in metrics],
                    "composite_quality_score": composite_scores,
                },
            }

        quality_ranking = sorted(
            [
                {"vendor_id": vid, "vendor_name": data["vendor_name"], "avg_composite": data["avg_composite_quality_score"]}
                for vid, data in vendor_quality.items()
            ],
            key=lambda v: v["avg_composite"],
            reverse=True,
        )

        all_composites = [d["avg_composite_quality_score"] for d in vendor_quality.values()]
        overall_quality_index = round(sum(all_composites) / len(all_composites), 4) if all_composites else 0.0

        result: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "window_days": effective_window,
            "per_vendor_quality_trends": vendor_quality,
            "quality_ranking": quality_ranking,
            "overall_quality_index": overall_quality_index,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Quality trends computed",
            tenant_id=str(tenant_id),
            vendor_count=len(vendor_quality),
            overall_quality_index=overall_quality_index,
        )

        return result

    async def compute_usage_distribution(
        self,
        tenant_id: uuid.UUID,
        usage_records: list[dict[str, Any]],
        window_days: int | None = None,
    ) -> dict[str, Any]:
        """Compute token/request usage distribution across vendors.

        Args:
            tenant_id: Requesting tenant UUID.
            usage_records: List of usage dicts with:
                - vendor_id: str
                - vendor_name: str
                - date: str (ISO date)
                - token_count: int
                - request_count: int
                - cost_usd: float
            window_days: Days of history to include.

        Returns:
            Dict with usage_by_vendor, usage_percentages, dominant_vendor,
            and daily_total_token_series fields.
        """
        effective_window = window_days or self._default_trend_days
        cutoff_date = (datetime.now(tz=timezone.utc) - timedelta(days=effective_window)).date()

        logger.info(
            "Computing usage distribution",
            tenant_id=str(tenant_id),
            window_days=effective_window,
        )

        vendor_totals: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"vendor_name": "Unknown", "tokens": 0, "requests": 0, "cost_usd": 0.0}
        )
        daily_tokens: dict[str, int] = defaultdict(int)

        for record in usage_records:
            try:
                from datetime import date
                record_date = date.fromisoformat(record.get("date", ""))
                if record_date < cutoff_date:
                    continue
            except ValueError:
                continue

            vendor_id = record.get("vendor_id", "unknown")
            vendor_totals[vendor_id]["vendor_name"] = record.get("vendor_name", "Unknown")
            vendor_totals[vendor_id]["tokens"] += record.get("token_count", 0)
            vendor_totals[vendor_id]["requests"] += record.get("request_count", 0)
            vendor_totals[vendor_id]["cost_usd"] += record.get("cost_usd", 0.0)
            daily_tokens[record.get("date", "")] += record.get("token_count", 0)

        total_tokens = sum(d["tokens"] for d in vendor_totals.values())
        usage_by_vendor: list[dict[str, Any]] = []
        for vendor_id, data in vendor_totals.items():
            token_pct = round(data["tokens"] / total_tokens * 100.0, 1) if total_tokens > 0 else 0.0
            usage_by_vendor.append({
                "vendor_id": vendor_id,
                "vendor_name": data["vendor_name"],
                "total_tokens": data["tokens"],
                "total_requests": data["requests"],
                "total_cost_usd": round(data["cost_usd"], 2),
                "usage_percent": token_pct,
            })

        usage_by_vendor.sort(key=lambda v: v["total_tokens"], reverse=True)
        dominant_vendor = usage_by_vendor[0]["vendor_name"] if usage_by_vendor else "none"

        sorted_dates = sorted(daily_tokens.keys())
        daily_series = [
            {"date": d, "total_tokens": daily_tokens[d]}
            for d in sorted_dates
        ]

        result: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "window_days": effective_window,
            "usage_by_vendor": usage_by_vendor,
            "total_tokens": total_tokens,
            "dominant_vendor": dominant_vendor,
            "daily_total_token_series": daily_series,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Usage distribution computed",
            tenant_id=str(tenant_id),
            vendor_count=len(usage_by_vendor),
            total_tokens=total_tokens,
        )

        return result

    async def generate_vendor_health_summary(
        self,
        tenant_id: uuid.UUID,
        vendor_health_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate a vendor health summary for the dashboard header.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_health_data: List of current vendor health dicts from SLAMonitor.

        Returns:
            Dict with overall_fleet_health, healthy_count, degraded_count,
            critical_count, health_by_vendor, and top_risk_vendor fields.
        """
        logger.info(
            "Generating vendor health summary",
            tenant_id=str(tenant_id),
            vendor_count=len(vendor_health_data),
        )

        healthy: list[dict[str, Any]] = []
        degraded: list[dict[str, Any]] = []
        critical: list[dict[str, Any]] = []

        for vendor in vendor_health_data:
            compliance_percent = vendor.get("compliance_percent", 100.0)
            circuit_state = vendor.get("state", "closed")

            if circuit_state == "open" or compliance_percent < 90.0:
                critical.append(vendor)
            elif circuit_state == "half_open" or compliance_percent < 99.0:
                degraded.append(vendor)
            else:
                healthy.append(vendor)

        fleet_health = (
            "critical" if critical
            else "degraded" if degraded
            else "healthy"
        )

        top_risk_vendor: dict[str, Any] | None = None
        if critical:
            top_risk_vendor = min(critical, key=lambda v: v.get("compliance_percent", 0.0))
        elif degraded:
            top_risk_vendor = min(degraded, key=lambda v: v.get("compliance_percent", 100.0))

        result: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "overall_fleet_health": fleet_health,
            "healthy_count": len(healthy),
            "degraded_count": len(degraded),
            "critical_count": len(critical),
            "total_vendor_count": len(vendor_health_data),
            "health_by_vendor": vendor_health_data,
            "top_risk_vendor": top_risk_vendor,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Vendor health summary generated",
            tenant_id=str(tenant_id),
            fleet_health=fleet_health,
            healthy_count=len(healthy),
            critical_count=len(critical),
        )

        return result

    async def export_executive_dashboard(
        self,
        tenant_id: uuid.UUID,
        performance_trends: dict[str, Any],
        cost_trends: dict[str, Any],
        quality_trends: dict[str, Any],
        usage_distribution: dict[str, Any],
        health_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Compose a complete executive dashboard JSON export.

        Args:
            tenant_id: Requesting tenant UUID.
            performance_trends: Output from compute_vendor_performance_trends.
            cost_trends: Output from compute_cost_trends.
            quality_trends: Output from compute_quality_trends.
            usage_distribution: Output from compute_usage_distribution.
            health_summary: Output from generate_vendor_health_summary.

        Returns:
            Complete executive dashboard dict ready for API response or storage.
        """
        logger.info(
            "Exporting executive dashboard",
            tenant_id=str(tenant_id),
        )

        # Compute composite vendor rankings
        vendor_scores: dict[str, float] = {}
        for vendor_id, perf in performance_trends.get("per_vendor_trends", {}).items():
            score = perf.get("performance_score", 0.0)
            vendor_scores[vendor_id] = score

        ranked_vendors = sorted(
            [
                {
                    "vendor_id": vid,
                    "vendor_name": performance_trends["per_vendor_trends"].get(vid, {}).get("vendor_name", "Unknown"),
                    "performance_score": score,
                }
                for vid, score in vendor_scores.items()
            ],
            key=lambda v: v["performance_score"],
            reverse=True,
        )

        dashboard: dict[str, Any] = {
            "dashboard_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "executive_summary": {
                "fleet_health": health_summary.get("overall_fleet_health"),
                "total_vendors": health_summary.get("total_vendor_count"),
                "healthy_vendors": health_summary.get("healthy_count"),
                "total_cost_usd": cost_trends.get("total_cost_usd"),
                "overall_performance_index": performance_trends.get("overall_performance_index"),
                "overall_quality_index": quality_trends.get("overall_quality_index"),
                "dominant_vendor": usage_distribution.get("dominant_vendor"),
                "top_risk_vendor": health_summary.get("top_risk_vendor", {}).get("vendor_name") if health_summary.get("top_risk_vendor") else None,
            },
            "vendor_rankings": ranked_vendors,
            "performance_section": performance_trends,
            "cost_section": cost_trends,
            "quality_section": quality_trends,
            "usage_section": usage_distribution,
            "health_section": health_summary,
        }

        logger.info(
            "Executive dashboard exported",
            tenant_id=str(tenant_id),
            vendor_count=len(ranked_vendors),
        )

        return dashboard

    @staticmethod
    def _compute_trend_direction(
        series: list[float],
        invert: bool = False,
    ) -> str:
        """Compute trend direction from a numeric series.

        Args:
            series: List of numeric values in chronological order.
            invert: If True, increasing values are considered negative (e.g. latency).

        Returns:
            Trend string: "improving" | "stable" | "degrading".
        """
        if len(series) < 3:
            return "stable"

        first_half = sum(series[:len(series) // 2]) / (len(series) // 2)
        second_half = sum(series[len(series) // 2:]) / (len(series) - len(series) // 2)

        if first_half == 0:
            return "stable"

        change_pct = (second_half - first_half) / abs(first_half) * 100.0

        if invert:
            change_pct = -change_pct

        if change_pct > 2.0:
            return "improving"
        if change_pct < -2.0:
            return "degrading"
        return "stable"
