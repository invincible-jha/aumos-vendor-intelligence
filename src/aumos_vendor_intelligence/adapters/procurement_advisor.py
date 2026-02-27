"""ProcurementAdvisor adapter for AI vendor selection and procurement guidance.

Matches requirements to vendors using multi-criteria scoring, generates
vendor shortlists, prepares RFP templates, identifies negotiation leverage
points, and produces vendor comparison matrices for procurement decisions.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Default scoring weights for multi-criteria vendor evaluation
DEFAULT_SCORING_WEIGHTS: dict[str, float] = {
    "cost": 0.25,
    "quality": 0.25,
    "reliability": 0.20,
    "compliance": 0.20,
    "support": 0.10,
}

# Compliance certifications recognised by AumOS procurement policy
RECOGNISED_CERTIFICATIONS: frozenset[str] = frozenset({
    "soc2_type2",
    "iso27001",
    "gdpr",
    "hipaa",
    "pci_dss",
    "fedramp",
    "csa_star",
    "iso27017",
    "iso27018",
})


class ProcurementAdvisor:
    """AI vendor procurement advisory and recommendation engine.

    Accepts structured procurement requirements and scores candidate vendors
    against a five-dimension weighted model (cost, quality, reliability,
    compliance, support). Produces shortlists, RFP templates, negotiation
    playbooks, and structured comparison matrices.
    """

    def __init__(
        self,
        scoring_weights: dict[str, float] | None = None,
        minimum_shortlist_score: float = 0.60,
        maximum_shortlist_size: int = 5,
    ) -> None:
        """Initialise the ProcurementAdvisor.

        Args:
            scoring_weights: Weight dict with keys: cost, quality, reliability,
                compliance, support. Must sum to 1.0.
            minimum_shortlist_score: Minimum composite score to appear on shortlist.
            maximum_shortlist_size: Maximum number of vendors on the shortlist.
        """
        weights = scoring_weights or DEFAULT_SCORING_WEIGHTS
        weight_sum = sum(weights.values())
        if abs(weight_sum - 1.0) > 0.01:
            # Normalise weights silently
            weights = {k: v / weight_sum for k, v in weights.items()}
        self._weights = weights
        self._min_score = minimum_shortlist_score
        self._max_shortlist = maximum_shortlist_size

    async def match_requirements_to_vendors(
        self,
        tenant_id: uuid.UUID,
        requirements: dict[str, Any],
        candidate_vendors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Match procurement requirements to candidate vendors.

        Args:
            tenant_id: Requesting tenant UUID.
            requirements: Dict specifying procurement needs:
                - use_case: str (e.g., "text_generation", "embeddings")
                - required_certifications: list[str]
                - max_latency_ms: int | None
                - max_monthly_budget_usd: float | None
                - min_uptime_percent: float | None
                - data_residency_regions: list[str]
                - required_features: list[str]
            candidate_vendors: List of vendor capability dicts with:
                - vendor_id: str
                - vendor_name: str
                - supported_use_cases: list[str]
                - certifications: list[str]
                - avg_latency_ms: float
                - monthly_cost_usd: float
                - uptime_percent: float
                - data_residency_regions: list[str]
                - features: list[str]

        Returns:
            List of matching vendor dicts with match_score and unmet_requirements
            fields, sorted by match_score descending.
        """
        logger.info(
            "Matching requirements to vendors",
            tenant_id=str(tenant_id),
            candidate_count=len(candidate_vendors),
            use_case=requirements.get("use_case"),
        )

        use_case = requirements.get("use_case", "")
        required_certs = set(requirements.get("required_certifications", []))
        max_latency = requirements.get("max_latency_ms")
        max_budget = requirements.get("max_monthly_budget_usd")
        min_uptime = requirements.get("min_uptime_percent", 99.0)
        required_regions = set(requirements.get("data_residency_regions", []))
        required_features = set(requirements.get("required_features", []))

        matched: list[dict[str, Any]] = []

        for vendor in candidate_vendors:
            unmet: list[str] = []

            supported_uses = set(vendor.get("supported_use_cases", []))
            if use_case and use_case not in supported_uses:
                unmet.append(f"Unsupported use case: {use_case}")

            vendor_certs = set(vendor.get("certifications", []))
            missing_certs = required_certs - vendor_certs
            if missing_certs:
                unmet.append(f"Missing certifications: {', '.join(missing_certs)}")

            vendor_latency = vendor.get("avg_latency_ms", 0.0)
            if max_latency and vendor_latency > max_latency:
                unmet.append(f"Latency {vendor_latency:.0f}ms exceeds max {max_latency}ms")

            vendor_cost = vendor.get("monthly_cost_usd", 0.0)
            if max_budget and vendor_cost > max_budget:
                unmet.append(f"Cost ${vendor_cost:.0f}/mo exceeds budget ${max_budget:.0f}/mo")

            vendor_uptime = vendor.get("uptime_percent", 0.0)
            if vendor_uptime < min_uptime:
                unmet.append(f"Uptime {vendor_uptime:.2f}% below minimum {min_uptime:.2f}%")

            vendor_regions = set(vendor.get("data_residency_regions", []))
            missing_regions = required_regions - vendor_regions
            if missing_regions:
                unmet.append(f"Missing data residency: {', '.join(missing_regions)}")

            vendor_features = set(vendor.get("features", []))
            missing_features = required_features - vendor_features
            if missing_features:
                unmet.append(f"Missing features: {', '.join(missing_features)}")

            hard_block_count = sum(
                1 for u in unmet
                if "Unsupported use case" in u or "Missing certifications" in u
            )
            requirements_met = len(unmet) == 0
            partial_match = hard_block_count == 0 and len(unmet) > 0

            if hard_block_count == 0:
                # Score partial and full matches
                cert_coverage = len(vendor_certs & required_certs) / len(required_certs) if required_certs else 1.0
                latency_score = max(0.0, 1.0 - (vendor_latency / max_latency)) if max_latency else 1.0
                cost_score = max(0.0, 1.0 - (vendor_cost / max_budget)) if max_budget else 1.0
                uptime_score = min(1.0, vendor_uptime / 100.0)
                feature_coverage = (
                    len(vendor_features & required_features) / len(required_features)
                    if required_features else 1.0
                )
                match_score = round(
                    (cert_coverage + latency_score + cost_score + uptime_score + feature_coverage) / 5.0,
                    4,
                )

                matched.append({
                    **vendor,
                    "match_score": match_score,
                    "requirements_met": requirements_met,
                    "partial_match": partial_match,
                    "unmet_requirements": unmet,
                })

        matched.sort(key=lambda v: v["match_score"], reverse=True)

        logger.info(
            "Requirement matching completed",
            tenant_id=str(tenant_id),
            matched_count=len(matched),
            full_match_count=sum(1 for v in matched if v["requirements_met"]),
        )

        return matched

    async def score_vendors_multi_criteria(
        self,
        tenant_id: uuid.UUID,
        vendor_data: list[dict[str, Any]],
        custom_weights: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Score vendors using the weighted multi-criteria model.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_data: List of vendor dicts with normalised dimension scores
                (0.0–1.0) for: cost_score, quality_score, reliability_score,
                compliance_score, support_score.
            custom_weights: Optional weight overrides for this evaluation.

        Returns:
            List of scored vendor dicts with composite_score, dimension_scores,
            and rank fields, sorted by composite_score descending.
        """
        weights = custom_weights or self._weights

        logger.info(
            "Scoring vendors with multi-criteria model",
            tenant_id=str(tenant_id),
            vendor_count=len(vendor_data),
        )

        scored: list[dict[str, Any]] = []
        for vendor in vendor_data:
            dimension_scores = {
                "cost": float(vendor.get("cost_score", 0.0)),
                "quality": float(vendor.get("quality_score", 0.0)),
                "reliability": float(vendor.get("reliability_score", 0.0)),
                "compliance": float(vendor.get("compliance_score", 0.0)),
                "support": float(vendor.get("support_score", 0.0)),
            }

            composite = sum(
                score * weights.get(dimension, 0.0)
                for dimension, score in dimension_scores.items()
            )

            scored.append({
                **vendor,
                "dimension_scores": dimension_scores,
                "composite_score": round(composite, 4),
                "weights_used": weights,
            })

        scored.sort(key=lambda v: v["composite_score"], reverse=True)
        for rank, vendor in enumerate(scored, start=1):
            vendor["rank"] = rank

        logger.info(
            "Multi-criteria scoring completed",
            tenant_id=str(tenant_id),
            top_vendor=scored[0]["vendor_name"] if scored else "none",
            top_score=scored[0]["composite_score"] if scored else 0.0,
        )

        return scored

    async def generate_shortlist(
        self,
        tenant_id: uuid.UUID,
        scored_vendors: list[dict[str, Any]],
        shortlist_rationale: str | None = None,
    ) -> dict[str, Any]:
        """Generate a vendor shortlist from scored candidates.

        Args:
            tenant_id: Requesting tenant UUID.
            scored_vendors: List of scored vendor dicts with composite_score.
            shortlist_rationale: Optional text explaining shortlisting criteria.

        Returns:
            Dict with shortlisted vendors, excluded vendors, and selection
            reasoning for each shortlisted vendor.
        """
        logger.info(
            "Generating vendor shortlist",
            tenant_id=str(tenant_id),
            candidate_count=len(scored_vendors),
        )

        shortlisted = [
            v for v in scored_vendors
            if v.get("composite_score", 0.0) >= self._min_score
        ][:self._max_shortlist]

        excluded = [
            v for v in scored_vendors
            if v not in shortlisted
        ]

        for vendor in shortlisted:
            dim_scores = vendor.get("dimension_scores", {})
            strengths = [
                dim for dim, score in dim_scores.items()
                if score >= 0.75
            ]
            weaknesses = [
                dim for dim, score in dim_scores.items()
                if score < 0.50
            ]
            vendor["shortlist_reasoning"] = (
                f"Composite score {vendor['composite_score']:.3f} "
                f"({', '.join(strengths)} are strengths"
                + (f"; {', '.join(weaknesses)} are areas of concern" if weaknesses else "")
                + ")."
            )

        result: dict[str, Any] = {
            "shortlist_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "shortlisted_vendors": shortlisted,
            "excluded_vendors": [
                {"vendor_id": v.get("vendor_id"), "vendor_name": v.get("vendor_name"),
                 "composite_score": v.get("composite_score"), "exclusion_reason": "Below score threshold"}
                for v in excluded
            ],
            "shortlist_size": len(shortlisted),
            "selection_criteria": {
                "minimum_score": self._min_score,
                "maximum_shortlist_size": self._max_shortlist,
                "rationale": shortlist_rationale or "Default multi-criteria scoring.",
            },
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Vendor shortlist generated",
            tenant_id=str(tenant_id),
            shortlist_size=len(shortlisted),
            excluded_count=len(excluded),
        )

        return result

    async def prepare_rfp_template(
        self,
        tenant_id: uuid.UUID,
        procurement_requirements: dict[str, Any],
        shortlisted_vendors: list[dict[str, Any]],
        issuing_organization: str,
    ) -> dict[str, Any]:
        """Prepare an RFP template tailored to procurement requirements.

        Args:
            tenant_id: Requesting tenant UUID.
            procurement_requirements: Requirements dict (use_case, budget, etc.).
            shortlisted_vendors: Vendors to address in the RFP.
            issuing_organization: Name of the organisation issuing the RFP.

        Returns:
            Dict with rfp_sections (list of section dicts), evaluation_criteria,
            submission_instructions, and vendor_specific_questions.
        """
        logger.info(
            "Preparing RFP template",
            tenant_id=str(tenant_id),
            use_case=procurement_requirements.get("use_case"),
            vendor_count=len(shortlisted_vendors),
        )

        use_case = procurement_requirements.get("use_case", "AI Services")
        budget = procurement_requirements.get("max_monthly_budget_usd")
        required_certs = procurement_requirements.get("required_certifications", [])

        sections: list[dict[str, Any]] = [
            {
                "section": "1. Overview",
                "content": (
                    f"{issuing_organization} is soliciting proposals for AI services "
                    f"to support {use_case} workloads. "
                    f"We seek a vendor who can meet our performance, security, and compliance requirements."
                ),
            },
            {
                "section": "2. Scope of Work",
                "content": (
                    f"Provide {use_case} capabilities including API access, SLA guarantees, "
                    "monitoring dashboards, and enterprise support. "
                    "Describe your data handling practices and regional availability."
                ),
            },
            {
                "section": "3. Technical Requirements",
                "content": (
                    f"Vendors must demonstrate: "
                    f"{'99.9%+ uptime SLA, ' if not procurement_requirements.get('min_uptime_percent') else str(procurement_requirements['min_uptime_percent']) + '% uptime SLA, '}"
                    f"P95 latency ≤ {procurement_requirements.get('max_latency_ms', 2000)}ms, "
                    f"compliance with: {', '.join(required_certs) if required_certs else 'SOC2 Type II, ISO27001'}."
                ),
            },
            {
                "section": "4. Pricing",
                "content": (
                    f"Provide all-inclusive pricing covering API usage, support, and data egress. "
                    + (f"Monthly budget ceiling: ${budget:,.0f}. " if budget else "")
                    + "Include volume discount schedules and multi-year commitment incentives."
                ),
            },
            {
                "section": "5. Security & Compliance",
                "content": (
                    "Submit current compliance certifications, penetration test summary (last 12 months), "
                    "data processing agreement, and incident response runbook."
                ),
            },
            {
                "section": "6. References",
                "content": "Provide three enterprise references using similar AI workloads.",
            },
            {
                "section": "7. Evaluation Criteria",
                "content": (
                    "Proposals will be evaluated on: "
                    "Technical capability (30%), Pricing (25%), Compliance (20%), Support (15%), References (10%)."
                ),
            },
        ]

        vendor_specific_questions: list[dict[str, Any]] = []
        for vendor in shortlisted_vendors:
            vendor_name = vendor.get("vendor_name", "Vendor")
            dim_scores = vendor.get("dimension_scores", {})
            weakness_dims = [d for d, s in dim_scores.items() if s < 0.65]

            questions: list[str] = []
            if "compliance" in weakness_dims:
                questions.append(
                    f"Describe {vendor_name}'s roadmap for obtaining additional compliance certifications."
                )
            if "reliability" in weakness_dims:
                questions.append(
                    f"Provide your incident history and MTTR metrics for the past 12 months."
                )
            if "support" in weakness_dims:
                questions.append(
                    f"Detail your enterprise support tiers and escalation procedures."
                )
            questions.append(
                f"What differentiates {vendor_name} from competing providers for {use_case} workloads?"
            )

            if questions:
                vendor_specific_questions.append({
                    "vendor": vendor_name,
                    "questions": questions,
                })

        result: dict[str, Any] = {
            "rfp_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "issuing_organization": issuing_organization,
            "use_case": use_case,
            "rfp_sections": sections,
            "evaluation_criteria": {
                "technical_capability": 0.30,
                "pricing": 0.25,
                "compliance": 0.20,
                "support": 0.15,
                "references": 0.10,
            },
            "submission_instructions": {
                "format": "PDF or Word document",
                "max_pages": 30,
                "submission_deadline": None,
                "contact": f"procurement@{issuing_organization.lower().replace(' ', '')}.com",
            },
            "vendor_specific_questions": vendor_specific_questions,
            "vendors_addressed": [v.get("vendor_name") for v in shortlisted_vendors],
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "RFP template prepared",
            tenant_id=str(tenant_id),
            section_count=len(sections),
            vendor_count=len(shortlisted_vendors),
        )

        return result

    async def identify_negotiation_points(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_name: str,
        vendor_profile: dict[str, Any],
        market_comparisons: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify negotiation leverage points for a specific vendor.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            vendor_name: Vendor name.
            vendor_profile: Current vendor offering dict.
            market_comparisons: List of competing vendor data for benchmarking.

        Returns:
            List of negotiation point dicts, each with category, leverage,
            target, and suggested_language fields, sorted by priority.
        """
        logger.info(
            "Identifying negotiation points",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
        )

        negotiation_points: list[dict[str, Any]] = []

        # Price negotiation — if higher than market median
        if market_comparisons:
            market_costs = [m.get("monthly_total_cost_usd", 0.0) for m in market_comparisons]
            market_median = sorted(market_costs)[len(market_costs) // 2]
            vendor_cost = vendor_profile.get("monthly_total_cost_usd", 0.0)

            if vendor_cost > market_median * 1.10:
                savings_target = round(vendor_cost - market_median, 2)
                negotiation_points.append({
                    "category": "pricing",
                    "priority": 1,
                    "leverage": f"Market median is ${market_median:,.0f}/mo; vendor is ${vendor_cost:,.0f}/mo (+10%).",
                    "target": f"Reduce monthly cost by ${savings_target:,.0f} to market rate.",
                    "suggested_language": (
                        "We have received competitive proposals at significantly lower rates. "
                        "We require a pricing adjustment to proceed with your proposal."
                    ),
                })

        # SLA upgrade — if below 99.9% uptime
        vendor_uptime = vendor_profile.get("uptime_percent", 100.0)
        if vendor_uptime < 99.9:
            negotiation_points.append({
                "category": "sla",
                "priority": 2,
                "leverage": f"Current SLA commitment is {vendor_uptime:.2f}%; enterprise standard is 99.9%.",
                "target": "Commit to 99.9% uptime with financial remedies for breaches.",
                "suggested_language": (
                    "Our enterprise risk policy requires a 99.9% uptime SLA with service credits of "
                    "10% per hour of downtime beyond the threshold."
                ),
            })

        # Liability cap — negotiate up from weak cap
        liability_cap_months = vendor_profile.get("liability_cap_months")
        if liability_cap_months is not None and liability_cap_months <= 3:
            negotiation_points.append({
                "category": "liability",
                "priority": 1,
                "leverage": f"Current liability cap of {liability_cap_months} month(s) is below enterprise standard.",
                "target": "Minimum 12-month fee liability cap or $5M aggregate, whichever is greater.",
                "suggested_language": (
                    "Our legal policy mandates a minimum liability cap equivalent to 12 months of fees. "
                    "The current cap is insufficient for enterprise AI risk exposure."
                ),
            })

        # Data portability — negotiate exit rights
        if not vendor_profile.get("data_portability"):
            negotiation_points.append({
                "category": "data_portability",
                "priority": 2,
                "leverage": "No data portability commitment identified in current terms.",
                "target": "Guaranteed data export in open format within 30 days of termination.",
                "suggested_language": (
                    "We require a contractual commitment to provide all tenant data in machine-readable "
                    "format within 30 calendar days of contract termination at no additional charge."
                ),
            })

        # Volume discount — trigger next tier
        if vendor_profile.get("tokens_to_next_tier") is not None:
            tokens_needed = vendor_profile["tokens_to_next_tier"]
            negotiation_points.append({
                "category": "volume_pricing",
                "priority": 3,
                "leverage": f"We are {tokens_needed:,} tokens from the next pricing tier.",
                "target": f"Apply next-tier pricing immediately in exchange for a 12-month volume commitment.",
                "suggested_language": (
                    f"We are willing to commit to a 12-month volume contract to access "
                    f"the next pricing tier immediately."
                ),
            })

        negotiation_points.sort(key=lambda p: p["priority"])

        logger.info(
            "Negotiation points identified",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
            point_count=len(negotiation_points),
        )

        return negotiation_points

    async def generate_comparison_matrix(
        self,
        tenant_id: uuid.UUID,
        scored_vendors: list[dict[str, Any]],
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured vendor comparison matrix.

        Args:
            tenant_id: Requesting tenant UUID.
            scored_vendors: List of multi-criteria scored vendor dicts.
            dimensions: Dimensions to include in the matrix (defaults to all 5).

        Returns:
            Dict with matrix header, rows per vendor, and dimension-by-dimension
            ranking summary for executive presentation.
        """
        effective_dimensions = dimensions or list(DEFAULT_SCORING_WEIGHTS.keys())

        logger.info(
            "Generating vendor comparison matrix",
            tenant_id=str(tenant_id),
            vendor_count=len(scored_vendors),
            dimension_count=len(effective_dimensions),
        )

        matrix_rows: list[dict[str, Any]] = []
        for vendor in scored_vendors:
            dim_scores = vendor.get("dimension_scores", {})
            row: dict[str, Any] = {
                "vendor_name": vendor.get("vendor_name", "Unknown"),
                "vendor_id": vendor.get("vendor_id"),
                "composite_score": vendor.get("composite_score"),
                "rank": vendor.get("rank"),
            }
            for dim in effective_dimensions:
                score = dim_scores.get(dim, 0.0)
                row[f"{dim}_score"] = round(score, 3)
                row[f"{dim}_rating"] = self._score_to_rating(score)
            matrix_rows.append(row)

        # Dimension-level winner
        dimension_winners: dict[str, str] = {}
        for dim in effective_dimensions:
            best_vendor = max(
                scored_vendors,
                key=lambda v: v.get("dimension_scores", {}).get(dim, 0.0),
                default=None,
            )
            if best_vendor:
                dimension_winners[dim] = best_vendor.get("vendor_name", "Unknown")

        result: dict[str, Any] = {
            "matrix_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "dimensions": effective_dimensions,
            "matrix_rows": matrix_rows,
            "dimension_winners": dimension_winners,
            "overall_winner": matrix_rows[0]["vendor_name"] if matrix_rows else "Unknown",
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Comparison matrix generated",
            tenant_id=str(tenant_id),
            vendor_count=len(matrix_rows),
        )

        return result

    @staticmethod
    def _score_to_rating(score: float) -> str:
        """Convert a numeric score to a qualitative rating label.

        Args:
            score: Score between 0.0 and 1.0.

        Returns:
            Rating string: Excellent / Good / Fair / Poor.
        """
        if score >= 0.80:
            return "Excellent"
        if score >= 0.65:
            return "Good"
        if score >= 0.50:
            return "Fair"
        return "Poor"
