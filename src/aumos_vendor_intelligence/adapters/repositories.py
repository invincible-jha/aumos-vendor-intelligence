"""SQLAlchemy repository implementations for the Vendor Intelligence service.

All repositories extend BaseRepository from aumos-common and implement the
Protocol interfaces defined in core/interfaces.py.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aumos_common.database import BaseRepository
from aumos_common.observability import get_logger

from aumos_vendor_intelligence.core.models import (
    Contract,
    InsuranceGap,
    LockInAssessment,
    Vendor,
    VendorEvaluation,
)

logger = get_logger(__name__)


class VendorRepository(BaseRepository[Vendor]):
    """Persistence for Vendor entities.

    All queries are tenant-scoped via aumos-common's RLS session setup.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, Vendor)

    async def create(
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
        """Create and persist a new Vendor profile.

        Args:
            tenant_id: Owning tenant UUID.
            name: Vendor name.
            category: Vendor category.
            description: Optional description.
            website_url: Optional website URL.
            api_compatibility: API compatibility metadata.
            data_portability: Data portability metadata.
            contact_info: Contact information.
            registered_by: Optional registering user UUID.

        Returns:
            Newly created Vendor in under_review status.
        """
        vendor = Vendor(
            tenant_id=tenant_id,
            name=name,
            category=category,
            description=description,
            website_url=website_url,
            api_compatibility=api_compatibility,
            data_portability=data_portability,
            contact_info=contact_info,
            registered_by=registered_by,
            status="under_review",
        )
        self._session.add(vendor)
        await self._session.flush()
        await self._session.refresh(vendor)
        return vendor

    async def get_by_id(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Vendor | None:
        """Retrieve a vendor by UUID within a tenant.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            Vendor or None if not found.
        """
        result = await self._session.execute(
            select(Vendor).where(
                Vendor.id == vendor_id,
                Vendor.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        category: str | None,
        status: str | None,
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
        query = select(Vendor).where(Vendor.tenant_id == tenant_id)

        if category is not None:
            query = query.where(Vendor.category == category)
        if status is not None:
            query = query.where(Vendor.status == status)

        count_result = await self._session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        offset = (page - 1) * page_size
        result = await self._session.execute(
            query.order_by(Vendor.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update_score(
        self,
        vendor_id: uuid.UUID,
        overall_score: float,
        risk_level: str,
        last_evaluated_at: datetime,
    ) -> Vendor:
        """Update the vendor's computed score and risk level.

        Args:
            vendor_id: Vendor UUID.
            overall_score: New composite score.
            risk_level: New risk level.
            last_evaluated_at: Evaluation timestamp.

        Returns:
            Updated Vendor.
        """
        await self._session.execute(
            update(Vendor)
            .where(Vendor.id == vendor_id)
            .values(
                overall_score=overall_score,
                risk_level=risk_level,
                last_evaluated_at=last_evaluated_at,
            )
        )
        await self._session.flush()

        result = await self._session.execute(
            select(Vendor).where(Vendor.id == vendor_id)
        )
        return result.scalar_one()

    async def update_status(
        self, vendor_id: uuid.UUID, status: str
    ) -> Vendor:
        """Update vendor operational status.

        Args:
            vendor_id: Vendor UUID.
            status: New status value.

        Returns:
            Updated Vendor.
        """
        await self._session.execute(
            update(Vendor).where(Vendor.id == vendor_id).values(status=status)
        )
        await self._session.flush()

        result = await self._session.execute(
            select(Vendor).where(Vendor.id == vendor_id)
        )
        return result.scalar_one()

    async def list_for_comparison(
        self,
        tenant_id: uuid.UUID,
        vendor_ids: list[uuid.UUID],
    ) -> list[Vendor]:
        """Retrieve multiple vendors for comparison, ordered by score descending.

        Args:
            tenant_id: Requesting tenant.
            vendor_ids: List of vendor UUIDs.

        Returns:
            List of Vendor instances ordered by overall_score descending.
        """
        result = await self._session.execute(
            select(Vendor)
            .where(
                Vendor.id.in_(vendor_ids),
                Vendor.tenant_id == tenant_id,
            )
            .order_by(Vendor.overall_score.desc().nulls_last())
        )
        return list(result.scalars().all())


class EvaluationRepository(BaseRepository[VendorEvaluation]):
    """Persistence for VendorEvaluation entities."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, VendorEvaluation)

    async def create(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        evaluator_id: uuid.UUID | None,
        api_compatibility_score: float,
        data_portability_score: float,
        security_posture_score: float,
        pricing_transparency_score: float,
        support_quality_score: float,
        overall_score: float,
        notes: str | None,
        raw_responses: dict[str, Any],
    ) -> VendorEvaluation:
        """Create a new vendor evaluation record.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Parent vendor UUID.
            evaluator_id: Optional evaluating user UUID.
            api_compatibility_score: API compatibility score.
            data_portability_score: Data portability score.
            security_posture_score: Security posture score.
            pricing_transparency_score: Pricing transparency score.
            support_quality_score: Support quality score.
            overall_score: Weighted composite score.
            notes: Optional evaluation notes.
            raw_responses: Raw evidence dict.

        Returns:
            Newly created VendorEvaluation marked is_current=True.
        """
        evaluation = VendorEvaluation(
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
            is_current=True,
        )
        self._session.add(evaluation)
        await self._session.flush()
        await self._session.refresh(evaluation)
        return evaluation

    async def get_current(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> VendorEvaluation | None:
        """Get the most recent evaluation for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            Most recent VendorEvaluation or None.
        """
        result = await self._session.execute(
            select(VendorEvaluation).where(
                VendorEvaluation.vendor_id == vendor_id,
                VendorEvaluation.tenant_id == tenant_id,
                VendorEvaluation.is_current.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def list_by_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[VendorEvaluation]:
        """List all evaluations for a vendor ordered by newest first.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            List of VendorEvaluation instances.
        """
        result = await self._session.execute(
            select(VendorEvaluation)
            .where(
                VendorEvaluation.vendor_id == vendor_id,
                VendorEvaluation.tenant_id == tenant_id,
            )
            .order_by(VendorEvaluation.created_at.desc())
        )
        return list(result.scalars().all())

    async def deactivate_previous(
        self, vendor_id: uuid.UUID, exclude_id: uuid.UUID
    ) -> None:
        """Mark all evaluations for a vendor as not current except the given one.

        Args:
            vendor_id: Vendor UUID.
            exclude_id: Evaluation UUID to keep as is_current=True.
        """
        await self._session.execute(
            update(VendorEvaluation)
            .where(
                VendorEvaluation.vendor_id == vendor_id,
                VendorEvaluation.id != exclude_id,
            )
            .values(is_current=False)
        )
        await self._session.flush()


class LockInRepository(BaseRepository[LockInAssessment]):
    """Persistence for LockInAssessment entities."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, LockInAssessment)

    async def create(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        assessed_by: uuid.UUID | None,
        lock_in_score: float,
        risk_level: str,
        proprietary_formats_score: float,
        switching_cost_score: float,
        api_openness_score: float,
        data_egress_score: float,
        contractual_lock_in_score: float,
        risk_factors: list[dict[str, Any]],
        recommendations: list[dict[str, Any]],
    ) -> LockInAssessment:
        """Create a new lock-in assessment.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Parent vendor UUID.
            assessed_by: Optional user UUID.
            lock_in_score: Composite lock-in score.
            risk_level: Risk level: low | medium | high.
            proprietary_formats_score: Proprietary format usage score.
            switching_cost_score: Switching cost score.
            api_openness_score: API openness score.
            data_egress_score: Data egress score.
            contractual_lock_in_score: Contractual lock-in score.
            risk_factors: Identified risk factors.
            recommendations: Actionable recommendations.

        Returns:
            Newly created LockInAssessment.
        """
        assessment = LockInAssessment(
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
            is_current=True,
        )
        self._session.add(assessment)
        await self._session.flush()
        await self._session.refresh(assessment)
        return assessment

    async def get_current(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> LockInAssessment | None:
        """Get the most recent lock-in assessment for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            Most recent LockInAssessment or None.
        """
        result = await self._session.execute(
            select(LockInAssessment).where(
                LockInAssessment.vendor_id == vendor_id,
                LockInAssessment.tenant_id == tenant_id,
                LockInAssessment.is_current.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def list_by_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[LockInAssessment]:
        """List all lock-in assessments for a vendor ordered by newest first.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            List of LockInAssessment instances.
        """
        result = await self._session.execute(
            select(LockInAssessment)
            .where(
                LockInAssessment.vendor_id == vendor_id,
                LockInAssessment.tenant_id == tenant_id,
            )
            .order_by(LockInAssessment.created_at.desc())
        )
        return list(result.scalars().all())

    async def deactivate_previous(
        self, vendor_id: uuid.UUID, exclude_id: uuid.UUID
    ) -> None:
        """Mark all assessments for a vendor as not current except the given one.

        Args:
            vendor_id: Vendor UUID.
            exclude_id: Assessment UUID to keep as is_current=True.
        """
        await self._session.execute(
            update(LockInAssessment)
            .where(
                LockInAssessment.vendor_id == vendor_id,
                LockInAssessment.id != exclude_id,
            )
            .values(is_current=False)
        )
        await self._session.flush()


class ContractRepository(BaseRepository[Contract]):
    """Persistence for Contract entities."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, Contract)

    async def create(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        contract_name: str,
        contract_type: str,
        effective_date: datetime | None,
        expiry_date: datetime | None,
        annual_value_usd: int | None,
        analysed_by: uuid.UUID | None,
    ) -> Contract:
        """Create a new contract record.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Parent vendor UUID.
            contract_name: Contract title.
            contract_type: Contract type.
            effective_date: Optional effective date.
            expiry_date: Optional expiry date.
            annual_value_usd: Optional annual value.
            analysed_by: Optional submitting user UUID.

        Returns:
            Newly created Contract.
        """
        contract = Contract(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            contract_name=contract_name,
            contract_type=contract_type,
            effective_date=effective_date,
            expiry_date=expiry_date,
            annual_value_usd=annual_value_usd,
            analysed_by=analysed_by,
            has_liability_cap_warning=False,
            auto_renewal_clause=False,
            identified_risks=[],
            clauses={},
        )
        self._session.add(contract)
        await self._session.flush()
        await self._session.refresh(contract)
        return contract

    async def get_by_id(
        self, contract_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Contract | None:
        """Retrieve a contract by UUID.

        Args:
            contract_id: Contract UUID.
            tenant_id: Requesting tenant.

        Returns:
            Contract or None if not found.
        """
        result = await self._session.execute(
            select(Contract).where(
                Contract.id == contract_id,
                Contract.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[Contract]:
        """List all contracts for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            List of Contract instances.
        """
        result = await self._session.execute(
            select(Contract)
            .where(
                Contract.vendor_id == vendor_id,
                Contract.tenant_id == tenant_id,
            )
            .order_by(Contract.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_risk_analysis(
        self,
        contract_id: uuid.UUID,
        liability_cap_months: float | None,
        liability_cap_fraction: float | None,
        has_liability_cap_warning: bool,
        auto_renewal_clause: bool,
        governing_law: str | None,
        risk_score: float,
        risk_level: str,
        identified_risks: list[dict[str, Any]],
        clauses: dict[str, Any],
        analysed_at: datetime,
    ) -> Contract:
        """Persist risk analysis results onto a contract.

        Args:
            contract_id: Contract UUID.
            liability_cap_months: Liability cap in months.
            liability_cap_fraction: Liability cap fraction.
            has_liability_cap_warning: Cap warning flag.
            auto_renewal_clause: Auto-renewal flag.
            governing_law: Governing law jurisdiction.
            risk_score: Composite risk score.
            risk_level: Risk level classification.
            identified_risks: List of risk dicts.
            clauses: Extracted clause dict.
            analysed_at: Analysis timestamp.

        Returns:
            Updated Contract.
        """
        values: dict[str, Any] = {
            "liability_cap_months": liability_cap_months,
            "liability_cap_fraction": liability_cap_fraction,
            "has_liability_cap_warning": has_liability_cap_warning,
            "auto_renewal_clause": auto_renewal_clause,
            "governing_law": governing_law,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "identified_risks": identified_risks,
            "clauses": clauses,
            "analysed_at": analysed_at,
        }

        await self._session.execute(
            update(Contract).where(Contract.id == contract_id).values(**values)
        )
        await self._session.flush()

        result = await self._session.execute(
            select(Contract).where(Contract.id == contract_id)
        )
        return result.scalar_one()


class InsuranceGapRepository(BaseRepository[InsuranceGap]):
    """Persistence for InsuranceGap entities."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an async SQLAlchemy session.

        Args:
            session: AsyncSession with RLS context already set.
        """
        super().__init__(session, InsuranceGap)

    async def create(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        contract_id: uuid.UUID | None,
        coverage_type: str,
        required_coverage_usd: int,
        actual_coverage_usd: int | None,
        severity: str,
        description: str | None,
        detected_by: uuid.UUID | None,
    ) -> InsuranceGap:
        """Create a new insurance gap record.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor UUID.
            contract_id: Optional related contract UUID.
            coverage_type: Type of coverage with the gap.
            required_coverage_usd: Required coverage in USD.
            actual_coverage_usd: Actual coverage in USD.
            severity: low | medium | high | critical.
            description: Optional gap description.
            detected_by: Optional user UUID.

        Returns:
            Newly created InsuranceGap with status=open.
        """
        from datetime import timezone  # noqa: PLC0415

        gap_amount: int | None = None
        if actual_coverage_usd is not None:
            gap_amount = max(0, required_coverage_usd - actual_coverage_usd)

        gap = InsuranceGap(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            contract_id=contract_id,
            coverage_type=coverage_type,
            required_coverage_usd=required_coverage_usd,
            actual_coverage_usd=actual_coverage_usd,
            gap_amount_usd=gap_amount,
            severity=severity,
            description=description,
            detected_by=detected_by,
            status="open",
            detected_at=datetime.now(tz=timezone.utc),
        )
        self._session.add(gap)
        await self._session.flush()
        await self._session.refresh(gap)
        return gap

    async def get_by_id(
        self, gap_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> InsuranceGap | None:
        """Retrieve an insurance gap by UUID.

        Args:
            gap_id: InsuranceGap UUID.
            tenant_id: Requesting tenant.

        Returns:
            InsuranceGap or None if not found.
        """
        result = await self._session.execute(
            select(InsuranceGap).where(
                InsuranceGap.id == gap_id,
                InsuranceGap.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_vendor(
        self,
        vendor_id: uuid.UUID,
        tenant_id: uuid.UUID,
        status: str | None,
    ) -> list[InsuranceGap]:
        """List all insurance gaps for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.
            status: Optional status filter.

        Returns:
            List of InsuranceGap instances.
        """
        query = select(InsuranceGap).where(
            InsuranceGap.vendor_id == vendor_id,
            InsuranceGap.tenant_id == tenant_id,
        )
        if status is not None:
            query = query.where(InsuranceGap.status == status)

        result = await self._session.execute(
            query.order_by(InsuranceGap.detected_at.desc().nulls_last())
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        gap_id: uuid.UUID,
        status: str,
        remediation_notes: str | None,
        remediated_at: datetime | None,
    ) -> InsuranceGap:
        """Update the status of an insurance gap.

        Args:
            gap_id: InsuranceGap UUID.
            status: New status value.
            remediation_notes: Optional remediation notes.
            remediated_at: Optional resolution timestamp.

        Returns:
            Updated InsuranceGap.
        """
        values: dict[str, Any] = {"status": status}
        if remediation_notes is not None:
            values["remediation_notes"] = remediation_notes
        if remediated_at is not None:
            values["remediated_at"] = remediated_at

        await self._session.execute(
            update(InsuranceGap).where(InsuranceGap.id == gap_id).values(**values)
        )
        await self._session.flush()

        result = await self._session.execute(
            select(InsuranceGap).where(InsuranceGap.id == gap_id)
        )
        return result.scalar_one()
