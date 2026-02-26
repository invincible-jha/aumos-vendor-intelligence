# Changelog

All notable changes to `aumos-vendor-intelligence` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-02-26

### Added
- Initial service implementation with hexagonal architecture
- `VendorScorerService` — multi-criteria vendor evaluation with 5 weighted dimensions
- `LockInAssessorService` — vendor lock-in risk assessment across 5 dimensions
- `ContractAnalyzerService` — contract risk analysis with AumOS 88% cap liability policy
- `InsuranceCheckerService` — insurance coverage gap detection and tracking
- DB models: `vin_vendors`, `vin_evaluations`, `vin_lock_in_assessments`, `vin_contracts`, `vin_insurance_gaps`
- REST API with 12 endpoints across vendors, evaluations, lock-in, contracts, and insurance
- Kafka event publishing for all domain events
- Side-by-side vendor comparison endpoint
- Configurable scoring weights and risk thresholds via `AUMOS_VENDOR_` env vars
- Docker and docker-compose.dev.yml for local development
- CI pipeline via GitHub Actions
