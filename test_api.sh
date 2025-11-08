#!/bin/bash

# Port can be overridden via environment variable
PORT=${PORT:-8000}
BASE_URL="http://localhost:${PORT}"

echo "🧪 Testing Golden Architecture V5.1 API..."
echo "📡 Using ${BASE_URL}"
echo ""

# Test 1: Health
echo "1. Health Check:"
curl -s ${BASE_URL}/health | python3 -m json.tool
echo ""

# Test 2: Root
echo "2. Root Endpoint:"
curl -s ${BASE_URL}/ | python3 -m json.tool
echo ""

# Test 3: Governance
echo "3. Governance Status:"
curl -s ${BASE_URL}/governance/status | python3 -m json.tool
echo ""

# Test 4: Stats
echo "4. System Stats:"
curl -s ${BASE_URL}/stats | python3 -m json.tool
echo ""

# Test 5: Injection test
echo "5. SQL Injection Test:"
curl -s -X POST ${BASE_URL}/test/injection \
  -H 'Content-Type: application/json' \
  -d '{"input":"DROP TABLE users; SELECT * FROM secrets;"}' \
  | python3 -m json.tool
echo ""

echo "✅ All tests complete!"
