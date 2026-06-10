#!/usr/bin/env bash
# scripts/security_audit.sh
#
# Dependency vulnerability audit for OPNGMS.
# Runs:
#   1. pip-audit against the backend Python dependencies
#      (ignores PYSEC-2026-196 — pip self-upgrade finding; not a runtime dep;
#       closed in the Dockerfile by upgrading pip before installing the app)
#   2. npm audit --omit=dev against the frontend production dependencies
#
# Exits non-zero if any real app-dependency vulnerability is found.
# Run locally: bash scripts/security_audit.sh
# Run in CI:   called by the "audit" job in .github/workflows/ci.yml
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── helpers ──────────────────────────────────────────────────────────────────
header() { echo; echo "╔══════════════════════════════════════════════════════════════╗"; printf  "║  %-60s║\n" "$1"; echo "╚══════════════════════════════════════════════════════════════╝"; }
ok()     { echo "[OK]  $*"; }
fail()   { echo "[FAIL] $*" >&2; }

# ── 1. Backend — pip-audit ────────────────────────────────────────────────────
header "Backend — pip-audit"

cd "${REPO_ROOT}/backend"

# Resolve pip-audit: prefer the venv binary, then PATH, then install into the
# active Python environment.
PIP_AUDIT_BIN=""
if [ -x ".venv/bin/pip-audit" ]; then
    PIP_AUDIT_BIN=".venv/bin/pip-audit"
elif command -v pip-audit &>/dev/null; then
    PIP_AUDIT_BIN="pip-audit"
else
    echo "pip-audit not found — installing via pip..."
    python3 -m pip install -q pip-audit
    PIP_AUDIT_BIN="pip-audit"
fi

# --skip-editable: don't re-audit the project package itself (opngms-backend
#   is an editable install; its deps are already audited transitively).
# --ignore-vuln PYSEC-2026-196: the pip self-upgrade advisory (pip 26.1.1 →
#   26.1.2); this is not a runtime dependency — the Dockerfile already upgrades
#   pip before installing the application.
"${PIP_AUDIT_BIN}" \
    --skip-editable \
    --ignore-vuln PYSEC-2026-196

ok "Backend audit passed — no app-dependency vulnerabilities."

# ── 2. Frontend — npm audit ───────────────────────────────────────────────────
header "Frontend — npm audit (production deps)"

cd "${REPO_ROOT}/frontend"

# --omit=dev: only audit production (bundled) dependencies.
# npm audit exits 1 when vulnerabilities are found.
npm audit --omit=dev

ok "Frontend audit passed — no production dependency vulnerabilities."

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════"
echo "  Security audit: OK"
echo "════════════════════════════════════════════"
