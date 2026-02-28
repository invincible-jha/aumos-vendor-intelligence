"""SQLAlchemy repository adapter for ISO/IEC 42001:2023 compliance tracking.

Implements IIso42001Repository using async SQLAlchemy 2.0 ORM.
"""

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aumos_common.observability import get_logger

from aumos_vendor_intelligence.core.interfaces import IIso42001Repository
from aumos_vendor_intelligence.core.models import (
    VinIso42001Control,
    VinVendorIso42001Assessment,
)

logger = get_logger(__name__)


class Iso42001Repository(IIso42001Repository):
    """SQLAlchemy async repository for ISO 42001 controls and vendor assessments.

    Args:
        session: Async SQLAlchemy database session with RLS applied.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------------------
    # ISO 42001 Control library operations
    # ---------------------------------------------------------------------------

    async def list_controls(
        self,
        domain: str | None = None,
    ) -> list[VinIso42001Control]:
        """List ISO 42001 controls, optionally filtered by domain.

        Args:
            domain: Optional domain filter (e.g., "governance", "risk_management",
                "data_management", "transparency", "accountability").

        Returns:
            List of VinIso42001Control instances.
        """
        stmt = select(VinIso42001Control)
        if domain:
            stmt = stmt.where(VinIso42001Control.domain == domain)
        stmt = stmt.order_by(VinIso42001Control.control_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_control(self, control_id: str) -> VinIso42001Control | None:
        """Retrieve a single ISO 42001 control by its control ID.

        Args:
            control_id: ISO 42001 control identifier (e.g., "6.1.2").

        Returns:
            VinIso42001Control instance or None if not found.
        """
        result = await self._session.execute(
            select(VinIso42001Control).where(
                VinIso42001Control.control_id == control_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert_control(
        self,
        control_id: str,
        title: str,
        description: str,
        domain: str,
        guidance: str | None,
    ) -> VinIso42001Control:
        """Upsert a control into the ISO 42001 control library.

        Creates the control if it does not exist, or updates it if the
        control_id already exists.

        Args:
            control_id: ISO 42001 control identifier (e.g., "6.1.2").
            title: Short title of the control.
            description: Full description of the control requirement.
            domain: Control domain (governance | risk_management | data_management |
                transparency | accountability).
            guidance: Optional implementation guidance text.

        Returns:
            Upserted VinIso42001Control instance.
        """
        existing = await self.get_control(control_id)
        if existing:
            await self._session.execute(
                update(VinIso42001Control)
                .where(VinIso42001Control.control_id == control_id)
                .values(
                    title=title,
                    description=description,
                    domain=domain,
                    guidance=guidance,
                )
            )
            await self._session.refresh(existing)
            return existing

        control = VinIso42001Control(
            control_id=control_id,
            title=title,
            description=description,
            domain=domain,
            guidance=guidance,
        )
        self._session.add(control)
        await self._session.flush()
        logger.info("iso42001_control_created", control_id=control_id, domain=domain)
        return control

    # ---------------------------------------------------------------------------
    # Vendor assessment operations
    # ---------------------------------------------------------------------------

    async def upsert_vendor_assessment(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        control_id: str,
        status: str,
        evidence: dict[str, Any] | None,
        notes: str | None,
        assessed_by: uuid.UUID | None,
    ) -> VinVendorIso42001Assessment:
        """Upsert a vendor's compliance assessment for a specific ISO 42001 control.

        Creates or replaces the assessment record for (vendor_id, control_id, tenant_id).

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor UUID being assessed.
            control_id: ISO 42001 control ID being assessed.
            status: Compliance status: compliant | partial | non_compliant | not_applicable.
            evidence: Optional dict of evidence artifacts (doc refs, screenshots).
            notes: Optional free-form assessment notes.
            assessed_by: Optional user UUID who performed the assessment.

        Returns:
            Upserted VinVendorIso42001Assessment instance.
        """
        result = await self._session.execute(
            select(VinVendorIso42001Assessment).where(
                VinVendorIso42001Assessment.vendor_id == vendor_id,
                VinVendorIso42001Assessment.control_id == control_id,
                VinVendorIso42001Assessment.tenant_id == tenant_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            await self._session.execute(
                update(VinVendorIso42001Assessment)
                .where(VinVendorIso42001Assessment.id == existing.id)
                .values(
                    status=status,
                    evidence=evidence or {},
                    notes=notes,
                    assessed_by=assessed_by,
                )
            )
            await self._session.refresh(existing)
            return existing

        assessment = VinVendorIso42001Assessment(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            control_id=control_id,
            status=status,
            evidence=evidence or {},
            notes=notes,
            assessed_by=assessed_by,
        )
        self._session.add(assessment)
        await self._session.flush()
        logger.info(
            "iso42001_assessment_created",
            vendor_id=str(vendor_id),
            control_id=control_id,
            status=status,
        )
        return assessment

    async def list_vendor_assessments(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
    ) -> list[VinVendorIso42001Assessment]:
        """List all ISO 42001 assessments for a vendor.

        Args:
            tenant_id: Tenant UUID for RLS enforcement.
            vendor_id: Vendor UUID to query assessments for.

        Returns:
            List of VinVendorIso42001Assessment instances.
        """
        result = await self._session.execute(
            select(VinVendorIso42001Assessment).where(
                VinVendorIso42001Assessment.vendor_id == vendor_id,
                VinVendorIso42001Assessment.tenant_id == tenant_id,
            )
        )
        return list(result.scalars().all())
