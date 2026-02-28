"""LLM-backed negotiation playbook generator adapter.

Implements INegotiationPlaybookGenerator using the configured LLM model
to synthesise vendor evaluation data into actionable negotiation strategy.
"""

import uuid
from typing import Any

from aumos_common.observability import get_logger

from aumos_vendor_intelligence.core.interfaces import INegotiationPlaybookGenerator

logger = get_logger(__name__)

_SYSTEM_PROMPT = """You are an expert enterprise technology contract negotiator.
You will receive structured vendor intelligence data and produce a concise,
actionable negotiation playbook in JSON format.

The playbook must contain:
- executive_summary: 2–3 sentence overview of the negotiation position
- leverage_points: list of specific leverage points (with evidence)
- risk_mitigations: list of contract clauses or terms to request
- walk_away_conditions: list of conditions under which to walk away
- opening_positions: list of opening ask positions for key contract terms
- concession_hierarchy: ordered list of concessions AumOS can make

Respond ONLY with valid JSON matching this structure. Do not include markdown fences."""


class LlmNegotiationPlaybookGenerator(INegotiationPlaybookGenerator):
    """LLM-backed negotiation playbook generator.

    Uses the configured LLM model to synthesise vendor scoring, lock-in
    assessment, and contract risk data into an actionable playbook.

    Args:
        llm_model: LLM model ID to use for playbook generation.
        max_tokens: Maximum tokens for the LLM response.
        api_base_url: Base URL for the LLM API (internal routing).
    """

    def __init__(
        self,
        llm_model: str = "claude-opus-4-6",
        max_tokens: int = 4096,
        api_base_url: str = "https://llm-serving.internal",
    ) -> None:
        self._llm_model = llm_model
        self._max_tokens = max_tokens
        self._api_base_url = api_base_url

    async def generate(
        self,
        vendor_id: uuid.UUID,
        tenant_id: uuid.UUID,
        vendor_data: dict[str, Any],
        lock_in_data: dict[str, Any] | None,
        contract_data: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Generate a negotiation playbook for a vendor.

        Synthesises vendor evaluation scores, lock-in risk dimensions, and
        contract risk findings into a structured negotiation strategy.

        Args:
            vendor_id: Vendor UUID being negotiated with.
            tenant_id: Tenant UUID for context.
            vendor_data: Vendor profile dict with scores and metadata.
            lock_in_data: Optional lock-in assessment dict with dimension scores.
            contract_data: Optional list of contract risk dicts for the vendor.

        Returns:
            Playbook dict with keys: executive_summary, leverage_points,
            risk_mitigations, walk_away_conditions, opening_positions,
            concession_hierarchy.
        """
        logger.info(
            "generating_negotiation_playbook",
            vendor_id=str(vendor_id),
            tenant_id=str(tenant_id),
            has_lock_in_data=lock_in_data is not None,
            contract_count=len(contract_data) if contract_data else 0,
        )

        # Build context payload for LLM
        context: dict[str, Any] = {
            "vendor": vendor_data,
            "lock_in_assessment": lock_in_data,
            "contracts": contract_data or [],
        }

        # Stub implementation — returns a structured template in development.
        # Production integration: call aumos-llm-serving with structured output.
        vendor_name = vendor_data.get("name", "Vendor")
        overall_score = vendor_data.get("overall_score")
        risk_level = vendor_data.get("risk_level", "unknown")

        playbook: dict[str, Any] = {
            "executive_summary": (
                f"Negotiation playbook for {vendor_name} (risk level: {risk_level}, "
                f"score: {overall_score}). "
                "This playbook outlines leverage points and contract protections "
                "based on vendor intelligence data."
            ),
            "leverage_points": self._derive_leverage_points(vendor_data, lock_in_data),
            "risk_mitigations": self._derive_risk_mitigations(contract_data or []),
            "walk_away_conditions": [
                "Vendor refuses liability cap above 12 months of fees",
                "No SOC 2 Type II certification within 90 days",
                "Data egress not guaranteed in contract",
            ],
            "opening_positions": [
                {"term": "liability_cap", "ask": "24 months of fees"},
                {"term": "data_export", "ask": "Full export within 30 days of termination"},
                {"term": "audit_rights", "ask": "Annual third-party security audit access"},
                {"term": "sla_uptime", "ask": "99.9% uptime with financial penalties"},
            ],
            "concession_hierarchy": [
                "Accept 12-month liability cap if audit rights included",
                "Accept 60-day data export window if encryption-at-rest guaranteed",
                "Accept reduced SLA penalty if incident response SLA added",
            ],
            "generated_by": "llm_stub",
            "model": self._llm_model,
            "context_hash": str(hash(str(context)))[:8],
        }

        return playbook

    def _derive_leverage_points(
        self,
        vendor_data: dict[str, Any],
        lock_in_data: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Derive leverage points from vendor and lock-in data.

        Args:
            vendor_data: Vendor profile dict.
            lock_in_data: Optional lock-in assessment dict.

        Returns:
            List of leverage point dicts with evidence and impact.
        """
        points: list[dict[str, Any]] = []

        score = vendor_data.get("overall_score")
        if score is not None and score < 0.6:
            points.append({
                "point": "Below-average vendor evaluation score",
                "evidence": f"Vendor scored {score:.2f}/1.0 on AumOS evaluation criteria",
                "impact": "high",
            })

        if lock_in_data:
            lock_in_score = lock_in_data.get("lock_in_score")
            if lock_in_score is not None and lock_in_score > 0.5:
                points.append({
                    "point": "Elevated lock-in risk",
                    "evidence": f"Lock-in score: {lock_in_score:.2f} — proprietary formats detected",
                    "impact": "high",
                })

        return points

    def _derive_risk_mitigations(
        self,
        contracts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Derive risk mitigations from contract risk findings.

        Args:
            contracts: List of contract risk dicts.

        Returns:
            List of mitigation dicts with clause and priority.
        """
        mitigations: list[dict[str, Any]] = []

        for contract in contracts:
            if contract.get("has_liability_cap_warning"):
                mitigations.append({
                    "clause": "Increase liability cap to minimum 12 months of fees",
                    "priority": "high",
                    "trigger": "liability_cap_warning",
                })
            if contract.get("auto_renewal_clause"):
                mitigations.append({
                    "clause": "Add 90-day written cancellation notice requirement",
                    "priority": "medium",
                    "trigger": "auto_renewal",
                })

        if not mitigations:
            mitigations.append({
                "clause": "Add standard data processing agreement (DPA)",
                "priority": "medium",
                "trigger": "default",
            })

        return mitigations
