"""Unit tests for Vendor Intelligence services.

Tests use mock repositories to verify service business logic in isolation.
"""

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aumos_vendor_intelligence.core.models import (
    Contract,
    InsuranceGap,
    LockInAssessment,
    Vendor,
    VendorEvaluation,
)
from aumos_vendor_intelligence.core.services import (
    ContractAnalyzerService,
    InsuranceCheckerService,
    LockInAssessorService,
    VendorScorerService,
)


def _make_vendor(
    vendor_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    name: str = "Test Vendor",
    category: str = "llm_provider",
    overall_score: float | None = None,
    risk_level: str | None = None,
) -> Vendor:
    """Create a minimal Vendor instance for testing."""
    vendor = MagicMock(spec=Vendor)
    vendor.id = vendor_id or uuid.uuid4()
    vendor.tenant_id = tenant_id or uuid.uuid4()
    vendor.name = name
    vendor.category = category
    vendor.overall_score = overall_score
    vendor.risk_level = risk_level
    return vendor


def _make_evaluation(
    evaluation_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    overall_score: float = 0.75,
) -> VendorEvaluation:
    """Create a minimal VendorEvaluation for testing."""
    evaluation = MagicMock(spec=VendorEvaluation)
    evaluation.id = evaluation_id or uuid.uuid4()
    evaluation.vendor_id = vendor_id or uuid.uuid4()
    evaluation.overall_score = overall_score
    evaluation.is_current = True
    return evaluation


def _make_lock_in_assessment(
    assessment_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    lock_in_score: float = 0.3,
    risk_level: str = "low",
) -> LockInAssessment:
    """Create a minimal LockInAssessment for testing."""
    assessment = MagicMock(spec=LockInAssessment)
    assessment.id = assessment_id or uuid.uuid4()
    assessment.vendor_id = vendor_id or uuid.uuid4()
    assessment.lock_in_score = lock_in_score
    assessment.risk_level = risk_level
    assessment.is_current = True
    return assessment


def _make_contract(
    contract_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    contract_type: str = "msa",
) -> Contract:
    """Create a minimal Contract for testing."""
    contract = MagicMock(spec=Contract)
    contract.id = contract_id or uuid.uuid4()
    contract.vendor_id = vendor_id or uuid.uuid4()
    contract.contract_type = contract_type
    contract.has_liability_cap_warning = False
    contract.identified_risks = []
    return contract


# ---------------------------------------------------------------------------
# VendorScorerService tests
# ---------------------------------------------------------------------------


