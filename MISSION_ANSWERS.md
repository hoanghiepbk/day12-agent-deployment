# Day 12 Lab — Mission Answers

> **Student:** Phạm Hữu Hoàng Hiệp
> **Student ID:** 2A202600415
> **Date:** 17/04/2026

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns tìm thấy trong `01-localhost-vs-production/develop/app.py`

Tìm được **5 vấn đề** (đã được comment trong code):

| # | Vấn đề | Dòng code | Tại sao nguy hiểm |
|---|--------|-----------|-------------------|
| 1 | **API key hardcode trong code** | `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` | Push lên GitHub → key bị lộ ngay lập tức, kẻ xấu dùng key của ta |
| 2 | **Database password hardcode** | `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"` | Lộ credentials DB production, attacker có thể xóa toàn bộ data |
| 3 | **Dùng `print()` thay vì logging** | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | Log secrets ra stdout, không có log level, không thể filter |
| 4 | **Không có health check endpoint** | Không có `/health` route | Platform không biết khi agent bị crash → không tự restart |
| 5 | **Port và host cứng, debug mode luôn bật** | `host="localhost", port=8000, reload=True` | `localhost` trong container = không nhận traffic; `reload=True` trong production gây memory leak |

### Exercise 1.2: Kết quả chạy basic version

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt
python app.py
# → Server chạy nhưng KHÔNG production-ready
```

Kết quả test:
```bash
curl http://localhost:8000/ask -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello"}'
# → {"answer": "..."} — Chạy được, nhưng có nhiều vấn đề
```

**Quan sát:** App chạy được trên máy local, nhưng sẽ fail khi deploy vì:
- `host="localhost"` → container không nhận được traffic từ bên ngoài
- Nếu lộ GitHub repo → API key và DB password bị lộ

### Exercise 1.3: Bảng so sánh Develop vs Production

| Feature | Develop (`develop/app.py`) | Production (`production/app.py`) | Tại sao quan trọng? |
|---------|---------------------------|----------------------------------|---------------------|
| **Config** | Hardcode trực tiếp trong code (`OPENAI_API_KEY = "sk-..."`) | Đọc từ environment variables qua `settings` object | Secrets không bị commit lên Git; dễ thay đổi giữa dev/staging/prod mà không sửa code |
| **Health check** | ❌ Không có | ✅ `/health` trả về `{"status":"ok"}` và `/ready` probe | Platform (Railway, K8s) cần biết khi nào restart container; load balancer biết khi nào route traffic |
| **Logging** | `print(f"[DEBUG]...")` — không có level, log cả secret | `logging` với JSON format, không bao giờ log secrets | JSON log dễ index và search trong Datadog/CloudWatch; có log level để filter |
| **Shutdown** | Đột ngột (không xử lý SIGTERM) | `signal.signal(SIGTERM, handle_sigterm)` + lifespan cleanup | Graceful shutdown cho phép request đang xử lý hoàn thành trước khi tắt → không mất data |
| **Port/Host** | `host="localhost", port=8000` cứng trong code | `host=settings.host` (0.0.0.0) và `port=settings.port` từ env | `0.0.0.0` nhận traffic trong container; PORT env var được Railway/Render inject tự động |
| **Debug mode** | `reload=True` luôn bật | `reload=settings.debug` (False trong production) | Auto-reload gây memory leak và chậm trong production |

---

## Part 2: Docker Containerization

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

**1. Base image là gì?**  
`python:3.11` — Full Python distribution (~1 GB). Đây là single-stage build dùng để minh họa khái niệm cơ bản.

**2. Working directory là gì?**  
`WORKDIR /app` — Tất cả lệnh tiếp theo (COPY, RUN, CMD) đều chạy trong `/app` bên trong container.

**3. Tại sao COPY requirements.txt trước khi COPY code?**  
**Docker Layer Cache optimization:** Mỗi `RUN`, `COPY` tạo ra một layer. Docker cache lại layer nếu input không đổi.
- `requirements.txt` ít thay đổi → `pip install` được cache
- Code thay đổi thường xuyên → chỉ rebuild layer COPY code trở đi
- Nếu COPY code trước → mỗi lần sửa code đều phải `pip install` lại từ đầu → **build chậm hơn 10×**

**4. CMD vs ENTRYPOINT khác nhau thế nào?**

| | CMD | ENTRYPOINT |
|--|-----|-----------|
| Mục đích | Lệnh mặc định, có thể override | Lệnh chính, không thể override bằng `docker run image <cmd>` |
| Override | `docker run image python other.py` | Chỉ override arguments, không override lệnh |
| Kết hợp | Khi dùng với ENTRYPOINT → làm arguments mặc định | Nhận CMD làm arguments |
| Ví dụ trong lab | `CMD ["python", "app.py"]` | Không dùng ENTRYPOINT trong basic version |

**Tại sao production dùng exec form `["cmd", "arg"]` thay vì shell form `"cmd arg"`?**  
Exec form → process là PID 1 → nhận SIGTERM trực tiếp → graceful shutdown hoạt động.  
Shell form → `/bin/sh` là PID 1 → SIGTERM tới shell, không tới app → forced kill!

### Exercise 2.2: Image size so sánh

```bash
# Build develop image
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker images my-agent:develop
# → SIZE: ~1.1 GB (full python:3.11 + dependencies)
```

### Exercise 2.3: Multi-stage build

**Stage 1 (builder) làm gì?**  
Cài đặt build tools (gcc, libpq-dev) và chạy `pip install`. Mục đích: compile các Python package cần C compiler (như pydantic-core, cryptography).

**Stage 2 (runtime) làm gì?**  
Bắt đầu từ base image sạch, chỉ COPY kết quả đã compile từ Stage 1, không copy compiler/tools.

**Tại sao image nhỏ hơn?**  
Stage 2 không chứa: gcc (~50MB), libpq-dev (~30MB), pip cache, header files, .pyc files, build artifacts.

```bash
# Build production image
docker build -t my-agent:advanced .
docker images | grep my-agent
# my-agent  develop  → ~1.1 GB
# my-agent  advanced → ~250 MB  (giảm ~77%)
```

### Exercise 2.4: Docker Compose Stack Architecture

Services được start:
1. **redis** — In-memory store (healthcheck: `redis-cli ping`)
2. **agent** — FastAPI app (depends on redis being healthy)

```
Client
  │
  ▼
