"""ContractAnalyzer adapter for AI vendor contract terms extraction and risk analysis.

Extracts and classifies contractual terms including SLA provisions, pricing
structures, data handling obligations, termination rights, and liability
limitations. Produces structured risk summaries for procurement decisions.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Keywords for clause detection — ordered by specificity
SLA_KEYWORDS: list[str] = [
    "service level", "sla", "uptime", "availability", "response time",
    "resolution time", "incident", "outage", "maintenance window",
]

PRICING_KEYWORDS: list[str] = [
    "price", "pricing", "fee", "cost", "rate", "charge", "invoice",
    "billing", "payment", "subscription", "per seat", "per unit",
    "volume discount", "tier", "overage",
]

DATA_HANDLING_KEYWORDS: list[str] = [
    "data", "personal data", "personal information", "gdpr", "ccpa",
    "data processing", "data retention", "data deletion", "data portability",
    "subprocessor", "data transfer", "encryption", "data residency",
]

TERMINATION_KEYWORDS: list[str] = [
    "termination", "terminate", "cancellation", "notice period",
    "convenience", "breach", "cure period", "wind-down", "offboarding",
    "data export", "transition assistance",
]

LIABILITY_KEYWORDS: list[str] = [
    "liability", "limitation of liability", "cap", "indemnif",
    "consequential damages", "indirect damages", "punitive damages",
    "aggregate liability", "exclusive remedy",
]

# Risk severity thresholds
RISK_SCORE_HIGH: float = 0.60
RISK_SCORE_MEDIUM: float = 0.30


class ContractAnalyzer:
    """AI vendor contract terms extraction and risk analysis engine.

    Parses contract text to identify and classify key provisions across
    five categories: SLA terms, pricing structures, data handling obligations,
    termination rights, and liability limitations. Applies the AumOS 88%
    liability cap policy during liability analysis.

    Each analysis produces a structured clause dictionary and an ordered list
    of identified risks with severity classifications and remediation guidance.
    """

    def __init__(
        self,
        liability_cap_warning_months: float = 1.0,
        minimum_uptime_sla_percent: float = 99.9,
        minimum_notice_period_days: int = 30,
    ) -> None:
        """Initialise the ContractAnalyzer.

        Args:
            liability_cap_warning_months: Cap in months below which a warning
                fires (AumOS policy default: 1.0 month).
            minimum_uptime_sla_percent: Minimum acceptable uptime SLA percent.
            minimum_notice_period_days: Minimum acceptable termination notice
                period in days.
        """
        self._cap_warning_months = liability_cap_warning_months
        self._min_uptime = minimum_uptime_sla_percent
        self._min_notice_days = minimum_notice_period_days

    async def extract_sla_terms(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        contract_text: str,
    ) -> dict[str, Any]:
        """Extract SLA provisions from contract text.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_id: Contract UUID being analyzed.
            contract_text: Full contract text or the SLA section.

        Returns:
            Dict with uptime_percent, response_time_hours, resolution_time_hours,
            maintenance_window_hours_per_month, has_financial_remedy,
            remedy_credit_percent, sla_clauses (list of extracted sentences),
            and sla_risk_level fields.
        """
        logger.info(
            "Extracting SLA terms",
            tenant_id=str(tenant_id),
            contract_id=str(contract_id),
        )

        sla_clauses = self._extract_relevant_sentences(contract_text, SLA_KEYWORDS)

        uptime_percent = self._extract_uptime_percent(contract_text)
        response_time = self._extract_numeric_pattern(
            contract_text, r"response\s+time[^\d]*(\d+(?:\.\d+)?)\s*hours?"
        )
        resolution_time = self._extract_numeric_pattern(
            contract_text, r"resolution\s+time[^\d]*(\d+(?:\.\d+)?)\s*hours?"
        )
        maintenance_hours = self._extract_numeric_pattern(
            contract_text, r"maintenance[^\d]*(\d+(?:\.\d+)?)\s*hours?\s*per\s*month"
        )

        has_financial_remedy = any(
            keyword in contract_text.lower()
            for keyword in ["service credit", "credit", "remedy", "compensation"]
        )
        remedy_credit = self._extract_numeric_pattern(
            contract_text, r"(\d+(?:\.\d+)?)\s*%\s*(?:service\s+)?credit"
        )

        sla_risk_level = "low"
        if uptime_percent is not None and uptime_percent < self._min_uptime:
            sla_risk_level = "high" if uptime_percent < 99.0 else "medium"
        elif uptime_percent is None:
            sla_risk_level = "medium"

        result: dict[str, Any] = {
            "uptime_percent": uptime_percent,
            "response_time_hours": response_time,
            "resolution_time_hours": resolution_time,
            "maintenance_window_hours_per_month": maintenance_hours,
            "has_financial_remedy": has_financial_remedy,
            "remedy_credit_percent": remedy_credit,
            "sla_clauses": sla_clauses[:10],
            "sla_risk_level": sla_risk_level,
            "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "SLA terms extracted",
            contract_id=str(contract_id),
            uptime_percent=uptime_percent,
            sla_risk_level=sla_risk_level,
        )

        return result

    async def parse_pricing_structure(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        contract_text: str,
    ) -> dict[str, Any]:
        """Parse pricing model and structure from contract text.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_id: Contract UUID being analyzed.
            contract_text: Full contract text.

        Returns:
            Dict with pricing_model, has_volume_discounts, has_auto_price_increase,
            price_increase_cap_percent, has_most_favoured_nation_clause,
            pricing_clauses, and pricing_risk_level fields.
        """
        logger.info(
            "Parsing pricing structure",
            tenant_id=str(tenant_id),
            contract_id=str(contract_id),
        )

        pricing_clauses = self._extract_relevant_sentences(contract_text, PRICING_KEYWORDS)
        text_lower = contract_text.lower()

        pricing_model = "subscription"
        if "per token" in text_lower or "per api call" in text_lower:
            pricing_model = "usage_based"
        elif "per seat" in text_lower or "per user" in text_lower:
            pricing_model = "per_seat"
        elif "flat fee" in text_lower or "fixed fee" in text_lower:
            pricing_model = "flat_fee"
        elif "consumption" in text_lower or "pay-as-you-go" in text_lower:
            pricing_model = "consumption"

        has_volume_discounts = any(
            kw in text_lower for kw in ["volume discount", "tiered pricing", "committed use"]
        )
        has_auto_increase = any(
            kw in text_lower for kw in ["price increase", "annual increase", "cpi", "inflation"]
        )
        price_increase_cap = self._extract_numeric_pattern(
            contract_text, r"price\s+increase[^\d]*(?:not\s+to\s+exceed|capped\s+at|maximum\s+of)[^\d]*(\d+(?:\.\d+)?)\s*%"
        )
        has_mfn = any(
            kw in text_lower
            for kw in ["most favoured nation", "most favored nation", "mfn clause", "best price"]
        )

        pricing_risk_level = "low"
        if has_auto_increase and price_increase_cap is None:
            pricing_risk_level = "high"
        elif has_auto_increase:
            pricing_risk_level = "medium"

        result: dict[str, Any] = {
            "pricing_model": pricing_model,
            "has_volume_discounts": has_volume_discounts,
            "has_auto_price_increase": has_auto_increase,
            "price_increase_cap_percent": price_increase_cap,
            "has_most_favoured_nation_clause": has_mfn,
            "pricing_clauses": pricing_clauses[:10],
            "pricing_risk_level": pricing_risk_level,
            "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Pricing structure parsed",
            contract_id=str(contract_id),
            pricing_model=pricing_model,
            pricing_risk_level=pricing_risk_level,
        )

        return result

    async def identify_data_handling_terms(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        contract_text: str,
    ) -> dict[str, Any]:
        """Identify data handling, privacy, and compliance provisions.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_id: Contract UUID being analyzed.
            contract_text: Full contract text.

        Returns:
            Dict with gdpr_compliant, ccpa_compliant, data_retention_days,
            has_data_deletion_right, has_data_portability, subprocessor_list_url,
            encryption_standard, data_residency_region, data_handling_risk_level,
            and data_clauses fields.
        """
        logger.info(
            "Identifying data handling terms",
            tenant_id=str(tenant_id),
            contract_id=str(contract_id),
        )

        data_clauses = self._extract_relevant_sentences(contract_text, DATA_HANDLING_KEYWORDS)
        text_lower = contract_text.lower()

        gdpr_compliant = "gdpr" in text_lower or "general data protection" in text_lower
        ccpa_compliant = "ccpa" in text_lower or "california consumer privacy" in text_lower

        retention_days = self._extract_numeric_pattern(
            contract_text,
            r"retain[^\d]*(\d+)\s*days?",
        )
        if retention_days is None:
            retention_months = self._extract_numeric_pattern(
                contract_text, r"retain[^\d]*(\d+)\s*months?"
            )
            if retention_months is not None:
                retention_days = retention_months * 30

        has_deletion = any(
            kw in text_lower
            for kw in ["right to erasure", "deletion upon request", "data deletion", "delete your data"]
        )
        has_portability = any(
            kw in text_lower
            for kw in ["data portability", "export your data", "data export", "machine-readable format"]
        )

        subprocessor_url: str | None = None
        url_match = re.search(
            r"subprocessor[s]?\s+(?:list|registry)[^\n]*?(https?://\S+)",
            contract_text,
            re.IGNORECASE,
        )
        if url_match:
            subprocessor_url = url_match.group(1)

        encryption_standard = "AES-256" if "aes-256" in text_lower else (
            "AES-128" if "aes-128" in text_lower else (
                "TLS" if "tls" in text_lower else None
            )
        )

        data_residency: str | None = None
        for region in ["eu", "us", "uk", "apac", "australia", "canada", "germany"]:
            if f"data residency: {region}" in text_lower or f"stored in {region}" in text_lower:
                data_residency = region.upper()
                break

        risk_factors = []
        if not gdpr_compliant and not ccpa_compliant:
            risk_factors.append("no_privacy_compliance_commitment")
        if not has_deletion:
            risk_factors.append("no_deletion_right")
        if not has_portability:
            risk_factors.append("no_portability_right")

        data_risk_level = (
            "high" if len(risk_factors) >= 2
            else "medium" if len(risk_factors) == 1
            else "low"
        )

        result: dict[str, Any] = {
            "gdpr_compliant": gdpr_compliant,
            "ccpa_compliant": ccpa_compliant,
            "data_retention_days": retention_days,
            "has_data_deletion_right": has_deletion,
            "has_data_portability": has_portability,
            "subprocessor_list_url": subprocessor_url,
            "encryption_standard": encryption_standard,
            "data_residency_region": data_residency,
            "data_handling_risk_level": data_risk_level,
            "data_clauses": data_clauses[:10],
            "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Data handling terms identified",
            contract_id=str(contract_id),
            data_risk_level=data_risk_level,
            gdpr_compliant=gdpr_compliant,
        )

        return result

    async def analyze_termination_clauses(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        contract_text: str,
    ) -> dict[str, Any]:
        """Analyze termination rights and obligations.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_id: Contract UUID being analyzed.
            contract_text: Full contract text.

        Returns:
            Dict with termination_for_convenience, notice_period_days,
            cure_period_days, has_transition_assistance, data_export_days_after_termination,
            auto_renewal, renewal_notice_days, termination_clauses, and termination_risk_level.
        """
        logger.info(
            "Analyzing termination clauses",
            tenant_id=str(tenant_id),
            contract_id=str(contract_id),
        )

        termination_clauses = self._extract_relevant_sentences(contract_text, TERMINATION_KEYWORDS)
        text_lower = contract_text.lower()

        termination_for_convenience = any(
            kw in text_lower
            for kw in ["terminate for convenience", "termination for convenience", "terminate without cause"]
        )

        notice_days = self._extract_numeric_pattern(
            contract_text, r"(\d+)\s*days?\s*(?:written\s+)?notice"
        )
        cure_days = self._extract_numeric_pattern(
            contract_text, r"cure\s+period[^\d]*(\d+)\s*days?"
        )
        has_transition = any(
            kw in text_lower
            for kw in ["transition assistance", "wind-down period", "data migration support"]
        )
        export_days = self._extract_numeric_pattern(
            contract_text,
            r"(?:data\s+export|access\s+to\s+data)[^\d]*(\d+)\s*days?\s*(?:after|following)\s*termination"
        )
        auto_renewal = any(
            kw in text_lower
            for kw in ["auto-renew", "automatically renew", "automatic renewal"]
        )
        renewal_notice = self._extract_numeric_pattern(
            contract_text, r"renewal[^\d]*(\d+)\s*days?\s*(?:prior|before|advance)"
        )

        termination_risks: list[str] = []
        if not termination_for_convenience:
            termination_risks.append("no_termination_for_convenience")
        if notice_days is not None and notice_days < self._min_notice_days:
            termination_risks.append("short_notice_period")
        if notice_days is None:
            termination_risks.append("no_notice_period_specified")
        if auto_renewal and renewal_notice is not None and renewal_notice < 30:
            termination_risks.append("short_auto_renewal_notice")

        termination_risk_level = (
            "high" if len(termination_risks) >= 2
            else "medium" if len(termination_risks) == 1
            else "low"
        )

        result: dict[str, Any] = {
            "termination_for_convenience": termination_for_convenience,
            "notice_period_days": notice_days,
            "cure_period_days": cure_days,
            "has_transition_assistance": has_transition,
            "data_export_days_after_termination": export_days,
            "auto_renewal": auto_renewal,
            "renewal_notice_days": renewal_notice,
            "termination_clauses": termination_clauses[:10],
            "termination_risk_level": termination_risk_level,
            "identified_risks": termination_risks,
            "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Termination clauses analyzed",
            contract_id=str(contract_id),
            termination_risk_level=termination_risk_level,
            auto_renewal=auto_renewal,
        )

        return result

    async def detect_liability_limitations(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        contract_text: str,
        annual_value_usd: int | None = None,
    ) -> dict[str, Any]:
        """Detect and assess vendor liability limitation clauses.

        Applies the AumOS 88% cap policy: contracts capping liability at or
        below approximately 1 month of fees receive a high-severity warning.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_id: Contract UUID being analyzed.
            contract_text: Full contract text.
            annual_value_usd: Annual contract value for cap fraction computation.

        Returns:
            Dict with liability_cap_months, liability_cap_usd, liability_cap_fraction,
            has_liability_cap_warning, excludes_consequential_damages,
            excludes_indirect_damages, indemnification_scope,
            liability_clauses, and liability_risk_level fields.
        """
        logger.info(
            "Detecting liability limitations",
            tenant_id=str(tenant_id),
            contract_id=str(contract_id),
        )

        liability_clauses = self._extract_relevant_sentences(contract_text, LIABILITY_KEYWORDS)
        text_lower = contract_text.lower()

        cap_months = self._extract_numeric_pattern(
            contract_text,
            r"(?:liability|damages)[^\d]*(?:limited\s+to|not\s+exceed|aggregate)[^\d]*(\d+(?:\.\d+)?)\s*months?"
        )
        cap_usd = self._extract_numeric_pattern(
            contract_text,
            r"(?:liability|damages)[^\d]*(?:limited\s+to|not\s+exceed|aggregate)[^\d]*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:USD|dollars)?"
        )
        cap_usd_clean: float | None = None
        if cap_usd is not None:
            cap_usd_clean = cap_usd

        cap_fraction: float | None = None
        has_cap_warning = False
        if cap_months is not None:
            cap_fraction = round(cap_months / 12.0, 4)
            has_cap_warning = cap_months <= self._cap_warning_months
        elif cap_usd_clean is not None and annual_value_usd is not None and annual_value_usd > 0:
            cap_fraction = round(cap_usd_clean / annual_value_usd, 4)
            # Cap fraction <= 1/12 (one month) triggers warning
            has_cap_warning = cap_fraction <= (1.0 / 12.0 + 0.001)

        excludes_consequential = any(
            kw in text_lower
            for kw in ["consequential damages", "consequential loss", "exclude consequential"]
        )
        excludes_indirect = any(
            kw in text_lower
            for kw in ["indirect damages", "indirect loss", "exclude indirect"]
        )

        indemnification_scope = "standard"
        if "mutual indemnification" in text_lower:
            indemnification_scope = "mutual"
        elif "indemnification" not in text_lower:
            indemnification_scope = "none"
        elif "broad indemnification" in text_lower:
            indemnification_scope = "broad"

        liability_risks: list[str] = []
        if has_cap_warning:
            liability_risks.append("liability_cap_below_one_month")
        if excludes_consequential:
            liability_risks.append("excludes_consequential_damages")
        if excludes_indirect:
            liability_risks.append("excludes_indirect_damages")
        if cap_months is None and cap_usd is None:
            liability_risks.append("no_cap_specified")

        liability_risk_level = (
            "critical" if has_cap_warning and excludes_consequential
            else "high" if has_cap_warning or (len(liability_risks) >= 2)
            else "medium" if len(liability_risks) == 1
            else "low"
        )

        result: dict[str, Any] = {
            "liability_cap_months": cap_months,
            "liability_cap_usd": cap_usd_clean,
            "liability_cap_fraction": cap_fraction,
            "has_liability_cap_warning": has_cap_warning,
            "excludes_consequential_damages": excludes_consequential,
            "excludes_indirect_damages": excludes_indirect,
            "indemnification_scope": indemnification_scope,
            "liability_clauses": liability_clauses[:10],
            "liability_risk_level": liability_risk_level,
            "identified_risks": liability_risks,
            "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Liability limitations detected",
            contract_id=str(contract_id),
            has_cap_warning=has_cap_warning,
            liability_risk_level=liability_risk_level,
        )

        return result

    async def compare_contracts(
        self,
        tenant_id: uuid.UUID,
        contract_analyses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compare multiple contract analyses side-by-side.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_analyses: List of analysis dicts, each containing
                contract_id, vendor_name, sla_terms, pricing, data_handling,
                termination, and liability analysis results.

        Returns:
            Comparison matrix dict with per-contract summaries and
            recommended contract ranked by composite risk score.
        """
        logger.info(
            "Comparing contracts",
            tenant_id=str(tenant_id),
            contract_count=len(contract_analyses),
        )

        scored_contracts: list[dict[str, Any]] = []
        for analysis in contract_analyses:
            risk_scores = {
                "sla": self._risk_level_to_score(
                    analysis.get("sla_terms", {}).get("sla_risk_level", "unknown")
                ),
                "pricing": self._risk_level_to_score(
                    analysis.get("pricing", {}).get("pricing_risk_level", "unknown")
                ),
                "data": self._risk_level_to_score(
                    analysis.get("data_handling", {}).get("data_handling_risk_level", "unknown")
                ),
                "termination": self._risk_level_to_score(
                    analysis.get("termination", {}).get("termination_risk_level", "unknown")
                ),
                "liability": self._risk_level_to_score(
                    analysis.get("liability", {}).get("liability_risk_level", "unknown")
                ),
            }
            composite_risk = sum(risk_scores.values()) / len(risk_scores)
            scored_contracts.append({
                "contract_id": analysis.get("contract_id"),
                "vendor_name": analysis.get("vendor_name", "Unknown"),
                "risk_scores": risk_scores,
                "composite_risk_score": round(composite_risk, 4),
                "risk_level": self._score_to_risk_level(composite_risk),
            })

        scored_contracts.sort(key=lambda c: c["composite_risk_score"])
        best_contract = scored_contracts[0] if scored_contracts else None

        result: dict[str, Any] = {
            "comparison_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "contracts_compared": len(contract_analyses),
            "contract_rankings": scored_contracts,
            "recommended_contract": best_contract,
            "comparison_matrix": {
                "dimensions": ["sla", "pricing", "data_handling", "termination", "liability"],
                "entries": scored_contracts,
            },
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Contract comparison completed",
            tenant_id=str(tenant_id),
            contracts_compared=len(contract_analyses),
            recommended_contract_id=best_contract.get("contract_id") if best_contract else None,
        )

        return result

    async def generate_key_terms_summary(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        sla_terms: dict[str, Any],
        pricing_terms: dict[str, Any],
        data_handling_terms: dict[str, Any],
        termination_terms: dict[str, Any],
        liability_terms: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate an executive summary of all extracted contract terms.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_id: Contract UUID being summarized.
            sla_terms: Extracted SLA terms dict.
            pricing_terms: Extracted pricing terms dict.
            data_handling_terms: Extracted data handling terms dict.
            termination_terms: Extracted termination terms dict.
            liability_terms: Extracted liability terms dict.

        Returns:
            Dict with executive_summary, key_risks (ordered by severity),
            positive_terms, negotiation_priorities, and overall_risk_level.
        """
        all_risks: list[dict[str, Any]] = []

        if liability_terms.get("has_liability_cap_warning"):
            all_risks.append({
                "category": "liability",
                "severity": "critical",
                "description": (
                    f"Liability capped at {liability_terms.get('liability_cap_months', '?')} "
                    "month(s) of fees — below AumOS minimum."
                ),
                "recommendation": "Negotiate minimum 12-month liability cap.",
            })

        if termination_terms.get("termination_risk_level") in ("high", "critical"):
            all_risks.append({
                "category": "termination",
                "severity": termination_terms["termination_risk_level"],
                "description": "Unfavorable termination provisions detected.",
                "recommendation": "Add termination for convenience with 90-day notice.",
            })

        if sla_terms.get("sla_risk_level") in ("high", "critical"):
            all_risks.append({
                "category": "sla",
                "severity": sla_terms["sla_risk_level"],
                "description": "SLA terms below enterprise minimum requirements.",
                "recommendation": f"Require minimum {self._min_uptime}% uptime SLA with financial remedies.",
            })

        if data_handling_terms.get("data_handling_risk_level") in ("high", "critical"):
            all_risks.append({
                "category": "data_handling",
                "severity": data_handling_terms["data_handling_risk_level"],
                "description": "Data privacy and handling provisions are insufficient.",
                "recommendation": "Require explicit GDPR/CCPA compliance and data deletion rights.",
            })

        positive_terms: list[str] = []
        if sla_terms.get("has_financial_remedy"):
            positive_terms.append("Contract includes financial SLA remedies.")
        if data_handling_terms.get("gdpr_compliant"):
            positive_terms.append("GDPR compliance commitment present.")
        if pricing_terms.get("has_most_favoured_nation_clause"):
            positive_terms.append("Most Favoured Nation pricing clause included.")
        if termination_terms.get("termination_for_convenience"):
            positive_terms.append("Termination for convenience right included.")

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        all_risks.sort(key=lambda r: severity_order.get(r["severity"], 4))

        risk_levels = [
            sla_terms.get("sla_risk_level", "low"),
            pricing_terms.get("pricing_risk_level", "low"),
            data_handling_terms.get("data_handling_risk_level", "low"),
            termination_terms.get("termination_risk_level", "low"),
            liability_terms.get("liability_risk_level", "low"),
        ]
        overall_risk = max(risk_levels, key=lambda r: severity_order.get(r, 4))

        negotiation_priorities: list[str] = [r["recommendation"] for r in all_risks[:5]]

        result: dict[str, Any] = {
            "summary_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "contract_id": str(contract_id),
            "overall_risk_level": overall_risk,
            "key_risks": all_risks,
            "positive_terms": positive_terms,
            "negotiation_priorities": negotiation_priorities,
            "executive_summary": (
                f"Contract has {len(all_risks)} identified risk(s) with overall "
                f"{overall_risk} risk level. "
                f"{len(positive_terms)} favorable terms found. "
                f"Priority: {negotiation_priorities[0] if negotiation_priorities else 'None'}."
            ),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Key terms summary generated",
            tenant_id=str(tenant_id),
            contract_id=str(contract_id),
            overall_risk_level=overall_risk,
            risk_count=len(all_risks),
        )

        return result

    @staticmethod
    def _extract_relevant_sentences(text: str, keywords: list[str]) -> list[str]:
        """Extract sentences containing any of the given keywords.

        Args:
            text: Source contract text.
            keywords: List of keywords to match (case-insensitive).

        Returns:
            List of relevant sentence strings, deduplicated.
        """
        sentences = re.split(r"(?<=[.!?])\s+", text)
        relevant: list[str] = []
        seen: set[str] = set()

        for sentence in sentences:
            sentence_lower = sentence.lower()
            if any(kw in sentence_lower for kw in keywords):
                clean = sentence.strip()
                if clean and clean not in seen:
                    seen.add(clean)
                    relevant.append(clean)

        return relevant

    @staticmethod
    def _extract_numeric_pattern(text: str, pattern: str) -> float | None:
        """Extract the first numeric value matching a regex pattern.

        Args:
            text: Source text to search.
            pattern: Regex pattern with one capturing group for the number.

        Returns:
            Extracted float value, or None if no match found.
        """
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value_str = match.group(1).replace(",", "")
                return float(value_str)
            except (ValueError, IndexError):
                return None
        return None

    @staticmethod
    def _extract_uptime_percent(text: str) -> float | None:
        """Extract uptime SLA percentage from contract text.

        Args:
            text: Contract text to search.

        Returns:
            Uptime percentage as float, or None if not found.
        """
        patterns = [
            r"(\d{2,3}(?:\.\d+)?)\s*%\s*uptime",
            r"uptime\s+(?:of\s+)?(\d{2,3}(?:\.\d+)?)\s*%",
            r"availability\s+(?:of\s+)?(\d{2,3}(?:\.\d+)?)\s*%",
            r"(\d{2,3}(?:\.\d+)?)\s*%\s*availability",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    value = float(match.group(1))
                    if 0.0 <= value <= 100.0:
                        return value
                except ValueError:
                    continue
        return None

    @staticmethod
    def _risk_level_to_score(risk_level: str) -> float:
        """Convert a risk level string to a numeric score.

        Args:
            risk_level: Risk level string (low/medium/high/critical).

        Returns:
            Numeric score 0.0–1.0.
        """
        mapping = {"low": 0.1, "medium": 0.4, "high": 0.7, "critical": 1.0}
        return mapping.get(risk_level.lower(), 0.5)

    @staticmethod
    def _score_to_risk_level(score: float) -> str:
        """Convert a numeric composite risk score to a risk level string.

        Args:
            score: Composite risk score 0.0–1.0.

        Returns:
            Risk level string: low | medium | high | critical.
        """
        if score >= 0.75:
            return "critical"
        if score >= 0.50:
            return "high"
        if score >= 0.25:
            return "medium"
        return "low"