class TestVendorScorerService:
    """Tests for VendorScorerService."""

    def _make_service(
        self,
        vendor_repo: Any | None = None,
        evaluation_repo: Any | None = None,
        publisher: Any | None = None,
    ) -> VendorScorerService:
        vendor_repo = vendor_repo or AsyncMock()
        evaluation_repo = evaluation_repo or AsyncMock()
        publisher = publisher or AsyncMock()
        return VendorScorerService(
            vendor_repo=vendor_repo,
            evaluation_repo=evaluation_repo,
            event_publisher=publisher,
        )

    @pytest.mark.asyncio
    async def test_register_vendor_valid_category(self) -> None:
        """VendorScorerService.register_vendor creates vendor for valid category."""
        vendor_repo = AsyncMock()
        tenant_id = uuid.uuid4()
        expected_vendor = _make_vendor(tenant_id=tenant_id, category="llm_provider")
        vendor_repo.create.return_value = expected_vendor

        service = self._make_service(vendor_repo=vendor_repo)
        vendor = await service.register_vendor(
            tenant_id=tenant_id,
            name="TestCo",
            category="llm_provider",
        )

        assert vendor.category == "llm_provider"
        vendor_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_vendor_invalid_category_raises(self) -> None:
        """VendorScorerService.register_vendor raises ConflictError for bad category."""
        from aumos_common.errors import ConflictError  # noqa: PLC0415

        service = self._make_service()
        with pytest.raises(ConflictError):
            await service.register_vendor(
                tenant_id=uuid.uuid4(),
                name="BadCo",
                category="not_a_real_category",
            )

    @pytest.mark.asyncio
    async def test_run_evaluation_computes_weighted_score(self) -> None:
        """VendorScorerService.run_evaluation computes weighted composite correctly."""
        tenant_id = uuid.uuid4()
        vendor_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)
        vendor_repo.update_score.return_value = _make_vendor(vendor_id=vendor_id)

        evaluation_repo = AsyncMock()
        expected_eval = _make_evaluation(vendor_id=vendor_id, overall_score=0.7)
        evaluation_repo.create.return_value = expected_eval
        evaluation_repo.get_current.return_value = None

        service = self._make_service(
            vendor_repo=vendor_repo,
            evaluation_repo=evaluation_repo,
        )

        evaluation = await service.run_evaluation(
            vendor_id=vendor_id,
            tenant_id=tenant_id,
            api_compatibility_score=0.8,
            data_portability_score=0.6,
            security_posture_score=0.7,
            pricing_transparency_score=0.8,
            support_quality_score=0.6,
        )

        # Expected: 0.8*0.25 + 0.6*0.25 + 0.7*0.20 + 0.8*0.15 + 0.6*0.15 = 0.70
        assert evaluation_repo.create.called
        vendor_repo.update_score.assert_called_once()
        call_kwargs = vendor_repo.update_score.call_args.kwargs
        assert abs(call_kwargs["overall_score"] - 0.70) < 0.001

    @pytest.mark.asyncio
    async def test_run_evaluation_invalid_score_raises(self) -> None:
        """VendorScorerService.run_evaluation raises ConflictError for out-of-range score."""
        from aumos_common.errors import ConflictError  # noqa: PLC0415

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor()

        service = self._make_service(vendor_repo=vendor_repo)
        with pytest.raises(ConflictError):
            await service.run_evaluation(
                vendor_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                api_compatibility_score=1.5,  # invalid
                data_portability_score=0.5,
                security_posture_score=0.5,
                pricing_transparency_score=0.5,
                support_quality_score=0.5,
            )

    @pytest.mark.asyncio
    async def test_compare_vendors_requires_minimum_two(self) -> None:
        """VendorScorerService.compare_vendors raises ConflictError with < 2 IDs."""
        from aumos_common.errors import ConflictError  # noqa: PLC0415

        service = self._make_service()
        with pytest.raises(ConflictError):
            await service.compare_vendors(
                tenant_id=uuid.uuid4(),
                vendor_ids=[uuid.uuid4()],
            )

    @pytest.mark.asyncio
    async def test_compare_vendors_max_ten(self) -> None:
        """VendorScorerService.compare_vendors raises ConflictError with > 10 IDs."""
        from aumos_common.errors import ConflictError  # noqa: PLC0415

        service = self._make_service()
        with pytest.raises(ConflictError):
            await service.compare_vendors(
                tenant_id=uuid.uuid4(),
                vendor_ids=[uuid.uuid4() for _ in range(11)],
            )

    def test_classify_risk_low_score_returns_critical(self) -> None:
        """VendorScorerService._classify_risk: score < 0.25 is critical risk."""
        assert VendorScorerService._classify_risk(0.10) == "critical"

    def test_classify_risk_high_score_returns_low(self) -> None:
        """VendorScorerService._classify_risk: score >= 0.75 is low risk."""
        assert VendorScorerService._classify_risk(0.90) == "low"


# ---------------------------------------------------------------------------
# LockInAssessorService tests
# ---------------------------------------------------------------------------


class TestLockInAssessorService:
    """Tests for LockInAssessorService."""

    def _make_service(
        self,
        vendor_repo: Any | None = None,
        lock_in_repo: Any | None = None,
        publisher: Any | None = None,
    ) -> LockInAssessorService:
        vendor_repo = vendor_repo or AsyncMock()
        lock_in_repo = lock_in_repo or AsyncMock()
        publisher = publisher or AsyncMock()
        return LockInAssessorService(
            vendor_repo=vendor_repo,
            lock_in_repo=lock_in_repo,
            event_publisher=publisher,
        )

    @pytest.mark.asyncio
    async def test_assess_lock_in_computes_average_score(self) -> None:
        """LockInAssessorService.assess_lock_in averages 5 dimension scores."""
        vendor_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)

        lock_in_repo = AsyncMock()
        expected_assessment = _make_lock_in_assessment(
            vendor_id=vendor_id, lock_in_score=0.5, risk_level="medium"
        )
        lock_in_repo.create.return_value = expected_assessment
        lock_in_repo.get_current.return_value = None

        service = self._make_service(
            vendor_repo=vendor_repo, lock_in_repo=lock_in_repo
        )

        assessment = await service.assess_lock_in(
            vendor_id=vendor_id,
            tenant_id=tenant_id,
            proprietary_formats_score=0.5,
            switching_cost_score=0.5,
            api_openness_score=0.5,
            data_egress_score=0.5,
            contractual_lock_in_score=0.5,
        )

        assert lock_in_repo.create.called
        call_kwargs = lock_in_repo.create.call_args.kwargs
        assert abs(call_kwargs["lock_in_score"] - 0.5) < 0.001

    @pytest.mark.asyncio
    async def test_assess_lock_in_high_risk_classification(self) -> None:
        """LockInAssessorService classifies score >= 0.70 as high risk."""
        vendor_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)

        lock_in_repo = AsyncMock()
        lock_in_repo.create.return_value = _make_lock_in_assessment(
            vendor_id=vendor_id, lock_in_score=0.9, risk_level="high"
        )
        lock_in_repo.get_current.return_value = None

        service = self._make_service(
            vendor_repo=vendor_repo, lock_in_repo=lock_in_repo
        )

        await service.assess_lock_in(
            vendor_id=vendor_id,
            tenant_id=uuid.uuid4(),
            proprietary_formats_score=0.9,
            switching_cost_score=0.9,
            api_openness_score=0.9,
            data_egress_score=0.9,
            contractual_lock_in_score=0.9,
        )

        call_kwargs = lock_in_repo.create.call_args.kwargs
        assert call_kwargs["risk_level"] == "high"

    @pytest.mark.asyncio
    async def test_get_lock_in_not_found_raises(self) -> None:
        """LockInAssessorService.get_lock_in_assessment raises NotFoundError when absent."""
        from aumos_common.errors import NotFoundError  # noqa: PLC0415

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor()

        lock_in_repo = AsyncMock()
        lock_in_repo.get_current.return_value = None

        service = self._make_service(
            vendor_repo=vendor_repo, lock_in_repo=lock_in_repo
        )

        with pytest.raises(NotFoundError):
            await service.get_lock_in_assessment(uuid.uuid4(), uuid.uuid4())


