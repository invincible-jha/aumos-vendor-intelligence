# aumos-vendor-intelligence

AI vendor evaluation, lock-in risk assessment, contract risk analysis, and insurance gap detection for enterprise AI procurement decisions.

## Overview

`aumos-vendor-intelligence` provides procurement teams with structured tooling to assess AI vendors across risk dimensions before engagement and throughout the vendor relationship lifecycle.

### Key capabilities

- **Vendor evaluation** — weighted multi-criteria scoring across API compatibility, data portability, security posture, pricing transparency, and support quality
- **Lock-in risk assessment** — composite score across proprietary formats, switching costs, API openness, data egress, and contractual constraints
- **Contract risk analysis** — automated detection of high-risk clauses with AumOS 88% cap liability policy enforcement
- **Insurance gap detection** — comparison of vendor coverage against minimum required amounts for cyber liability, E&O, and tech professional liability

## AumOS 88% Cap Liability Policy

Contracts that cap vendor liability at approximately 1 month of fees or less (liability cap fraction >= 0.88) are automatically flagged with `has_liability_cap_warning: true` and a high-severity risk entry. This implements AumOS enterprise procurement policy for AI vendor contracts.

## Architecture

Hexagonal architecture with clear boundaries:

```
src/aumos_vendor_intelligence/
├── api/            # FastAPI router + Pydantic schemas
├── core/           # Domain models, services, interfaces
├── adapters/       # SQLAlchemy repositories + Kafka publisher
├── main.py         # FastAPI application entry point
└── settings.py     # Pydantic settings (AUMOS_VENDOR_ prefix)
```

## Quick Start

```bash
cp .env.example .env
make install
make migrate
make dev
# Service available at http://localhost:8000
```

## API

Full OpenAPI docs at `http://localhost:8000/docs` once running.

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/vendors` | Register a vendor |
| `GET /api/v1/vendors` | List vendors |
| `GET /api/v1/vendors/compare?vendor_ids=...` | Side-by-side comparison |
| `GET /api/v1/vendors/{id}` | Vendor detail |
| `POST /api/v1/vendors/{id}/evaluate` | Run evaluation |
| `GET /api/v1/vendors/{id}/lock-in` | Get lock-in assessment |
| `POST /api/v1/vendors/{id}/lock-in` | Run lock-in assessment |
| `POST /api/v1/contracts/analyze` | Analyse contract |
| `GET /api/v1/contracts/{id}/risks` | Contract risk report |
| `POST /api/v1/insurance/check` | Check insurance gaps |
| `GET /api/v1/vendors/{id}/insurance` | List insurance gaps |
| `PATCH /api/v1/insurance/gaps/{id}` | Update gap status |

## Configuration

All settings use the `AUMOS_VENDOR_` prefix. See `.env.example` for the full list.

Key settings:
- `AUMOS_VENDOR_LIABILITY_CAP_WARNING_THRESHOLD` — default `0.88` (1 month cap)
- `AUMOS_VENDOR_MINIMUM_COVERAGE_AMOUNT_USD` — default `5000000` ($5M)
- `AUMOS_VENDOR_LOCK_IN_HIGH_RISK_THRESHOLD` — default `0.70`

## License

Apache 2.0 — see [LICENSE](LICENSE).
