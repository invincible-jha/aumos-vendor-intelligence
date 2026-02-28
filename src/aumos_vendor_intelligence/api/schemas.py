"""Pydantic request and response schemas for the Vendor Intelligence API.

All API inputs and outputs are typed Pydantic models — never raw dicts.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / embedded schemas
# ---------------------------------------------------------------------------


class RiskFactorSchema(BaseModel):
    """A single identified risk factor."""

    risk_type: str
    severity: str
    description: str
    recommendation: str | None = None
    clause_reference: str | None = None


class RecommendationSchema(BaseModel):
    """An actionable risk mitigation recommendation."""

    action: str
    priority: str = Field(pattern="^(low|medium|high|critical)$")
    effort: str = Field(pattern="^(low|medium|high)$")


class InsuranceCoverageInput(BaseModel):
    """A single insurance coverage entry for gap detection."""

    type: str = Field(
        ...,
        description=(
            "Coverage type: cyber_liability | errors_and_omissions | "
            "technology_professional_liability | general_liability | workers_compensation"
        ),
    )
    amount_usd: int = Field(..., gt=0, description="Coverage amount in USD")
    policy_number: str | None = Field(
        default=None,
        description="Optional policy reference number",
    )
    expiry_date: datetime | None = Field(
        default=None,
        description="Optional coverage expiry date",
    )


# ---------------------------------------------------------------------------
# Vendor schemas
# ---------------------------------------------------------------------------


class VendorCreateRequest(BaseModel):
    """Request body for registering a new vendor."""

    name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Vendor company or product name",
        examples=["Anthropic", "OpenAI", "Databricks"],
    )
    category: str = Field(
        ...,
        description=(
            "Vendor category: llm_provider | mlops_platform | data_platform | "
            "observability | security | infrastructure | other"
        ),
        examples=["llm_provider"],
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional vendor description",
    )
    website_url: str | None = Field(
        default=None,
        max_length=512,
        description="Optional vendor website URL",
    )
    api_compatibility: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "API compatibility metadata: "
            "{\"openai_compatible\": true, \"grpc\": false, \"sdk_languages\": [\"python\"]}"
        ),
    )
    data_portability: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Data portability metadata: "
            "{\"export_formats\": [\"jsonl\"], \"model_export\": true}"
        ),
    )
    contact_info: dict[str, Any] = Field(
        default_factory=dict,
        description="Vendor contact information",
    )


class VendorResponse(BaseModel):
    """Response schema for a vendor profile."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    category: str
    description: str | None
    website_url: str | None
    api_compatibility: dict[str, Any]
    data_portability: dict[str, Any]
    contact_info: dict[str, Any]
    status: str
    overall_score: float | None
    risk_level: str | None
    last_evaluated_at: datetime | None
    registered_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VendorListResponse(BaseModel):
    """Paginated vendor list response."""

    items: list[VendorResponse]
    total: int
    page: int
    page_size: int


class VendorCompareResponse(BaseModel):
    """Side-by-side vendor comparison response."""

    vendors: list[VendorResponse]
    comparison_dimensions: list[str] = Field(
        default=[
            "overall_score",
            "risk_level",
            "api_compatibility",
            "data_portability",
            "last_evaluated_at",
        ]
    )


# ---------------------------------------------------------------------------
# Evaluation schemas
# ---------------------------------------------------------------------------


class EvaluationCreateRequest(BaseModel):
    """Request body for running a vendor evaluation."""

    api_compatibility_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="API compatibility criterion score (0.0–1.0)",
    )
    data_portability_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Data portability criterion score (0.0–1.0)",
    )
    security_posture_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Security posture criterion score (0.0–1.0)",
    )
    pricing_transparency_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Pricing transparency criterion score (0.0–1.0)",
    )
    support_quality_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Support quality criterion score (0.0–1.0)",
    )
    notes: str | None = Field(
        default=None,
        max_length=5000,
        description="Free-form evaluation notes",
    )
    raw_responses: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw evaluation evidence keyed by criterion",
    )


class EvaluationResponse(BaseModel):
    """Response schema for a vendor evaluation."""

    id: uuid.UUID
    vendor_id: uuid.UUID
    tenant_id: uuid.UUID
    evaluator_id: uuid.UUID | None
    api_compatibility_score: float
    data_portability_score: float
    security_posture_score: float
    pricing_transparency_score: float
    support_quality_score: float
    overall_score: float
    notes: str | None
    is_current: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Lock-in assessment schemas
# ---------------------------------------------------------------------------


class LockInAssessmentRequest(BaseModel):
    """Request body for running a lock-in risk assessment."""

    proprietary_formats_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Degree of proprietary data/model format usage (0.0=open, 1.0=fully proprietary)",
    )
    switching_cost_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Estimated relative switching cost (0.0=trivial, 1.0=prohibitive)",
    )
    api_openness_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="API lock-in degree (0.0=open standard, 1.0=fully proprietary)",
    )
    data_egress_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Data egress difficulty (0.0=trivial export, 1.0=impossible to extract)",
    )
    contractual_lock_in_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Contractual lock-in degree via exclusivity or auto-renewal clauses (0.0–1.0)",
    )
    risk_factors: list[RiskFactorSchema] = Field(
        default_factory=list,
        description="Identified risk factors with severity and mitigation notes",
    )
    recommendations: list[RecommendationSchema] = Field(
        default_factory=list,
        description="Actionable recommendations to reduce lock-in",
    )