agent:8000  ←→  redis:6379
```

Chúng communicate qua Docker internal network: container `agent` kết nối tới `redis:6379` — Docker DNS tự resolve hostname `redis` thành IP của redis container.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway Deployment

**Steps đã thực hiện:**
```bash
npm i -g @railway/cli
railway login
railway init
railway variables set PORT=8000
railway variables set AGENT_API_KEY=<key>
railway up
railway domain
```

**URL đã deploy:** *(Xem DEPLOYMENT.md)*

**Test commands:**
```bash
# Health check
curl https://<your-app>.railway.app/health
# → {"status":"ok","version":"1.0.0",...}

# API test
curl https://<your-app>.railway.app/ask -X POST \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is Docker?"}'
```

### Exercise 3.2: So sánh render.yaml vs railway.toml

| | `railway.toml` | `render.yaml` |
|--|----------------|---------------|
| Platform | Railway | Render |
| Format | TOML | YAML |
| Build command | `[build] builder = "DOCKERFILE"` | `env: docker` |
| Auto-detect | Railway tự detect Dockerfile | Render đọc render.yaml là "Blueprint" |
| Env vars | Set qua `railway variables set` hoặc dashboard | Set trong dashboard hoặc render.yaml |
| Health check | Cấu hình trong dashboard | `healthCheckPath: /health` trong render.yaml |

---

## Part 4: API Security

### Exercise 4.1: API Key Authentication

**API key được check ở đâu?**  
Trong `04-api-gateway/develop/app.py`, middleware hoặc dependency `verify_api_key` check header `X-API-Key` trong mỗi request bảo vệ.

**Điều gì xảy ra nếu sai key?**  
Trả về `HTTP 401 Unauthorized`:
```json
{"error": "Invalid or missing API key", "hint": "Include header: X-API-Key: <key>"}
```

**Làm sao rotate key?**  
Cập nhật `AGENT_API_KEY` environment variable trên platform (Railway/Render) rồi restart service. Không cần thay đổi code.

**Test kết quả:**
```bash
# Không có key → 401
curl http://localhost:8000/ask -X POST -d '{"question":"Hello"}'
# → 401 {"error":"Missing API key"}

