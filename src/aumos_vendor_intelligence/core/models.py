"""SQLAlchemy ORM models for the AumOS Vendor Intelligence service.

All tables use the `vin_` prefix. Tenant-scoped tables extend AumOSModel
which supplies id (UUID), tenant_id, created_at, and updated_at columns.

Domain model:
  Vendor              — AI vendor profile with compatibility and portability metadata
  VendorEvaluation    — Multi-criteria evaluation score for a vendor
  LockInAssessment    — Vendor lock-in risk analysis result
  Contract            — Vendor contract metadata and risk analysis
  InsuranceGap        — Identified insurance gap from vendor usage
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aumos_common.database import AumOSModel


class Vendor(AumOSModel):
    """An AI vendor profile registered for evaluation.

    Tracks vendor identity, integration characteristics, and overall
    evaluation status to support procurement risk decisions.

    Status transitions:
        active — vendor is approved for use
        under_review — vendor evaluation in progress
        flagged — vendor has identified risk issues
        deactivated — vendor removed from approved list

    Table: vin_vendors
    """

    __tablename__ = "vin_vendors"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Vendor company or product name",
    )
    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=(
            "Vendor category: llm_provider | mlops_platform | data_platform | "
            "observability | security | infrastructure"
        ),
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Freeform description of the vendor's products and services",
    )
    website_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Vendor website URL for reference",
    )
    api_compatibility: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment=(
            "API compatibility metadata: "
            "{\"openai_compatible\": true, \"grpc\": false, "
            "\"sdk_languages\": [\"python\", \"typescript\"], "
            "\"rest_api\": true, \"streaming\": true}"
        ),
    )
    data_portability: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment=(
            "Data portability metadata: "
            "{\"export_formats\": [\"jsonl\", \"parquet\"], "
            "\"model_export\": true, \"vendor_lock_formats\": false, "
            "\"migration_tools\": true}"
        ),
    )
    contact_info: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Vendor contact information: {\"sales\": \"...\", \"support\": \"...\"}",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="under_review",
        index=True,
        comment="active | under_review | flagged | deactivated",
    )
    overall_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Computed composite evaluation score (0.0–1.0), null if not yet evaluated",
    )
    risk_level: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="Overall risk level: low | medium | high | critical",
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the most recent evaluation run",
    )
    registered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User UUID who registered this vendor",
    )

    evaluations: Mapped[list["VendorEvaluation"]] = relationship(
        "VendorEvaluation",
        back_populates="vendor",
        cascade="all, delete-orphan",
        order_by="VendorEvaluation.created_at.desc()",
    )
    lock_in_assessments: Mapped[list["LockInAssessment"]] = relationship(
        "LockInAssessment",
        back_populates="vendor",
        cascade="all, delete-orphan",
        order_by="LockInAssessment.created_at.desc()",
    )
    contracts: Mapped[list["Contract"]] = relationship(
        "Contract",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )
    insurance_gaps: Mapped[list["InsuranceGap"]] = relationship(
        "InsuranceGap",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )


class VendorEvaluation(AumOSModel):
    """Multi-criteria evaluation score for an AI vendor.

    Records weighted scores across evaluation dimensions to produce a
    composite overall_score used for comparison and risk classification.

    Evaluation criteria:
        api_compatibility — openness and breadth of integration options
        data_portability  — ability to migrate data and models out
        security_posture  — certifications, audit history, pen test results
        pricing_transparency — clarity of pricing, no hidden costs
        support_quality   — SLA, response time, escalation paths

    Table: vin_evaluations
    """

    __tablename__ = "vin_evaluations"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vin_vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Parent vendor UUID",
    )
    evaluator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User UUID who performed this evaluation",
    )
    api_compatibility_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="API compatibility criterion score (0.0–1.0)",
    )
    data_portability_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Data portability criterion score (0.0–1.0)",
    )
    security_posture_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Security posture criterion score (0.0–1.0)",
    )
    pricing_transparency_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Pricing transparency criterion score (0.0–1.0)",
    )
    support_quality_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Support quality criterion score (0.0–1.0)",
    )
    overall_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Weighted composite score across all criteria (0.0–1.0)",
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Free-form evaluation notes and justification",
    )
    raw_responses: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Raw evaluation data and evidence: {criterion: {evidence, rationale}}",
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="True for the most recent evaluation — older evaluations are set to False",
    )

    vendor: Mapped["Vendor"] = relationship(
        "Vendor",
        back_populates="evaluations",
    )


class LockInAssessment(AumOSModel):
    """Vendor lock-in risk analysis result.

    Analyses switching costs, proprietary dependencies, data escape hatches,
    and contractual constraints to produce a composite lock-in risk score.

    Risk levels:
        low    — lock_in_score < medium_threshold
        medium — lock_in_score in [medium_threshold, high_threshold)
        high   — lock_in_score >= high_threshold

    Table: vin_lock_in_assessments
    """

    __tablename__ = "vin_lock_in_assessments"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vin_vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Parent vendor UUID",
    )
    assessed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User UUID who triggered this assessment",
    )
    lock_in_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Composite lock-in risk score (0.0=no lock-in, 1.0=complete lock-in)",
    )
    risk_level: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="low | medium | high",
    )
    proprietary_formats_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Degree of proprietary data/model format usage (0.0–1.0)",
    )
    switching_cost_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Estimated relative switching cost (0.0=trivial, 1.0=prohibitive)",
    )
    api_openness_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Openness of vendor APIs to standard protocols (0.0–1.0, inverted)",
    )
    data_egress_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Ease of extracting data from vendor platform (0.0=impossible, 1.0=trivial, inverted for risk)",
    )
    contractual_lock_in_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Degree of contractual lock-in via exclusivity or auto-renewal clauses (0.0–1.0)",
    )
    risk_factors: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment=(
            "List of identified risk factors: "
            "[{\"factor\": \"proprietary_model_format\", \"severity\": \"high\", "
            "\"description\": \"...\", \"mitigation\": \"...\"}]"
        ),
    )
    recommendations: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment=(
            "Actionable recommendations to reduce lock-in: "
            "[{\"action\": \"...\", \"priority\": \"high\", \"effort\": \"medium\"}]"
        ),
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="True for the most recent assessment for this vendor",
    )

    vendor: Mapped["Vendor"] = relationship(
        "Vendor",
        back_populates="lock_in_assessments",
    )


class Contract(AumOSModel):
    """Vendor contract metadata and risk analysis.

    Stores contract terms and the results of automated risk analysis, with
    special attention to liability caps that limit remedies to <= 1 month fees
    (the 88% cap liability threshold from AumOS policy).

    Table: vin_contracts
    """

    __tablename__ = "vin_contracts"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vin_vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Vendor this contract is with",
    )
    contract_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Contract title or reference name",
    )
    contract_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="msa | sow | order_form | addendum | nda",
    )
    effective_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Contract effective date",
    )
    expiry_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Contract expiry or renewal date",
    )
    annual_value_usd: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Approximate annual contract value in USD",
    )
    liability_cap_months: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "Liability cap expressed as months of fees. "
            "Value <= 1.0 triggers the 88% cap liability warning."
        ),
    )
    liability_cap_fraction: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "Liability cap as a fraction of annual fees (0.0–1.0+). "
            "Values >= 0.88 are flagged per AumOS policy."
        ),
    )
    has_liability_cap_warning: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="True when liability_cap_fraction >= 0.88 (1 month or less of fees)",
    )
    auto_renewal_clause: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True if the contract contains an auto-renewal clause",
    )
    governing_law: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Governing law jurisdiction (e.g., 'Delaware, USA')",
    )
    risk_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Composite contract risk score (0.0–1.0), null if not yet analysed",
    )
    risk_level: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="low | medium | high | critical",
    )
    identified_risks: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment=(
            "List of identified contract risks: "
            "[{\"risk_type\": \"liability_cap\", \"severity\": \"high\", "
            "\"clause_reference\": \"Section 12.3\", \"description\": \"...\", "
            "\"recommendation\": \"...\"}]"
        ),
    )
    clauses: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Extracted contract clauses keyed by clause type for analysis",
    )
    analysed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the most recent risk analysis run",
    )
    analysed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User UUID who submitted the contract for analysis",
    )

    vendor: Mapped["Vendor"] = relationship(
        "Vendor",
        back_populates="contracts",
    )
    insurance_gaps: Mapped[list["InsuranceGap"]] = relationship(
        "InsuranceGap",
        back_populates="contract",
        cascade="all, delete-orphan",
    )


class InsuranceGap(AumOSModel):
    """Identified insurance gap from vendor usage.

    Records coverage deficiencies found when comparing a vendor's insurance
    documentation against AumOS minimum coverage requirements.

    Gap statuses:
        open      — gap identified, not yet addressed
        remediated — vendor has provided evidence of coverage
        accepted  — risk accepted by appropriate authority
        escalated — gap escalated for executive review

    Table: vin_insurance_gaps
    """

    __tablename__ = "vin_insurance_gaps"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id",
            "coverage_type",
            "tenant_id",
            name="uq_vin_insurance_gaps_vendor_coverage_tenant",
        ),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vin_vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Vendor with the identified insurance gap",
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vin_contracts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Related contract where the gap was identified (optional)",
    )
    coverage_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=(
            "Type of coverage with the gap: "
            "cyber_liability | errors_and_omissions | "
            "technology_professional_liability | general_liability | "
            "workers_compensation"
        ),
    )
    required_coverage_usd: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Required minimum coverage amount in USD",
    )
    actual_coverage_usd: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Actual vendor coverage amount in USD (null if undocumented)",
    )
    gap_amount_usd: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Coverage shortfall in USD (required - actual), null if undocumented",
    )
    severity: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="medium",
        comment="low | medium | high | critical",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
        index=True,
        comment="open | remediated | accepted | escalated",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Description of the insurance gap and its implications",
    )
    remediation_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Notes on remediation steps taken or planned",
    )
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when this gap was first identified",
    )
    remediated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when this gap was resolved",
    )
    detected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User UUID who identified this gap",
    )

    vendor: Mapped["Vendor"] = relationship(
        "Vendor",
        back_populates="insurance_gaps",
    )
    contract: Mapped["Contract | None"] = relationship(
        "Contract",
        back_populates="insurance_gaps",
    )
