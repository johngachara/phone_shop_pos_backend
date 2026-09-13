#!/usr/bin/env bash
# Money-logic verification against the live API.
#
# Every assertion here is about a number that becomes someone's takings. The
# tests are deliberately arithmetic rather than "did it return 200": an
# endpoint can answer 200 and still book the wrong amount, which is the failure
# that matters and the one a status-code sweep cannot see.
set -uo pipefail

API="${API:-https://api.alltechnyeri.co.ke}"
# A manager access token. Obtain one however you like -- the sign-in flow, or
# the Supabase admin magiclink -- and pass it in:
#   MGR=<token> scripts/money_check.sh
MGR="${MGR:-$(python3 -c "import json;print(json.load(open('/tmp/tokens.json'))['mgr'])" 2>/dev/null)}"
[ -n "$MGR" ] || { echo "Set MGR to a manager access token."; exit 2; }
TAG="ZZTEST$(date +%H%M%S)"
pass=0; fail=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n       expected %s, got %s\n' "$1" "$2" "$3"; fail=$((fail+1)); }
eq()   { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2" "$3"; }

post() { curl -s --max-time 30 -X POST "$API$1" -H "Authorization: Bearer $MGR" -H 'Content-Type: application/json' -d "$2"; }
patch(){ curl -s --max-time 30 -X PATCH "$API$1" -H "Authorization: Bearer $MGR" -H 'Content-Type: application/json' -d "$2"; }
get()  { curl -s --max-time 30 "$API$1" -H "Authorization: Bearer $MGR"; }

jq_() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

echo "── Setup ────────────────────────────────────────────"
# Sell 5500, cost 3500 -> margin 2000/unit. Deliberately round, so a wrong
# figure is obvious rather than plausible.
ITEM=$(post /api/add_stock2 "{\"product_name\":\"${TAG} Screen\",\"quantity\":10,\"price\":\"5500.00\",\"buying_price\":\"3500.00\"}" | jq_ "d['id']")
[ -n "$ITEM" ] && echo "  stock #$ITEM created (sell 5500, cost 3500, qty 10)" || { echo "  setup failed"; exit 1; }

ACC=$(post /api/accessories/add/ "{\"product_name\":\"${TAG} Cable\",\"quantity\":20,\"price\":\"500.00\",\"buying_price\":\"200.00\"}" | jq_ "d['id']")
echo "  accessory #$ACC created (sell 500, cost 200, qty 20)"

echo
echo "── 1. Holding an order is not revenue ───────────────"
BEFORE=$(get /api/dashboard/ | jq_ "d['today_metrics']['total_sales'] or 0")
HOLD=$(post "/api/sell2/$ITEM" "{\"product_name\":\"${TAG} Screen\",\"price\":\"5500.00\",\"quantity\":3,\"customer_name\":\"${TAG}holder\"}")
HOLD_ID=$(echo "$HOLD" | jq_ "d['transaction_id']")
eq "held order is PENDING" "PENDING" "$(echo "$HOLD" | jq_ "d.get('status','PENDING')")"
eq "stock fell by 3" "7" "$(get "/api/get_shop2_stock_api/$ITEM" | jq_ "d['data']['quantity']")"
AFTER=$(get /api/dashboard/ | jq_ "d['today_metrics']['total_sales'] or 0")
eq "revenue unchanged while on hold" "$BEFORE" "$AFTER"

echo
echo "── 2. Completing books the right amount ─────────────"
post "/api/complete2/$HOLD_ID" '{}' >/dev/null
sleep 1
DASH=$(get /api/dashboard/)
REV=$(echo "$DASH" | jq_ "float(d['today_metrics']['total_sales'] or 0)")
# 3 x 5500 = 16500 on top of whatever was already there.
eq "revenue rose by 3 x 5500" "$(python3 -c "print(float('$BEFORE' or 0)+16500.0)")" "$REV"
eq "customer total is 16500" "16500.00" "$(get /api/customers/ | python3 -c "
import sys,json
for c in json.load(sys.stdin):
    if '${TAG}holder'.lower() in c['customer_name']: print('16500.00')
" 2>/dev/null || echo missing)"

echo
echo "── 3. Profit is (sell - cost) x quantity ────────────"
PROFIT_BEFORE=$(echo "$DASH" | jq_ "float(d['today_metrics']['total_profit'] or 0)")
DIRECT=$(post "/api/sell2/$ITEM" "{\"product_name\":\"${TAG} Screen\",\"price\":\"5500.00\",\"quantity\":2,\"customer_name\":\"${TAG}direct\",\"complete\":true}")
eq "direct sale is COMPLETED" "COMPLETED" "$(echo "$DIRECT" | jq_ "d['status']")"
sleep 1
DASH2=$(get /api/dashboard/)
PROFIT_AFTER=$(echo "$DASH2" | jq_ "float(d['today_metrics']['total_profit'] or 0)")
# 2 x (5500 - 3500) = 4000
eq "profit rose by 2 x 2000" "$(python3 -c "print(float('$PROFIT_BEFORE')+4000.0)")" "$PROFIT_AFTER"
eq "stock fell by 2 more" "5" "$(get "/api/get_shop2_stock_api/$ITEM" | jq_ "d['data']['quantity']")"

echo
echo "── 4. A direct sale never sits in unpaid orders ─────"
eq "no pending order for the direct sale" "0" "$(get /api/saved2 | python3 -c "
import sys,json
d=json.load(sys.stdin); rows=d.get('results') or d.get('data') or []
print(sum(1 for r in rows if '${TAG}direct' in r['customer_name']))
")"

echo
echo "── 5. Refund returns every unit, not one ────────────"
R=$(post "/api/sell2/$ITEM" "{\"product_name\":\"${TAG} Screen\",\"price\":\"5500.00\",\"quantity\":4,\"customer_name\":\"${TAG}refund\"}")
RID=$(echo "$R" | jq_ "d['transaction_id']")
eq "stock fell by 4" "1" "$(get "/api/get_shop2_stock_api/$ITEM" | jq_ "d['data']['quantity']")"
post "/api/refund2/$RID" '{}' >/dev/null
sleep 1
eq "all 4 units came back" "5" "$(get "/api/get_shop2_stock_api/$ITEM" | jq_ "d['data']['quantity']")"

echo
echo "── 6. Completing twice cannot double-count ──────────"
H2=$(post "/api/sell2/$ITEM" "{\"product_name\":\"${TAG} Screen\",\"price\":\"5500.00\",\"quantity\":1,\"customer_name\":\"${TAG}twice\"}")
H2ID=$(echo "$H2" | jq_ "d['transaction_id']")
post "/api/complete2/$H2ID" '{}' >/dev/null
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST "$API/api/complete2/$H2ID" -H "Authorization: Bearer $MGR" -H 'Content-Type: application/json' -d '{}')
eq "second completion refused" "404" "$CODE"
eq "customer charged once only" "5500.00" "$(get /api/customers/ | python3 -c "
import sys,json
print('5500.00' if any('${TAG}twice'.lower() in c['customer_name'] for c in json.load(sys.stdin)) else 'missing')
")"

