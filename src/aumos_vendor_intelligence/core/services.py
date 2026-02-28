"""Business logic services for the AumOS Vendor Intelligence service.

All services depend on repository and adapter interfaces (not concrete
implementations) and receive dependencies via constructor injection.
No framework code (FastAPI, SQLAlchemy) belongs here.

Key invariants enforced by services:
- Vendor scores are always recomputed from individual criteria, never set directly.
- Lock-in risk levels are derived from score thresholds (configurable).
- Liability cap warnings are triggered when cap fraction >= 0.88 (AumOS policy).
- Insurance gaps are deduplicated by vendor + coverage_type per tenant.
- Questionnaire tokens expire after configured due_days.
- ISO 42001 assessments are created/updated per control per vendor.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
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
    IIso42001Repository,
    ILockInRepository,
    IMonitoringAlertRepository,
    INegotiationPlaybookGenerator,
    IProcurementAdvisor,
    IQuestionnaireRepository,
    ISLAMonitor,
    IVendorDashboardAggregator,
    IVendorDataEnricher,
    IVendorMonitoringAdapter,
    IVendorRepository,
)
from aumos_vendor_intelligence.core.models import (
    Contract,
    InsuranceGap,
    LockInAssessment,
    Vendor,
    VendorEvaluation,
    VinMonitoringAlert,
    VinQuestionnaireSubmission,
    VinQuestionnaireTemplate,
    VinVendorIso42001Assessment,
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

# Evaluation criteria weights (must sum to 1.0)
EVALUATION_WEIGHTS: dict[str, float] = {
    "api_compatibility": 0.25,
    "data_portability": 0.25,
    "security_posture": 0.20,
    "pricing_transparency": 0.15,
    "support_quality": 0.15,
}

# Lock-in risk thresholds
LOCK_IN_HIGH_THRESHOLD: float = 0.70
LOCK_IN_MEDIUM_THRESHOLD: float = 0.40

# Insurance coverage requirements
REQUIRED_COVERAGE_TYPES: list[str] = [
    "cyber_liability",
    "errors_and_omissions",
    "technology_professional_liability",
]
MINIMUM_COVERAGE_USD: int = 5_000_000

# Liability cap threshold (AumOS policy)
LIABILITY_CAP_WARNING_THRESHOLD: float = 0.88


class VendorScorerService:
    """Register vendors and compute multi-criteria evaluation scores.

    Args:
        vendor_repo: Repository for Vendor persistence.
        evaluation_repo: Repository for VendorEvaluation persistence.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        evaluation_repo: IEvaluationRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._vendor_repo = vendor_repo
        self._evaluation_repo = evaluation_repo
        self._event_publisher = event_publisher

    async def register_vendor(
        self,
        tenant_id: uuid.UUID,
        name: str,
        category: str,
        description: str | None,
        website_url: str | None,
        api_compatibility: dict[str, Any],
        data_portability: dict[str, Any],
        contact_info: dict[str, Any],
        registered_by: uuid.UUID | None,
    ) -> Vendor:
        """Register a new vendor for evaluation.

        Args:
            tenant_id: Owning tenant UUID.
            name: Vendor name.
            category: Vendor category from VALID_VENDOR_CATEGORIES.
            description: Optional description.
            website_url: Optional website URL.
            api_compatibility: API compatibility metadata.
            data_portability: Data portability metadata.
            contact_info: Contact information dict.
            registered_by: User UUID who registered the vendor.

        Returns:
            Newly created Vendor in under_review status.

        Raises:
            ValueError: If category is not in VALID_VENDOR_CATEGORIES.
        """
        if category not in VALID_VENDOR_CATEGORIES:
            raise ValueError(
                f"Invalid vendor category '{category}'. "
                f"Valid values: {sorted(VALID_VENDOR_CATEGORIES)}"
            )

        vendor = await self._vendor_repo.create(
            tenant_id=tenant_id,
            name=name,
            category=category,
            description=description,
            website_url=website_url,
            api_compatibility=api_compatibility,
            data_portability=data_portability,
            contact_info=contact_info,
            registered_by=registered_by,
        )

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "vendor.registered",
                "vendor_id": str(vendor.id),
                "tenant_id": str(tenant_id),
                "vendor_name": name,
                "category": category,
            },
        )

        logger.info("vendor_registered", vendor_id=str(vendor.id), tenant_id=str(tenant_id))
        return vendor

    def _compute_weighted_score(
        self,
        api_compatibility_score: float,
        data_portability_score: float,
        security_posture_score: float,
        pricing_transparency_score: float,
        support_quality_score: float,
    ) -> float:
        """Compute weighted composite evaluation score.

        Args:
            api_compatibility_score: Score 0.0–1.0 for API compatibility.
            data_portability_score: Score 0.0–1.0 for data portability.
            security_posture_score: Score 0.0–1.0 for security posture.
            pricing_transparency_score: Score 0.0–1.0 for pricing transparency.
            support_quality_score: Score 0.0–1.0 for support quality.

        Returns:
            Weighted composite score 0.0–1.0.
        """
        return (
            api_compatibility_score * EVALUATION_WEIGHTS["api_compatibility"]
            + data_portability_score * EVALUATION_WEIGHTS["data_portability"]
            + security_posture_score * EVALUATION_WEIGHTS["security_posture"]
            + pricing_transparency_score * EVALUATION_WEIGHTS["pricing_transparency"]
            + support_quality_score * EVALUATION_WEIGHTS["support_quality"]
        )

    async def evaluate_vendor(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        evaluator_id: uuid.UUID | None,
        api_compatibility_score: float,
        data_portability_score: float,
        security_posture_score: float,
        pricing_transparency_score: float,
        support_quality_score: float,
        notes: str | None,
        raw_responses: dict[str, Any],
    ) -> VendorEvaluation:
        """Run a multi-criteria evaluation for a vendor.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor to evaluate.
            evaluator_id: User performing the evaluation.
            api_compatibility_score: Score 0.0–1.0.
            data_portability_score: Score 0.0–1.0.
            security_posture_score: Score 0.0–1.0.
            pricing_transparency_score: Score 0.0–1.0.
            support_quality_score: Score 0.0–1.0.
            notes: Optional evaluation notes.
            raw_responses: Evidence and rationale by criterion.

        Returns:
            Created VendorEvaluation with computed overall_score.

        Raises:
            NotFoundError: If vendor does not exist for tenant.
            ValueError: If any score is outside 0.0–1.0.
        """
        for name, score in [
            ("api_compatibility_score", api_compatibility_score),
            ("data_portability_score", data_portability_score),
            ("security_posture_score", security_posture_score),
            ("pricing_transparency_score", pricing_transparency_score),
            ("support_quality_score", support_quality_score),
        ]:
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"{name} must be between 0.0 and 1.0, got {score}")

        vendor = await self._vendor_repo.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                f"Vendor {vendor_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        overall_score = self._compute_weighted_score(
            api_compatibility_score=api_compatibility_score,
            data_portability_score=data_portability_score,
            security_posture_score=security_posture_score,
            pricing_transparency_score=pricing_transparency_score,
            support_quality_score=support_quality_score,
        )

        evaluation = await self._evaluation_repo.create(
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
            raw_responses=raw_responses,
        )

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "vendor.evaluated",
                "vendor_id": str(vendor_id),
                "evaluation_id": str(evaluation.id),
                "tenant_id": str(tenant_id),
                "overall_score": overall_score,
            },
        )

        logger.info(
            "vendor_evaluated",
            vendor_id=str(vendor_id),
            evaluation_id=str(evaluation.id),
            overall_score=overall_score,
        )
        return evaluation

    async def get_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Vendor:
        """Retrieve a vendor by ID.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Tenant UUID for RLS enforcement.

        Returns:
            Vendor instance.

        Raises:
            NotFoundError: If vendor does not exist.
        """
        vendor = await self._vendor_repo.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                f"Vendor {vendor_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )
        return vendor

    async def list_vendors(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        category: str | None = None,
        status: str | None = None,
    ) -> list[Vendor]:
        """List vendors for a tenant with optional filters.

        Args:
            tenant_id: Tenant UUID.
            page: Page number (1-based).
            page_size: Records per page.
            category: Optional category filter.
            status: Optional status filter.

        Returns:
            Paginated list of Vendor records.
        """
        return await self._vendor_repo.list_by_tenant(
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
            category=category,
            status=status,
        )

    async def compare_vendors(
        self,
        tenant_id: uuid.UUID,
        vendor_ids: list[uuid.UUID],
    ) -> list[Vendor]:
        """Retrieve multiple vendors for side-by-side comparison.

        Args:
            tenant_id: Tenant UUID.
            vendor_ids: List of vendor UUIDs to compare.

        Returns:
            List of Vendor instances (may be shorter if some not found).
        """
        return await self._vendor_repo.get_many_by_ids(vendor_ids, tenant_id)


