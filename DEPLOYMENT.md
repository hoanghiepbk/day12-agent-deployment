# Deployment Information — Day 12 Lab
> **Student:** Phạm Hữu Hoàng Hiệp
> **Student ID:** 2A202600415
> **Date:** 17/04/2026
> Điền thông tin sau khi deploy lên Railway hoặc Render

---

## Public URL

```
https://ai-agent-production-production-6c41.up.railway.app
```

## Platform

- [x] Railway
- [ ] Render  
- [ ] GCP Cloud Run

## Deploy Steps Đã Thực Hiện

```bash
# 1. Vào thư mục 06-lab-complete
cd 06-lab-complete

# 2. Cài Railway CLI
npm i -g @railway/cli

# 3. Login
railway login

# 4. Init project
railway init

# 5. Set environment variables
railway variables set AGENT_API_KEY=<your-secure-key>
railway variables set ENVIRONMENT=production
railway variables set RATE_LIMIT_PER_MINUTE=10
railway variables set MONTHLY_BUDGET_USD =10.0

# 6. Deploy
railway up

# 7. Lấy domain
railway domain
```

## Test Commands

### Health Check
```bash
curl https://ai-agent-production-production-6c41.up.railway.app/health
# Expected: {"status":"ok","version":"1.0.0","environment":"production",...}
```

### Authentication Test (expect 401)
```bash
curl -X POST https://ai-agent-production-production-6c41.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Hello"}'
# Expected: 401 {"error":"Missing API key"}
```

### API Test (with authentication)
```bash
curl -X POST https://ai-agent-production-production-6c41.up.railway.app/ask \
  -H "X-API-Key: YOUR_AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is Docker?","session_id":"test-1"}'
# Expected: 200 {"question":...,"answer":...,"model":...}
```

### Rate Limit Test (expect 429 after 10 requests)
```bash
for i in $(seq 1 13); do
  curl -s -o /dev/null -w "%{http_code} " \
    -X POST https://ai-agent-production-production-6c41.up.railway.app/ask \
    -H "X-API-Key: YOUR_AGENT_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"question\":\"Test $i\"}"
done
# Expected: 200 200 200 200 200 200 200 200 200 200 429 429 429
```

### Readiness Probe
```bash
curl https://ai-agent-production-production-6c41.up.railway.app/ready
# Expected: {"ready":true}
```

## Environment Variables Set trên Platform

| Variable | Value |
|----------|-------|
| `PORT` | auto (Railway inject) |
| `ENVIRONMENT` | `production` |
| `AGENT_API_KEY` | *(secret)* |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `REDIS_URL` | *(nếu có Redis addon)* |

## Screenshots

*(Thêm screenshots sau khi deploy)*

- `screenshots/railway-dashboard.png` — Railway dashboard
- `screenshots/deployment-success.png` — Deployment successful
- `screenshots/health-check.png` — Health check response
- `screenshots/api-test.png` — API test result

## Local Test Results (Pre-deployment)

```
=== Test Suite: 10/10 PASS ===
✅ Root endpoint (200)
✅ Health check (200)
✅ Readiness probe (200)
✅ Auth rejected - no key (401)
✅ Auth rejected - wrong key (401)
✅ Ask with valid key (200)
✅ Conversation history preserved
✅ Rate limiting works (429 after 10 req)
✅ Metrics endpoint (200)
✅ Production ready check: 20/20 (100%)
```

## Architecture Deployed

```
Internet
   │
   ▼
[Nginx :80]     ← Load Balancer
   │
Round-robin
 ┌─┼─┐
 ▼ ▼ ▼
[Agent][Agent][Agent]   ← FastAPI (3 instances)
   └──────┬──────┘
          ▼
       [Redis]          ← Shared state
```
