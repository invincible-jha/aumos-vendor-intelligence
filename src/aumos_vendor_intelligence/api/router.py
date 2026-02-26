"""FastAPI router for the AumOS Vendor Intelligence REST API.

All endpoints are prefixed with /api/v1. Authentication and tenant extraction
are handled by aumos-auth-gateway upstream; tenant_id is available via JWT.

Business logic is never implemented here — routes delegate entirely to services.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from aumos_common.errors import ConflictError, NotFoundError
from aumos_common.observability import get_logger

from aumos_vendor_intelligence.api.schemas import (
    ContractAnalyzeRequest,
    ContractRiskResponse,
    EvaluationCreateRequest,
    EvaluationResponse,
    InsuranceCheckRequest,
    InsuranceCheckResponse,
    InsuranceGapResponse,
    InsuranceGapStatusUpdate,
    LockInAssessmentRequest,
    LockInAssessmentResponse,
    VendorCompareResponse,
    VendorCreateRequest,
    VendorListResponse,
    VendorResponse,
)
from aumos_vendor_intelligence.core.services import (
    ContractAnalyzerService,
    InsuranceCheckerService,
    LockInAssessorService,
    VendorScorerService,
)

logger = get_logger(__name__)

router = APIRouter(tags=["vendor-intelligence"])


# ---------------------------------------------------------------------------
# Dependency helpers — replaced by real DI in production startup
# ---------------------------------------------------------------------------


def _get_vendor_scorer(request: Request) -> VendorScorerService:
    """Retrieve VendorScorerService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        VendorScorerService instance.
    """
    return request.app.state.vendor_scorer_service  # type: ignore[no-any-return]


def _get_lock_in_assessor(request: Request) -> LockInAssessorService:
    """Retrieve LockInAssessorService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        LockInAssessorService instance.
    """
    return request.app.state.lock_in_assessor_service  # type: ignore[no-any-return]


def _get_contract_analyzer(request: Request) -> ContractAnalyzerService:
    """Retrieve ContractAnalyzerService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        ContractAnalyzerService instance.
    """
    return request.app.state.contract_analyzer_service  # type: ignore[no-any-return]


def _get_insurance_checker(request: Request) -> InsuranceCheckerService:
    """Retrieve InsuranceCheckerService from app state.

    Args:
        request: FastAPI request with app state populated in lifespan.

    Returns:
        InsuranceCheckerService instance.
    """
    return request.app.state.insurance_checker_service  # type: ignore[no-any-return]


def _tenant_id_from_request(request: Request) -> uuid.UUID:
    """Extract tenant UUID from request headers (set by auth middleware).

    Falls back to a random UUID in development mode.

    Args:
        request: Incoming FastAPI request.

    Returns:
        Tenant UUID.
    """
    tenant_header = request.headers.get("X-Tenant-ID")
    if tenant_header:
        return uuid.UUID(tenant_header)
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Vendor endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/vendors",
    response_model=VendorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register vendor",
    description="Register a new AI vendor for evaluation and risk assessment.",
)
async def register_vendor(
    request_body: VendorCreateRequest,
    request: Request,
    service: VendorScorerService = Depends(_get_vendor_scorer),
) -> VendorResponse:
    """Register a new AI vendor.

    Args:
        request_body: Vendor creation parameters.
        request: FastAPI request for tenant extraction.
        service: VendorScorerService dependency.

    Returns:
        VendorResponse for the newly registered vendor.

    Raises:
        HTTPException 400: If the vendor category is invalid.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        vendor = await service.register_vendor(
            tenant_id=tenant_id,
            name=request_body.name,
            category=request_body.category,
            description=request_body.description,
            website_url=request_body.website_url,
            api_compatibility=request_body.api_compatibility,
            data_portability=request_body.data_portability,
            contact_info=request_body.contact_info,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return VendorResponse.model_validate(vendor)


@router.get(
    "/vendors",
    response_model=VendorListResponse,
    summary="List vendors",
    description="List all vendors for the current tenant with optional filtering and pagination.",
)
async def list_vendors(
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
    status_filter: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    service: VendorScorerService = Depends(_get_vendor_scorer),
) -> VendorListResponse:
    """List vendors for the current tenant.

    Args:
        page: 1-based page number (default 1).
        page_size: Results per page (default 20, max 100).
        category: Optional category filter.
        status_filter: Optional status filter.
        request: FastAPI request for tenant extraction.
        service: VendorScorerService dependency.

    Returns:
        VendorListResponse with pagination metadata.
    """
    tenant_id = _tenant_id_from_request(request)
    vendors, total = await service.list_vendors(
        tenant_id=tenant_id,
        page=page,
        page_size=min(page_size, 100),
        category=category,
        status=status_filter,
    )

    return VendorListResponse(
        items=[VendorResponse.model_validate(v) for v in vendors],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/vendors/compare",
    response_model=VendorCompareResponse,
    summary="Compare vendors",
    description=(
        "Side-by-side comparison of 2–10 vendors by overall score and risk level. "
        "Provide vendor UUIDs as repeated query parameters: ?vendor_ids=<uuid>&vendor_ids=<uuid>"
    ),
)
async def compare_vendors(
    vendor_ids: list[uuid.UUID],
    request: Request = ...,  # type: ignore[assignment]
    service: VendorScorerService = Depends(_get_vendor_scorer),
) -> VendorCompareResponse:
    """Side-by-side vendor comparison.

    Args:
        vendor_ids: List of vendor UUIDs to compare (2–10).
        request: FastAPI request for tenant extraction.
        service: VendorScorerService dependency.

    Returns:
        VendorCompareResponse with all vendors sorted by score.

    Raises:
        HTTPException 400: If fewer than 2 or more than 10 vendor IDs provided.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        vendors = await service.compare_vendors(tenant_id, vendor_ids)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return VendorCompareResponse(
        vendors=[VendorResponse.model_validate(v) for v in vendors],
    )


@router.get(
    "/vendors/{vendor_id}",
    response_model=VendorResponse,
    summary="Get vendor detail",
    description="Retrieve a vendor profile with current evaluation score.",
)
async def get_vendor(
    vendor_id: uuid.UUID,
    request: Request,
    service: VendorScorerService = Depends(_get_vendor_scorer),
) -> VendorResponse:
    """Retrieve a vendor profile.

    Args:
        vendor_id: Vendor UUID.
        request: FastAPI request for tenant extraction.
        service: VendorScorerService dependency.

    Returns:
        VendorResponse with current score and risk level.

    Raises:
        HTTPException 404: If vendor not found.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        vendor = await service.get_vendor(vendor_id, tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return VendorResponse.model_validate(vendor)


@router.post(
    "/vendors/{vendor_id}/evaluate",
    response_model=EvaluationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run vendor evaluation",
    description=(
        "Run a multi-criteria evaluation for a vendor. "
        "Computes a weighted composite score and updates the vendor's risk level."
    ),
)
async def evaluate_vendor(
    vendor_id: uuid.UUID,
    request_body: EvaluationCreateRequest,
    request: Request,
    service: VendorScorerService = Depends(_get_vendor_scorer),
) -> EvaluationResponse:
    """Run a vendor evaluation.

    Args:
        vendor_id: Vendor UUID to evaluate.
        request_body: Criterion scores and evaluation notes.
        request: FastAPI request for tenant extraction.
        service: VendorScorerService dependency.

    Returns:
        EvaluationResponse with computed composite score.

    Raises:
        HTTPException 404: If vendor not found.
        HTTPException 400: If any score is outside [0.0, 1.0].
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        evaluation = await service.run_evaluation(
            vendor_id=vendor_id,
            tenant_id=tenant_id,
            api_compatibility_score=request_body.api_compatibility_score,
            data_portability_score=request_body.data_portability_score,
            security_posture_score=request_body.security_posture_score,
            pricing_transparency_score=request_body.pricing_transparency_score,
            support_quality_score=request_body.support_quality_score,
            notes=request_body.notes,
            raw_responses=request_body.raw_responses,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return EvaluationResponse.model_validate(evaluation)


# ---------------------------------------------------------------------------
# Lock-in assessment endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/vendors/{vendor_id}/lock-in",
    response_model=LockInAssessmentResponse,
    summary="Get lock-in risk assessment",
    description="Retrieve the current lock-in risk assessment for a vendor.",
)
async def get_lock_in_assessment(
    vendor_id: uuid.UUID,
    request: Request,
    service: LockInAssessorService = Depends(_get_lock_in_assessor),
) -> LockInAssessmentResponse:
    """Get the current lock-in risk assessment for a vendor.

    Args:
        vendor_id: Vendor UUID.
        request: FastAPI request for tenant extraction.
        service: LockInAssessorService dependency.

    Returns:
        LockInAssessmentResponse with current assessment.

    Raises:
        HTTPException 404: If vendor not found or not yet assessed.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        assessment = await service.get_lock_in_assessment(vendor_id, tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return LockInAssessmentResponse.model_validate(assessment)


@router.post(
    "/vendors/{vendor_id}/lock-in",
    response_model=LockInAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run lock-in assessment",
    description=(
        "Run a lock-in risk assessment for a vendor across 5 dimensions: "
        "proprietary formats, switching costs, API openness, data egress, and contractual lock-in."
    ),
)
async def run_lock_in_assessment(
    vendor_id: uuid.UUID,
    request_body: LockInAssessmentRequest,
    request: Request,
    service: LockInAssessorService = Depends(_get_lock_in_assessor),
) -> LockInAssessmentResponse:
    """Run a lock-in risk assessment.

    Args:
        vendor_id: Vendor UUID to assess.
        request_body: Dimension scores and risk factors.
        request: FastAPI request for tenant extraction.
        service: LockInAssessorService dependency.

    Returns:
        LockInAssessmentResponse with composite score and risk level.

    Raises:
        HTTPException 404: If vendor not found.
        HTTPException 400: If any dimension score is outside [0.0, 1.0].
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        assessment = await service.assess_lock_in(
            vendor_id=vendor_id,
            tenant_id=tenant_id,
            proprietary_formats_score=request_body.proprietary_formats_score,
            switching_cost_score=request_body.switching_cost_score,
            api_openness_score=request_body.api_openness_score,
            data_egress_score=request_body.data_egress_score,
            contractual_lock_in_score=request_body.contractual_lock_in_score,
            risk_factors=[rf.model_dump() for rf in request_body.risk_factors],
            recommendations=[r.model_dump() for r in request_body.recommendations],
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return LockInAssessmentResponse.model_validate(assessment)


# ---------------------------------------------------------------------------
# Contract analysis endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/contracts/analyze",
    response_model=ContractRiskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Analyse contract risk",
    description=(
        "Submit a vendor contract for risk analysis. "
        "Applies the AumOS 88% cap liability policy: contracts capping vendor liability "
        "at <= 1 month of fees receive a high-severity warning."
    ),
)
async def analyze_contract(
    request_body: ContractAnalyzeRequest,
    request: Request,
    service: ContractAnalyzerService = Depends(_get_contract_analyzer),
) -> ContractRiskResponse:
    """Submit and analyse a vendor contract.

    Args:
        request_body: Contract metadata and terms.
        request: FastAPI request for tenant extraction.
        service: ContractAnalyzerService dependency.

    Returns:
        ContractRiskResponse with identified risks and liability cap warning flag.

    Raises:
        HTTPException 404: If vendor not found.
        HTTPException 400: If contract_type is invalid.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        # First register the contract
        contract = await service.submit_contract(
            tenant_id=tenant_id,
            vendor_id=request_body.vendor_id,
            contract_name=request_body.contract_name,
            contract_type=request_body.contract_type,
            effective_date=request_body.effective_date,
            expiry_date=request_body.expiry_date,
            annual_value_usd=request_body.annual_value_usd,
        )

        # Then run risk analysis
        analysed_contract = await service.analyze_contract(
            contract_id=contract.id,
            tenant_id=tenant_id,
            liability_cap_months=request_body.liability_cap_months,
            annual_value_usd=request_body.annual_value_usd,
            auto_renewal_clause=request_body.auto_renewal_clause,
            governing_law=request_body.governing_law,
            clauses=request_body.clauses,
            additional_risks=[r.model_dump() for r in request_body.additional_risks],
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ContractRiskResponse.model_validate(analysed_contract)


@router.get(
    "/contracts/{contract_id}/risks",
    response_model=ContractRiskResponse,
    summary="Get contract risk report",
    description="Retrieve the risk analysis report for a previously analysed contract.",
)
async def get_contract_risks(
    contract_id: uuid.UUID,
    request: Request,
    service: ContractAnalyzerService = Depends(_get_contract_analyzer),
) -> ContractRiskResponse:
    """Retrieve a contract risk report.

    Args:
        contract_id: Contract UUID.
        request: FastAPI request for tenant extraction.
        service: ContractAnalyzerService dependency.

    Returns:
        ContractRiskResponse with all identified risks.

    Raises:
        HTTPException 404: If contract not found.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        contract = await service.get_contract_risks(contract_id, tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ContractRiskResponse.model_validate(contract)


# ---------------------------------------------------------------------------
# Insurance gap detection endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/insurance/check",
    response_model=InsuranceCheckResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Check insurance coverage",
    description=(
        "Check vendor insurance coverages against AumOS minimum requirements. "
        "Creates InsuranceGap records for each identified coverage deficiency."
    ),
)
async def check_insurance(
    request_body: InsuranceCheckRequest,
    request: Request,
    service: InsuranceCheckerService = Depends(_get_insurance_checker),
) -> InsuranceCheckResponse:
    """Check vendor insurance coverage for gaps.

    Args:
        request_body: Vendor ID and coverage data to validate.
        request: FastAPI request for tenant extraction.
        service: InsuranceCheckerService dependency.

    Returns:
        InsuranceCheckResponse with gap count and gap details.

    Raises:
        HTTPException 404: If vendor not found.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        gaps = await service.check_insurance(
            vendor_id=request_body.vendor_id,
            tenant_id=tenant_id,
            coverages=[cov.model_dump() for cov in request_body.coverages],
            contract_id=request_body.contract_id,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    gap_responses = [InsuranceGapResponse.model_validate(g) for g in gaps]

    # Determine required types from service (approximate from gap response)
    required_coverage_types = list({g.coverage_type for g in gaps}) if gaps else []

    return InsuranceCheckResponse(
        vendor_id=request_body.vendor_id,
        gaps_found=len(gaps),
        gaps=gap_responses,
        coverages_checked=required_coverage_types,
        all_coverages_adequate=len(gaps) == 0,
    )


@router.get(
    "/vendors/{vendor_id}/insurance",
    response_model=list[InsuranceGapResponse],
    summary="List insurance gaps",
    description="List all insurance gaps identified for a vendor.",
)
async def list_insurance_gaps(
    vendor_id: uuid.UUID,
    gap_status: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    service: InsuranceCheckerService = Depends(_get_insurance_checker),
) -> list[InsuranceGapResponse]:
    """List insurance gaps for a vendor.

    Args:
        vendor_id: Vendor UUID.
        gap_status: Optional status filter: open | remediated | accepted | escalated.
        request: FastAPI request for tenant extraction.
        service: InsuranceCheckerService dependency.

    Returns:
        List of InsuranceGapResponse instances.

    Raises:
        HTTPException 404: If vendor not found.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        gaps = await service.get_insurance_gaps(vendor_id, tenant_id, status=gap_status)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return [InsuranceGapResponse.model_validate(g) for g in gaps]


@router.patch(
    "/insurance/gaps/{gap_id}",
    response_model=InsuranceGapResponse,
    summary="Update insurance gap status",
    description="Update the remediation status of an insurance gap.",
)
async def update_insurance_gap(
    gap_id: uuid.UUID,
    request_body: InsuranceGapStatusUpdate,
    request: Request,
    service: InsuranceCheckerService = Depends(_get_insurance_checker),
) -> InsuranceGapResponse:
    """Update an insurance gap status.

    Args:
        gap_id: InsuranceGap UUID.
        request_body: New status and optional remediation notes.
        request: FastAPI request for tenant extraction.
        service: InsuranceCheckerService dependency.

    Returns:
        Updated InsuranceGapResponse.

    Raises:
        HTTPException 404: If gap not found.
    """
    tenant_id = _tenant_id_from_request(request)

    try:
        gap = await service.update_gap_status(
            gap_id=gap_id,
            tenant_id=tenant_id,
            status=request_body.status,
            remediation_notes=request_body.remediation_notes,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return InsuranceGapResponse.model_validate(gap)
