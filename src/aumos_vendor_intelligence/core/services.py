"""Business logic services for the AumOS Vendor Intelligence service.

All services depend on repository and adapter interfaces (not concrete
implementations) and receive dependencies via constructor injection.
No framework code (FastAPI, SQLAlchemy) belongs here.

Key invariants enforced by services:
- Vendor scores are always recomputed from individual criteria, never set directly.
- Lock-in risk levels are derived from score thresholds (configurable).
- Liability cap warnings are triggered when cap fraction >= 0.88 (AumOS policy).
- Insurance gaps are deduplicated by vendor + coverage_type per tenant.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from aumos_common.errors import ConflictError, ErrorCode, NotFoundError
from aumos_common.events import EventPublisher, Topics
from aumos_common.observability import get_logger

from aumos_vendor_intelligence.core.interfaces import (
    IArbitrageDetector,
    IBenchmarkingRunner,
    IContractAnalyzer,
    IContractRepository,
    IEvaluationRepository,
    IFallbackRouter,
    IInsuranceGapRepository,
    ILockInRepository,
    IProcurementAdvisor,
    ISLAMonitor,
    IVendorDashboardAggregator,
    IVendorDataEnricher,
    IVendorRepository,
)
from aumos_vendor_intelligence.core.models import (
    Contract,
    InsuranceGap,
    LockInAssessment,
    Vendor,
    VendorEvaluation,
)

logger = get_logger(__name__)

# Valid vendor category values
VALID_VENDOR_CATEGORIES: frozenset[str] = frozenset({
    "llm_provider",
    "mlops_platform",
    "data_platform",
    "observability",
    "security",
    "infrastructure",
    "other",
})

# Valid contract types
VALID_CONTRACT_TYPES: frozenset[str] = frozenset({
    "msa",
    "sow",
    "order_form",
    "addendum",
    "nda",
})

# AumOS policy: liability cap fraction at or above this threshold triggers a warning
LIABILITY_CAP_WARNING_THRESHOLD: float = 0.88

# Default required insurance coverage types per AumOS policy
REQUIRED_COVERAGE_TYPES: frozenset[str] = frozenset({
    "cyber_liability",
    "errors_and_omissions",
    "technology_professional_liability",
})


class VendorScorerService:
    """Register vendors and run multi-criteria evaluation scoring.

    Computes a weighted composite score from 5 evaluation dimensions and
    classifies the vendor into a risk level. Updates the vendor's overall_score
    and risk_level after each evaluation run.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        evaluation_repo: IEvaluationRepository,
        event_publisher: EventPublisher,
        weight_api_compatibility: float = 0.25,
        weight_data_portability: float = 0.25,
        weight_security_posture: float = 0.20,
        weight_pricing_transparency: float = 0.15,
        weight_support_quality: float = 0.15,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            vendor_repo: Vendor persistence.
            evaluation_repo: VendorEvaluation persistence.
            event_publisher: Kafka event publisher.
            weight_api_compatibility: Weight for API compatibility criterion.
            weight_data_portability: Weight for data portability criterion.
            weight_security_posture: Weight for security posture criterion.
            weight_pricing_transparency: Weight for pricing transparency criterion.
            weight_support_quality: Weight for support quality criterion.
        """
        self._vendors = vendor_repo
        self._evaluations = evaluation_repo
        self._publisher = event_publisher
        self._weights = {
            "api_compatibility": weight_api_compatibility,
            "data_portability": weight_data_portability,
            "security_posture": weight_security_posture,
            "pricing_transparency": weight_pricing_transparency,
            "support_quality": weight_support_quality,
        }

    async def register_vendor(
        self,
        tenant_id: uuid.UUID,
        name: str,
        category: str,
        description: str | None = None,
        website_url: str | None = None,
        api_compatibility: dict[str, Any] | None = None,
        data_portability: dict[str, Any] | None = None,
        contact_info: dict[str, Any] | None = None,
        registered_by: uuid.UUID | None = None,
    ) -> Vendor:
        """Register a new AI vendor for evaluation.

        Args:
            tenant_id: Owning tenant UUID.
            name: Vendor company or product name.
            category: Vendor category classification.
            description: Optional vendor description.
            website_url: Optional vendor website URL.
            api_compatibility: Optional API compatibility metadata.
            data_portability: Optional data portability metadata.
            contact_info: Optional vendor contact information.
            registered_by: Optional UUID of the registering user.

        Returns:
            Newly created Vendor in under_review status.

        Raises:
            ConflictError: If the vendor category is invalid.
        """
        if category not in VALID_VENDOR_CATEGORIES:
            raise ConflictError(
                message=f"Invalid vendor category '{category}'. Valid categories: {VALID_VENDOR_CATEGORIES}",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        vendor = await self._vendors.create(
            tenant_id=tenant_id,
            name=name,
            category=category,
            description=description,
            website_url=website_url,
            api_compatibility=api_compatibility or {},
            data_portability=data_portability or {},
            contact_info=contact_info or {},
            registered_by=registered_by,
        )

        logger.info(
            "Vendor registered",
            tenant_id=str(tenant_id),
            vendor_id=str(vendor.id),
            name=name,
            category=category,
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "vendor.registered",
                "tenant_id": str(tenant_id),
                "vendor_id": str(vendor.id),
                "name": name,
                "category": category,
            },
        )

        return vendor

    async def get_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Vendor:
        """Retrieve a vendor by ID.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            Vendor with current evaluation populated.

        Raises:
            NotFoundError: If vendor not found.
        """
        vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                message=f"Vendor {vendor_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )
        return vendor

    async def list_vendors(
        self,
        tenant_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Vendor], int]:
        """List vendors for a tenant with pagination.

        Args:
            tenant_id: Requesting tenant.
            page: 1-based page number.
            page_size: Results per page.
            category: Optional category filter.
            status: Optional status filter.

        Returns:
            Tuple of (vendors, total_count).
        """
        return await self._vendors.list_by_tenant(
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
            category=category,
            status=status,
        )

    async def run_evaluation(
        self,
        vendor_id: uuid.UUID,
        tenant_id: uuid.UUID,
        api_compatibility_score: float,
        data_portability_score: float,
        security_posture_score: float,
        pricing_transparency_score: float,
        support_quality_score: float,
        notes: str | None = None,
        raw_responses: dict[str, Any] | None = None,
        evaluator_id: uuid.UUID | None = None,
    ) -> VendorEvaluation:
        """Run a multi-criteria evaluation for a vendor.

        Computes a weighted composite score, classifies risk, and updates
        the vendor's overall_score and risk_level.

        Args:
            vendor_id: Vendor UUID to evaluate.
            tenant_id: Requesting tenant.
            api_compatibility_score: API compatibility criterion (0.0–1.0).
            data_portability_score: Data portability criterion (0.0–1.0).
            security_posture_score: Security posture criterion (0.0–1.0).
            pricing_transparency_score: Pricing transparency criterion (0.0–1.0).
            support_quality_score: Support quality criterion (0.0–1.0).
            notes: Optional free-form evaluation notes.
            raw_responses: Optional raw evidence dict.
            evaluator_id: Optional user UUID performing the evaluation.

        Returns:
            Newly created VendorEvaluation marked as current.

        Raises:
            NotFoundError: If vendor not found.
            ConflictError: If any score is outside [0.0, 1.0].
        """
        await self.get_vendor(vendor_id, tenant_id)

        scores = {
            "api_compatibility": api_compatibility_score,
            "data_portability": data_portability_score,
            "security_posture": security_posture_score,
            "pricing_transparency": pricing_transparency_score,
            "support_quality": support_quality_score,
        }
        for criterion, score in scores.items():
            if not 0.0 <= score <= 1.0:
                raise ConflictError(
                    message=f"Score for '{criterion}' must be between 0.0 and 1.0, got {score}.",
                    error_code=ErrorCode.INVALID_OPERATION,
                )

        overall_score = (
            api_compatibility_score * self._weights["api_compatibility"]
            + data_portability_score * self._weights["data_portability"]
            + security_posture_score * self._weights["security_posture"]
            + pricing_transparency_score * self._weights["pricing_transparency"]
            + support_quality_score * self._weights["support_quality"]
        )
        overall_score = round(overall_score, 4)
        risk_level = self._classify_risk(overall_score)

        # Deactivate previous evaluations
        existing_evaluation = await self._evaluations.get_current(vendor_id, tenant_id)

        evaluation = await self._evaluations.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            evaluator_id=evaluator_id,
            api_compatibility_score=api_compatibility_score,
            data_portability_score=data_portability_score,
            security_posture_score=security_posture_score,
            pricing_transparency_score=pricing_transparency_score,
            support_quality_score=support_quality_score,
            overall_score=overall_score,
            notes=notes,
            raw_responses=raw_responses or {},
        )

        if existing_evaluation is not None:
            await self._evaluations.deactivate_previous(vendor_id, evaluation.id)

        now = datetime.now(tz=timezone.utc)
        await self._vendors.update_score(
            vendor_id=vendor_id,
            overall_score=overall_score,
            risk_level=risk_level,
            last_evaluated_at=now,
        )

        logger.info(
            "Vendor evaluation completed",
            vendor_id=str(vendor_id),
            tenant_id=str(tenant_id),
            overall_score=overall_score,
            risk_level=risk_level,
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "vendor.evaluated",
                "tenant_id": str(tenant_id),
                "vendor_id": str(vendor_id),
                "overall_score": overall_score,
                "risk_level": risk_level,
                "evaluation_id": str(evaluation.id),
            },
        )

        return evaluation

    async def compare_vendors(
        self,
        tenant_id: uuid.UUID,
        vendor_ids: list[uuid.UUID],
    ) -> list[Vendor]:
        """Retrieve multiple vendors for side-by-side comparison.

        Args:
            tenant_id: Requesting tenant.
            vendor_ids: List of vendor UUIDs to compare (2–10 vendors).

        Returns:
            List of Vendor instances ordered by overall_score descending.

        Raises:
            ConflictError: If fewer than 2 or more than 10 vendor IDs are provided.
        """
        if len(vendor_ids) < 2:
            raise ConflictError(
                message="At least 2 vendor IDs are required for comparison.",
                error_code=ErrorCode.INVALID_OPERATION,
            )
        if len(vendor_ids) > 10:
            raise ConflictError(
                message="At most 10 vendors can be compared at once.",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        return await self._vendors.list_for_comparison(tenant_id, vendor_ids)

    @staticmethod
    def _classify_risk(score: float) -> str:
        """Classify an evaluation score into a risk level.

        A higher score is BETTER (less risk). Risk levels are inverted:
        a low score = high risk, a high score = low risk.

        Args:
            score: Composite evaluation score (0.0–1.0).

        Returns:
            Risk level string: low | medium | high | critical.
        """
        if score >= 0.75:
            return "low"
        if score >= 0.50:
            return "medium"
        if score >= 0.25:
            return "high"
        return "critical"


class LockInAssessorService:
    """Assess vendor lock-in risk across multiple dimensions.

    Analyses proprietary format usage, switching costs, API openness, data
    egress capabilities, and contractual constraints to produce a composite
    lock-in risk score and prioritised recommendations.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        lock_in_repo: ILockInRepository,
        event_publisher: EventPublisher,
        high_risk_threshold: float = 0.70,
        medium_risk_threshold: float = 0.40,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            vendor_repo: Vendor persistence.
            lock_in_repo: LockInAssessment persistence.
            event_publisher: Kafka event publisher.
            high_risk_threshold: Score at/above which risk is HIGH.
            medium_risk_threshold: Score at/above which risk is MEDIUM.
        """
        self._vendors = vendor_repo
        self._lock_in = lock_in_repo
        self._publisher = event_publisher
        self._high_threshold = high_risk_threshold
        self._medium_threshold = medium_risk_threshold

    async def assess_lock_in(
        self,
        vendor_id: uuid.UUID,
        tenant_id: uuid.UUID,
        proprietary_formats_score: float,
        switching_cost_score: float,
        api_openness_score: float,
        data_egress_score: float,
        contractual_lock_in_score: float,
        risk_factors: list[dict[str, Any]] | None = None,
        recommendations: list[dict[str, Any]] | None = None,
        assessed_by: uuid.UUID | None = None,
    ) -> LockInAssessment:
        """Run a lock-in risk assessment for a vendor.

        Computes a weighted composite lock-in score and classifies risk level.
        All dimension scores represent risk (0.0=no risk, 1.0=maximum risk).

        Args:
            vendor_id: Vendor UUID to assess.
            tenant_id: Requesting tenant.
            proprietary_formats_score: Degree of proprietary format usage (0.0–1.0).
            switching_cost_score: Relative switching cost estimate (0.0–1.0).
            api_openness_score: API lock-in degree (0.0=open standard, 1.0=proprietary).
            data_egress_score: Data egress difficulty (0.0=trivial, 1.0=impossible).
            contractual_lock_in_score: Contractual lock-in degree (0.0–1.0).
            risk_factors: Optional list of identified risk factor dicts.
            recommendations: Optional list of recommendation dicts.
            assessed_by: Optional user UUID triggering the assessment.

        Returns:
            Newly created LockInAssessment.

        Raises:
            NotFoundError: If vendor not found.
            ConflictError: If any score is outside [0.0, 1.0].
        """
        vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                message=f"Vendor {vendor_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        dimension_scores = {
            "proprietary_formats": proprietary_formats_score,
            "switching_cost": switching_cost_score,
            "api_openness": api_openness_score,
            "data_egress": data_egress_score,
            "contractual_lock_in": contractual_lock_in_score,
        }
        for dimension, score in dimension_scores.items():
            if not 0.0 <= score <= 1.0:
                raise ConflictError(
                    message=f"Score for '{dimension}' must be between 0.0 and 1.0, got {score}.",
                    error_code=ErrorCode.INVALID_OPERATION,
                )

        # Equal-weighted composite lock-in score
        lock_in_score = round(
            (
                proprietary_formats_score
                + switching_cost_score
                + api_openness_score
                + data_egress_score
                + contractual_lock_in_score
            )
            / 5.0,
            4,
        )
        risk_level = self._classify_lock_in_risk(lock_in_score)

        existing = await self._lock_in.get_current(vendor_id, tenant_id)

        assessment = await self._lock_in.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            assessed_by=assessed_by,
            lock_in_score=lock_in_score,
            risk_level=risk_level,
            proprietary_formats_score=proprietary_formats_score,
            switching_cost_score=switching_cost_score,
            api_openness_score=api_openness_score,
            data_egress_score=data_egress_score,
            contractual_lock_in_score=contractual_lock_in_score,
            risk_factors=risk_factors or [],
            recommendations=recommendations or [],
        )

        if existing is not None:
            await self._lock_in.deactivate_previous(vendor_id, assessment.id)

        logger.info(
            "Lock-in assessment completed",
            vendor_id=str(vendor_id),
            tenant_id=str(tenant_id),
            lock_in_score=lock_in_score,
            risk_level=risk_level,
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "vendor.lock_in_assessed",
                "tenant_id": str(tenant_id),
                "vendor_id": str(vendor_id),
                "lock_in_score": lock_in_score,
                "risk_level": risk_level,
                "assessment_id": str(assessment.id),
            },
        )

        return assessment

    async def get_lock_in_assessment(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> LockInAssessment:
        """Retrieve the current lock-in assessment for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            Current LockInAssessment.

        Raises:
            NotFoundError: If vendor not found or not yet assessed.
        """
        vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                message=f"Vendor {vendor_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        assessment = await self._lock_in.get_current(vendor_id, tenant_id)
        if assessment is None:
            raise NotFoundError(
                message=f"No lock-in assessment found for vendor {vendor_id}. Run an assessment first.",
                error_code=ErrorCode.NOT_FOUND,
            )
        return assessment

    def _classify_lock_in_risk(self, score: float) -> str:
        """Classify a lock-in score into a risk level.

        A higher score = higher lock-in risk.

        Args:
            score: Composite lock-in score (0.0–1.0).

        Returns:
            Risk level string: low | medium | high.
        """
        if score >= self._high_threshold:
            return "high"
        if score >= self._medium_threshold:
            return "medium"
        return "low"


class ContractAnalyzerService:
    """Analyse vendor contract risk with focus on liability cap detection.

    Implements the AumOS 88% cap liability policy: contracts that limit
    remedies to approximately 1 month of fees (fraction >= 0.88) receive
    an explicit warning and are elevated to high/critical risk level.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        contract_repo: IContractRepository,
        event_publisher: EventPublisher,
        liability_cap_warning_threshold: float = LIABILITY_CAP_WARNING_THRESHOLD,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            vendor_repo: Vendor persistence.
            contract_repo: Contract persistence.
            event_publisher: Kafka event publisher.
            liability_cap_warning_threshold: Fraction at which cap warning fires.
        """
        self._vendors = vendor_repo
        self._contracts = contract_repo
        self._publisher = event_publisher
        self._cap_threshold = liability_cap_warning_threshold

    async def submit_contract(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        contract_name: str,
        contract_type: str,
        effective_date: datetime | None = None,
        expiry_date: datetime | None = None,
        annual_value_usd: int | None = None,
        analysed_by: uuid.UUID | None = None,
    ) -> Contract:
        """Register a new contract for risk analysis.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor UUID this contract is with.
            contract_name: Contract title.
            contract_type: msa | sow | order_form | addendum | nda.
            effective_date: Optional contract effective date.
            expiry_date: Optional contract expiry date.
            annual_value_usd: Optional annual contract value in USD.
            analysed_by: Optional user UUID submitting the contract.

        Returns:
            Newly created Contract with no risk analysis yet.

        Raises:
            NotFoundError: If vendor not found.
            ConflictError: If contract_type is invalid.
        """
        vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                message=f"Vendor {vendor_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        if contract_type not in VALID_CONTRACT_TYPES:
            raise ConflictError(
                message=f"Invalid contract type '{contract_type}'. Valid types: {VALID_CONTRACT_TYPES}",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        contract = await self._contracts.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            contract_name=contract_name,
            contract_type=contract_type,
            effective_date=effective_date,
            expiry_date=expiry_date,
            annual_value_usd=annual_value_usd,
            analysed_by=analysed_by,
        )

        logger.info(
            "Contract submitted for analysis",
            tenant_id=str(tenant_id),
            contract_id=str(contract.id),
            vendor_id=str(vendor_id),
            contract_type=contract_type,
        )

        return contract

    async def analyze_contract(
        self,
        contract_id: uuid.UUID,
        tenant_id: uuid.UUID,
        liability_cap_months: float | None,
        annual_value_usd: int | None,
        auto_renewal_clause: bool,
        governing_law: str | None,
        clauses: dict[str, Any] | None = None,
        additional_risks: list[dict[str, Any]] | None = None,
    ) -> Contract:
        """Run risk analysis on a submitted contract.

        Applies the AumOS 88% cap liability policy to flag contracts where
        vendor liability is capped at approximately 1 month of fees or less.

        Args:
            contract_id: Contract UUID.
            tenant_id: Requesting tenant.
            liability_cap_months: Liability cap expressed in months of fees.
            annual_value_usd: Annual contract value in USD (used for cap computation).
            auto_renewal_clause: True if the contract auto-renews.
            governing_law: Governing law jurisdiction.
            clauses: Optional extracted clause dict.
            additional_risks: Optional list of additional risk dicts from external analysis.

        Returns:
            Updated Contract with risk analysis populated.

        Raises:
            NotFoundError: If contract not found.
        """
        contract = await self._contracts.get_by_id(contract_id, tenant_id)
        if contract is None:
            raise NotFoundError(
                message=f"Contract {contract_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        # Compute liability cap fraction
        liability_cap_fraction: float | None = None
        has_liability_cap_warning = False

        if liability_cap_months is not None:
            # Convert months to fraction of annual fees
            # 1 month = 1/12 = 0.0833... of annual
            liability_cap_fraction = round(liability_cap_months / 12.0, 4)
            has_liability_cap_warning = liability_cap_fraction <= (1.0 / 12.0 + 0.001)
            # Also flag by the raw fraction threshold
            if liability_cap_months <= 1.0:
                has_liability_cap_warning = True

        # Build risk list
        identified_risks: list[dict[str, Any]] = list(additional_risks or [])

        if has_liability_cap_warning:
            identified_risks.insert(0, {
                "risk_type": "liability_cap",
                "severity": "high",
                "clause_reference": "Liability / Indemnification Section",
                "description": (
                    f"Contract caps vendor liability at {liability_cap_months} month(s) of fees "
                    f"(fraction: {liability_cap_fraction:.2%}). "
                    "AumOS policy flags caps at or below 1 month as inadequate for enterprise AI risk."
                ),
                "recommendation": (
                    "Negotiate to raise the liability cap to a minimum of 12 months of fees "
                    "or a fixed amount (e.g., $5M), whichever is greater."
                ),
            })

        if auto_renewal_clause:
            identified_risks.append({
                "risk_type": "auto_renewal",
                "severity": "medium",
                "clause_reference": "Term / Renewal Section",
                "description": (
                    "Contract auto-renews without explicit opt-out. "
                    "Missed cancellation windows can result in unintended multi-year commitments."
                ),
                "recommendation": (
                    "Add calendar reminders 90 and 30 days before each renewal date. "
                    "Consider negotiating a longer notice window (60-90 days minimum)."
                ),
            })

        # Compute risk score: starts at 0, increases for each identified risk
        risk_score = self._compute_risk_score(
            has_liability_cap_warning=has_liability_cap_warning,
            auto_renewal_clause=auto_renewal_clause,
            additional_risk_count=len(additional_risks or []),
        )
        risk_level = self._classify_contract_risk(risk_score)

        now = datetime.now(tz=timezone.utc)
        updated_contract = await self._contracts.update_risk_analysis(
            contract_id=contract_id,
            liability_cap_months=liability_cap_months,
            liability_cap_fraction=liability_cap_fraction,
            has_liability_cap_warning=has_liability_cap_warning,
            auto_renewal_clause=auto_renewal_clause,
            governing_law=governing_law,
            risk_score=risk_score,
            risk_level=risk_level,
            identified_risks=identified_risks,
            clauses=clauses or {},
            analysed_at=now,
        )

        logger.info(
            "Contract risk analysis completed",
            contract_id=str(contract_id),
            tenant_id=str(tenant_id),
            risk_score=risk_score,
            risk_level=risk_level,
            has_liability_cap_warning=has_liability_cap_warning,
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "contract.analyzed",
                "tenant_id": str(tenant_id),
                "contract_id": str(contract_id),
                "vendor_id": str(contract.vendor_id),
                "risk_score": risk_score,
                "risk_level": risk_level,
                "has_liability_cap_warning": has_liability_cap_warning,
                "risk_count": len(identified_risks),
            },
        )

        return updated_contract

    async def get_contract_risks(
        self, contract_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Contract:
        """Retrieve the contract risk report for a contract.

        Args:
            contract_id: Contract UUID.
            tenant_id: Requesting tenant.

        Returns:
            Contract with risk analysis populated.

        Raises:
            NotFoundError: If contract not found.
        """
        contract = await self._contracts.get_by_id(contract_id, tenant_id)
        if contract is None:
            raise NotFoundError(
                message=f"Contract {contract_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )
        return contract

    @staticmethod
    def _compute_risk_score(
        has_liability_cap_warning: bool,
        auto_renewal_clause: bool,
        additional_risk_count: int,
    ) -> float:
        """Compute a normalised risk score for a contract.

        Args:
            has_liability_cap_warning: True if liability cap is below threshold.
            auto_renewal_clause: True if auto-renewal clause is present.
            additional_risk_count: Number of additional risks identified.

        Returns:
            Risk score (0.0–1.0).
        """
        score = 0.0
        if has_liability_cap_warning:
            score += 0.50  # Liability cap is the most significant risk
        if auto_renewal_clause:
            score += 0.20
        # Each additional risk adds diminishing weight
        score += min(additional_risk_count * 0.05, 0.30)
        return round(min(score, 1.0), 4)

    @staticmethod
    def _classify_contract_risk(score: float) -> str:
        """Classify a contract risk score into a risk level.

        Args:
            score: Contract risk score (0.0–1.0).

        Returns:
            Risk level: low | medium | high | critical.
        """
        if score >= 0.75:
            return "critical"
        if score >= 0.50:
            return "high"
        if score >= 0.25:
            return "medium"
        return "low"


class InsuranceCheckerService:
    """Detect insurance coverage gaps in AI vendor relationships.

    Compares vendor insurance documentation against AumOS minimum coverage
    requirements. Creates gap records for deficiencies and tracks remediation.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        insurance_gap_repo: IInsuranceGapRepository,
        event_publisher: EventPublisher,
        required_coverage_types: list[str] | None = None,
        minimum_coverage_amount_usd: int = 5_000_000,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            vendor_repo: Vendor persistence.
            insurance_gap_repo: InsuranceGap persistence.
            event_publisher: Kafka event publisher.
            required_coverage_types: Coverage types required by AumOS policy.
            minimum_coverage_amount_usd: Minimum per-incident coverage in USD.
        """
        self._vendors = vendor_repo
        self._gaps = insurance_gap_repo
        self._publisher = event_publisher
        self._required_types = required_coverage_types or list(REQUIRED_COVERAGE_TYPES)
        self._minimum_coverage = minimum_coverage_amount_usd

    async def check_insurance(
        self,
        vendor_id: uuid.UUID,
        tenant_id: uuid.UUID,
        coverages: list[dict[str, Any]],
        contract_id: uuid.UUID | None = None,
        checked_by: uuid.UUID | None = None,
    ) -> list[InsuranceGap]:
        """Check vendor insurance coverages and create gap records for deficiencies.

        Compares provided coverage data against required types and minimum amounts.
        Creates InsuranceGap records for each identified deficiency.

        Args:
            vendor_id: Vendor UUID to check.
            tenant_id: Requesting tenant.
            coverages: List of coverage dicts:
                [{\"type\": \"cyber_liability\", \"amount_usd\": 3000000, ...}]
            contract_id: Optional related contract UUID.
            checked_by: Optional user UUID performing the check.

        Returns:
            List of newly created InsuranceGap records.

        Raises:
            NotFoundError: If vendor not found.
        """
        vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                message=f"Vendor {vendor_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        covered_types: dict[str, int] = {
            cov["type"]: cov.get("amount_usd", 0)
            for cov in coverages
            if "type" in cov
        }

        new_gaps: list[InsuranceGap] = []
        now = datetime.now(tz=timezone.utc)

        for required_type in self._required_types:
            actual_coverage = covered_types.get(required_type)

            if actual_coverage is None:
                # Coverage type is completely missing
                severity = "critical"
                description = (
                    f"Vendor has no documented {required_type.replace('_', ' ')} insurance. "
                    f"AumOS policy requires a minimum of ${self._minimum_coverage:,} coverage."
                )
                gap_amount = self._minimum_coverage

            elif actual_coverage < self._minimum_coverage:
                # Coverage exists but is below minimum threshold
                severity = "high" if actual_coverage < self._minimum_coverage * 0.5 else "medium"
                gap_amount = self._minimum_coverage - actual_coverage
                description = (
                    f"Vendor {required_type.replace('_', ' ')} insurance (${actual_coverage:,}) "
                    f"is below the required minimum (${self._minimum_coverage:,}). "
                    f"Gap: ${gap_amount:,}."
                )

            else:
                # Coverage is adequate — no gap
                continue

            gap = await self._gaps.create(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                contract_id=contract_id,
                coverage_type=required_type,
                required_coverage_usd=self._minimum_coverage,
                actual_coverage_usd=actual_coverage,
                severity=severity,
                description=description,
                detected_by=checked_by,
            )
            new_gaps.append(gap)

            await self._publisher.publish(
                Topics.VENDOR_INTELLIGENCE,
                {
                    "event_type": "insurance.gap_detected",
                    "tenant_id": str(tenant_id),
                    "vendor_id": str(vendor_id),
                    "gap_id": str(gap.id),
                    "coverage_type": required_type,
                    "severity": severity,
                    "required_coverage_usd": self._minimum_coverage,
                    "actual_coverage_usd": actual_coverage,
                },
            )

        logger.info(
            "Insurance check completed",
            vendor_id=str(vendor_id),
            tenant_id=str(tenant_id),
            gaps_found=len(new_gaps),
            coverages_checked=len(self._required_types),
        )

        return new_gaps

    async def get_insurance_gaps(
        self,
        vendor_id: uuid.UUID,
        tenant_id: uuid.UUID,
        status: str | None = None,
    ) -> list[InsuranceGap]:
        """Retrieve all insurance gaps for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.
            status: Optional status filter.

        Returns:
            List of InsuranceGap instances.

        Raises:
            NotFoundError: If vendor not found.
        """
        vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                message=f"Vendor {vendor_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        return await self._gaps.list_by_vendor(vendor_id, tenant_id, status)

    async def update_gap_status(
        self,
        gap_id: uuid.UUID,
        tenant_id: uuid.UUID,
        status: str,
        remediation_notes: str | None = None,
    ) -> InsuranceGap:
        """Update the status of an insurance gap.

        Args:
            gap_id: InsuranceGap UUID.
            tenant_id: Requesting tenant.
            status: New status: open | remediated | accepted | escalated.
            remediation_notes: Optional notes on remediation steps.

        Returns:
            Updated InsuranceGap.

        Raises:
            NotFoundError: If gap not found.
        """
        gap = await self._gaps.get_by_id(gap_id, tenant_id)
        if gap is None:
            raise NotFoundError(
                message=f"Insurance gap {gap_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        remediated_at: datetime | None = None
        if status == "remediated":
            remediated_at = datetime.now(tz=timezone.utc)

        updated_gap = await self._gaps.update_status(
            gap_id=gap_id,
            status=status,
            remediation_notes=remediation_notes,
            remediated_at=remediated_at,
        )

        logger.info(
            "Insurance gap status updated",
            gap_id=str(gap_id),
            tenant_id=str(tenant_id),
            new_status=status,
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "insurance.gap_updated",
                "tenant_id": str(tenant_id),
                "gap_id": str(gap_id),
                "vendor_id": str(gap.vendor_id),
                "coverage_type": gap.coverage_type,
                "new_status": status,
            },
        )

        return updated_gap


class BenchmarkingService:
    """Orchestrate cross-vendor benchmarking runs and persist comparison reports.

    Delegates low-level benchmark execution to IBenchmarkingRunner and publishes
    results as vendor intelligence events for downstream analytics.
    """

    def __init__(
        self,
        benchmarking_runner: IBenchmarkingRunner,
        vendor_repo: IVendorRepository,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            benchmarking_runner: Adapter that executes latency/quality benchmarks.
            vendor_repo: Vendor persistence for validation.
            event_publisher: Kafka event publisher.
        """
        self._runner = benchmarking_runner
        self._vendors = vendor_repo
        self._publisher = event_publisher

    async def run_vendor_benchmark(
        self,
        tenant_id: uuid.UUID,
        vendor_ids: list[uuid.UUID],
        prompt_payloads: list[dict[str, Any]],
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Execute a latency and quality benchmark across multiple vendors.

        Validates that all vendors exist within the tenant before running the
        benchmark. Publishes results as a ``benchmark.completed`` event.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_ids: List of vendor UUIDs to benchmark.
            prompt_payloads: Prompt/response pairs to send to each vendor.
            timeout_seconds: Per-request timeout in seconds.

        Returns:
            Comparison report dict with latency, quality, and cost metrics.

        Raises:
            NotFoundError: If any vendor ID is not found for this tenant.
            ConflictError: If fewer than 2 vendor IDs are provided.
        """
        if len(vendor_ids) < 2:
            raise ConflictError(
                message="At least 2 vendor IDs are required for benchmarking.",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        for vendor_id in vendor_ids:
            vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
            if vendor is None:
                raise NotFoundError(
                    message=f"Vendor {vendor_id} not found.",
                    error_code=ErrorCode.NOT_FOUND,
                )

        latency_results = await self._runner.run_latency_benchmark(
            vendor_ids=vendor_ids,
            prompt_payloads=prompt_payloads,
            timeout_seconds=timeout_seconds,
        )

        report = await self._runner.generate_comparison_report(
            benchmark_results=latency_results,
            tenant_id=tenant_id,
        )

        logger.info(
            "Vendor benchmark completed",
            tenant_id=str(tenant_id),
            vendor_count=len(vendor_ids),
            prompt_count=len(prompt_payloads),
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "benchmark.completed",
                "tenant_id": str(tenant_id),
                "vendor_ids": [str(v) for v in vendor_ids],
                "prompt_count": len(prompt_payloads),
            },
        )

        return report


class ContractTextAnalysisService:
    """Analyse vendor contract text for risk, SLA terms, and pricing structure.

    Wraps IContractAnalyzer to provide validated contract text analysis
    without requiring database-backed contract records.
    """

    def __init__(
        self,
        contract_analyzer: IContractAnalyzer,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            contract_analyzer: Adapter for contract text parsing.
            event_publisher: Kafka event publisher.
        """
        self._analyzer = contract_analyzer
        self._publisher = event_publisher

    async def analyze_contract_text(
        self,
        tenant_id: uuid.UUID,
        contract_text: str,
        annual_value_usd: float | None = None,
    ) -> dict[str, Any]:
        """Run full contract text analysis and return combined risk report.

        Args:
            tenant_id: Requesting tenant UUID.
            contract_text: Raw contract text to analyse.
            annual_value_usd: Optional annual value for liability cap computation.

        Returns:
            Combined analysis dict with sla_terms, pricing_structure,
            liability_analysis, termination_analysis, and key_terms_summary.

        Raises:
            ConflictError: If contract_text is empty.
        """
        if not contract_text or not contract_text.strip():
            raise ConflictError(
                message="Contract text must not be empty.",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        sla_terms = await self._analyzer.extract_sla_terms(contract_text)
        pricing = await self._analyzer.parse_pricing_structure(contract_text)
        termination = await self._analyzer.analyze_termination_clauses(contract_text)
        liability = await self._analyzer.detect_liability_limitations(
            contract_text, annual_value_usd
        )
        summary = await self._analyzer.generate_key_terms_summary(contract_text)

        report = {
            "tenant_id": str(tenant_id),
            "sla_terms": sla_terms,
            "pricing_structure": pricing,
            "termination_analysis": termination,
            "liability_analysis": liability,
            "key_terms_summary": summary,
        }

        logger.info(
            "Contract text analysis completed",
            tenant_id=str(tenant_id),
            has_liability_warning=liability.get("has_cap_warning", False),
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "contract.text_analyzed",
                "tenant_id": str(tenant_id),
                "has_liability_warning": liability.get("has_cap_warning", False),
            },
        )

        return report


class RoutingService:
    """Manage vendor health monitoring and automatic failover routing.

    Uses IFallbackRouter to select healthy vendors and ISLAMonitor to
    track ongoing SLA compliance across the vendor portfolio.
    """

    def __init__(
        self,
        fallback_router: IFallbackRouter,
        sla_monitor: ISLAMonitor,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            fallback_router: Circuit-breaker based vendor router.
            sla_monitor: SLA compliance tracker.
            event_publisher: Kafka event publisher.
        """
        self._router = fallback_router
        self._sla_monitor = sla_monitor
        self._publisher = event_publisher

    async def select_routing_target(
        self,
        tenant_id: uuid.UUID,
        excluded_vendor_ids: list[uuid.UUID] | None = None,
    ) -> uuid.UUID | None:
        """Select the best healthy vendor for request routing.

        Args:
            tenant_id: Requesting tenant UUID.
            excluded_vendor_ids: Optional vendor IDs to skip.

        Returns:
            UUID of the selected vendor or None if no healthy vendors available.
        """
        selected = await self._router.select_vendor(
            tenant_id=tenant_id,
            excluded_vendor_ids=excluded_vendor_ids,
        )

        if selected is None:
            logger.warning(
                "No healthy vendor available for routing",
                tenant_id=str(tenant_id),
            )
        else:
            logger.debug(
                "Vendor selected for routing",
                tenant_id=str(tenant_id),
                vendor_id=str(selected),
            )

        return selected

    async def record_request_outcome(
        self,
        vendor_id: uuid.UUID,
        success: bool,
        latency_ms: float,
        error_code: str | None = None,
    ) -> None:
        """Record a completed request outcome for health tracking.

        Args:
            vendor_id: Vendor that handled the request.
            success: Whether the request succeeded.
            latency_ms: Request latency in milliseconds.
            error_code: Optional error code if the request failed.
        """
        from datetime import datetime, timezone
        await self._router.record_vendor_outcome(vendor_id, success, latency_ms)
        await self._sla_monitor.record_health_check(
            vendor_id=vendor_id,
            is_up=success,
            latency_ms=latency_ms,
            error_code=error_code,
            checked_at=datetime.now(tz=timezone.utc),
        )


class ArbitrageService:
    """Detect vendor pricing arbitrage opportunities and generate savings reports.

    Coordinates IArbitrageDetector to identify cost optimisation opportunities
    across the vendor portfolio.
    """

    def __init__(
        self,
        arbitrage_detector: IArbitrageDetector,
        vendor_repo: IVendorRepository,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            arbitrage_detector: Pareto and pricing analysis adapter.
            vendor_repo: Vendor persistence for validation.
            event_publisher: Kafka event publisher.
        """
        self._detector = arbitrage_detector
        self._vendors = vendor_repo
        self._publisher = event_publisher

    async def generate_savings_report(
        self,
        tenant_id: uuid.UUID,
        current_vendor_id: uuid.UUID,
        candidate_vendor_ids: list[uuid.UUID],
        monthly_token_volume: int,
    ) -> dict[str, Any]:
        """Generate a vendor arbitrage savings report.

        Args:
            tenant_id: Requesting tenant UUID.
            current_vendor_id: Currently used vendor UUID.
            candidate_vendor_ids: Alternative vendor UUIDs to evaluate.
            monthly_token_volume: Monthly token consumption estimate.

        Returns:
            Savings report with potential cost reductions and recommendations.

        Raises:
            NotFoundError: If current vendor not found.
        """
        vendor = await self._vendors.get_by_id(current_vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                message=f"Vendor {current_vendor_id} not found.",
                error_code=ErrorCode.NOT_FOUND,
            )

        report = await self._detector.generate_savings_report(
            tenant_id=tenant_id,
            current_vendor_id=current_vendor_id,
            candidate_vendors=candidate_vendor_ids,
            monthly_token_volume=monthly_token_volume,
        )

        logger.info(
            "Arbitrage savings report generated",
            tenant_id=str(tenant_id),
            current_vendor=str(current_vendor_id),
            candidates=len(candidate_vendor_ids),
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "arbitrage.report_generated",
                "tenant_id": str(tenant_id),
                "current_vendor_id": str(current_vendor_id),
                "candidate_count": len(candidate_vendor_ids),
                "potential_savings_usd": report.get("total_potential_savings_usd", 0.0),
            },
        )

        return report


class ProcurementService:
    """Provide vendor shortlisting, RFP generation, and comparison matrices.

    Coordinates IProcurementAdvisor and IVendorDataEnricher to support
    structured procurement decision workflows.
    """

    def __init__(
        self,
        procurement_advisor: IProcurementAdvisor,
        data_enricher: IVendorDataEnricher,
        vendor_repo: IVendorRepository,
        event_publisher: EventPublisher,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            procurement_advisor: Multi-criteria scoring and RFP adapter.
            data_enricher: Vendor profile enrichment adapter.
            vendor_repo: Vendor persistence.
            event_publisher: Kafka event publisher.
        """
        self._advisor = procurement_advisor
        self._enricher = data_enricher
        self._vendors = vendor_repo
        self._publisher = event_publisher

    async def run_procurement_evaluation(
        self,
        tenant_id: uuid.UUID,
        requirements: dict[str, Any],
        candidate_vendor_ids: list[uuid.UUID],
        top_n: int = 3,
        scoring_weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Run a full procurement evaluation and generate a shortlist.

        Args:
            tenant_id: Requesting tenant UUID.
            requirements: Procurement requirements dict.
            candidate_vendor_ids: Vendor UUIDs to evaluate.
            top_n: Number of vendors to include in the shortlist.
            scoring_weights: Optional custom scoring weights.

        Returns:
            Dict with shortlist, comparison_matrix, and rfp_template.

        Raises:
            NotFoundError: If any vendor ID is not found.
            ConflictError: If fewer than 2 candidates are provided.
        """
        if len(candidate_vendor_ids) < 2:
            raise ConflictError(
                message="At least 2 candidate vendors are required for procurement evaluation.",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        vendor_profiles: list[dict[str, Any]] = []
        for vendor_id in candidate_vendor_ids:
            vendor = await self._vendors.get_by_id(vendor_id, tenant_id)
            if vendor is None:
                raise NotFoundError(
                    message=f"Vendor {vendor_id} not found.",
                    error_code=ErrorCode.NOT_FOUND,
                )
            completeness = await self._enricher.score_profile_completeness(
                {
                    "id": str(vendor.id),
                    "name": vendor.name,
                    "category": vendor.category,
                    "overall_score": vendor.overall_score,
                    "website_url": vendor.website_url,
                }
            )
            vendor_profiles.append(
                {
                    "id": str(vendor.id),
                    "name": vendor.name,
                    "category": vendor.category,
                    "overall_score": vendor.overall_score,
                    "profile_completeness": completeness.get("completeness_score", 0.0),
                }
            )

        matched = await self._advisor.match_requirements_to_vendors(
            requirements=requirements,
            available_vendors=vendor_profiles,
        )
        scored = await self._advisor.score_vendors_multi_criteria(
            vendors=matched,
            scoring_weights=scoring_weights,
        )
        shortlist = await self._advisor.generate_shortlist(
            scored_vendors=scored,
            top_n=top_n,
        )
        matrix = await self._advisor.generate_comparison_matrix(scored_vendors=scored)
        rfp = await self._advisor.prepare_rfp_template(
            requirements=requirements,
            shortlisted_vendor_ids=[
                uuid.UUID(v["id"]) for v in shortlist.get("shortlisted_vendors", [])
            ],
        )

        result = {
            "shortlist": shortlist,
            "comparison_matrix": matrix,
            "rfp_template": rfp,
        }

        logger.info(
            "Procurement evaluation completed",
            tenant_id=str(tenant_id),
            candidates_evaluated=len(candidate_vendor_ids),
            shortlist_size=len(shortlist.get("shortlisted_vendors", [])),
        )

        await self._publisher.publish(
            Topics.VENDOR_INTELLIGENCE,
            {
                "event_type": "procurement.evaluation_completed",
                "tenant_id": str(tenant_id),
                "candidates_evaluated": len(candidate_vendor_ids),
            },
        )

        return result


class VendorDashboardService:
    """Aggregate vendor intelligence data for executive dashboards.

    Coordinates IVendorDashboardAggregator to compile performance trends,
    cost trends, and usage distribution into a unified dashboard payload.
    """

    def __init__(
        self,
        dashboard_aggregator: IVendorDashboardAggregator,
        vendor_repo: IVendorRepository,
    ) -> None:
        """Initialise with injected dependencies.

        Args:
            dashboard_aggregator: Dashboard data aggregation adapter.
            vendor_repo: Vendor persistence for validation.
        """
        self._aggregator = dashboard_aggregator
        self._vendors = vendor_repo

    async def get_executive_dashboard(
        self,
        tenant_id: uuid.UUID,
        vendor_ids: list[uuid.UUID],
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Compile the executive vendor intelligence dashboard.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_ids: Vendor UUIDs to include in the dashboard.
            period_days: Lookback period in days.

        Returns:
            Complete executive dashboard payload.

        Raises:
            ConflictError: If no vendor IDs are provided.
        """
        if not vendor_ids:
            raise ConflictError(
                message="At least one vendor ID is required for the dashboard.",
                error_code=ErrorCode.INVALID_OPERATION,
            )

        dashboard = await self._aggregator.export_executive_dashboard(
            tenant_id=tenant_id,
            vendor_ids=vendor_ids,
            period_days=period_days,
        )

        logger.info(
            "Executive vendor dashboard compiled",
            tenant_id=str(tenant_id),
            vendor_count=len(vendor_ids),
            period_days=period_days,
        )

        return dashboard
