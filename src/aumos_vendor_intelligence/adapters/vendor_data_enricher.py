"""VendorDataEnricher adapter for AI vendor profile enrichment.

Enriches vendor profiles with data from public sources including API
documentation, pricing pages, feature matrices, compliance certifications,
and news monitoring. Scores profile completeness for procurement readiness.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Profile completeness weights — must sum to 1.0
COMPLETENESS_WEIGHTS: dict[str, float] = {
    "api_documentation": 0.15,
    "pricing_data": 0.20,
    "feature_matrix": 0.15,
    "compliance_certifications": 0.25,
    "contact_information": 0.10,
    "sla_documentation": 0.10,
    "support_documentation": 0.05,
}

# Recognised compliance certifications for enrichment
COMPLIANCE_CERTIFICATIONS: list[str] = [
    "soc2_type2", "soc2_type1", "iso27001", "iso27017", "iso27018",
    "gdpr", "hipaa", "pci_dss", "fedramp", "csa_star", "ccpa",
    "nist_csf", "hitrust",
]


class VendorDataEnricher:
    """AI vendor profile enrichment via public data extraction.

    Compiles and structures data from vendor websites, API documentation,
    pricing pages, compliance registries, and news sources to build
    comprehensive vendor intelligence profiles. Each enrichment source
    is tracked for freshness and reliability scoring.

    In production, the fetch_fn callable would wrap an httpx async client
    with retry logic. In tests, it can be replaced with a mock returning
    structured HTML or JSON responses.
    """

    def __init__(
        self,
        fetch_fn: Any | None = None,
        cache_ttl_hours: int = 24,
        user_agent: str = "AumOS-VendorIntelligence/1.0",
    ) -> None:
        """Initialise the VendorDataEnricher.

        Args:
            fetch_fn: Optional async callable(url: str) -> str for HTTP fetches.
                If None, enrichment methods return structured placeholders.
            cache_ttl_hours: Cache lifetime for fetched content in hours.
            user_agent: User-Agent string for HTTP requests.
        """
        self._fetch_fn = fetch_fn
        self._cache_ttl = cache_ttl_hours
        self._user_agent = user_agent
        self._cache: dict[str, dict[str, Any]] = {}

    async def scrape_api_documentation(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_name: str,
        docs_url: str,
    ) -> dict[str, Any]:
        """Extract API documentation metadata from vendor docs URL.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            vendor_name: Vendor name for logging.
            docs_url: URL of the vendor's API documentation.

        Returns:
            Dict with endpoints_detected, authentication_methods, sdk_languages,
            rate_limit_documented, versioning_documented, openapi_spec_available,
            documentation_quality_score, and source_url fields.
        """
        logger.info(
            "Scraping API documentation",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
            docs_url=docs_url,
        )

        content = await self._fetch_url(docs_url)

        content_lower = content.lower() if content else ""

        authentication_methods: list[str] = []
        for auth in ["api key", "oauth", "jwt", "bearer token", "basic auth", "mtls"]:
            if auth in content_lower:
                authentication_methods.append(auth.replace(" ", "_"))

        sdk_languages: list[str] = []
        for lang in ["python", "javascript", "typescript", "go", "java", "rust", "ruby", "curl"]:
            if lang in content_lower:
                sdk_languages.append(lang)

        openapi_available = any(
            kw in content_lower for kw in ["openapi", "swagger", "api spec", "openapi.json"]
        )
        rate_limit_documented = any(
            kw in content_lower for kw in ["rate limit", "rate-limit", "ratelimit", "requests per"]
        )
        versioning_documented = any(
            kw in content_lower for kw in ["api version", "versioning", "v1/", "v2/", "deprecat"]
        )

        # Estimate endpoint count from common patterns in docs
        import re
        endpoint_patterns = re.findall(r"(GET|POST|PUT|DELETE|PATCH)\s+/\S+", content)
        endpoints_detected = len(set(endpoint_patterns))

        quality_score = self._compute_doc_quality_score(
            has_openapi=openapi_available,
            auth_method_count=len(authentication_methods),
            sdk_count=len(sdk_languages),
            rate_limit_documented=rate_limit_documented,
            versioning_documented=versioning_documented,
            endpoint_count=endpoints_detected,
        )

        result: dict[str, Any] = {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "source_url": docs_url,
            "endpoints_detected": endpoints_detected,
            "authentication_methods": authentication_methods,
            "sdk_languages": sdk_languages,
            "rate_limit_documented": rate_limit_documented,
            "versioning_documented": versioning_documented,
            "openapi_spec_available": openapi_available,
            "documentation_quality_score": quality_score,
            "enriched_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "API documentation scraped",
            vendor_id=vendor_id,
            endpoints_detected=endpoints_detected,
            quality_score=quality_score,
        )

        return result

    async def extract_pricing_page_data(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_name: str,
        pricing_url: str,
    ) -> dict[str, Any]:
        """Extract pricing data from vendor pricing page.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            vendor_name: Vendor name.
            pricing_url: URL of the vendor pricing page.

        Returns:
            Dict with pricing_model, has_free_tier, has_volume_pricing,
            has_enterprise_pricing, currency, pricing_transparency_score,
            detected_tiers, and source_url fields.
        """
        logger.info(
            "Extracting pricing page data",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
            pricing_url=pricing_url,
        )

        content = await self._fetch_url(pricing_url)
        content_lower = content.lower() if content else ""

        pricing_model = "unknown"
        if "per token" in content_lower or "per million" in content_lower:
            pricing_model = "usage_based"
        elif "per month" in content_lower or "monthly" in content_lower:
            pricing_model = "subscription"
        elif "per seat" in content_lower or "per user" in content_lower:
            pricing_model = "per_seat"

        has_free_tier = any(kw in content_lower for kw in ["free tier", "free plan", "no cost", "free forever"])
        has_volume = any(kw in content_lower for kw in ["volume discount", "volume pricing", "bulk"])
        has_enterprise = any(kw in content_lower for kw in ["enterprise", "custom pricing", "contact us"])

        import re
        # Detect USD pricing mentions
        price_mentions = re.findall(r"\$[\d,]+(?:\.\d+)?", content)
        detected_prices = list(set(price_mentions))[:10]

        transparency_score = self._compute_pricing_transparency(
            has_published_prices=len(detected_prices) > 0,
            has_free_tier=has_free_tier,
            has_volume_pricing=has_volume,
            pricing_model_identified=pricing_model != "unknown",
        )

        result: dict[str, Any] = {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "source_url": pricing_url,
            "pricing_model": pricing_model,
            "has_free_tier": has_free_tier,
            "has_volume_pricing": has_volume,
            "has_enterprise_pricing": has_enterprise,
            "currency": "USD",
            "detected_price_points": detected_prices,
            "pricing_transparency_score": transparency_score,
            "enriched_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Pricing page data extracted",
            vendor_id=vendor_id,
            pricing_model=pricing_model,
            transparency_score=transparency_score,
        )

        return result

    async def compile_feature_matrix(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_name: str,
        features_url: str,
        required_features: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compile a vendor feature matrix from documentation or features page.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            vendor_name: Vendor name.
            features_url: URL of features or product page.
            required_features: Optional list of features to check for.

        Returns:
            Dict with detected_features, required_features_present,
            required_features_missing, feature_coverage_percent, and feature_count.
        """
        logger.info(
            "Compiling feature matrix",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
        )

        content = await self._fetch_url(features_url)
        content_lower = content.lower() if content else ""

        standard_features = [
            "streaming", "function calling", "tool use", "embeddings",
            "fine-tuning", "batch api", "image input", "vision",
            "code generation", "multilingual", "json mode", "structured output",
            "system prompts", "long context", "retrieval augmented generation",
        ]

        detected_features: list[str] = [
            feature for feature in standard_features
            if feature in content_lower
        ]

        required_present: list[str] = []
        required_missing: list[str] = []
        if required_features:
            for feature in required_features:
                if feature.lower() in content_lower:
                    required_present.append(feature)
                else:
                    required_missing.append(feature)

        coverage_percent = (
            round(len(required_present) / len(required_features) * 100.0, 1)
            if required_features else 100.0
        )

        result: dict[str, Any] = {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "source_url": features_url,
            "detected_features": detected_features,
            "feature_count": len(detected_features),
            "required_features_present": required_present,
            "required_features_missing": required_missing,
            "required_feature_coverage_percent": coverage_percent,
            "enriched_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Feature matrix compiled",
            vendor_id=vendor_id,
            feature_count=len(detected_features),
            required_coverage_percent=coverage_percent,
        )

        return result

    async def track_compliance_certifications(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_name: str,
        trust_page_url: str,
    ) -> dict[str, Any]:
        """Track vendor compliance certifications from trust/security page.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            vendor_name: Vendor name.
            trust_page_url: URL of vendor trust/security/compliance page.

        Returns:
            Dict with confirmed_certifications, pending_certifications,
            certification_coverage_score, soc2_type2_confirmed,
            iso27001_confirmed, gdpr_confirmed, and last_audit_year fields.
        """
        logger.info(
            "Tracking compliance certifications",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
        )

        content = await self._fetch_url(trust_page_url)
        content_lower = content.lower() if content else ""

        confirmed: list[str] = []
        pending: list[str] = []

        for cert in COMPLIANCE_CERTIFICATIONS:
            cert_display = cert.replace("_", " ")
            cert_variants = [cert, cert_display, cert.replace("_", "-").upper(), cert.upper()]

            found = any(v.lower() in content_lower for v in cert_variants)
            if found:
                if any(kw in content_lower for kw in ["in progress", "pending", "upcoming"]):
                    pending.append(cert)
                else:
                    confirmed.append(cert)

        import re
        audit_years: list[int] = []
        year_matches = re.findall(r"20[2-3]\d", content)
        for year_str in year_matches:
            year = int(year_str)
            if 2020 <= year <= 2030:
                audit_years.append(year)

        last_audit_year = max(audit_years) if audit_years else None

        coverage_score = min(1.0, len(confirmed) / max(len(COMPLIANCE_CERTIFICATIONS) / 2, 1))

        result: dict[str, Any] = {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "source_url": trust_page_url,
            "confirmed_certifications": confirmed,
            "pending_certifications": pending,
            "certification_count": len(confirmed),
            "certification_coverage_score": round(coverage_score, 4),
            "soc2_type2_confirmed": "soc2_type2" in confirmed,
            "iso27001_confirmed": "iso27001" in confirmed,
            "gdpr_confirmed": "gdpr" in confirmed,
            "hipaa_confirmed": "hipaa" in confirmed,
            "last_audit_year": last_audit_year,
            "enriched_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Compliance certifications tracked",
            vendor_id=vendor_id,
            confirmed_count=len(confirmed),
            coverage_score=coverage_score,
        )

        return result

    async def monitor_news_and_updates(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_name: str,
        news_sources: list[str],
    ) -> list[dict[str, Any]]:
        """Monitor vendor news and product updates from public sources.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            vendor_name: Vendor name.
            news_sources: List of URLs to check for vendor news.

        Returns:
            List of news item dicts with title, summary, sentiment,
            relevance_category, and source_url fields.
        """
        logger.info(
            "Monitoring vendor news",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
            source_count=len(news_sources),
        )

        news_items: list[dict[str, Any]] = []

        positive_signals = ["launch", "partnership", "expansion", "funding", "award", "certified"]
        negative_signals = ["breach", "outage", "vulnerability", "lawsuit", "fine", "penalty"]
        product_signals = ["new feature", "api update", "model release", "deprecat", "end of life"]

        for source_url in news_sources:
            content = await self._fetch_url(source_url)
            if not content:
                continue

            content_lower = content.lower()
            vendor_lower = vendor_name.lower()

            if vendor_lower not in content_lower:
                continue

            import re
            headlines = re.findall(r"<h[1-3][^>]*>([^<]+)</h[1-3]>", content, re.IGNORECASE)
            for headline in headlines[:5]:
                headline_lower = headline.lower()
                if vendor_lower not in headline_lower:
                    continue

                sentiment = "neutral"
                if any(s in headline_lower for s in positive_signals):
                    sentiment = "positive"
                elif any(s in headline_lower for s in negative_signals):
                    sentiment = "negative"

                category = "general"
                if any(s in headline_lower for s in product_signals):
                    category = "product_update"
                elif any(s in headline_lower for s in negative_signals):
                    category = "risk_signal"
                elif any(s in headline_lower for s in positive_signals):
                    category = "growth_signal"

                news_items.append({
                    "vendor_id": vendor_id,
                    "title": headline.strip(),
                    "summary": headline.strip(),
                    "sentiment": sentiment,
                    "relevance_category": category,
                    "source_url": source_url,
                    "detected_at": datetime.now(tz=timezone.utc).isoformat(),
                })

        logger.info(
            "Vendor news monitoring completed",
            vendor_id=vendor_id,
            news_item_count=len(news_items),
        )

        return news_items

    def score_profile_completeness(
        self,
        tenant_id: uuid.UUID,
        vendor_id: str,
        vendor_profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Score the completeness of a vendor intelligence profile.

        Args:
            tenant_id: Requesting tenant UUID.
            vendor_id: Vendor identifier.
            vendor_profile: Dict containing all collected vendor data sections.

        Returns:
            Dict with overall_completeness_score, dimension_scores,
            missing_sections, and readiness_level fields.
        """
        logger.info(
            "Scoring profile completeness",
            tenant_id=str(tenant_id),
            vendor_id=vendor_id,
        )

        dimension_present: dict[str, bool] = {
            "api_documentation": bool(vendor_profile.get("api_documentation")),
            "pricing_data": bool(vendor_profile.get("pricing_data")),
            "feature_matrix": bool(vendor_profile.get("feature_matrix")),
            "compliance_certifications": bool(vendor_profile.get("compliance_certifications")),
            "contact_information": bool(vendor_profile.get("contact_info")),
            "sla_documentation": bool(vendor_profile.get("sla_terms")),
            "support_documentation": bool(vendor_profile.get("support_info")),
        }

        dimension_scores: dict[str, float] = {}
        for dimension, present in dimension_present.items():
            if not present:
                dimension_scores[dimension] = 0.0
                continue

            section_data = vendor_profile.get(dimension, {}) or {}
            fill_rate = sum(
                1 for v in section_data.values()
                if v is not None and v != [] and v != {}
            ) / max(len(section_data), 1) if isinstance(section_data, dict) else 0.5
            dimension_scores[dimension] = round(fill_rate, 4)

        overall_score = sum(
            score * COMPLETENESS_WEIGHTS.get(dim, 0.0)
            for dim, score in dimension_scores.items()
        )

        missing_sections = [
            dim for dim, present in dimension_present.items() if not present
        ]

        readiness = (
            "procurement_ready" if overall_score >= 0.80
            else "partially_ready" if overall_score >= 0.50
            else "insufficient"
        )

        result: dict[str, Any] = {
            "vendor_id": vendor_id,
            "overall_completeness_score": round(overall_score, 4),
            "dimension_scores": dimension_scores,
            "missing_sections": missing_sections,
            "readiness_level": readiness,
            "scored_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "Profile completeness scored",
            vendor_id=vendor_id,
            overall_score=overall_score,
            readiness=readiness,
        )

        return result

    async def _fetch_url(self, url: str) -> str:
        """Fetch URL content using the injected fetch function.

        Args:
            url: URL to fetch.

        Returns:
            Response content string, or empty string on failure.
        """
        if url in self._cache:
            cache_entry = self._cache[url]
            cached_at = datetime.fromisoformat(cache_entry["cached_at"])
            age_hours = (datetime.now(tz=timezone.utc) - cached_at).total_seconds() / 3600
            if age_hours < self._cache_ttl:
                return cache_entry["content"]

        if self._fetch_fn is None:
            # No fetch function configured — return empty content
            return ""

        try:
            content = await self._fetch_fn(url)
            self._cache[url] = {
                "content": content,
                "cached_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            return content
        except Exception as exc:
            logger.warning(
                "URL fetch failed",
                url=url,
                error=str(exc),
            )
            return ""

    @staticmethod
    def _compute_doc_quality_score(
        has_openapi: bool,
        auth_method_count: int,
        sdk_count: int,
        rate_limit_documented: bool,
        versioning_documented: bool,
        endpoint_count: int,
    ) -> float:
        """Compute API documentation quality score.

        Args:
            has_openapi: True if OpenAPI spec is available.
            auth_method_count: Number of authentication methods documented.
            sdk_count: Number of SDK languages provided.
            rate_limit_documented: True if rate limits are documented.
            versioning_documented: True if API versioning is documented.
            endpoint_count: Number of detected API endpoints.

        Returns:
            Quality score between 0.0 and 1.0.
        """
        score = 0.0
        score += 0.25 if has_openapi else 0.0
        score += min(auth_method_count * 0.05, 0.15)
        score += min(sdk_count * 0.03, 0.15)
        score += 0.10 if rate_limit_documented else 0.0
        score += 0.10 if versioning_documented else 0.0
        score += min(endpoint_count * 0.01, 0.25)
        return round(min(score, 1.0), 4)

    @staticmethod
    def _compute_pricing_transparency(
        has_published_prices: bool,
        has_free_tier: bool,
        has_volume_pricing: bool,
        pricing_model_identified: bool,
    ) -> float:
        """Compute pricing transparency score.

        Args:
            has_published_prices: True if prices are publicly listed.
            has_free_tier: True if a free tier is offered.
            has_volume_pricing: True if volume discounts are documented.
            pricing_model_identified: True if pricing model is clear.

        Returns:
            Transparency score between 0.0 and 1.0.
        """
        score = 0.0
        score += 0.50 if has_published_prices else 0.0
        score += 0.15 if has_free_tier else 0.0
        score += 0.15 if has_volume_pricing else 0.0
        score += 0.20 if pricing_model_identified else 0.0
        return round(min(score, 1.0), 4)
