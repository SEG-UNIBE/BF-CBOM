#!/bin/bash
# Health check script for BF-CBOM services

set -e

echo "🏥 BF-CBOM Health Check"
echo "======================="

# Function to check if a service is responding
check_service() {
    local name=$1
    local url=$2
    local expected_status=${3:-200}
    
    echo -n "Checking $name... "
    
    if response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null); then
        if [ "$response" -eq "$expected_status" ]; then
            echo "✅ OK ($response)"
            return 0
        else
            echo "❌ FAIL (HTTP $response)"
            return 1
        fi
    else
        echo "❌ UNREACHABLE"
        return 1
    fi
}

# Check services
check_service "API Health" "http://localhost:8000/health"
check_service "API Docs" "http://localhost:8000/docs"
check_service "Grafana" "http://localhost:3000/login"
check_service "Prometheus" "http://localhost:9090/graph"
check_service "Nginx" "http://localhost:80/health"

echo ""
echo "🐳 Docker Compose Status:"
docker compose ps

echo ""
echo "📊 Service Summary:"
echo "- API: http://localhost:8000"
echo "- Docs: http://localhost:8000/docs"
echo "- Grafana: http://localhost:3000 (admin/admin)"
echo "- Prometheus: http://localhost:9090"
echo ""
echo "Health check complete! ✨"