# Có key đúng → 200
curl http://localhost:8000/ask -X POST \
  -H "X-API-Key: secret-key-123" \
  -d '{"question":"Hello"}'
# → 200 {"answer":"..."}
```

### Exercise 4.2: JWT Authentication Flow

**JWT Flow (in `04-api-gateway/production/auth.py`):**

```
1. Client POST /token {username, password}
2. Server verify credentials
3. Server tạo JWT: header.payload.signature
   - payload chứa: user_id, exp (expiry), role
   - sign bằng JWT_SECRET
4. Client nhận token, lưu lại
5. Client gửi request: Authorization: Bearer <token>
6. Server verify signature → decode payload → lấy user_id
```

**Tại sao JWT tốt hơn API key thuần?**
- JWT **stateless**: server không cần lưu session
- JWT có **expiry**: token tự hết hạn → security tốt hơn
- JWT chứa **claims** (role, user_id) → không cần query DB mỗi request

### Exercise 4.3: Rate Limiting

**Algorithm:** Sliding Window Counter (trong `04-api-gateway/production/rate_limiter.py`)

**Cách hoạt động:**
```
Giữ deque timestamps của N request gần nhất
Mỗi request:
  1. Xóa timestamps cũ hơn 60 giây
  2. Đếm số timestamps còn lại
  3. Nếu count >= limit → từ chối (429)
  4. Nếu OK → thêm timestamp hiện tại
```

**Limit:** `rate_limiter_user = RateLimiter(max_requests=10, window_seconds=60)` → 10 req/phút cho user

**Admin bypass:** `rate_limiter_admin = RateLimiter(max_requests=100, window_seconds=60)` → 100 req/phút cho admin

**Test output khi hit limit:**
```json
{
  "error": "Rate limit exceeded",
  "limit": 10,
  "window_seconds": 60,
  "retry_after_seconds": 45
}
```

### Exercise 4.4: Cost Guard Implementation

**Approach của mình:**
- Track chi phí theo ngày (key format: `cost:2026-04-17`)
- Lưu vào Redis với `INCRBYFLOAT` (atomic, thread-safe)
- Reset tự động khi sang ngày mới (TTL 25h)
- `INCRBYFLOAT` tránh race condition khi nhiều instances cùng ghi

```python
def check_budget(user_id: str, estimated_cost: float) -> bool:
    """Return True nếu còn budget, False nếu vượt."""
    month_key = datetime.now().strftime("%Y-%m")
    key = f"budget:{user_id}:{month_key}"

    # Lấy spending hiện tại
    current = float(r.get(key) or 0)

    # Kiểm tra vượt budget chưa ($10/tháng)
    if current + estimated_cost > 10:
        return False

    # Ghi nhận chi phí
    r.incrbyfloat(key, estimated_cost)
    r.expire(key, 32 * 24 * 3600)  # TTL 32 ngày
    return True
```

**Tại sao dùng `INCRBYFLOAT` thay vì GET + SET?**  
Vì khi scale 3 instances: 2 instances đọc `current=5.0` cùng lúc, cả 2 cộng thêm và set `5.1` → undercount spending! `INCRBYFLOAT` là atomic operation → thread-safe.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health và Readiness Checks

**Implement `/health` (Liveness probe):**
```python
@app.get("/health")
def health():
    # Chỉ check process còn sống, không check external deps
    return {"status": "ok", "uptime": round(time.time() - START_TIME, 1)}