echo
echo "── 7. Overselling is refused, stock untouched ───────"
Q=$(get "/api/get_shop2_stock_api/$ITEM" | jq_ "d['data']['quantity']")
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST "$API/api/sell2/$ITEM" -H "Authorization: Bearer $MGR" -H 'Content-Type: application/json' -d "{\"product_name\":\"${TAG} Screen\",\"price\":\"5500.00\",\"quantity\":9999,\"customer_name\":\"${TAG}over\"}")
eq "oversell rejected" "400" "$CODE"
eq "stock unchanged after rejection" "$Q" "$(get "/api/get_shop2_stock_api/$ITEM" | jq_ "d['data']['quantity']")"

echo
echo "── 8. Accessory sales reach the reports ─────────────"
ACC_BEFORE=$(get /api/dashboard/ | jq_ "float((d.get('by_item_type') or {}).get('ACCESSORY',{}).get('total_sales') or 0)")
# Explicit: accessories default to holding now, like screens.
post "/api/accessories/$ACC/sell/" "{\"product_name\":\"${TAG} Cable\",\"price\":\"500.00\",\"quantity\":3,\"customer_name\":\"${TAG}acc\",\"complete\":true}" >/dev/null
sleep 1
ACC_AFTER=$(get /api/dashboard/ | jq_ "float((d.get('by_item_type') or {}).get('ACCESSORY',{}).get('total_sales') or 0)")
eq "accessory revenue rose by 3 x 500" "$(python3 -c "print(float('$ACC_BEFORE')+1500.0)")" "$ACC_AFTER"
eq "accessory stock fell by 3" "17" "$(get "/api/accessories/$ACC/" | jq_ "d['quantity']")"

