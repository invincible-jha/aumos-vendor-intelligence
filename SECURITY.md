# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

Report security vulnerabilities to security@aumos.ai. Do not open public GitHub issues for security vulnerabilities.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if available)

We aim to acknowledge reports within 48 hours and provide a fix within 14 days for critical issues.

## Security considerations

- All DB queries are parameterised — never use string concatenation in SQL
- Tenant isolation via RLS (`SET app.current_tenant`) on every session
- Contract and vendor data may be commercially sensitive — treat as confidential
- The 88% cap liability detection is a compliance-critical feature — changes require security review
- Insurance gap data includes financial amounts that must not be exposed cross-tenant