```

**Implement `/ready` (Readiness probe):**
```python
@app.get("/ready")
def ready():
    # Check tất cả dependencies trước khi nhận traffic
    try:
        r.ping()           # Redis
        # db.execute("SELECT 1")  # Database
        return {"status": "ready"}
    except:
        return JSONResponse(status_code=503, content={"status": "not ready"})
```

**Sự khác biệt quan trọng:**
- `/health` fail → platform **restart** container (process bị crash)
- `/ready` fail → load balancer **dừng route traffic** (container đang khởi động/quá tải)

### Exercise 5.2: Graceful Shutdown

```python
import signal

def shutdown_handler(signum, frame):
    global _is_ready
    logger.info("SIGTERM received — graceful shutdown...")

    # 1. Dừng nhận traffic mới (load balancer kiểm tra /ready)
    _is_ready = False

    # 2. Uvicorn tự hoàn thành in-flight requests
    #    (timeout_graceful_shutdown=30 trong uvicorn config)

    # 3. Cleanup connections (nếu cần)
    # redis_client.close()
    # db.close()

    # 4. Exit (uvicorn tự gọi sau khi drain requests)

signal.signal(signal.SIGTERM, shutdown_handler)
```

**Test:**
```bash
python app.py &
PID=$!
curl http://localhost:8000/ask -X POST -d '{"question":"Long task"}' &
kill -TERM $PID
# → Request hoàn thành trước khi server tắt ✅
```

### Exercise 5.3: Stateless Design

**Anti-pattern (State trong memory):**
```python
# ❌ Khi scale 3 instances, mỗi instance có dict riêng
# User gửi request A → instance 1 (lưu history)
# User gửi request B → instance 2 (không có history!) → mất context
conversation_history = {}
```

**Correct approach (State trong Redis):**
```python
# ✅ Tất cả instances đọc cùng 1 Redis → share state
@app.post("/ask")
def ask(user_id: str, question: str):
    # Đọc từ Redis (shared storage)
    history = r.lrange(f"history:{user_id}", 0, -1)
    # ... xử lý ...
    # Lưu vào Redis
    r.rpush(f"history:{user_id}", question, answer)
    r.expire(f"history:{user_id}", 3600)  # TTL 1 giờ
```

### Exercise 5.4: Load Balancing

```bash
docker compose up --scale agent=3
```

**Quan sát:**
- 3 containers `agent_1`, `agent_2`, `agent_3` được start
- Nginx phân phối request theo Round-robin: req1→agent1, req2→agent2, req3→agent3
- Kiểm tra logs: `docker compose logs agent | grep "request"` → thấy mỗi agent xử lý ~1/3 số request

**Test:**
```bash
for i in {1..10}; do
  curl http://localhost/ask -X POST \
    -H "Content-Type: application/json" \
    -d '{"question": "Request '$i'"}'
done
docker compose logs agent  # → requests chia đều giữa 3 instances
```

### Exercise 5.5: Test Stateless

```bash
python test_stateless.py
```

**Kết quả:** Conversation history được giữ ngay cả khi instance bị kill và request tiếp theo đến instance khác, vì tất cả đọc từ Redis.

---

## Tóm Tắt Concepts Học Được

| Concept | Vấn đề giải quyết | Solution |
|---------|------------------|---------|
| 12-Factor Config | "Works on my machine" | Environment variables |
| Docker Multi-stage | Image quá lớn | Builder + Runtime stages |
| API Key Auth | Ai cũng gọi được API | `X-API-Key` header check |
| Rate Limiting | Spam/abuse | Sliding window counter |
| Cost Guard | Hóa đơn bất ngờ | Daily budget tracking |
| Health/Ready probe | Platform không biết app trạng thái | `/health` + `/ready` endpoints |
| Graceful shutdown | Request bị ngắt giữa chừng | SIGTERM handler |
| Stateless design | Không scale được | State trong Redis |
| Load balancing | 1 instance không đủ | Nginx round-robin |