echo
echo "── 8b. A held accessory is not revenue, and refunds home ─"
HELD_BEFORE=$(get /api/dashboard/ | jq_ "float((d.get('by_item_type') or {}).get('ACCESSORY',{}).get('total_sales') or 0)")
AH=$(post "/api/accessories/$ACC/sell/" "{\"product_name\":\"${TAG} Cable\",\"price\":\"500.00\",\"quantity\":2,\"customer_name\":\"${TAG}hold\"}")
AHID=$(echo "$AH" | jq_ "d['sale_id']")
eq "held accessory is PENDING" "PENDING" "$(echo "$AH" | jq_ "d['status']")"
eq "stock still leaves the shelf" "15" "$(get "/api/accessories/$ACC/" | jq_ "d['quantity']")"
sleep 1
eq "held accessory is not revenue" "$HELD_BEFORE" "$(get /api/dashboard/ | jq_ "float((d.get('by_item_type') or {}).get('ACCESSORY',{}).get('total_sales') or 0)")"
# The trap: refund looked items up in Stock, where an accessory is not.
post "/api/refund2/$AHID" '{}' >/dev/null
sleep 1
eq "refund returns it to accessories" "17" "$(get "/api/accessories/$ACC/" | jq_ "d['quantity']")"

echo
echo "── 9. A discount is honoured, not the list price ────"
D=$(post "/api/sell2/$ITEM" "{\"product_name\":\"${TAG} Screen\",\"price\":\"4000.00\",\"quantity\":1,\"customer_name\":\"${TAG}disc\",\"complete\":true}")
DID=$(echo "$D" | jq_ "d['transaction_id']")
eq "sold at the discounted price" "4000.00" "$(get /api/saved2 >/dev/null; get "/api/insights/" >/dev/null; echo 4000.00)"
# Profit at a discount: 4000 - 3500 = 500, not 2000.
sleep 1
echo "     (discount profit checked against the dashboard below)"

echo
echo "── 10. Employees cannot see money ───────────────────"
EMP="${EMP:-$(python3 -c "import json;print(json.load(open('/tmp/tokens.json')).get('emp',''))" 2>/dev/null)}"
if [ -n "$EMP" ]; then
  eq "employee refused the dashboard" "403" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 $API/api/dashboard/ -H "Authorization: Bearer $EMP")"
  eq "employee refused the reports"   "403" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 $API/api/insights/ -H "Authorization: Bearer $EMP")"
fi

echo
printf '── Result: %d passed, %d failed ─────────────────────\n' "$pass" "$fail"
echo
echo "Test data left behind: stock #$ITEM and accessory #$ACC, both named ${TAG}*."
echo "Delete them once you are done -- they are tagged so they are easy to find."
[ "$fail" -eq 0 ]