# ---------------------------------------------------------------------------
# ContractAnalyzerService tests
# ---------------------------------------------------------------------------


class TestContractAnalyzerService:
    """Tests for ContractAnalyzerService."""

    def _make_service(
        self,
        vendor_repo: Any | None = None,
        contract_repo: Any | None = None,
        publisher: Any | None = None,
    ) -> ContractAnalyzerService:
        vendor_repo = vendor_repo or AsyncMock()
        contract_repo = contract_repo or AsyncMock()
        publisher = publisher or AsyncMock()
        return ContractAnalyzerService(
            vendor_repo=vendor_repo,
            contract_repo=contract_repo,
            event_publisher=publisher,
        )

    @pytest.mark.asyncio
    async def test_analyze_contract_flags_one_month_cap(self) -> None:
        """ContractAnalyzerService sets has_liability_cap_warning for 1-month cap."""
        vendor_id = uuid.uuid4()
        contract_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)

        contract = _make_contract(contract_id=contract_id, vendor_id=vendor_id)
        contract_repo = AsyncMock()
        contract_repo.create.return_value = contract
        contract_repo.get_by_id.return_value = contract
        updated_contract = _make_contract(contract_id=contract_id, vendor_id=vendor_id)
        updated_contract.has_liability_cap_warning = True
        contract_repo.update_risk_analysis.return_value = updated_contract

        service = self._make_service(
            vendor_repo=vendor_repo, contract_repo=contract_repo
        )

        # Submit contract
        await service.submit_contract(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            contract_name="Test MSA",
            contract_type="msa",
        )

        # Analyse with 1 month cap
        analysed = await service.analyze_contract(
            contract_id=contract_id,
            tenant_id=tenant_id,
            liability_cap_months=1.0,
            annual_value_usd=120_000,
            auto_renewal_clause=False,
            governing_law="Delaware, USA",
        )

        update_kwargs = contract_repo.update_risk_analysis.call_args.kwargs
        assert update_kwargs["has_liability_cap_warning"] is True

    @pytest.mark.asyncio
    async def test_analyze_contract_no_warning_for_large_cap(self) -> None:
        """ContractAnalyzerService does not warn for 12-month liability cap."""
        vendor_id = uuid.uuid4()
        contract_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)

        contract = _make_contract(contract_id=contract_id, vendor_id=vendor_id)
        contract_repo = AsyncMock()
        contract_repo.create.return_value = contract
        contract_repo.get_by_id.return_value = contract
        contract_repo.update_risk_analysis.return_value = contract

        service = self._make_service(
            vendor_repo=vendor_repo, contract_repo=contract_repo
        )

        await service.submit_contract(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            contract_name="Test MSA",
            contract_type="msa",
        )

        await service.analyze_contract(
            contract_id=contract_id,
            tenant_id=tenant_id,
            liability_cap_months=12.0,
            annual_value_usd=120_000,
            auto_renewal_clause=False,
            governing_law=None,
        )

        update_kwargs = contract_repo.update_risk_analysis.call_args.kwargs
        assert update_kwargs["has_liability_cap_warning"] is False

    @pytest.mark.asyncio
    async def test_submit_contract_invalid_type_raises(self) -> None:
        """ContractAnalyzerService.submit_contract raises ConflictError for bad type."""
        from aumos_common.errors import ConflictError  # noqa: PLC0415

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor()

        service = self._make_service(vendor_repo=vendor_repo)

        with pytest.raises(ConflictError):
            await service.submit_contract(
                tenant_id=uuid.uuid4(),
                vendor_id=uuid.uuid4(),
                contract_name="Test",
                contract_type="invalid_type",
            )

    def test_compute_risk_score_with_cap_warning(self) -> None:
        """ContractAnalyzerService._compute_risk_score: cap warning contributes 0.50."""
        score = ContractAnalyzerService._compute_risk_score(
            has_liability_cap_warning=True,
            auto_renewal_clause=False,
            additional_risk_count=0,
        )
        assert abs(score - 0.50) < 0.001

    def test_compute_risk_score_cap_plus_autorenewal(self) -> None:
        """ContractAnalyzerService: cap (0.50) + auto-renewal (0.20) = 0.70 → high."""
        score = ContractAnalyzerService._compute_risk_score(
            has_liability_cap_warning=True,
            auto_renewal_clause=True,
            additional_risk_count=0,
        )
        assert abs(score - 0.70) < 0.001
        assert ContractAnalyzerService._classify_contract_risk(score) == "high"


