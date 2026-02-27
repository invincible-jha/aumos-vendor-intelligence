"""ArbitrageDetector adapter for AI vendor cost optimization and arbitrage detection.

Identifies pricing disparities across vendors for equivalent model capabilities,
detects spot pricing opportunities, volume discount thresholds, and generates
cost-optimised multi-vendor allocation strategies.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Pareto front dominance tolerance
PARETO_TOLERANCE: float = 0.02

# Minimum savings threshold to surface an arbitrage opportunity (USD/month)
MIN_SAVINGS_THRESHOLD_USD: float = 50.0


class ArbitrageDetector:
    """Cross-vendor AI pricing arbitrage and cost optimisation engine.

    Analyses pricing data from multiple vendors providing equivalent model
    capabilities to identify cost arbitrage opportunities, Pareto-optimal
    allocations, spot pricing windows, volume discount thresholds, and
    multi-vendor cost splits. Produces actionable savings reports.
    """

    def __init__(
        self,
        min_savings_threshold_usd: float = MIN_SAVINGS_THRESHOLD_USD,
        pareto_tolerance: float = PARETO_TOLERANCE,
    ) -> None:
        """Initialise the ArbitrageDetector.

        Args:
            min_savings_threshold_usd: Minimum monthly savings to surface
                an arbitrage opportunity in reports.
            pareto_tolerance: Fractional tolerance for Pareto dominance checks
                (e.g., 0.02 = 2% margin to handle pricing rounding).
        """
        self._min_savings = min_savings_threshold_usd
        self._pareto_tolerance = pareto_tolerance

    async def compare_pricing_for_equivalent_models(
        self,
        tenant_id: uuid.UUID,
        capability_tier: str,
        vendor_pricing: list[dict[str, Any]],
        monthly_token_volume: int,
        input_output_ratio: float = 0.25,
    ) -> list[dict[str, Any]]:
        """Compare per-token pricing across vendors for equivalent model capabilities.

        Args:
            tenant_id: Requesting tenant UUID.
            capability_tier: Model capability tier being compared
                (e.g., "premium", "standard", "economy").
            vendor_pricing: List of vendor pricing dicts, each with:
                - vendor_id: str
                - vendor_name: str
                - model_name: str
                - input_cost_per_million_usd: float
                - output_cost_per_million_usd: float
                - quality_score: float (0.0–1.0)
            monthly_token_volume: Total monthly tokens consumed (input + output).
            input_output_ratio: Fraction that is input tokens (default 0.25 = 1:3 ratio).

        Returns:
            List of pricing comparison dicts sorted by monthly_cost_usd ascending,
            each with vendor details, cost breakdown, and savings_vs_most_expensive.
        """
        logger.info(
            "Comparing pricing for equivalent models",
            tenant_id=str(tenant_id),
            capability_tier=capability_tier,
            vendor_count=len(vendor_pricing),
            monthly_token_volume=monthly_token_volume,
        )

        input_tokens = int(monthly_token_volume * input_output_ratio)
        output_tokens = monthly_token_volume - input_tokens

        comparisons: list[dict[str, Any]] = []
        for vendor in vendor_pricing:
            input_cost = vendor.get("input_cost_per_million_usd", 0.0)
            output_cost = vendor.get("output_cost_per_million_usd", 0.0)

            monthly_input_cost = (input_tokens / 1_000_000) * input_cost
            monthly_output_cost = (output_tokens / 1_000_000) * output_cost
            monthly_total = monthly_input_cost + monthly_output_cost

            comparisons.append({
                "vendor_id": vendor.get("vendor_id"),
                "vendor_name": vendor.get("vendor_name", "Unknown"),
                "model_name": vendor.get("model_name", "Unknown"),
                "capability_tier": capability_tier,
                "input_cost_per_million_usd": input_cost,
                "output_cost_per_million_usd": output_cost,
                "monthly_input_cost_usd": round(monthly_input_cost, 2),
                "monthly_output_cost_usd": round(monthly_output_cost, 2),
                "monthly_total_cost_usd": round(monthly_total, 2),
                "quality_score": vendor.get("quality_score", 0.0),
            })

        comparisons.sort(key=lambda c: c["monthly_total_cost_usd"])
        most_expensive = comparisons[-1]["monthly_total_cost_usd"] if comparisons else 0.0

        for comparison in comparisons:
            comparison["savings_vs_most_expensive_usd"] = round(
                most_expensive - comparison["monthly_total_cost_usd"], 2
            )

        logger.info(
            "Pricing comparison completed",
            tenant_id=str(tenant_id),
            capability_tier=capability_tier,
            cheapest_vendor=comparisons[0]["vendor_name"] if comparisons else "none",
            max_savings_usd=comparisons[0]["savings_vs_most_expensive_usd"] if comparisons else 0.0,
        )

        return comparisons

    async def compute_cost_quality_pareto(
        self,
        tenant_id: uuid.UUID,
        vendor_pricing: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute Pareto-optimal vendors on the cost vs quality frontier.

        A vendor is Pareto-optimal if no other vendor offers both lower cost
        AND equal or higher quality (within the configured tolerance).

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_pricing: List of vendor dicts with monthly_total_cost_usd
                and quality_score fields.

        Returns:
            Dict with pareto_front (list of Pareto-optimal vendors),
            dominated_vendors (list of dominated options),
            recommended_vendor (best balance point), and frontier_summary.
        """
        logger.info(
            "Computing cost-quality Pareto front",
            tenant_id=str(tenant_id),
            vendor_count=len(vendor_pricing),
        )

        pareto_front: list[dict[str, Any]] = []
        dominated: list[dict[str, Any]] = []

        for candidate in vendor_pricing:
            is_dominated = False
            for other in vendor_pricing:
                if other is candidate:
                    continue
                # Other dominates candidate if it has lower/equal cost AND higher/equal quality
                cost_dominated = (
                    other["monthly_total_cost_usd"]
                    <= candidate["monthly_total_cost_usd"] * (1 + self._pareto_tolerance)
                )
                quality_dominated = (
                    other["quality_score"]
                    >= candidate["quality_score"] * (1 - self._pareto_tolerance)
                )
                strictly_better = (
                    other["monthly_total_cost_usd"] < candidate["monthly_total_cost_usd"]
                    or other["quality_score"] > candidate["quality_score"]
                )
                if cost_dominated and quality_dominated and strictly_better:
                    is_dominated = True
                    break

            if is_dominated:
                dominated.append({**candidate, "dominated": True})
            else:
                pareto_front.append({**candidate, "pareto_optimal": True})

        # Recommend the Pareto-optimal vendor with best cost-quality balance
        # Using F-score: harmonic mean of normalised cost-efficiency and quality
        recommended: dict[str, Any] | None = None
        if pareto_front:
            max_quality = max(v["quality_score"] for v in pareto_front)
            max_cost = max(v["monthly_total_cost_usd"] for v in pareto_front) or 1.0

            for vendor in pareto_front:
                quality_norm = vendor["quality_score"] / max_quality if max_quality > 0 else 0.0
                cost_efficiency = 1.0 - (vendor["monthly_total_cost_usd"] / max_cost)
                if quality_norm > 0 and cost_efficiency > 0:
                    f_score = 2 * (quality_norm * cost_efficiency) / (quality_norm + cost_efficiency)
                else:
                    f_score = 0.0
                vendor["balance_score"] = round(f_score, 4)

            pareto_front.sort(key=lambda v: v.get("balance_score", 0.0), reverse=True)
            recommended = pareto_front[0]

        result: dict[str, Any] = {
            "pareto_analysis_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "pareto_front": pareto_front,
            "dominated_vendors": dominated,
            "pareto_front_size": len(pareto_front),
            "dominated_count": len(dominated),
            "recommended_vendor": recommended,
            "frontier_summary": (
                f"{len(pareto_front)} Pareto-optimal option(s) identified. "
                f"{len(dominated)} vendor(s) dominated on cost and quality. "
                + (
                    f"Recommended: {recommended['vendor_name']} "
                    f"(balance score: {recommended.get('balance_score', 0):.3f})."
                    if recommended else "No recommendation available."
                )
            ),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Pareto analysis completed",
            tenant_id=str(tenant_id),
            pareto_front_size=len(pareto_front),
            recommended=recommended.get("vendor_name") if recommended else "none",
        )

        return result

    async def detect_spot_pricing_opportunities(
        self,
        tenant_id: uuid.UUID,
        vendor_spot_data: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify spot pricing windows where vendors offer discounted rates.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_spot_data: List of spot pricing dicts, each with:
                - vendor_id: str
                - vendor_name: str
                - standard_price_per_million_usd: float
                - spot_price_per_million_usd: float
                - available_from: str (ISO datetime)
                - available_until: str (ISO datetime)
                - max_tokens: int (capacity available at spot price)
                - reliability_score: float (0.0–1.0, spot reliability)

        Returns:
            List of opportunity dicts with discount_percent, savings_per_million,
            and recommendation fields, sorted by discount_percent descending.
        """
        logger.info(
            "Detecting spot pricing opportunities",
            tenant_id=str(tenant_id),
            vendor_count=len(vendor_spot_data),
        )

        opportunities: list[dict[str, Any]] = []
        for spot in vendor_spot_data:
            standard = spot.get("standard_price_per_million_usd", 0.0)
            spot_price = spot.get("spot_price_per_million_usd", 0.0)

            if standard <= 0 or spot_price <= 0:
                continue

            discount_fraction = (standard - spot_price) / standard
            discount_percent = round(discount_fraction * 100, 1)

            if discount_percent <= 0:
                continue

            savings_per_million = round(standard - spot_price, 4)
            reliability = spot.get("reliability_score", 0.5)

            # Risk-adjusted savings: higher reliability = higher effective savings
            risk_adjusted_savings = savings_per_million * reliability

            opportunities.append({
                "vendor_id": spot.get("vendor_id"),
                "vendor_name": spot.get("vendor_name", "Unknown"),
                "standard_price_per_million_usd": standard,
                "spot_price_per_million_usd": spot_price,
                "discount_percent": discount_percent,
                "savings_per_million_usd": savings_per_million,
                "risk_adjusted_savings_per_million_usd": round(risk_adjusted_savings, 4),
                "available_from": spot.get("available_from"),
                "available_until": spot.get("available_until"),
                "max_tokens": spot.get("max_tokens"),
                "reliability_score": reliability,
                "recommendation": (
                    "High value" if discount_percent >= 30 and reliability >= 0.8
                    else "Moderate value" if discount_percent >= 15
                    else "Low value — reliability risk may offset savings"
                ),
            })

        opportunities.sort(key=lambda o: o["discount_percent"], reverse=True)

        logger.info(
            "Spot pricing opportunities identified",
            tenant_id=str(tenant_id),
            opportunity_count=len(opportunities),
        )

        return opportunities

    async def identify_volume_discounts(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_name: str,
        pricing_tiers: list[dict[str, Any]],
        current_monthly_tokens: int,
    ) -> dict[str, Any]:
        """Identify volume discount thresholds and savings potential.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor being analyzed.
            vendor_name: Vendor name for reporting.
            pricing_tiers: List of pricing tier dicts with:
                - min_tokens: int (minimum tokens for this tier)
                - max_tokens: int | None (None = unlimited)
                - price_per_million_usd: float
                - tier_name: str
            current_monthly_tokens: Current monthly token consumption.

        Returns:
            Dict with current_tier, next_tier, tokens_to_next_tier,
            savings_at_next_tier_usd, and all tier details.
        """
        logger.info(
            "Identifying volume discounts",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
            current_monthly_tokens=current_monthly_tokens,
        )

        sorted_tiers = sorted(pricing_tiers, key=lambda t: t.get("min_tokens", 0))

        current_tier: dict[str, Any] | None = None
        next_tier: dict[str, Any] | None = None

        for tier in sorted_tiers:
            min_tokens = tier.get("min_tokens", 0)
            max_tokens = tier.get("max_tokens")

            in_tier = current_monthly_tokens >= min_tokens and (
                max_tokens is None or current_monthly_tokens < max_tokens
            )
            if in_tier:
                current_tier = tier
                break

        if current_tier is not None:
            current_index = sorted_tiers.index(current_tier)
            if current_index < len(sorted_tiers) - 1:
                next_tier = sorted_tiers[current_index + 1]

        tokens_to_next: int | None = None
        savings_at_next: float | None = None

        if next_tier is not None and current_tier is not None:
            tokens_to_next = next_tier.get("min_tokens", 0) - current_monthly_tokens
            current_price = current_tier.get("price_per_million_usd", 0.0)
            next_price = next_tier.get("price_per_million_usd", 0.0)
            tokens_at_next_level = next_tier.get("min_tokens", current_monthly_tokens)
            savings_at_next = round(
                (current_price - next_price) * (tokens_at_next_level / 1_000_000), 2
            )

        result: dict[str, Any] = {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "current_monthly_tokens": current_monthly_tokens,
            "current_tier": current_tier,
            "next_tier": next_tier,
            "tokens_to_next_tier": tokens_to_next,
            "monthly_savings_at_next_tier_usd": savings_at_next,
            "all_tiers": sorted_tiers,
            "analyzed_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Volume discount analysis completed",
            vendor_id=vendor_id,
            current_tier_name=current_tier.get("tier_name") if current_tier else "unknown",
            tokens_to_next_tier=tokens_to_next,
        )

        return result

    async def optimize_multi_vendor_allocation(
        self,
        tenant_id: uuid.UUID,
        vendors: list[dict[str, Any]],
        total_monthly_tokens: int,
        quality_minimum: float = 0.80,
        max_vendors: int = 3,
    ) -> dict[str, Any]:
        """Compute optimal token distribution across multiple vendors to minimise cost.

        Args:
            tenant_id: Requesting tenant UUID.
            vendors: List of vendor capability dicts with:
                - vendor_id: str
                - vendor_name: str
                - price_per_million_usd: float
                - quality_score: float (0.0–1.0)
                - max_monthly_tokens: int | None (capacity limit)
            total_monthly_tokens: Total tokens to allocate across vendors.
            quality_minimum: Minimum acceptable quality score for any vendor.
            max_vendors: Maximum vendors to include in the allocation.

        Returns:
            Dict with optimal_allocation (per-vendor token and cost breakdown),
            total_monthly_cost_usd, weighted_quality_score, and savings_vs_single_vendor.
        """
        logger.info(
            "Optimizing multi-vendor token allocation",
            tenant_id=str(tenant_id),
            total_monthly_tokens=total_monthly_tokens,
            vendor_count=len(vendors),
        )

        eligible = [
            v for v in vendors
            if v.get("quality_score", 0.0) >= quality_minimum
        ]
        eligible.sort(key=lambda v: v.get("price_per_million_usd", 999.0))
        eligible = eligible[:max_vendors]

        if not eligible:
            return {
                "error": "No vendors meet the quality minimum requirement.",
                "quality_minimum": quality_minimum,
                "vendors_evaluated": len(vendors),
            }

        # Greedy allocation: assign tokens to cheapest vendor up to its capacity
        allocation: list[dict[str, Any]] = []
        remaining_tokens = total_monthly_tokens

        for vendor in eligible:
            if remaining_tokens <= 0:
                break
            capacity = vendor.get("max_monthly_tokens")
            allocated = min(remaining_tokens, capacity) if capacity else remaining_tokens
            monthly_cost = (allocated / 1_000_000) * vendor.get("price_per_million_usd", 0.0)

            allocation.append({
                "vendor_id": vendor.get("vendor_id"),
                "vendor_name": vendor.get("vendor_name", "Unknown"),
                "allocated_tokens": allocated,
                "allocation_percent": round(100 * allocated / total_monthly_tokens, 1),
                "price_per_million_usd": vendor.get("price_per_million_usd"),
                "monthly_cost_usd": round(monthly_cost, 2),
                "quality_score": vendor.get("quality_score"),
            })
            remaining_tokens -= allocated

        if remaining_tokens > 0:
            # Overflow: add to the last vendor in allocation if present
            if allocation:
                overflow_vendor = allocation[-1]
                extra_cost = (remaining_tokens / 1_000_000) * overflow_vendor["price_per_million_usd"]
                overflow_vendor["allocated_tokens"] += remaining_tokens
                overflow_vendor["monthly_cost_usd"] = round(
                    overflow_vendor["monthly_cost_usd"] + extra_cost, 2
                )

        total_cost = sum(a["monthly_cost_usd"] for a in allocation)
        weighted_quality = sum(
            a["quality_score"] * (a["allocated_tokens"] / total_monthly_tokens)
            for a in allocation
        )

        # Single vendor cost baseline (most expensive eligible vendor)
        most_expensive_price = max(
            v.get("price_per_million_usd", 0.0) for v in eligible
        )
        single_vendor_cost = (total_monthly_tokens / 1_000_000) * most_expensive_price
        savings_vs_single = round(single_vendor_cost - total_cost, 2)

        result: dict[str, Any] = {
            "optimization_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "optimal_allocation": allocation,
            "total_monthly_tokens": total_monthly_tokens,
            "total_monthly_cost_usd": round(total_cost, 2),
            "weighted_quality_score": round(weighted_quality, 4),
            "savings_vs_single_vendor_usd": max(0.0, savings_vs_single),
            "vendors_in_allocation": len(allocation),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Multi-vendor allocation optimized",
            tenant_id=str(tenant_id),
            total_monthly_cost_usd=result["total_monthly_cost_usd"],
            savings_vs_single_usd=result["savings_vs_single_vendor_usd"],
            vendors_in_allocation=len(allocation),
        )

        return result

    async def generate_savings_report(
        self,
        tenant_id: uuid.UUID,
        current_vendor_id: str,
        current_vendor_name: str,
        current_monthly_cost_usd: float,
        alternative_analyses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate a structured cost savings report comparing current vs alternatives.

        Args:
            tenant_id: Requesting tenant UUID.
            current_vendor_id: Current vendor identifier.
            current_vendor_name: Current vendor name.
            current_monthly_cost_usd: Current monthly spend.
            alternative_analyses: List of alternative vendor analysis dicts
                with monthly_total_cost_usd and vendor_name fields.

        Returns:
            Dict with savings opportunities sorted by monthly_savings_usd descending,
            total_addressable_savings, best_alternative, and executive summary.
        """
        logger.info(
            "Generating savings report",
            tenant_id=str(tenant_id),
            current_vendor=current_vendor_name,
            current_monthly_cost_usd=current_monthly_cost_usd,
        )

        opportunities: list[dict[str, Any]] = []
        for alternative in alternative_analyses:
            alt_cost = alternative.get("monthly_total_cost_usd", 0.0)
            monthly_savings = current_monthly_cost_usd - alt_cost
            annual_savings = monthly_savings * 12

            if monthly_savings < self._min_savings:
                continue

            opportunities.append({
                "alternative_vendor_id": alternative.get("vendor_id"),
                "alternative_vendor_name": alternative.get("vendor_name", "Unknown"),
                "alternative_monthly_cost_usd": round(alt_cost, 2),
                "monthly_savings_usd": round(monthly_savings, 2),
                "annual_savings_usd": round(annual_savings, 2),
                "savings_percent": round(monthly_savings / current_monthly_cost_usd * 100, 1)
                if current_monthly_cost_usd > 0 else 0.0,
                "quality_score": alternative.get("quality_score"),
                "switching_complexity": alternative.get("switching_complexity", "medium"),
            })

        opportunities.sort(key=lambda o: o["monthly_savings_usd"], reverse=True)
        best_alternative = opportunities[0] if opportunities else None
        total_addressable = sum(o["monthly_savings_usd"] for o in opportunities[:3])

        result: dict[str, Any] = {
            "report_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "current_vendor_id": current_vendor_id,
            "current_vendor_name": current_vendor_name,
            "current_monthly_cost_usd": current_monthly_cost_usd,
            "savings_opportunities": opportunities,
            "total_addressable_monthly_savings_usd": round(total_addressable, 2),
            "best_alternative": best_alternative,
            "executive_summary": (
                f"Identified {len(opportunities)} cost-saving alternative(s) to {current_vendor_name}. "
                + (
                    f"Best option: {best_alternative['alternative_vendor_name']} "
                    f"saves ${best_alternative['monthly_savings_usd']:,.2f}/month "
                    f"({best_alternative['savings_percent']:.1f}%)."
                    if best_alternative else "No qualifying alternatives identified."
                )
            ),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Savings report generated",
            tenant_id=str(tenant_id),
            opportunity_count=len(opportunities),
            best_monthly_savings=best_alternative["monthly_savings_usd"] if best_alternative else 0.0,
        )

        return result
