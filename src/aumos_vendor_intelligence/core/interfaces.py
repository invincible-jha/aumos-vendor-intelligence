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
    VinIso42001Control,
    VinMonitoringAlert,
    VinQuestionnaireLink,
    VinQuestionnaireSubmission,
    VinQuestionnaireTemplate,
    VinVendorIso42001Assessment,
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


# ---------------------------------------------------------------------------
# Interfaces for domain-specific analytics adapters
# ---------------------------------------------------------------------------


@runtime_checkable
class IBenchmarkingRunner(Protocol):
    """Interface for cross-provider benchmark execution."""

    async def run_latency_benchmark(
        self,
        vendor_ids: list[uuid.UUID],
        prompt_payloads: list[dict[str, Any]],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Measure end-to-end latency across vendors for identical prompts."""
        ...

    async def compare_cost_per_token(
        self,
        vendor_ids: list[uuid.UUID],
        model_tiers: dict[str, str],
        token_volume: int,
    ) -> dict[str, Any]:
        """Compare cost-per-token across vendors for equivalent model tiers."""
        ...

    async def score_output_quality(
        self,
        vendor_outputs: dict[str, list[str]],
        reference_outputs: list[str],
    ) -> dict[str, Any]:
        """Score output quality (BLEU-proxy, semantic similarity, coherence)."""
        ...

    async def measure_throughput(
        self,
        vendor_ids: list[uuid.UUID],
        requests_per_second: float,
        duration_seconds: int,
    ) -> dict[str, Any]:
        """Measure sustained request throughput and error rates under load."""
        ...

    async def generate_comparison_report(
        self,
        benchmark_results: dict[str, Any],
        tenant_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Compile all benchmark results into a structured comparison report."""
        ...


@runtime_checkable
class IContractAnalyzer(Protocol):
    """Interface for contract text analysis and risk extraction."""

    async def extract_sla_terms(
        self,
        contract_text: str,
    ) -> dict[str, Any]:
        """Extract SLA commitments (uptime, latency, support response time)."""
        ...

    async def parse_pricing_structure(
        self,
        contract_text: str,
    ) -> dict[str, Any]:
        """Parse pricing models (per-token, per-request, subscription tiers)."""
        ...

    async def analyze_termination_clauses(
        self,
        contract_text: str,
    ) -> dict[str, Any]:
        """Identify termination triggers, notice periods, and exit obligations."""
        ...

    async def detect_liability_limitations(
        self,
        contract_text: str,
        annual_contract_value_usd: float | None,
    ) -> dict[str, Any]:
        """Detect liability cap clauses and assess AumOS 88% policy compliance."""
        ...

    async def compare_contracts(
        self,
        contract_texts: list[str],
        contract_ids: list[uuid.UUID],
    ) -> dict[str, Any]:
        """Side-by-side comparison of multiple contracts on key risk dimensions."""
        ...

    async def generate_key_terms_summary(
        self,
        contract_text: str,
    ) -> dict[str, Any]:
        """Produce an executive-level plain-language summary of key contract terms."""
        ...


@runtime_checkable
class IFallbackRouter(Protocol):
    """Interface for vendor health monitoring and automatic failover routing."""

    async def register_vendor(
        self,
        vendor_id: uuid.UUID,
        priority: int,
        health_endpoint: str | None,
    ) -> None:
        """Register a vendor in the routing pool with a priority rank."""
        ...

    async def select_vendor(
        self,
        tenant_id: uuid.UUID,
        excluded_vendor_ids: list[uuid.UUID] | None,
    ) -> uuid.UUID | None:
        """Select the highest-priority healthy vendor for routing."""
        ...

    async def record_vendor_outcome(
        self,
        vendor_id: uuid.UUID,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record a request outcome to update vendor health state."""
        ...

    async def get_vendor_status_dashboard(self) -> dict[str, Any]:
        """Return a health summary for all registered vendors."""
        ...


@runtime_checkable
class IArbitrageDetector(Protocol):
    """Interface for vendor pricing arbitrage and cost-quality optimisation."""

    async def compare_pricing_for_equivalent_models(
        self,
        vendor_pricing: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compare per-token pricing across vendors for equivalent capability tiers."""
        ...

    async def compute_cost_quality_pareto(
        self,
        vendor_scores: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute the Pareto frontier of cost vs quality across vendors."""
        ...

    async def detect_spot_pricing_opportunities(
        self,
        vendor_id: uuid.UUID,
        historical_pricing: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Identify time-based pricing patterns and spot pricing windows."""
        ...

    async def generate_savings_report(
        self,
        tenant_id: uuid.UUID,
        current_vendor_id: uuid.UUID,
        candidate_vendors: list[uuid.UUID],
        monthly_token_volume: int,
    ) -> dict[str, Any]:
        """Estimate potential savings from switching or blending vendors."""
        ...


@runtime_checkable
class ISLAMonitor(Protocol):
    """Interface for ongoing SLA compliance tracking and alerting."""

    async def record_health_check(
        self,
        vendor_id: uuid.UUID,
        is_up: bool,
        latency_ms: float,
        error_code: str | None,
        checked_at: datetime,
    ) -> None:
        """Record a health check result for a vendor."""
        ...

    async def get_uptime_percent(
        self,
        vendor_id: uuid.UUID,
        window_hours: int,
    ) -> float:
        """Compute vendor uptime percentage over a rolling window."""
        ...

    async def get_latency_percentiles(
        self,
        vendor_id: uuid.UUID,
        window_hours: int,
    ) -> dict[str, float]:
        """Return P50, P95, and P99 latency values over a rolling window."""
        ...

    async def generate_sla_compliance_report(
        self,
        vendor_id: uuid.UUID,
        period_days: int,
        sla_thresholds: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a detailed SLA compliance report for a vendor."""
        ...


@runtime_checkable
class IProcurementAdvisor(Protocol):
    """Interface for vendor shortlisting and procurement guidance."""

    async def match_requirements_to_vendors(
        self,
        requirements: dict[str, Any],
        available_vendors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Match procurement requirements to eligible vendors."""
        ...

    async def score_vendors_multi_criteria(
        self,
        vendors: list[dict[str, Any]],
        scoring_weights: dict[str, float] | None,
    ) -> list[dict[str, Any]]:
        """Score vendors across cost, quality, reliability, compliance, support."""
        ...

    async def generate_shortlist(
        self,
        scored_vendors: list[dict[str, Any]],
        top_n: int,
    ) -> dict[str, Any]:
        """Generate a ranked vendor shortlist with justifications."""
        ...

    async def prepare_rfp_template(
        self,
        requirements: dict[str, Any],
        shortlisted_vendor_ids: list[uuid.UUID],
    ) -> dict[str, Any]:
        """Generate an RFP template tailored to the requirement profile."""
        ...

    async def generate_comparison_matrix(
        self,
        scored_vendors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Produce a side-by-side vendor comparison matrix."""
        ...


@runtime_checkable
class IVendorDataEnricher(Protocol):
    """Interface for vendor profile enrichment from external sources."""

    async def extract_pricing_page_data(
        self,
        vendor_id: uuid.UUID,
        pricing_url: str,
    ) -> dict[str, Any]:
        """Extract pricing tiers and model costs from a vendor pricing page."""
        ...

    async def compile_feature_matrix(
        self,
        vendor_ids: list[uuid.UUID],
        feature_categories: list[str],
    ) -> dict[str, Any]:
        """Compile a feature comparison matrix across multiple vendors."""
        ...

    async def track_compliance_certifications(
        self,
        vendor_id: uuid.UUID,
        certification_types: list[str],
    ) -> dict[str, Any]:
        """Check and track vendor compliance certifications (SOC2, ISO27001, etc.)."""
        ...

    async def score_profile_completeness(
        self,
        vendor_profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Score how complete and current a vendor profile is."""
        ...


@runtime_checkable
class IVendorDashboardAggregator(Protocol):
    """Interface for vendor performance dashboard data aggregation."""

    async def compute_vendor_performance_trends(
        self,
        vendor_id: uuid.UUID,
        evaluation_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute performance score trends over time for a vendor."""
        ...

    async def compute_cost_trends(
        self,
        vendor_id: uuid.UUID,
        cost_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute cost per token or per request trends over time."""
        ...

    async def compute_usage_distribution(
        self,
        tenant_id: uuid.UUID,
        usage_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute vendor usage share and distribution metrics."""
        ...

    async def export_executive_dashboard(
        self,
        tenant_id: uuid.UUID,
        vendor_ids: list[uuid.UUID],
        period_days: int,
    ) -> dict[str, Any]:
        """Export a complete executive dashboard payload for the vendor portfolio."""
        ...


# ---------------------------------------------------------------------------
# New interfaces for GAPs 268-273
# ---------------------------------------------------------------------------


@runtime_checkable
class IQuestionnaireRepository(Protocol):
    """Persistence interface for vendor security questionnaire entities."""

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
            category: Template category.
            questions: List of question definition dicts.

        Returns:
            Newly created VinQuestionnaireTemplate.
        """
        ...

    async def get_template(
        self, template_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> VinQuestionnaireTemplate | None:
        """Retrieve a questionnaire template by UUID."""
        ...

    async def list_templates(
        self, tenant_id: uuid.UUID
    ) -> list[VinQuestionnaireTemplate]:
        """List all active questionnaire templates for a tenant."""
        ...

    async def create_submission(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        template_id: uuid.UUID,
        vendor_contact_email: str,
        sent_at: datetime,
        due_at: datetime,
        token: str,
    ) -> VinQuestionnaireSubmission:
        """Create a submission and its associated tokenised link.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor UUID.
            template_id: Template UUID.
            vendor_contact_email: Recipient email.
            sent_at: Send timestamp.
            due_at: Response deadline.
            token: URL-safe token for the public link.

        Returns:
            Newly created VinQuestionnaireSubmission in 'sent' status.
        """
        ...

    async def get_submission(
        self, submission_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> VinQuestionnaireSubmission | None:
        """Retrieve a questionnaire submission by UUID."""
        ...

    async def list_submissions_for_vendor(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[VinQuestionnaireSubmission]:
        """List all questionnaire submissions for a vendor."""
        ...

    async def get_link_by_token(self, token: str) -> VinQuestionnaireLink | None:
        """Retrieve a questionnaire link by its URL-safe token."""
        ...

    async def mark_link_used(self, link_id: uuid.UUID) -> None:
        """Mark a questionnaire link as used."""
        ...

    async def record_responses(
        self,
        submission_id: uuid.UUID,
        responses: dict[str, Any],
        completed_at: datetime,
    ) -> VinQuestionnaireSubmission:
        """Record vendor responses against a submission.

        Args:
            submission_id: Submission UUID.
            responses: Vendor answers keyed by question ID.
            completed_at: Completion timestamp.

        Returns:
            Updated submission in 'completed' status.
        """
        ...

    async def update_ai_review_scores(
        self,
        submission_id: uuid.UUID,
        tenant_id: uuid.UUID,
        scores: dict[str, float],
    ) -> VinQuestionnaireSubmission:
        """Store AI-generated review scores on a submission."""
        ...


@runtime_checkable
class IMonitoringAlertRepository(Protocol):
    """Persistence interface for VinMonitoringAlert entities."""

    async def create_alert(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        alert_type: str,
        severity: str,
        source: str,
        description: str,
        recommended_action: str | None,
    ) -> VinMonitoringAlert:
        """Create a new monitoring alert.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor UUID.
            alert_type: Alert category identifier.
            severity: low | medium | high | critical.
            source: Feed source identifier.
            description: Human-readable alert description.
            recommended_action: Optional recommended action.

        Returns:
            Newly created VinMonitoringAlert.
        """
        ...

    async def list_alerts(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID | None,
        resolved: bool | None,
    ) -> list[VinMonitoringAlert]:
        """List monitoring alerts with optional filters."""
        ...

    async def resolve_alert(
        self, alert_id: uuid.UUID, tenant_id: uuid.UUID, resolved_at: datetime
    ) -> VinMonitoringAlert:
        """Mark an alert as resolved."""
        ...


@runtime_checkable
class IVendorMonitoringAdapter(Protocol):
    """Interface for external intelligence feed adapters."""

    async def check_vendor(self, vendor: Vendor) -> list[dict[str, Any]]:
        """Check a vendor against this intelligence feed.

        Args:
            vendor: Vendor to check.

        Returns:
            List of alert data dicts for any detected issues. Empty if no issues.
        """
        ...


@runtime_checkable
class IIso42001Repository(Protocol):
    """Persistence interface for ISO 42001 control library and assessments."""

    async def list_all_controls(self) -> list[VinIso42001Control]:
        """List all ISO 42001 Annex A controls (platform-wide)."""
        ...

    async def get_control(self, control_id: str) -> VinIso42001Control | None:
        """Retrieve a single ISO 42001 control by its identifier."""
        ...

    async def upsert_vendor_assessment(
        self,
        tenant_id: uuid.UUID,
        vendor_id: uuid.UUID,
        control_id: str,
        compliance_status: str,
        evidence: str | None,
        assessed_from_questionnaire_id: uuid.UUID | None,
    ) -> VinVendorIso42001Assessment:
        """Create or update a vendor ISO 42001 assessment for one control.

        Args:
            tenant_id: Owning tenant UUID.
            vendor_id: Vendor UUID.
            control_id: ISO 42001 control ID.
            compliance_status: compliant | partially_compliant | non_compliant | not_applicable.
            evidence: Optional evidence text.
            assessed_from_questionnaire_id: Optional source questionnaire UUID.

        Returns:
            Created or updated VinVendorIso42001Assessment.
        """
        ...

    async def list_vendor_assessments(
        self, vendor_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[VinVendorIso42001Assessment]:
        """List all ISO 42001 assessments for a vendor."""
        ...


@runtime_checkable
class INegotiationPlaybookGenerator(Protocol):
    """Interface for generating vendor negotiation playbooks."""

    async def generate(
        self,
        vendor: Vendor,
        lock_in_assessment: LockInAssessment | None,
        latest_contract: Contract | None,
    ) -> dict[str, Any]:
        """Generate a negotiation playbook from vendor data.

        Args:
            vendor: Vendor profile with evaluation scores.
            lock_in_assessment: Current lock-in assessment or None.
            latest_contract: Most recent contract analysis or None.

        Returns:
            Playbook dict with leverage_points, red_lines, recommended_asks,
            and walk_away_triggers sections.
        """
        ...
