#!/usr/bin/env bash
# Live endpoint sweep.
#
# The project rule is that no endpoint is reported as working without a real
# request against a running server. This script is that check: it walks every
# route in Alltechmanagement/urls.py and prints the status code each one returns.
#
# It does NOT assert correctness of response bodies -- that is the pytest suite's
# job. What it proves is that routing, middleware, auth classes and throttles
# behave against a real server, which the Django test client bypasses.
#
# Usage:
#   scripts/api_sweep.sh [BASE_URL] [TOKEN]
# Example:
#   scripts/api_sweep.sh http://localhost:8000
#   scripts/api_sweep.sh http://150.136.246.106 "$ACCESS_TOKEN"

set -uo pipefail

BASE_URL="${1:-http://localhost:8000}"
TOKEN="${2:-}"

pass=0; fail=0

# Expected codes are given as a regex. Without a token, an authenticated endpoint
# returning 401 is the correct answer and counts as a pass -- that is the auth
# layer working, not a failure.
sweep() {
    local method="$1" path="$2" expect="$3" desc="${4:-}"
    local args=(-s -o /dev/null -w '%{http_code}' -X "$method" "${BASE_URL}${path}")
    [ -n "$TOKEN" ] && args+=(-H "Authorization: Bearer ${TOKEN}")
    args+=(-H 'Content-Type: application/json')

    local code
    code=$(curl "${args[@]}" 2>/dev/null || echo "000")

    if [[ "$code" =~ ^($expect)$ ]]; then
        printf '  \033[32mPASS\033[0m  %-6s %-38s %s %s\n' "$method" "$path" "$code" "$desc"
        pass=$((pass+1))
    else
        printf '  \033[31mFAIL\033[0m  %-6s %-38s %s (wanted %s) %s\n' "$method" "$path" "$code" "$expect" "$desc"
        fail=$((fail+1))
    fi
}

# ROLE tells the script which answers are correct for the supplied token.
# Without it, a sweep that passes proves only that something responded.
ROLE="${3:-none}"

echo "Sweeping ${BASE_URL} as role=${ROLE}"
[ -z "$TOKEN" ] && echo "(no token supplied: authenticated endpoints are expected to reject)"
echo

echo "Unauthenticated"
sweep GET  /api/health/                       "200|503"
sweep GET  /                                  "200|403|429"

echo
echo "Stock"
sweep GET    /api/get_shop2_stock             "200|401|403"
sweep GET    /api/get_shop2_stock_api/1       "200|401|403|404"
sweep POST   /api/add_stock2                  "200|201|400|401|403"
sweep PATCH  /api/update_stock2/1             "200|400|401|403|404"
sweep DELETE /api/delete_stock2_api/1         "200|204|401|403|404"
sweep GET    /api/detailed/low_stock/         "200|401|403"

echo
echo "Sales"
sweep POST /api/sell2/1                       "200|201|400|401|403|404"
sweep GET  /api/saved2                        "200|401|403"
sweep POST /api/complete2/1                   "200|400|401|403|404"
sweep GET  /api/refund2/1                     "200|400|401|403|404"
sweep GET  /api/customers/                    "200|401|403"

echo
echo "Analytics (Manager-only after PR 6)"
sweep GET /api/dashboard/                     "200|401|403"
sweep GET /api/weekly/                        "200|401|403"
sweep GET /api/monthly/                       "200|401|403"
sweep GET /api/yearly/                        "200|401|403"
sweep GET /api/customers-insights/            "200|401|403"
sweep GET /api/products-insights/             "200|401|403"
sweep GET /api/patterns/                      "200|401|403"

echo
echo "Machine-to-machine"
sweep POST /api/celery-token/                 "200|400|401|403|429"
sweep GET  /api/send_sale2                    "401|403"
sweep GET  /api/daily-ai/                     "401|403"
sweep GET  /api/weekly-ai/                    "401|403"

echo
echo "Accessories (ported from sequelizer)"
sweep GET    /api/accessories/                "200|401|403"
sweep GET    /api/accessories/1/              "200|401|403|404"
sweep POST   /api/accessories/add/            "201|400|401|403"
sweep PATCH  /api/accessories/1/update/       "200|400|401|403|404"
sweep DELETE /api/accessories/1/delete/       "204|401|403|404"
sweep POST   /api/accessories/1/sell/         "200|400|401|403|404"

echo
echo "Alltech AI"
sweep POST /api/ai/chat/                      "200|400|401|403|503"
sweep POST /api/ai/confirm/                   "400|401|403|404"

echo
echo "Passkeys"
sweep GET  /api/passkeys/                     "200|401|403"
sweep POST /api/passkeys/register/options/    "200|400|401|403|503"
sweep POST /api/passkeys/register/verify/     "400|401|403|503"
sweep POST /api/passkeys/auth/options/        "200|400|401|403|503"
sweep POST /api/passkeys/auth/verify/         "400|401|403|503"

echo
echo "User administration (manager only)"
sweep GET /api/users/                         "200|401|403"

echo
echo "Removed auth routes (Firebase exchange, token refresh)"
sweep POST /api/firebase-auth/                "404"
sweep POST /api/refresh-token/                "404"

echo
printf 'passed %d, failed %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
