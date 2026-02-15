#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/4] Django system check"
python3 manage.py check

echo "[2/4] Targeted RFQ tests"
python3 manage.py test rfq.tests.test_api_smoke rfq.tests.test_auth_guards rfq.tests.test_supplier_flow -v 1

echo "[3/4] Security sanity (production mode auth guard)"
DJANGO_DEBUG=0 python3 manage.py test rfq.tests.test_api_smoke.ApiSmokeTests.test_projects_requires_auth_in_production_mode -v 1

echo "[4/4] Done ✅ preflight passed"
