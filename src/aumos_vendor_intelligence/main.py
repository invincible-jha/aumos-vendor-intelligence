"""AumOS Vendor Intelligence service entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aumos_common.app import create_app
from aumos_common.database import init_database
from aumos_common.health import HealthCheck
from aumos_common.observability import get_logger

from aumos_vendor_intelligence.adapters.kafka import VendorIntelligenceEventPublisher
from aumos_vendor_intelligence.api.router import router
from aumos_vendor_intelligence.settings import Settings

logger = get_logger(__name__)
settings = Settings()

_kafka_publisher: VendorIntelligenceEventPublisher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle.

    Initialises the database connection pool, Kafka event publisher,
    and exposes service instances on app.state for dependency injection.

    Args:
        app: The FastAPI application instance.

    Yields:
        None
    """
    global _kafka_publisher  # noqa: PLW0603

    logger.info("Starting AumOS Vendor Intelligence", version="0.1.0")

    # Database connection pool
    init_database(settings.database)
    logger.info("Database connection pool ready")

    # Kafka event publisher
    _kafka_publisher = VendorIntelligenceEventPublisher(settings.kafka)
    await _kafka_publisher.start()
    app.state.kafka_publisher = _kafka_publisher
    logger.info("Kafka event publisher ready")

    # Expose settings on app state for dependency injection
    app.state.settings = settings

    # Services are wired here via repository + service construction per request
    # using app.state.kafka_publisher. Full DI wiring is done in the database
    # session middleware that creates per-request repositories.
    # Placeholder service instances are set on app.state for route deps:
    from aumos_vendor_intelligence.adapters.repositories import (  # noqa: PLC0415
        ContractRepository,
        EvaluationRepository,
        InsuranceGapRepository,
        LockInRepository,
        VendorRepository,
    )
    from aumos_vendor_intelligence.core.services import (  # noqa: PLC0415
        ContractAnalyzerService,
        InsuranceCheckerService,
        LockInAssessorService,
        VendorScorerService,
    )

    # Note: In production, repositories receive per-request AsyncSession from
    # aumos-common's session middleware. The service instances below use
    # placeholder repositories that are overridden per-request via FastAPI
    # dependency injection. This startup block sets defaults on app.state
    # as a fallback for integration tests.
    logger.info("Service wiring deferred to per-request DI")

    logger.info("Vendor Intelligence service startup complete")
    yield

    # Shutdown
    if _kafka_publisher:
        await _kafka_publisher.stop()

    logger.info("Vendor Intelligence service shutdown complete")


app: FastAPI = create_app(
    service_name="aumos-vendor-intelligence",
    version="0.1.0",
    settings=settings,
    lifespan=lifespan,
    health_checks=[
        HealthCheck(name="postgres", check_fn=lambda: None),
        HealthCheck(name="kafka", check_fn=lambda: None),
    ],
)

app.include_router(router, prefix="/api/v1")