class LockInAssessmentResponse(BaseModel):
    """Response schema for a lock-in risk assessment."""

    id: uuid.UUID
    vendor_id: uuid.UUID
    tenant_id: uuid.UUID
    assessed_by: uuid.UUID | None
    lock_in_score: float
    risk_level: str
    proprietary_formats_score: float
    switching_cost_score: float
    api_openness_score: float
    data_egress_score: float
    contractual_lock_in_score: float
    risk_factors: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    is_current: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Contract schemas
# ---------------------------------------------------------------------------


class ContractAnalyzeRequest(BaseModel):
    """Request body for submitting and analysing a contract."""

    vendor_id: uuid.UUID = Field(..., description="Vendor UUID this contract is with")
    contract_name: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Contract title or reference name",
        examples=["Anthropic Claude API — MSA 2025"],
    )
    contract_type: str = Field(
        ...,
        pattern="^(msa|sow|order_form|addendum|nda)$",
        description="Contract type: msa | sow | order_form | addendum | nda",
    )
    effective_date: datetime | None = Field(
        default=None,
        description="Contract effective date",
    )
    expiry_date: datetime | None = Field(
        default=None,
        description="Contract expiry or renewal date",
    )
    annual_value_usd: int | None = Field(
        default=None,
        gt=0,
        description="Approximate annual contract value in USD",
    )
    liability_cap_months: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Liability cap expressed as months of fees. "
            "Value <= 1.0 triggers the AumOS 88% cap liability warning."
        ),
    )
    auto_renewal_clause: bool = Field(
        default=False,
        description="True if the contract contains an auto-renewal clause",
    )
    governing_law: str | None = Field(
        default=None,
        max_length=100,
        description="Governing law jurisdiction",
    )
    clauses: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted contract clauses keyed by clause type",
    )
    additional_risks: list[RiskFactorSchema] = Field(
        default_factory=list,
        description="Additional risk factors identified via external review",
    )


class ContractRiskResponse(BaseModel):
    """Response schema for a contract risk report."""

    id: uuid.UUID
    vendor_id: uuid.UUID
    tenant_id: uuid.UUID
    contract_name: str
    contract_type: str
    effective_date: datetime | None
    expiry_date: datetime | None
    annual_value_usd: int | None
    liability_cap_months: float | None
    liability_cap_fraction: float | None
    has_liability_cap_warning: bool
    auto_renewal_clause: bool
    governing_law: str | None
    risk_score: float | None
    risk_level: str | None
    identified_risks: list[dict[str, Any]]
    analysed_at: datetime | None
    analysed_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Insurance gap schemas
# ---------------------------------------------------------------------------


class InsuranceCheckRequest(BaseModel):
    """Request body for checking vendor insurance coverage."""

    vendor_id: uuid.UUID = Field(..., description="Vendor UUID to check")
    coverages: list[InsuranceCoverageInput] = Field(
        ...,
        min_length=1,
        description="List of vendor insurance coverages to validate",
    )
    contract_id: uuid.UUID | None = Field(
        default=None,
        description="Optional related contract UUID",
    )


class InsuranceGapResponse(BaseModel):
    """Response schema for an insurance gap record."""

    id: uuid.UUID
    vendor_id: uuid.UUID
    contract_id: uuid.UUID | None
    tenant_id: uuid.UUID
    coverage_type: str
    required_coverage_usd: int
    actual_coverage_usd: int | None
    gap_amount_usd: int | None
    severity: str
    status: str
    description: str | None
    remediation_notes: str | None
    detected_at: datetime | None
    remediated_at: datetime | None
    detected_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InsuranceGapStatusUpdate(BaseModel):
    """Request body for updating insurance gap status."""

    status: str = Field(
        ...,
        pattern="^(open|remediated|accepted|escalated)$",
        description="New gap status: open | remediated | accepted | escalated",
    )
    remediation_notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional notes on remediation steps taken",
    )


class InsuranceCheckResponse(BaseModel):
    """Response after an insurance check run."""

    vendor_id: uuid.UUID
    gaps_found: int
    gaps: list[InsuranceGapResponse]
    coverages_checked: list[str]
    all_coverages_adequate: bool


# ---------------------------------------------------------------------------
# Questionnaire schemas (GAP-268)
# ---------------------------------------------------------------------------


class QuestionSchema(BaseModel):
    """A single question in a questionnaire template."""

    question_id: str
    text: str
    question_type: str = Field(
        pattern="^(text|boolean|rating|multiple_choice|file_upload)$"
    )
    required: bool = True
    options: list[str] | None = None


class QuestionnaireTemplateCreateRequest(BaseModel):
    """Request body for creating a questionnaire template."""

    name: str = Field(..., min_length=3, max_length=255)
    questions: list[QuestionSchema] = Field(..., min_length=1)
    category: str = Field(
        ...,
        pattern="^(security|compliance|general|iso42001|soc2|gdpr)$",
    )


