# Contributing to aumos-vendor-intelligence

## Development setup

```bash
git clone <repo>
cd aumos-vendor-intelligence
cp .env.example .env
make install
make dev
```

## Code standards

- Python 3.11+, type hints on all function signatures
- `ruff` for linting and formatting (`make lint`)
- `mypy --strict` for type checking (`make typecheck`)
- Tests alongside implementation in `tests/`
- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`

## Pull request process

1. Branch from `main`: `feature/`, `fix/`, or `docs/`
2. Run `make lint && make typecheck && make test` before pushing
3. Squash-merge PRs to keep history clean
4. Commit messages explain WHY, not WHAT

## Business rules (do not bypass)

- The 88% cap liability policy is non-negotiable — never remove the `has_liability_cap_warning` logic
- Insurance gap deduplication: unique constraint on (vendor, coverage_type, tenant) must be maintained
- Evaluation scores must always be validated to [0.0, 1.0] at the service layer
