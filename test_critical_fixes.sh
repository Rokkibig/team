#!/bin/bash

# Test Critical Fixes for Golden Architecture V5.1

echo "🧪 Starting critical fixes validation..."
echo ""

# Start server in background
.venv/bin/python demo_server.py > /tmp/test_server.log 2>&1 &
SERVER_PID=$!
echo "🚀 Server starting (PID: $SERVER_PID)..."
sleep 7

BASE_URL="http://localhost:8000"

# Test 1: Valid login
echo "📝 Test 1: Valid admin login..."
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}')
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
TOKEN=$(echo "$RESPONSE" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
echo "✅ Token extracted: ${TOKEN:0:60}..."
echo ""

# Test 2: Invalid login (should return auth.invalid_credentials)
echo "📝 Test 2: Invalid login (expect auth.invalid_credentials error_code)..."
curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "wrong", "password": "wrong"}' | python3 -m json.tool
echo ""

# Test 3: Budget insufficient (should return 409 budget.insufficient)
echo "📝 Test 3: Budget insufficient (expect 409 budget.insufficient)..."
curl -s -X POST "$BASE_URL/api/v1/budget/request" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"tenant_id":"test","project_id":"demo","task_id":"t1","model":"gpt-4","estimated_tokens":999999999}' | python3 -m json.tool
echo ""

# Test 4: DLQ messages (should use correct columns)
echo "📝 Test 4: DLQ messages list..."
curl -s "$BASE_URL/api/v1/dlq?resolved=false&limit=5" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
echo ""

# Cleanup
echo "🛑 Stopping server..."
kill $SERVER_PID 2>/dev/null
sleep 2

echo "✅ All tests complete!"