class QuestionnaireTemplateResponse(BaseModel):
    """Response schema for a questionnaire template."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    category: str
    questions: list[dict[str, Any]]
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class QuestionnaireDistributeRequest(BaseModel):
    """Request body for distributing a questionnaire to a vendor contact."""

    vendor_id: uuid.UUID
    template_id: uuid.UUID
    vendor_contact_email: str = Field(..., description="Email address of the vendor contact")
    expiry_days: int = Field(default=14, ge=1, le=90)


class QuestionnaireSubmissionResponse(BaseModel):
    """Response schema for a questionnaire submission."""

    id: uuid.UUID
    vendor_id: uuid.UUID
    template_id: uuid.UUID
    tenant_id: uuid.UUID
    vendor_contact_email: str
    status: str
    responses: dict[str, Any]
    ai_risk_summary: str | None
    ai_score: float | None
    requested_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class QuestionnaireDistributeResponse(BaseModel):
    """Response after distributing a questionnaire."""

    submission: QuestionnaireSubmissionResponse
    access_url: str
    expires_at: datetime


class QuestionnaireSubmitRequest(BaseModel):
    """Request body for vendor submitting questionnaire responses."""

    responses: dict[str, Any] = Field(..., description="Map of question_id to response value")


# ---------------------------------------------------------------------------
# Monitoring alert schemas (GAP-269)
# ---------------------------------------------------------------------------


class MonitoringAlertResponse(BaseModel):
    """Response schema for a vendor monitoring alert."""

    id: uuid.UUID
    vendor_id: uuid.UUID
    tenant_id: uuid.UUID
    source: str
    alert_type: str
    severity: str
    description: str
    raw_data: dict[str, Any]
    status: str
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MonitoringAlertResolveRequest(BaseModel):
    """Request body for resolving a monitoring alert."""

    resolution_notes: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# ISO 42001 schemas (GAP-270)
# ---------------------------------------------------------------------------


class Iso42001ControlResponse(BaseModel):
    """Response schema for an ISO 42001 control."""

    id: uuid.UUID
    control_id: str
    title: str
    description: str
    domain: str
    guidance: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class Iso42001AssessmentRequest(BaseModel):
    """Request body for assessing a vendor against ISO 42001 controls."""

    control_assessments: list[dict[str, Any]] = Field(
        ...,
        description=(
            "List of {control_id, status, evidence, notes} dicts. "
            "status: compliant | partial | non_compliant | not_applicable"
        ),
    )


class Iso42001AssessmentResponse(BaseModel):
    """Response schema for a vendor ISO 42001 assessment record."""

    id: uuid.UUID
    vendor_id: uuid.UUID
    control_id: str
    tenant_id: uuid.UUID
    status: str
    evidence: dict[str, Any]
    notes: str | None
    assessed_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class Iso42001ComplianceReportResponse(BaseModel):
    """Aggregated ISO 42001 compliance report for a vendor."""

    vendor_id: uuid.UUID
    total_controls: int
    compliant: int
    partial: int
    non_compliant: int
    not_applicable: int
    compliance_score: float
    by_domain: dict[str, Any]
    assessments: list[Iso42001AssessmentResponse]


# ---------------------------------------------------------------------------
# Negotiation playbook schemas (GAP-271)
# ---------------------------------------------------------------------------


class NegotiationPlaybookResponse(BaseModel):
    """Response schema for a generated negotiation playbook."""

    vendor_id: uuid.UUID
    executive_summary: str
    leverage_points: list[dict[str, Any]]
    risk_mitigations: list[dict[str, Any]]
    walk_away_conditions: list[str]
    opening_positions: list[dict[str, Any]]
    concession_hierarchy: list[str]
    generated_by: str
    model: str


# ---------------------------------------------------------------------------
# SaaS spend schemas (GAP-272)
# ---------------------------------------------------------------------------


class SaasSpendSyncRequest(BaseModel):
    """Request body for syncing SaaS spend data."""

    source: str = Field(
        ...,
        pattern="^(manual|csv|api_integration)$",
        description="Spend data source type",
    )
    spend_records: list[dict[str, Any]] = Field(
        ...,
        description=(
            "List of spend records: {vendor_id, period_month, amount_usd, "
            "invoice_reference, currency, exchange_rate}"
        ),
    )


class SaasSpendSyncResponse(BaseModel):
    """Response after a SaaS spend sync operation."""

    records_processed: int
    records_created: int
    records_updated: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Intelligence feed schemas (GAP-273)
# ---------------------------------------------------------------------------


class IntelligenceFeedIngestRequest(BaseModel):
    """Request body for ingesting a vendor intelligence feed."""

    feed_source: str = Field(..., description="Name of the intelligence source")
    vendor_id: uuid.UUID
    feed_data: dict[str, Any] = Field(..., description="Raw feed payload to ingest")


class IntelligenceFeedIngestResponse(BaseModel):
    """Response after ingesting an intelligence feed."""

    vendor_id: uuid.UUID
    feed_source: str
    alerts_created: int
    ingested_at: datetime
