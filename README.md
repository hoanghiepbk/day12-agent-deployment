# Production AI Agent — Day 12 Lab

> **AICB-P1 · VinUniversity 2026 — Part 6: Final Project**

## Kiến Trúc

```
Internet
   │
   ▼
[Nginx :80]  ← Load Balancer + Reverse Proxy
   │
Round-robin
 ┌─┼─┐
 ▼ ▼ ▼
[Agent][Agent][Agent]  ← FastAPI (port 8000 internal)
   │     │     │
   └──┬──┘
      ▼
   [Redis]  ← Rate limit, Cost tracking, Conversation history
```

## Cấu Trúc File

```
06-lab-complete/
├── app/
│   ├── __init__.py      # Python package marker
│   ├── main.py          # FastAPI app — entry point, tất cả endpoints
│   ├── config.py        # 12-Factor config từ environment variables
│   ├── auth.py          # API Key authentication
│   ├── rate_limiter.py  # Sliding Window rate limit (10 req/min)
│   └── cost_guard.py    # Daily budget guard ($5/day)
├── utils/
│   └── mock_llm.py      # Mock LLM (không cần OpenAI API key)
├── nginx/
│   └── nginx.conf       # Nginx load balancer config
├── Dockerfile           # Multi-stage build (< 500MB)
├── docker-compose.yml   # Full stack: agent×3 + redis + nginx
├── requirements.txt     # Python dependencies
├── .env.example         # Template (commit lên Git)
├── .env.local           # Secrets thật (KHÔNG commit)
└── .dockerignore
```

## Chạy Local (Không cần Docker)

```bash
cd 06-lab-complete

# 1. Cài dependencies
pip install -r requirements.txt

# 2. Chạy server
$env:AGENT_API_KEY="my-super-secret-key-2026"; python -m app.main
```

## Test Nhanh

```bash
# Health check (không cần auth)
curl http://localhost:8001/health

# Gọi agent (cần X-API-Key)
curl -X POST http://localhost:8001/ask \
  -H "X-API-Key: my-super-secret-key-2026" \
  -H "Content-Type: application/json" \
  -d '{"question": "Docker là gì?"}'

# Test 401 (không có key)
curl -X POST http://localhost:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello"}'

# Metrics (cần auth)
curl http://localhost:8001/metrics \
  -H "X-API-Key: my-super-secret-key-2026"
```

## Chạy với Docker Compose (Full Stack)

```bash
# Copy env file
cp .env.example .env.local
# Sửa AGENT_API_KEY trong .env.local

# Chạy 3 agent instances + Redis + Nginx
docker compose up --scale agent=3

# Test qua Nginx (port 80)
curl http://localhost/health
curl -X POST http://localhost/ask \
  -H "X-API-Key: my-super-secret-key-2026" \
  -H "Content-Type: application/json" \
  -d '{"question":"Explain microservices"}'
```

## Features đã implement

| Feature | Trạng thái | Chi tiết |
|---------|-----------|---------|
| REST API | ✅ | FastAPI với Pydantic validation |
| Conversation history | ✅ | session_id trong request |
| Multi-stage Dockerfile | ✅ | Builder + Runtime, < 500MB |
| Config từ env vars | ✅ | 12-Factor App compliant |
| API Key auth | ✅ | Header X-API-Key, 401 nếu thiếu/sai |
| Rate limiting | ✅ | Sliding window 10 req/min |
| Cost guard | ✅ | Daily budget $5, 503 khi vượt |
| Health check | ✅ | GET /health → liveness probe |
| Readiness check | ✅ | GET /ready → readiness probe |
| Graceful shutdown | ✅ | SIGTERM handler |
| Stateless design | ✅ | State trong Redis (fallback memory) |
| Structured JSON logging | ✅ | Mỗi request log JSON |
| Security headers | ✅ | X-Content-Type-Options, X-Frame-Options |
| Load balancing | ✅ | Nginx round-robin |
| Metrics endpoint | ✅ | GET /metrics (protected) |

## API Endpoints

| Method | Path | Auth | Mô tả |
|--------|------|------|-------|
| GET | `/` | ❌ | App info |
| POST | `/ask` | ✅ | Gửi câu hỏi cho agent |
| GET | `/health` | ❌ | Liveness probe |
| GET | `/ready` | ❌ | Readiness probe |
| GET | `/metrics` | ✅ | Metrics & budget info |
| GET | `/docs` | ❌ | Swagger UI (dev only) |

## Environment Variables

| Biến | Mặc định | Mô tả |
|------|---------|-------|
| `AGENT_API_KEY` | `dev-key-change-me` | API Key bảo vệ /ask |
| `PORT` | `8000` | Port lắng nghe |
| `ENVIRONMENT` | `development` | dev/staging/production |
| `RATE_LIMIT_PER_MINUTE` | `10` | Max requests/min/IP |
| `DAILY_BUDGET_USD` | `5.0` | Budget ngày ($) |
| `REDIS_URL` | `` | Redis connection (để trống = memory) |
| `OPENAI_API_KEY` | `` | OpenAI key (để trống = mock LLM) |
