# Security Policy

OPNGMS (OPNsense Global Management System) is a multi-tenant console that manages
and monitors fleets of OPNsense firewalls. Because it holds device credentials and
operates across tenant boundaries, security reports are taken seriously.

## Supported versions

OPNGMS is under active development. Security fixes are applied to the `main` branch;
run the latest `main` (or the latest tagged release, once releases are published).

| Version | Supported |
| ------- | --------- |
| `main` (latest) | ✅ |
| older commits | ❌ |

## Reporting a vulnerability

**Please report security issues privately. Do not open a public issue, PR, or
discussion for anything security-sensitive.**

Preferred channel — GitHub private vulnerability reporting:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability** ("Privately report a security vulnerability").
3. This opens a private advisory visible only to the maintainers.

(If private reporting is unavailable to you, open a minimal public issue that says
only "security report — please enable a private channel" with **no** technical
detail, and a maintainer will follow up.)

Please include, where possible:

- the affected component or endpoint (backend API, worker, frontend, deployment);
- the version/commit you tested;
- reproduction steps or a proof of concept;
- the impact (what an attacker gains);
- any suggested remediation.

### What to expect

- **Acknowledgement** within 5 business days.
- **Triage + severity assessment** and a remediation timeline after acknowledgement.
- **Coordinated disclosure:** we ask for reasonable time to ship a fix before any
  public disclosure. We will credit you in the advisory unless you prefer to remain
  anonymous.

## Scope

**In scope**

- The OPNGMS backend API and the ARQ worker (`backend/`).
- The frontend SPA (`frontend/`).
- Deployment artifacts in this repository (Dockerfiles, `docker-compose.prod.yml`,
  nginx config).
- Multi-tenant isolation, authentication/session handling, CSRF, SSRF, secret
  handling, and access control.

**Out of scope**

- Vulnerabilities in third-party dependencies that are already tracked by our
  automated tooling, unless they are exploitable in OPNGMS in a default
  configuration.
- Findings that require privileged local access to a deployment you already
  control, or that depend on a misconfiguration explicitly warned against in the
  documentation.
- Social engineering, physical attacks, and denial of service through brute
  resource exhaustion.

## Security posture (defence in depth)

OPNGMS ships with, among others:

- **Tenant isolation** via PostgreSQL Row-Level Security (ENABLE + FORCE,
  fail-closed); the application runs as a non-superuser role.
- **Authentication**: Argon2 password hashing; opaque session tokens stored only as
  a SHA-256 hash at rest; idle + absolute session expiry; session rotation on login;
  "log out everywhere".
- **CSRF**: per-session token validated with a constant-time comparison.
- **SSRF guard** on the OPNsense connector; optional **TLS certificate fingerprint
  pinning**.
- **Secret handling**: device credentials encrypted with Fernet; secret redaction in
  logs/audit.
- **Transport/app hardening**: security response headers (CSP, HSTS, X-Frame-Options
  DENY, nosniff, Referrer-Policy, Permissions-Policy); CORS closed by default; login
  rate limiting that fails closed.
- **Hardened XML parsing** (defusedxml) and an allowlist-based query layer.

### Continuous assurance (CI)

Every change to `main` must pass: an application-security test suite, CodeQL static
analysis (Python + TypeScript), Trivy container-image scanning, a dependency audit
(`pip-audit` + `npm audit`), Dependabot dependency review, and gitleaks secret
scanning. `main` is protected and requires these checks to be green before merge.

Thank you for helping keep OPNGMS and its users safe.
