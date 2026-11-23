#!/usr/bin/env bash
# Assemble the static Vercel UI from the API's own dashboard files.
#   scripts/build_vercel_ui.sh <api-base-url> <out-dir>
set -euo pipefail
API="${1:?api base url, e.g. https://trade-intel.1.2.3.4.sslip.io}"
OUT="${2:?output dir}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/src/trade_intel/api/static"
mkdir -p "$OUT/static"
sed "s#<meta name=\"ti-api\" content=\"\">#<meta name=\"ti-api\" content=\"${API}\">#" \
  "$SRC/dashboard.html" > "$OUT/index.html"
cp "$SRC/dashboard.js" "$OUT/static/dashboard.js"
cat > "$OUT/vercel.json" <<JSON
{
  "headers": [{
    "source": "/(.*)",
    "headers": [
      {"key": "Content-Security-Policy", "value": "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src ${API}; frame-ancestors 'none'"},
      {"key": "X-Content-Type-Options", "value": "nosniff"},
      {"key": "Referrer-Policy", "value": "no-referrer"},
      {"key": "X-Robots-Tag", "value": "noindex"}
    ]
  }]
}
JSON
