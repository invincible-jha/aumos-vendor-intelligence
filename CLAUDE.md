# aumos-vendor-intelligence — CLAUDE.md

## Service Purpose
AI vendor evaluation, lock-in risk assessment, contract risk analysis with the 88% cap liability policy, and insurance gap detection for enterprise AI procurement decisions.

## Package & Config
- Python package: `aumos_vendor_intelligence`
- DB table prefix: `vin_`
- Env prefix: `AUMOS_VENDOR_`
- Port: `8000`
- OTEL service name: `aumos-vendor-intelligence`

## Architecture
Hexagonal architecture with three layers:
- `api/` — FastAPI router + Pydantic schemas (no business logic)
- `core/` — Domain models, services, interfaces (no framework code)
- `adapters/` — SQLAlchemy repositories + Kafka publisher

## Domain Model
| Table | Purpose |
|-------|---------|
| `vin_vendors` | Vendor profiles with API compatibility and data portability metadata |
| `vin_evaluations` | Multi-criteria evaluation scores (5 weighted criteria) |
| `vin_lock_in_assessments` | Lock-in risk analysis across 5 dimensions |
| `vin_contracts` | Contract metadata + risk analysis with liability cap flag |
| `vin_insurance_gaps` | Insurance coverage deficiencies |

## Key Business Rules
1. **Vendor evaluation** — 5 weighted criteria: api_compatibility (0.25), data_portability (0.25), security_posture (0.20), pricing_transparency (0.15), support_quality (0.15). Higher score = lower risk.
2. **Lock-in risk** — Equal-weighted across 5 dimensions. score >= 0.70 = high, >= 0.40 = medium.
3. **88% cap liability policy** — Contracts capping vendor liability at <= 1 month of fees (cap fraction >= 0.88 or cap_months <= 1.0) receive a `has_liability_cap_warning=True` flag and auto-inserted high-severity risk entry.
4. **Insurance gaps** — Required coverage types: `cyber_liability`, `errors_and_omissions`, `technology_professional_liability`. Minimum $5M per incident.

## API Surface
```
POST   /api/v1/vendors                          # Register vendor
GET    /api/v1/vendors                          # List vendors (paginated)
GET    /api/v1/vendors/compare                  # Side-by-side comparison
GET    /api/v1/vendors/{id}                     # Vendor detail + score
POST   /api/v1/vendors/{id}/evaluate            # Run evaluation
GET    /api/v1/vendors/{id}/lock-in             # Get current lock-in assessment
POST   /api/v1/vendors/{id}/lock-in             # Run lock-in assessment
POST   /api/v1/contracts/analyze                # Analyse contract risk
GET    /api/v1/contracts/{id}/risks             # Contract risk report
POST   /api/v1/insurance/check                  # Check insurance coverage gaps
GET    /api/v1/vendors/{id}/insurance           # List vendor insurance gaps
PATCH  /api/v1/insurance/gaps/{id}              # Update gap status
```

## Services
| Service | Responsibility |
|---------|---------------|
| `VendorScorerService` | Register vendors, run evaluations, compare vendors |
| `LockInAssessorService` | Assess and retrieve lock-in risk |
| `ContractAnalyzerService` | Submit and analyse contract risk |
| `InsuranceCheckerService` | Check coverage gaps, update gap status |

## Conventions
- All scores are 0.0–1.0 floats; validated at service layer
- Evaluations and assessments have `is_current` flag; only the latest is `True`
- Insurance gaps are unique per (vendor, coverage_type, tenant)
- Kafka topics: `vendor.registered`, `vendor.evaluated`, `vendor.lock_in_assessed`, `contract.analyzed`, `insurance.gap_detected`, `insurance.gap_updated`
- RLS tenant isolation via `SET app.current_tenant` on every DB session

## Running Locally
```bash
cp .env.example .env
make install
make migrate
make dev
```
