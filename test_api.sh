#!/bin/bash

echo "🧪 Testing Golden Architecture V5.1 API..."
echo ""

# Test 1: Health
echo "1. Health Check:"
curl -s http://localhost:8001/health | python3 -m json.tool
echo ""

# Test 2: Root
echo "2. Root Endpoint:"
curl -s http://localhost:8001/ | python3 -m json.tool
echo ""

# Test 3: Governance
echo "3. Governance Status:"
curl -s http://localhost:8001/governance/status | python3 -m json.tool
echo ""

# Test 4: Stats
echo "4. System Stats:"
curl -s http://localhost:8001/stats | python3 -m json.tool
echo ""

# Test 5: Injection test
echo "5. SQL Injection Test:"
curl -s -X POST http://localhost:8001/test/injection \
  -H 'Content-Type: application/json' \
  -d '{"input":"DROP TABLE users; SELECT * FROM secrets;"}' \
  | python3 -m json.tool
echo ""

echo "✅ All tests complete!"