class LockInAssessorService:
    """Assess and retrieve vendor lock-in risk.

    Args:
        vendor_repo: Repository for Vendor persistence.
        lock_in_repo: Repository for LockInAssessment persistence.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        lock_in_repo: ILockInRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._vendor_repo = vendor_repo
        self._lock_in_repo = lock_in_repo
        self._event_publisher = event_publisher

    def _compute_risk_level(self, lock_in_score: float) -> str:
        """Map composite lock-in score to risk level string.

        Args:
            lock_in_score: Composite score 0.0–1.0.

        Returns:
            Risk level: 'low' | 'medium' | 'high'.
        """
        if lock_in_score >= LOCK_IN_HIGH_THRESHOLD:
            return "high"
        if lock_in_score >= LOCK_IN_MEDIUM_THRESHOLD:
            return "medium"
        return "low"

    async def assess_lock_in(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        assessed_by: uuid.UUID | None,
        proprietary_formats_score: float,
        switching_cost_score: float,
        api_openness_score: float,
        data_egress_score: float,
        contractual_lock_in_score: float,
        risk_factors: list[dict[str, Any]],
        recommendations: list[dict[str, Any]],
    ) -> LockInAssessment:
        """Run a lock-in risk assessment for a vendor.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor to assess.
            assessed_by: User triggering the assessment.
            proprietary_formats_score: Proprietary format usage score 0.0–1.0.
            switching_cost_score: Switching cost score 0.0–1.0.
            api_openness_score: API openness score 0.0–1.0 (inverted).
            data_egress_score: Data egress ease score 0.0–1.0 (inverted).
            contractual_lock_in_score: Contractual lock-in score 0.0–1.0.
            risk_factors: List of identified risk factor dicts.
            recommendations: List of recommendation dicts.

        Returns:
            Created LockInAssessment with computed lock_in_score and risk_level.

        Raises:
            NotFoundError: If vendor does not exist.
        """
        vendor = await self._vendor_repo.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                f"Vendor {vendor_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        lock_in_score = (
            proprietary_formats_score
            + switching_cost_score
            + api_openness_score
            + data_egress_score
            + contractual_lock_in_score
        ) / 5.0

        risk_level = self._compute_risk_level(lock_in_score)

        assessment = await self._lock_in_repo.create(
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
            risk_factors=risk_factors,
            recommendations=recommendations,
        )

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "vendor.lock_in_assessed",
                "vendor_id": str(vendor_id),
                "assessment_id": str(assessment.id),
                "tenant_id": str(tenant_id),
                "lock_in_score": lock_in_score,
                "risk_level": risk_level,
            },
        )

        logger.info(
            "lock_in_assessed",
            vendor_id=str(vendor_id),
            lock_in_score=lock_in_score,
            risk_level=risk_level,
        )
        return assessment

    async def get_current_assessment(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> LockInAssessment | None:
        """Retrieve the most recent lock-in assessment for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Tenant UUID.

        Returns:
            Current LockInAssessment or None if never assessed.
        """
        return await self._lock_in_repo.get_current(vendor_id, tenant_id)


class ContractAnalyzerService:
    """Submit and analyse vendor contract risk.

    Args:
        vendor_repo: Repository for Vendor persistence.
        contract_repo: Repository for Contract persistence.
        contract_analyzer: LLM-backed contract analysis adapter.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        contract_repo: IContractRepository,
        contract_analyzer: IContractAnalyzer,
        event_publisher: EventPublisher,
    ) -> None:
        self._vendor_repo = vendor_repo
        self._contract_repo = contract_repo
        self._contract_analyzer = contract_analyzer
        self._event_publisher = event_publisher

    def _check_liability_cap_warning(
        self,
        liability_cap_fraction: float | None,
        liability_cap_months: float | None,
    ) -> bool:
        """Determine whether the 88% liability cap policy is triggered.

        Args:
            liability_cap_fraction: Cap as fraction of annual fees.
            liability_cap_months: Cap expressed as months of fees.

        Returns:
            True if a liability cap warning should be raised.
        """
        if liability_cap_fraction is not None and liability_cap_fraction >= LIABILITY_CAP_WARNING_THRESHOLD:
            return True
        if liability_cap_months is not None and liability_cap_months <= 1.0:
            return True
        return False

    async def analyze_contract(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        analysed_by: uuid.UUID | None,
        contract_name: str,
        contract_type: str,
        effective_date: datetime | None,
        expiry_date: datetime | None,
        annual_value_usd: int | None,
        liability_cap_months: float | None,
        liability_cap_fraction: float | None,
        auto_renewal_clause: bool,
        governing_law: str | None,
        clauses: dict[str, Any],
    ) -> Contract:
        """Submit a contract for risk analysis.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Associated vendor UUID.
            analysed_by: User submitting the contract.
            contract_name: Contract reference name.
            contract_type: msa | sow | order_form | addendum | nda.
            effective_date: Optional effective date.
            expiry_date: Optional expiry/renewal date.
            annual_value_usd: Optional annual value in USD.
            liability_cap_months: Liability cap in months of fees.
            liability_cap_fraction: Liability cap as fraction of annual fees.
            auto_renewal_clause: Whether auto-renewal clause present.
            governing_law: Governing law jurisdiction.
            clauses: Extracted clause text keyed by clause type.

        Returns:
            Analysed Contract with risk_score, risk_level, and identified_risks.

        Raises:
            NotFoundError: If vendor does not exist.
        """
        vendor = await self._vendor_repo.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                f"Vendor {vendor_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        has_warning = self._check_liability_cap_warning(liability_cap_fraction, liability_cap_months)

        risk_score, risk_level, identified_risks = await self._contract_analyzer.analyze(
            contract_name=contract_name,
            contract_type=contract_type,
            clauses=clauses,
            has_liability_cap_warning=has_warning,
            auto_renewal_clause=auto_renewal_clause,
        )

        if has_warning:
            identified_risks = [
                {
                    "risk_type": "liability_cap",
                    "severity": "high",
                    "clause_reference": "Liability limitation clause",
                    "description": (
                        "Vendor liability is capped at or below 1 month of fees, "
                        "substantially limiting remedies for data breaches or service failures."
                    ),
                    "recommendation": (
                        "Negotiate a minimum 12-month liability cap or seek uncapped liability "
                        "for data protection violations and wilful misconduct."
                    ),
                },
                *identified_risks,
            ]

        contract = await self._contract_repo.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            analysed_by=analysed_by,
            contract_name=contract_name,
            contract_type=contract_type,
            effective_date=effective_date,
            expiry_date=expiry_date,
            annual_value_usd=annual_value_usd,
            liability_cap_months=liability_cap_months,
            liability_cap_fraction=liability_cap_fraction,
            has_liability_cap_warning=has_warning,
            auto_renewal_clause=auto_renewal_clause,
            governing_law=governing_law,
            risk_score=risk_score,
            risk_level=risk_level,
            identified_risks=identified_risks,
            clauses=clauses,
        )

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "contract.analyzed",
                "contract_id": str(contract.id),
                "vendor_id": str(vendor_id),
                "tenant_id": str(tenant_id),
                "risk_level": risk_level,
                "has_liability_cap_warning": has_warning,
            },
        )

        logger.info(
            "contract_analyzed",
            contract_id=str(contract.id),
            vendor_id=str(vendor_id),
            risk_level=risk_level,
            has_liability_cap_warning=has_warning,
        )
        return contract

    async def get_contract_risks(
        self, contract_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Contract:
        """Retrieve a contract and its risk analysis.

        Args:
            contract_id: Contract UUID.
            tenant_id: Tenant UUID.

        Returns:
            Contract with risk analysis.

        Raises:
            NotFoundError: If contract does not exist.
        """
        contract = await self._contract_repo.get_by_id(contract_id, tenant_id)
        if contract is None:
            raise NotFoundError(
                f"Contract {contract_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )
        return contract

    async def get_latest_analysis(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Contract | None:
        """Get the most recent contract analysis for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Tenant UUID.

        Returns:
            Most recent Contract or None.
        """
        return await self._contract_repo.get_latest_for_vendor(vendor_id, tenant_id)


class InsuranceCheckerService:
    """Check vendor insurance coverage gaps.

    Args:
        vendor_repo: Repository for Vendor persistence.
        insurance_gap_repo: Repository for InsuranceGap persistence.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        insurance_gap_repo: IInsuranceGapRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._vendor_repo = vendor_repo
        self._insurance_gap_repo = insurance_gap_repo
        self._event_publisher = event_publisher

    async def check_coverage(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        contract_id: uuid.UUID | None,
        detected_by: uuid.UUID | None,
        coverage_items: list[dict[str, Any]],
    ) -> list[InsuranceGap]:
        """Check vendor insurance coverage against AumOS requirements.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor to check.
            contract_id: Optional associated contract UUID.
            detected_by: User performing the check.
            coverage_items: List of coverage dicts with type and amount_usd.

        Returns:
            List of InsuranceGap records for identified deficiencies.

        Raises:
            NotFoundError: If vendor does not exist.
        """
        vendor = await self._vendor_repo.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                f"Vendor {vendor_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        provided: dict[str, int] = {
            item["type"]: item["amount_usd"] for item in coverage_items
        }

        gaps: list[InsuranceGap] = []
        for coverage_type in REQUIRED_COVERAGE_TYPES:
            actual = provided.get(coverage_type)
            if actual is None or actual < MINIMUM_COVERAGE_USD:
                gap_amount = (MINIMUM_COVERAGE_USD - actual) if actual is not None else None
                severity = "critical" if actual is None else "high"
                description = (
                    f"No {coverage_type.replace('_', ' ')} coverage documented."
                    if actual is None
                    else (
                        f"{coverage_type.replace('_', ' ').title()} coverage "
                        f"of ${actual:,} is below the required minimum of ${MINIMUM_COVERAGE_USD:,}."
                    )
                )

                gap = await self._insurance_gap_repo.upsert(
                    tenant_id=tenant_id,
                    vendor_id=vendor_id,
                    contract_id=contract_id,
                    coverage_type=coverage_type,
                    required_coverage_usd=MINIMUM_COVERAGE_USD,
                    actual_coverage_usd=actual,
                    gap_amount_usd=gap_amount,
                    severity=severity,
                    description=description,
                    detected_by=detected_by,
                )
                gaps.append(gap)

                await self._event_publisher.publish(
                    Topics.VENDOR_EVENTS,
                    {
                        "event_type": "insurance.gap_detected",
                        "gap_id": str(gap.id),
                        "vendor_id": str(vendor_id),
                        "tenant_id": str(tenant_id),
                        "coverage_type": coverage_type,
                        "severity": severity,
                    },
                )

        logger.info(
            "insurance_check_completed",
            vendor_id=str(vendor_id),
            gaps_found=len(gaps),
        )
        return gaps

    async def get_vendor_gaps(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[InsuranceGap]:
        """List all insurance gaps for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Tenant UUID.

        Returns:
            List of InsuranceGap records.
        """
        return await self._insurance_gap_repo.list_by_vendor(vendor_id, tenant_id)

    async def update_gap_status(
        self,
        gap_id: uuid.UUID,
        tenant_id: uuid.UUID,
        status: str,
        remediation_notes: str | None,
    ) -> InsuranceGap:
        """Update the status of an insurance gap.

        Args:
            gap_id: Gap UUID.
            tenant_id: Tenant UUID.
            status: New status: open | remediated | accepted | escalated.
            remediation_notes: Optional notes on remediation action.

        Returns:
            Updated InsuranceGap.

        Raises:
            NotFoundError: If gap does not exist.
        """
        gap = await self._insurance_gap_repo.get_by_id(gap_id, tenant_id)
        if gap is None:
            raise NotFoundError(
                f"Insurance gap {gap_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        remediated_at = None
        if status == "remediated":
            remediated_at = datetime.now(tz=timezone.utc)

        gap = await self._insurance_gap_repo.update_status(
            gap_id=gap_id,
            tenant_id=tenant_id,
            status=status,
            remediation_notes=remediation_notes,
            remediated_at=remediated_at,
        )

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "insurance.gap_updated",
                "gap_id": str(gap_id),
                "tenant_id": str(tenant_id),
                "new_status": status,
            },
        )

        return gap


# ---------------------------------------------------------------------------
# GAP-268: Vendor Security Questionnaire System
# ---------------------------------------------------------------------------


class QuestionnaireService:
    """Manages vendor security questionnaire lifecycle.

    Workflow:
    1. Admin creates questionnaire template.
    2. Admin distributes to vendor contact via secure tokenised link.
    3. Vendor fills out form at public endpoint (unauthenticated).
    4. AI reviews responses and auto-populates evaluation scores.
    5. AumOS operator reviews and confirms AI assessment.

    Args:
        questionnaire_repo: Repository for questionnaire data.
        llm_client: HTTP client to aumos-llm-serving for AI review.
        vendor_scorer: VendorScorerService for score population.
        event_publisher: Kafka event publisher.
        settings: Vendor intelligence settings.
    """

    def __init__(
        self,
        questionnaire_repo: IQuestionnaireRepository,
        llm_client: Any,
        vendor_scorer: VendorScorerService,
        event_publisher: EventPublisher,
        settings: Any,
    ) -> None:
        self._questionnaire_repo = questionnaire_repo
        self._llm_client = llm_client
        self._vendor_scorer = vendor_scorer
        self._event_publisher = event_publisher
        self._settings = settings

    async def create_template(
        self,
        tenant_id: uuid.UUID,
        name: str,
        category: str,
        questions: list[dict[str, Any]],
    ) -> VinQuestionnaireTemplate:
        """Create a new questionnaire template.

        Args:
            tenant_id: Owning tenant UUID.
            name: Template name.
            category: security_posture | data_portability | ai_safety | custom.
            questions: List of question definition dicts.

        Returns:
            Newly created VinQuestionnaireTemplate.
        """
        template = await self._questionnaire_repo.create_template(
            tenant_id=tenant_id,
            name=name,
            category=category,
            questions=questions,
        )
        logger.info(
            "questionnaire_template_created",
            template_id=str(template.id),
            tenant_id=str(tenant_id),
        )
        return template

    async def distribute_questionnaire(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        template_id: uuid.UUID,
        vendor_contact_email: str,
        due_days: int,
    ) -> VinQuestionnaireSubmission:
        """Create a submission and send secure tokenised link to vendor.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor receiving the questionnaire.
            template_id: Template to use for this submission.
            vendor_contact_email: Recipient email address.
            due_days: Number of days from now until response is due.

        Returns:
            Created VinQuestionnaireSubmission in 'sent' status.
        """
        token = secrets.token_urlsafe(48)
        due_at = datetime.now(tz=timezone.utc) + timedelta(days=due_days)

        submission = await self._questionnaire_repo.create_submission(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            template_id=template_id,
            vendor_contact_email=vendor_contact_email,
            sent_at=datetime.now(tz=timezone.utc),
            due_at=due_at,
            token=token,
        )

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "vendor.questionnaire_sent",
                "submission_id": str(submission.id),
                "vendor_id": str(vendor_id),
                "tenant_id": str(tenant_id),
                "vendor_contact_email": vendor_contact_email,
            },
        )

        logger.info(
            "questionnaire_distributed",
            submission_id=str(submission.id),
            vendor_id=str(vendor_id),
        )
        return submission

    async def submit_vendor_responses(
        self,
        token: str,
        responses: dict[str, Any],
    ) -> VinQuestionnaireSubmission:
        """Record vendor responses submitted via the public tokenised link.

        Args:
            token: URL-safe token from the questionnaire link.
            responses: Vendor answers keyed by question ID.

        Returns:
            Updated VinQuestionnaireSubmission in 'completed' status.

        Raises:
            NotFoundError: If token is invalid.
            ValueError: If token has expired or already been used.
        """
        link = await self._questionnaire_repo.get_link_by_token(token)
        if link is None:
            raise NotFoundError(
                "Questionnaire link not found or invalid",
                error_code=ErrorCode.NOT_FOUND,
            )

        now = datetime.now(tz=timezone.utc)
        if link.expires_at < now:
            raise ValueError("Questionnaire link has expired")
        if link.used:
            raise ValueError("Questionnaire link has already been used")

        submission = await self._questionnaire_repo.record_responses(
            submission_id=link.submission_id,
            responses=responses,
            completed_at=now,
        )
        await self._questionnaire_repo.mark_link_used(link.id)

        logger.info(
            "questionnaire_responses_submitted",
            submission_id=str(link.submission_id),
        )
        return submission

    async def ai_review_responses(
        self,
        submission_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> dict[str, float]:
        """Use LLM to review vendor responses and generate evaluation scores.

        Args:
            submission_id: Submission UUID to review.
            tenant_id: Tenant UUID.

        Returns:
            Scores by criterion category (0.0–1.0 each).

        Raises:
            NotFoundError: If submission does not exist.
        """
        submission = await self._questionnaire_repo.get_submission(submission_id, tenant_id)
        if submission is None:
            raise NotFoundError(
                f"Questionnaire submission {submission_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        template = await self._questionnaire_repo.get_template(submission.template_id, tenant_id)
        if template is None:
            raise NotFoundError(
                f"Questionnaire template {submission.template_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        # Build prompt with questions and vendor responses for LLM scoring
        question_map = {q["id"]: q for q in template.questions}
        category_scores: dict[str, list[float]] = {}

        for question_id, answer in submission.vendor_responses.items():
            question = question_map.get(question_id)
            if question is None:
                continue

            category = question.get("category", "general")
            weight = question.get("weight", 0.1)

            # LLM-as-judge: score each question response
            try:
                response = await self._llm_client.post(
                    "/api/v1/completions",
                    json={
                        "prompt": (
                            f"You are a security assessment expert. Score the following vendor "
                            f"response on a scale of 0.0 to 1.0 where 1.0 is fully compliant.\n\n"
                            f"Question: {question['text']}\n"
                            f"Expected evidence: {question.get('expected_evidence', 'N/A')}\n"
                            f"Vendor response: {answer}\n\n"
                            f"Respond with only a JSON object: {{\"score\": <0.0-1.0>}}"
                        ),
                        "max_tokens": 50,
                    },
                )
                score_data = response.json()
                score = float(score_data.get("score", 0.5))
            except Exception:
                score = 0.5  # Default to middle score on LLM failure

            if category not in category_scores:
                category_scores[category] = []
            category_scores[category].append(score * weight)

        # Aggregate scores by category
        aggregated: dict[str, float] = {
            cat: min(1.0, sum(scores) / max(len(scores), 1))
            for cat, scores in category_scores.items()
        }

        await self._questionnaire_repo.update_ai_review_scores(
            submission_id=submission_id,
            tenant_id=tenant_id,
            scores=aggregated,
        )

        logger.info(
            "ai_review_completed",
            submission_id=str(submission_id),
            categories=list(aggregated.keys()),
        )
        return aggregated


# ---------------------------------------------------------------------------
# GAP-269: Continuous Vendor Monitoring
# ---------------------------------------------------------------------------


class VendorMonitoringService:
    """Polls external intelligence feeds to detect vendor risk changes.

    Runs periodic monitoring cycles checking breach databases, SOC2 expiry,
    and regulatory action feeds for all active vendors.

    Args:
        vendor_repo: Repository for listing active vendors.
        monitoring_repo: Repository for VinMonitoringAlert persistence.
        monitoring_adapters: List of intelligence feed adapters.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        monitoring_repo: IMonitoringAlertRepository,
        monitoring_adapters: list[IVendorMonitoringAdapter],
        event_publisher: EventPublisher,
    ) -> None:
        self._vendor_repo = vendor_repo
        self._monitoring_repo = monitoring_repo
        self._monitoring_adapters = monitoring_adapters
        self._event_publisher = event_publisher

    async def run_monitoring_cycle(
        self,
        tenant_id: uuid.UUID,
    ) -> list[VinMonitoringAlert]:
        """Run one full monitoring cycle for all active vendors.

        Args:
            tenant_id: Tenant UUID to monitor vendors for.

        Returns:
            List of newly detected VinMonitoringAlert records.
        """
        vendors = await self._vendor_repo.list_active_vendors(tenant_id)
        alerts: list[VinMonitoringAlert] = []

        for vendor in vendors:
            for adapter in self._monitoring_adapters:
                try:
                    detected = await adapter.check_vendor(vendor)
                    for alert_data in detected:
                        alert = await self._monitoring_repo.create_alert(
                            tenant_id=tenant_id,
                            vendor_id=vendor.id,
                            alert_type=alert_data["alert_type"],
                            severity=alert_data["severity"],
                            source=alert_data["source"],
                            description=alert_data["description"],
                            recommended_action=alert_data.get("recommended_action"),
                        )
                        alerts.append(alert)

                        await self._event_publisher.publish(
                            Topics.VENDOR_EVENTS,
                            {
                                "event_type": "vendor.monitoring_alert",
                                "alert_id": str(alert.id),
                                "vendor_id": str(vendor.id),
                                "tenant_id": str(tenant_id),
                                "alert_type": alert_data["alert_type"],
                                "severity": alert_data["severity"],
                            },
                        )
                except Exception as exc:
                    logger.warning(
                        "monitoring_adapter_error",
                        vendor_id=str(vendor.id),
                        adapter=type(adapter).__name__,
                        error=str(exc),
                    )

        logger.info(
            "monitoring_cycle_complete",
            tenant_id=str(tenant_id),
            vendors_checked=len(vendors),
            alerts_raised=len(alerts),
        )
        return alerts

    async def list_alerts(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID | None = None,
        resolved: bool | None = None,
    ) -> list[VinMonitoringAlert]:
        """List monitoring alerts for a tenant.

        Args:
            tenant_id: Tenant UUID.
            vendor_id: Optional vendor filter.
            resolved: Optional filter by resolved status.

        Returns:
            List of VinMonitoringAlert records.
        """
        return await self._monitoring_repo.list_alerts(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            resolved=resolved,
        )


# ---------------------------------------------------------------------------
# GAP-270: ISO 42001 Compliance Mapping
# ---------------------------------------------------------------------------


class Iso42001ComplianceService:
    """Maps vendor questionnaire evidence to ISO 42001 Annex A controls.

    Generates per-vendor compliance assessments against the ISO/IEC 42001:2023
    AI Management System standard.

    Args:
        iso_repo: Repository for ISO 42001 controls and assessments.
        questionnaire_repo: Repository for questionnaire submissions.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        iso_repo: IIso42001Repository,
        questionnaire_repo: IQuestionnaireRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._iso_repo = iso_repo
        self._questionnaire_repo = questionnaire_repo
        self._event_publisher = event_publisher

    async def assess_vendor(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        questionnaire_submission_id: uuid.UUID | None,
        manual_assessments: list[dict[str, Any]] | None,
    ) -> list[VinVendorIso42001Assessment]:
        """Run or update ISO 42001 compliance assessment for a vendor.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor to assess.
            questionnaire_submission_id: Optional questionnaire to derive evidence from.
            manual_assessments: Optional list of manually provided assessment dicts.

        Returns:
            List of VinVendorIso42001Assessment records (one per control).
        """
        controls = await self._iso_repo.list_all_controls()
        assessments: list[VinVendorIso42001Assessment] = []

        # Derive evidence from questionnaire if provided
        evidence_map: dict[str, str] = {}
        if questionnaire_submission_id is not None:
            submission = await self._questionnaire_repo.get_submission(
                questionnaire_submission_id, tenant_id
            )
            if submission and submission.vendor_responses:
                for qid, answer in submission.vendor_responses.items():
                    evidence_map[qid] = str(answer)

        # Build manual assessment lookup
        manual_map: dict[str, dict[str, Any]] = {}
        if manual_assessments:
            for item in manual_assessments:
                manual_map[item["control_id"]] = item

        for control in controls:
            manual = manual_map.get(control.id)
            if manual:
                compliance_status = manual["compliance_status"]
                evidence = manual.get("evidence")
            elif evidence_map:
                # Default to partially_compliant when questionnaire evidence exists
                compliance_status = "partially_compliant"
                evidence = "; ".join(list(evidence_map.values())[:3])
            else:
                compliance_status = "non_compliant"
                evidence = None

            assessment = await self._iso_repo.upsert_vendor_assessment(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                control_id=control.id,
                compliance_status=compliance_status,
                evidence=evidence,
                assessed_from_questionnaire_id=questionnaire_submission_id,
            )
            assessments.append(assessment)

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "vendor.iso42001_assessed",
                "vendor_id": str(vendor_id),
                "tenant_id": str(tenant_id),
                "controls_assessed": len(assessments),
            },
        )

        logger.info(
            "iso42001_assessment_complete",
            vendor_id=str(vendor_id),
            controls_assessed=len(assessments),
        )
        return assessments

    async def get_compliance_report(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Generate an ISO 42001 compliance report for a vendor.

        Args:
            tenant_id: Tenant UUID.
            vendor_id: Vendor UUID.

        Returns:
            Compliance report dict with summary statistics and per-control status.
        """
        assessments = await self._iso_repo.list_vendor_assessments(vendor_id, tenant_id)
        controls = {c.id: c for c in await self._iso_repo.list_all_controls()}

        status_counts: dict[str, int] = {
            "compliant": 0,
            "partially_compliant": 0,
            "non_compliant": 0,
            "not_applicable": 0,
        }
        for assessment in assessments:
            status_counts[assessment.compliance_status] = (
                status_counts.get(assessment.compliance_status, 0) + 1
            )

        total = len(assessments)
        compliant_count = status_counts["compliant"]
        compliance_percentage = (compliant_count / total * 100) if total > 0 else 0.0

        return {
            "vendor_id": str(vendor_id),
            "total_controls": total,
            "compliance_percentage": round(compliance_percentage, 1),
            "status_summary": status_counts,
            "assessments": [
                {
                    "control_id": a.control_id,
                    "section": controls[a.control_id].section if a.control_id in controls else "",
                    "title": controls[a.control_id].title if a.control_id in controls else "",
                    "compliance_status": a.compliance_status,
                    "evidence": a.evidence,
                }
                for a in assessments
            ],
        }

    async def list_controls(self) -> list[Any]:
        """List all ISO 42001 Annex A controls.

        Returns:
            List of VinIso42001Control records.
        """
        return await self._iso_repo.list_all_controls()


# ---------------------------------------------------------------------------
# GAP-271: Negotiation Playbook Generator
# ---------------------------------------------------------------------------


class NegotiationPlaybookService:
    """Generates negotiation playbooks from vendor evaluation data.

    Combines evaluation scores, lock-in assessment, and contract analysis
    to identify leverage points, red lines, recommended asks, and walk-away triggers.

    Args:
        vendor_scorer: Service for retrieving current evaluations.
        lock_in_assessor: Service for retrieving lock-in assessments.
        contract_analyzer: Service for retrieving contract analyses.
        playbook_generator: Adapter for playbook content generation.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        vendor_scorer: VendorScorerService,
        lock_in_assessor: LockInAssessorService,
        contract_analyzer: ContractAnalyzerService,
        playbook_generator: INegotiationPlaybookGenerator,
        event_publisher: EventPublisher,
    ) -> None:
        self._vendor_scorer = vendor_scorer
        self._lock_in_assessor = lock_in_assessor
        self._contract_analyzer = contract_analyzer
        self._playbook_generator = playbook_generator
        self._event_publisher = event_publisher

    async def generate_playbook(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Generate a negotiation playbook for a vendor.

        Playbook sections:
        1. Leverage points — where the buyer has negotiating leverage.
        2. Red lines — terms that must be improved before signing.
        3. Recommended asks — specific contract language changes.
        4. Walk-away triggers — conditions that should kill the deal.

        Args:
            tenant_id: Tenant UUID.
            vendor_id: Vendor UUID.

        Returns:
            Negotiation playbook dict with all four sections.

        Raises:
            NotFoundError: If vendor does not exist.
        """
        vendor = await self._vendor_scorer.get_vendor(vendor_id, tenant_id)
        lock_in = await self._lock_in_assessor.get_current_assessment(vendor_id, tenant_id)
        contract = await self._contract_analyzer.get_latest_analysis(vendor_id, tenant_id)

        playbook = await self._playbook_generator.generate(
            vendor=vendor,
            lock_in_assessment=lock_in,
            latest_contract=contract,
        )

        await self._event_publisher.publish(
            Topics.VENDOR_EVENTS,
            {
                "event_type": "vendor.negotiation_playbook_generated",
                "vendor_id": str(vendor_id),
                "tenant_id": str(tenant_id),
            },
        )

        logger.info(
            "negotiation_playbook_generated",
            vendor_id=str(vendor_id),
            tenant_id=str(tenant_id),
        )
        return playbook


# ---------------------------------------------------------------------------
# GAP-272: SaaS Spend Integration
# ---------------------------------------------------------------------------


class SaasSpendService:
    """Integrates with procurement systems to pull actual SaaS spend data.

    Enriches vendor evaluations with real cost information from Zylo
    or Vendr procurement APIs.

    Args:
        procurement_advisor: Adapter for procurement system integration.
        vendor_repo: Repository for Vendor persistence.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        procurement_advisor: IProcurementAdvisor,
        vendor_repo: IVendorRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._procurement_advisor = procurement_advisor
        self._vendor_repo = vendor_repo
        self._event_publisher = event_publisher

    async def sync_spend_data(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Sync SaaS spend data for a vendor from procurement system.

        Args:
            tenant_id: Tenant UUID.
            vendor_id: Vendor UUID.

        Returns:
            Spend summary dict with annual_spend_usd and contract_details.

        Raises:
            NotFoundError: If vendor does not exist.
        """
        vendor = await self._vendor_repo.get_by_id(vendor_id, tenant_id)
        if vendor is None:
            raise NotFoundError(
                f"Vendor {vendor_id} not found",
                error_code=ErrorCode.NOT_FOUND,
            )

        spend_data = await self._procurement_advisor.get_vendor_spend(
            vendor_name=vendor.name,
            tenant_id=tenant_id,
        )

        logger.info(
            "saas_spend_synced",
            vendor_id=str(vendor_id),
            tenant_id=str(tenant_id),
        )
        return spend_data


# ---------------------------------------------------------------------------
# GAP-273: Vendor Intelligence Feeds
# ---------------------------------------------------------------------------


class VendorIntelligenceFeedService:
    """Ingests external vendor intelligence into the vendor database.

    Sources: breach disclosures, SOC2 report updates, regulatory sanctions.

    Args:
        vendor_repo: Repository for Vendor persistence.
        monitoring_repo: Repository for VinMonitoringAlert persistence.
        vendor_data_enricher: Adapter for vendor data enrichment.
        event_publisher: Kafka event publisher.
    """

    def __init__(
        self,
        vendor_repo: IVendorRepository,
        monitoring_repo: IMonitoringAlertRepository,
        vendor_data_enricher: IVendorDataEnricher,
        event_publisher: EventPublisher,
    ) -> None:
        self._vendor_repo = vendor_repo
        self._monitoring_repo = monitoring_repo
        self._vendor_data_enricher = vendor_data_enricher
        self._event_publisher = event_publisher

    async def ingest_intelligence_feed(
        self,
        tenant_id: uuid.UUID,
        feed_type: str,
        feed_data: list[dict[str, Any]],
    ) -> list[VinMonitoringAlert]:
        """Ingest a batch of intelligence feed entries.

        Args:
            tenant_id: Tenant UUID.
            feed_type: Feed type identifier (e.g. 'breach_disclosure', 'soc2_update').
            feed_data: List of feed entry dicts with vendor identifiers and details.

        Returns:
            List of newly created VinMonitoringAlert records.
        """
        alerts: list[VinMonitoringAlert] = []

        for entry in feed_data:
            vendor_name = entry.get("vendor_name", "")
            vendors = await self._vendor_repo.find_by_name_fuzzy(tenant_id, vendor_name)

            for vendor in vendors:
                alert = await self._monitoring_repo.create_alert(
                    tenant_id=tenant_id,
                    vendor_id=vendor.id,
                    alert_type=entry.get("alert_type", feed_type),
                    severity=entry.get("severity", "medium"),
                    source=entry.get("source", feed_type),
                    description=entry.get("description", ""),
                    recommended_action=entry.get("recommended_action"),
                )
                alerts.append(alert)

                await self._event_publisher.publish(
                    Topics.VENDOR_EVENTS,
                    {
                        "event_type": "vendor.intelligence_feed_alert",
                        "alert_id": str(alert.id),
                        "vendor_id": str(vendor.id),
                        "tenant_id": str(tenant_id),
                        "feed_type": feed_type,
                    },
                )

        logger.info(
            "intelligence_feed_ingested",
            tenant_id=str(tenant_id),
            feed_type=feed_type,
            entries=len(feed_data),
            alerts_created=len(alerts),
        )
        return alerts
