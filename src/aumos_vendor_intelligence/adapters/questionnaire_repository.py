"""SQLAlchemy repository adapter for vendor security questionnaires.

Implements IQuestionnaireRepository using async SQLAlchemy 2.0 ORM.
"""

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aumos_common.observability import get_logger

from aumos_vendor_intelligence.core.interfaces import IQuestionnaireRepository
from aumos_vendor_intelligence.core.models import (
    VinQuestionnaireLink,
    VinQuestionnaireSubmission,
    VinQuestionnaireTemplate,
)

logger = get_logger(__name__)


class QuestionnaireRepository(IQuestionnaireRepository):
    """SQLAlchemy async repository for questionnaire templates, submissions, and links.

    Args:
        session: Async SQLAlchemy database session with RLS applied.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------------------
    # Template operations
    # ---------------------------------------------------------------------------

    async def create_template(
        self,
        tenant_id: uuid.UUID,
        name: str,
        questions: list[dict[str, Any]],
        category: str,
        created_by: uuid.UUID | None,
    ) -> VinQuestionnaireTemplate:
        """Create a new questionnaire template.

        Args:
            tenant_id: Owning tenant UUID.
            name: Template display name.
            questions: List of question dicts with question_id, text, type, required.
            category: Questionnaire category (security | compliance | general).
            created_by: Optional user UUID who created the template.

        Returns:
            Newly created VinQuestionnaireTemplate ORM instance.
        """
        template = VinQuestionnaireTemplate(
            tenant_id=tenant_id,
            name=name,
            questions=questions,
            category=category,
            created_by=created_by,
        )
        self._session.add(template)
        await self._session.flush()
        logger.info(
            "questionnaire_template_created",
            template_id=str(template.id),
            tenant_id=str(tenant_id),
            category=category,
        )
        return template

    async def get_template(
        self,
        template_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> VinQuestionnaireTemplate | None:
        """Retrieve a questionnaire template by ID.

        Args:
            template_id: Template UUID.
            tenant_id: Tenant UUID for RLS enforcement.

        Returns:
            VinQuestionnaireTemplate instance or None if not found.
        """
        result = await self._session.execute(
            select(VinQuestionnaireTemplate).where(
                VinQuestionnaireTemplate.id == template_id,
                VinQuestionnaireTemplate.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_templates(
        self,
        tenant_id: uuid.UUID,
        category: str | None = None,
    ) -> list[VinQuestionnaireTemplate]:
        """List questionnaire templates for a tenant.

        Args:
            tenant_id: Tenant UUID for RLS enforcement.
            category: Optional category filter.

        Returns:
            List of VinQuestionnaireTemplate instances.
        """
        stmt = select(VinQuestionnaireTemplate).where(
            VinQuestionnaireTemplate.tenant_id == tenant_id
        )
        if category:
            stmt = stmt.where(VinQuestionnaireTemplate.category == category)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ---------------------------------------------------------------------------
    # Submission operations
    # ---------------------------------------------------------------------------

    async def create_submission(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        template_id: uuid.UUID,
        vendor_contact_email: str,
        requested_by: uuid.UUID | None,
    ) -> VinQuestionnaireSubmission:
        """Create a new questionnaire submission record.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor UUID this questionnaire is sent to.
            template_id: Template UUID used for the questionnaire.
            vendor_contact_email: Email address of the vendor contact.
            requested_by: Optional user UUID who initiated the questionnaire.

        Returns:
            Newly created VinQuestionnaireSubmission ORM instance.
        """
        submission = VinQuestionnaireSubmission(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            template_id=template_id,
            vendor_contact_email=vendor_contact_email,
            requested_by=requested_by,
            status="pending",
        )
        self._session.add(submission)
        await self._session.flush()
        logger.info(
            "questionnaire_submission_created",
            submission_id=str(submission.id),
            vendor_id=str(vendor_id),
            template_id=str(template_id),
        )
        return submission

    async def get_submission(
        self,
        submission_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> VinQuestionnaireSubmission | None:
        """Retrieve a questionnaire submission by ID.

        Args:
            submission_id: Submission UUID.
            tenant_id: Tenant UUID for RLS enforcement.

        Returns:
            VinQuestionnaireSubmission instance or None if not found.
        """
        result = await self._session.execute(
            select(VinQuestionnaireSubmission).where(
                VinQuestionnaireSubmission.id == submission_id,
                VinQuestionnaireSubmission.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_submissions(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> list[VinQuestionnaireSubmission]:
        """List questionnaire submissions for a tenant.

        Args:
            tenant_id: Tenant UUID for RLS enforcement.
            vendor_id: Optional vendor UUID filter.
            status: Optional status filter.

        Returns:
            List of VinQuestionnaireSubmission instances.
        """
        stmt = select(VinQuestionnaireSubmission).where(
            VinQuestionnaireSubmission.tenant_id == tenant_id
        )
        if vendor_id:
            stmt = stmt.where(VinQuestionnaireSubmission.vendor_id == vendor_id)
        if status:
            stmt = stmt.where(VinQuestionnaireSubmission.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_submission_responses(
        self,
        submission_id: uuid.UUID,
        responses: dict[str, Any],
        status: str,
    ) -> VinQuestionnaireSubmission | None:
        """Update submission with vendor responses.

        Args:
            submission_id: Submission UUID to update.
            responses: Dict mapping question_id to response value.
            status: New status (submitted | ai_reviewed | complete).

        Returns:
            Updated VinQuestionnaireSubmission or None if not found.
        """
        await self._session.execute(
            update(VinQuestionnaireSubmission)
            .where(VinQuestionnaireSubmission.id == submission_id)
            .values(responses=responses, status=status)
        )
        result = await self._session.execute(
            select(VinQuestionnaireSubmission).where(
                VinQuestionnaireSubmission.id == submission_id
            )
        )
        return result.scalar_one_or_none()

    async def update_submission_ai_review(
        self,
        submission_id: uuid.UUID,
        ai_risk_summary: str,
        ai_score: float,
        status: str,
    ) -> VinQuestionnaireSubmission | None:
        """Store AI review results on a submission.

        Args:
            submission_id: Submission UUID to update.
            ai_risk_summary: Natural language risk summary from the AI review.
            ai_score: Normalised risk score 0.0–1.0.
            status: New status (ai_reviewed | complete).

        Returns:
            Updated VinQuestionnaireSubmission or None if not found.
        """
        await self._session.execute(
            update(VinQuestionnaireSubmission)
            .where(VinQuestionnaireSubmission.id == submission_id)
            .values(
                ai_risk_summary=ai_risk_summary,
                ai_score=ai_score,
                status=status,
            )
        )
        result = await self._session.execute(
            select(VinQuestionnaireSubmission).where(
                VinQuestionnaireSubmission.id == submission_id
            )
        )
        return result.scalar_one_or_none()

    # ---------------------------------------------------------------------------
    # Link operations (public token-based access)
    # ---------------------------------------------------------------------------

    async def create_link(
        self,
        submission_id: uuid.UUID,
        token: str,
        expires_at: Any,
    ) -> VinQuestionnaireLink:
        """Create a public access link for a questionnaire submission.

        Args:
            submission_id: Submission UUID this link grants access to.
            token: Cryptographically secure URL-safe token (48 bytes).
            expires_at: Datetime at which the link expires.

        Returns:
            Newly created VinQuestionnaireLink ORM instance.
        """
        link = VinQuestionnaireLink(
            submission_id=submission_id,
            token=token,
            expires_at=expires_at,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def get_link_by_token(self, token: str) -> VinQuestionnaireLink | None:
        """Retrieve a questionnaire link by its access token.

        Args:
            token: URL-safe token string from the questionnaire invitation email.

        Returns:
            VinQuestionnaireLink instance or None if not found or expired.
        """
        result = await self._session.execute(
            select(VinQuestionnaireLink).where(VinQuestionnaireLink.token == token)
        )
        return result.scalar_one_or_none()
