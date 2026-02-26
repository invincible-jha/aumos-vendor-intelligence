"""Vendor Intelligence service settings extending AumOS base configuration."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from aumos_common.config import AumOSSettings


class Settings(AumOSSettings):
    """Configuration for the AumOS Vendor Intelligence service.

    Extends base AumOS settings with vendor-intelligence-specific configuration
    for evaluation scoring, lock-in assessment, and contract analysis.

    All settings use the AUMOS_VENDOR_ environment variable prefix.
    """

    service_name: str = "aumos-vendor-intelligence"

    # ---------------------------------------------------------------------------
    # Vendor evaluation scoring weights
    # ---------------------------------------------------------------------------
    scoring_weight_api_compatibility: float = Field(
        default=0.25,
        description="Weight for API compatibility criterion in vendor score (0–1)",
    )
    scoring_weight_data_portability: float = Field(
        default=0.25,
        description="Weight for data portability criterion in vendor score (0–1)",
    )
    scoring_weight_security_posture: float = Field(
        default=0.20,
        description="Weight for security posture criterion in vendor score (0–1)",
    )
    scoring_weight_pricing_transparency: float = Field(
        default=0.15,
        description="Weight for pricing transparency criterion in vendor score (0–1)",
    )
    scoring_weight_support_quality: float = Field(
        default=0.15,
        description="Weight for support quality criterion in vendor score (0–1)",
    )

    # ---------------------------------------------------------------------------
    # Lock-in risk thresholds
    # ---------------------------------------------------------------------------
    lock_in_high_risk_threshold: float = Field(
        default=0.70,
        description="Score above which vendor lock-in risk is rated HIGH (0–1)",
    )
    lock_in_medium_risk_threshold: float = Field(
        default=0.40,
        description="Score above which vendor lock-in risk is rated MEDIUM (0–1)",
    )

    # ---------------------------------------------------------------------------
    # Contract risk analysis
    # ---------------------------------------------------------------------------
    liability_cap_warning_threshold: float = Field(
        default=0.88,
        description=(
            "Liability cap fraction at which a contract risk warning is raised. "
            "Per AumOS policy, contracts capping liability at <= 1 month fees "
            "are flagged when the cap fraction is >= this threshold."
        ),
    )
    contract_analysis_llm_model: str = Field(
        default="claude-opus-4-6",
        description="LLM model ID used for contract clause analysis",
    )
    contract_analysis_max_tokens: int = Field(
        default=4096,
        description="Maximum tokens for contract analysis LLM calls",
    )

    # ---------------------------------------------------------------------------
    # Insurance gap detection
    # ---------------------------------------------------------------------------
    required_coverage_types: list[str] = Field(
        default=[
            "cyber_liability",
            "errors_and_omissions",
            "technology_professional_liability",
        ],
        description="Insurance coverage types required for vendor approval",
    )
    minimum_coverage_amount_usd: int = Field(
        default=5_000_000,
        description="Minimum required insurance coverage amount in USD per incident",
    )

    # ---------------------------------------------------------------------------
    # HTTP client settings
    # ---------------------------------------------------------------------------
    http_timeout: float = Field(
        default=30.0,
        description="Timeout in seconds for HTTP calls to downstream services",
    )
    http_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts for HTTP calls to upstream services",
    )

    model_config = SettingsConfigDict(env_prefix="AUMOS_VENDOR_")
