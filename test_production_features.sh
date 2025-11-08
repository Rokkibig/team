#!/bin/bash

echo "🧪 Testing Production Features: Bcrypt, Metrics, CORS, Audit"
echo ""

# Start server
.venv/bin/python demo_server.py > /tmp/prod_test.log 2>&1 &
SERVER_PID=$!
echo "🚀 Server starting (PID: $SERVER_PID)..."
sleep 8

BASE_URL="http://localhost:8000"

# Test 1: Prometheus /metrics
echo "📊 Test 1: Prometheus /metrics endpoint"
METRICS=$(curl -s "$BASE_URL/metrics" | head -15)
echo "$METRICS"
echo ""

# Test 2: Bcrypt login
echo "🔐 Test 2: Bcrypt password login (admin/admin123)"
LOGIN_RESULT=$(curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}')
echo "$LOGIN_RESULT" | python3 -m json.tool | head -8
echo ""

# Test 3: Check auth metrics
echo "📈 Test 3: Auth metrics after login"
curl -s "$BASE_URL/metrics" | grep "auth_logins_total"
echo ""

# Test 4: CORS headers
echo "🌐 Test 4: CORS configuration"
tail -5 /tmp/prod_test.log | grep "CORS"
echo ""

# Cleanup
echo "🛑 Stopping server..."
kill $SERVER_PID 2>/dev/null
sleep 2

echo "✅ All production features tested!"