# ---------------------------------------------------------------------------
# InsuranceCheckerService tests
# ---------------------------------------------------------------------------


class TestInsuranceCheckerService:
    """Tests for InsuranceCheckerService."""

    def _make_service(
        self,
        vendor_repo: Any | None = None,
        gap_repo: Any | None = None,
        publisher: Any | None = None,
        required_types: list[str] | None = None,
        minimum_coverage: int = 5_000_000,
    ) -> InsuranceCheckerService:
        vendor_repo = vendor_repo or AsyncMock()
        gap_repo = gap_repo or AsyncMock()
        publisher = publisher or AsyncMock()
        return InsuranceCheckerService(
            vendor_repo=vendor_repo,
            insurance_gap_repo=gap_repo,
            event_publisher=publisher,
            required_coverage_types=required_types or ["cyber_liability", "errors_and_omissions"],
            minimum_coverage_amount_usd=minimum_coverage,
        )

    @pytest.mark.asyncio
    async def test_check_insurance_creates_gap_for_missing_coverage(self) -> None:
        """InsuranceCheckerService creates a gap when coverage type is missing."""
        vendor_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)

        gap_repo = AsyncMock()
        gap = MagicMock(spec=InsuranceGap)
        gap.id = uuid.uuid4()
        gap.coverage_type = "cyber_liability"
        gap.severity = "critical"
        gap_repo.create.return_value = gap

        service = self._make_service(
            vendor_repo=vendor_repo,
            gap_repo=gap_repo,
            required_types=["cyber_liability"],
        )

        gaps = await service.check_insurance(
            vendor_id=vendor_id,
            tenant_id=uuid.uuid4(),
            coverages=[],  # No coverages provided
        )

        assert len(gaps) == 1
        assert gap_repo.create.called
        call_kwargs = gap_repo.create.call_args.kwargs
        assert call_kwargs["coverage_type"] == "cyber_liability"
        assert call_kwargs["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_check_insurance_no_gaps_for_adequate_coverage(self) -> None:
        """InsuranceCheckerService returns no gaps when coverage meets minimum."""
        vendor_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)

        gap_repo = AsyncMock()

        service = self._make_service(
            vendor_repo=vendor_repo,
            gap_repo=gap_repo,
            required_types=["cyber_liability"],
            minimum_coverage=5_000_000,
        )

        gaps = await service.check_insurance(
            vendor_id=vendor_id,
            tenant_id=uuid.uuid4(),
            coverages=[{"type": "cyber_liability", "amount_usd": 10_000_000}],
        )

        assert len(gaps) == 0
        gap_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_insurance_gap_for_insufficient_coverage(self) -> None:
        """InsuranceCheckerService creates a gap when coverage is below minimum."""
        vendor_id = uuid.uuid4()

        vendor_repo = AsyncMock()
        vendor_repo.get_by_id.return_value = _make_vendor(vendor_id=vendor_id)

        gap_repo = AsyncMock()
        gap = MagicMock(spec=InsuranceGap)
        gap.id = uuid.uuid4()
        gap.coverage_type = "cyber_liability"
        gap.severity = "medium"
        gap_repo.create.return_value = gap

        service = self._make_service(
            vendor_repo=vendor_repo,
            gap_repo=gap_repo,
            required_types=["cyber_liability"],
            minimum_coverage=5_000_000,
        )

        gaps = await service.check_insurance(
            vendor_id=vendor_id,
            tenant_id=uuid.uuid4(),
            coverages=[{"type": "cyber_liability", "amount_usd": 3_000_000}],
        )

        assert len(gaps) == 1
        call_kwargs = gap_repo.create.call_args.kwargs
        assert call_kwargs["actual_coverage_usd"] == 3_000_000
        assert call_kwargs["required_coverage_usd"] == 5_000_000
