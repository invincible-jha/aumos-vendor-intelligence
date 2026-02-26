"""Abstract interfaces (Protocol classes) for the AumOS Vendor Intelligence service.

All adapters implement these protocols so services depend only on abstractions,
enabling straightforward testing via mock implementations.
"""

import uuid
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from aumos_vendor_intelligence.core.models import (
    Contract,
    InsuranceGap,
    LockInAssessment,
    Vendor,
    VendorEvaluation,
)


@runtime_checkable
class IVendorRepository(Protocol):
    """Persistence interface for Vendor entities."""

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
            name: Vendor company or product name.
            category: Vendor category classification.
            description: Optional vendor description.
            website_url: Optional vendor website URL.
            api_compatibility: API compatibility metadata dict.
            data_portability: Data portability metadata dict.
            contact_info: Vendor contact information dict.
            registered_by: Optional UUID of the registering user.

        Returns:
            Newly created Vendor in under_review status.
        """
        ...

    async def get_by_id(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Vendor | None:
        """Retrieve a vendor by UUID within a tenant.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant for RLS enforcement.

        Returns:
            Vendor or None if not found.
        """
        ...

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        category: str | None,
        status: str | None,
    ) -> tuple[list[Vendor], int]:
        """List vendors for a tenant with pagination and optional filters.

        Args:
            tenant_id: Requesting tenant.
            page: 1-based page number.
            page_size: Results per page.
            category: Optional category filter.
            status: Optional status filter.

        Returns:
            Tuple of (vendors, total_count).
        """
        ...

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
            overall_score: New composite score (0.0–1.0).
            risk_level: New risk level: low | medium | high | critical.
            last_evaluated_at: Timestamp of the evaluation.

        Returns:
            Updated Vendor.
        """
        ...

    async def update_status(
        self, vendor_id: uuid.UUID, status: str
    ) -> Vendor:
        """Update vendor operational status.

        Args:
            vendor_id: Vendor UUID.
            status: New status: active | under_review | flagged | deactivated.

        Returns:
            Updated Vendor.
        """
        ...

    async def list_for_comparison(
        self,
        tenant_id: uuid.UUID,
        vendor_ids: list[uuid.UUID],
    ) -> list[Vendor]:
        """Retrieve multiple vendors for side-by-side comparison.

        Args:
            tenant_id: Requesting tenant.
            vendor_ids: List of vendor UUIDs to compare.

        Returns:
            List of Vendor instances (ordered by overall_score descending).
        """
        ...


@runtime_checkable
class IEvaluationRepository(Protocol):
    """Persistence interface for VendorEvaluation entities."""

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
            evaluator_id: Optional user UUID performing the evaluation.
            api_compatibility_score: API compatibility score (0.0–1.0).
            data_portability_score: Data portability score (0.0–1.0).
            security_posture_score: Security posture score (0.0–1.0).
            pricing_transparency_score: Pricing transparency score (0.0–1.0).
            support_quality_score: Support quality score (0.0–1.0).
            overall_score: Weighted composite score (0.0–1.0).
            notes: Optional evaluation notes.
            raw_responses: Raw evaluation evidence dict.

        Returns:
            Newly created VendorEvaluation marked as is_current=True.
        """
        ...

    async def get_current(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> VendorEvaluation | None:
        """Get the most recent evaluation for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            Most recent VendorEvaluation or None if not evaluated.
        """
        ...

    async def list_by_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[VendorEvaluation]:
        """List all evaluations for a vendor (newest first).

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            List of VendorEvaluation instances.
        """
        ...

    async def deactivate_previous(
        self, vendor_id: uuid.UUID, exclude_id: uuid.UUID
    ) -> None:
        """Mark all evaluations for a vendor as not current except the given one.

        Args:
            vendor_id: Vendor UUID.
            exclude_id: The evaluation UUID that should remain is_current=True.
        """
        ...


@runtime_checkable
class ILockInRepository(Protocol):
    """Persistence interface for LockInAssessment entities."""

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
        """Create a new lock-in assessment record.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Parent vendor UUID.
            assessed_by: Optional user UUID who triggered the assessment.
            lock_in_score: Composite lock-in score (0.0–1.0).
            risk_level: low | medium | high.
            proprietary_formats_score: Proprietary format usage score (0.0–1.0).
            switching_cost_score: Estimated switching cost score (0.0–1.0).
            api_openness_score: API openness score (0.0–1.0, inverted).
            data_egress_score: Data egress ease score (0.0–1.0, inverted).
            contractual_lock_in_score: Contractual lock-in score (0.0–1.0).
            risk_factors: List of identified risk factor dicts.
            recommendations: List of actionable recommendation dicts.

        Returns:
            Newly created LockInAssessment.
        """
        ...

    async def get_current(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> LockInAssessment | None:
        """Get the most recent lock-in assessment for a vendor.

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            Most recent LockInAssessment or None if not assessed.
        """
        ...

    async def list_by_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[LockInAssessment]:
        """List all lock-in assessments for a vendor (newest first).

        Args:
            vendor_id: Vendor UUID.
            tenant_id: Requesting tenant.

        Returns:
            List of LockInAssessment instances.
        """
        ...

    async def deactivate_previous(
        self, vendor_id: uuid.UUID, exclude_id: uuid.UUID
    ) -> None:
        """Mark all assessments for a vendor as not current except the given one.

        Args:
            vendor_id: Vendor UUID.
            exclude_id: The assessment UUID that should remain is_current=True.
        """
        ...


@runtime_checkable
class IContractRepository(Protocol):
    """Persistence interface for Contract entities."""

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
            contract_type: msa | sow | order_form | addendum | nda.
            effective_date: Optional contract effective date.
            expiry_date: Optional contract expiry date.
            annual_value_usd: Optional annual contract value in USD.
            analysed_by: Optional user UUID submitting the contract.

        Returns:
            Newly created Contract with no risk analysis yet.
        """
        ...

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
        ...

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
        ...

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
            liability_cap_months: Liability cap in months of fees.
            liability_cap_fraction: Liability cap as fraction of annual fees.
            has_liability_cap_warning: True if cap fraction >= 0.88.
            auto_renewal_clause: True if auto-renewal clause found.
            governing_law: Governing law jurisdiction string.
            risk_score: Composite risk score (0.0–1.0).
            risk_level: low | medium | high | critical.
            identified_risks: List of identified risk dicts.
            clauses: Extracted clause dict.
            analysed_at: Analysis completion timestamp.

        Returns:
            Updated Contract with risk analysis populated.
        """
        ...


@runtime_checkable
class IInsuranceGapRepository(Protocol):
    """Persistence interface for InsuranceGap entities."""

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
            vendor_id: Vendor with the gap.
            contract_id: Optional related contract UUID.
            coverage_type: Type of coverage that is deficient.
            required_coverage_usd: Required minimum coverage in USD.
            actual_coverage_usd: Actual vendor coverage in USD (None if unknown).
            severity: low | medium | high | critical.
            description: Optional description of the gap.
            detected_by: Optional user UUID who identified the gap.

        Returns:
            Newly created InsuranceGap with status=open.
        """
        ...

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
        ...

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
        ...

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
            status: New status: open | remediated | accepted | escalated.
            remediation_notes: Optional notes on resolution.
            remediated_at: Optional timestamp of resolution.

        Returns:
            Updated InsuranceGap.
        """
        ...
