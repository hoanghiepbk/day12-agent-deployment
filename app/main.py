"""
app/main.py — Production AI Agent: Entry Point

Đây là file chính kết hợp TẤT CẢ concepts của Day 12:

  ✅ 12-Factor config (từ environment variables, không hardcode)
  ✅ Structured JSON logging (dễ parse bằng log aggregator)
  ✅ API Key authentication (auth.py)
  ✅ Rate limiting — Sliding Window 10 req/min (rate_limiter.py)
  ✅ Cost guard — Daily budget $5 (cost_guard.py)
  ✅ Input validation với Pydantic
  ✅ /health endpoint — Liveness probe
  ✅ /ready endpoint  — Readiness probe
  ✅ Graceful shutdown — xử lý SIGTERM
  ✅ Security headers (X-Content-Type-Options, X-Frame-Options)
  ✅ CORS middleware
  ✅ Request logging middleware
  ✅ /metrics endpoint
  ✅ Conversation history (in-memory đơn giản, có thể upgrade sang Redis)

LUỒNG XỬ LÝ MỘT REQUEST:
  Client → CORS check → Request Middleware (log) → Auth check → Rate limit check
        → Budget check → LLM call → Response

CÁCH CHẠY:
  # Development (auto-reload khi sửa code):
  python -m app.main

  # Production (nhiều workers paralel):
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
"""
import os
import sys
import time
import signal
import logging
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# Import các module của chúng ta
from app.config import settings
from app.auth import verify_api_key
from app.rate_limiter import check_rate_limit
from app.cost_guard import check_budget, get_monthly_spending, estimate_cost

# Mock LLM (thay bằng OpenAI client khi có API key thật)
# utils/ đã có trong 06-lab-complete/ (cùng cấp với app/)
from utils.mock_llm import ask as llm_ask

# ─────────────────────────────────────────────────────────────────────────────
# Logging — JSON Structured Format
# ─────────────────────────────────────────────────────────────────────────────
# Tại sao JSON? Vì log aggregators (Datadog, Grafana Loki, CloudWatch) có thể
# parse JSON và filter theo field (ví dụ: chỉ xem request có status=500)
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    # Format đơn giản: timestamp + level + message
    # Message sẽ là JSON string do chúng ta tự encode
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Global State (server-level, không phải per-request)
# ─────────────────────────────────────────────────────────────────────────────
START_TIME = time.time()   # Thời điểm server khởi động (tính uptime)
_is_ready = False          # Flag: đã khởi động xong chưa? (cho /ready endpoint)
_request_count = 0         # Tổng số requests đã nhận
_error_count = 0           # Tổng số requests lỗi

# Conversation history đơn giản (in-memory, mất khi restart)
# Format: {"session_id": [{"role": "user", "content": "..."}, ...]}
# Production: nên lưu vào Redis với TTL để tự cleanup
_conversations: dict[str, list] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan — Khởi động & Tắt server
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager quản lý vòng đời của application.

    Code trước `yield` = khởi động (startup)
    Code sau  `yield` = tắt server (shutdown)

    Tại sao dùng lifespan thay vì @app.on_event("startup")?
    → FastAPI khuyến nghị dùng lifespan từ v0.93+
    → on_event sẽ bị deprecated trong tương lai
    """
    global _is_ready

    # ── STARTUP ───────────────────────────────────────────────────────────────
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "host": settings.host,
        "port": settings.port,
        "rate_limit": settings.rate_limit_per_minute,
        "daily_budget": settings.daily_budget_usd,
    }))

    # Giả lập init time (trong thực tế: load model, warm up cache, ...)
    time.sleep(0.1)

    # Đánh dấu server đã sẵn sàng nhận traffic
    _is_ready = True
    logger.info(json.dumps({"event": "ready", "message": "Server đã sẵn sàng!"}))

    yield  # ← Server đang chạy, nhận requests

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    # Code này chạy khi server nhận SIGTERM (Docker stop, Ctrl+C, ...)
    _is_ready = False
    logger.info(json.dumps({
        "event": "shutdown",
        "total_requests": _request_count,
        "total_errors": _error_count,
        "uptime_seconds": round(time.time() - START_TIME, 1),
    }))


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App Instance
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Production-ready AI Agent — Day 12 Lab VinUniversity",
    lifespan=lifespan,
    # /docs (Swagger UI) chỉ bật khi không phải production (bảo mật)
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
)


# ─────────────────────────────────────────────────────────────────────────────
# Middleware 1: CORS (Cross-Origin Resource Sharing)
# ─────────────────────────────────────────────────────────────────────────────
# CORS = cho phép browser từ domain khác gọi API của bạn
# Ví dụ: frontend ở https://app.com gọi API ở https://api.app.com
# Nếu không có CORS → browser block request (server vẫn nhận nhưng browser bỏ response)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,  # ["*"] = cho phép mọi domain (dev only)
    allow_methods=["GET", "POST"],           # Chỉ cho phép GET và POST
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    allow_credentials=False,                 # Không cho phép cookies cross-origin
)


# ─────────────────────────────────────────────────────────────────────────────
# Middleware 2: Request Logging + Security Headers
# ─────────────────────────────────────────────────────────────────────────────
@app.middleware("http")
async def request_middleware(request: Request, call_next):
    """
    Middleware chạy cho MỌI request (kể cả /health, /ready).

    Thứ tự xử lý:
      Request vào → middleware → endpoint → middleware → Response ra

    Làm 3 việc:
      1. Log mỗi request (method, path, status, latency)
      2. Thêm security headers vào response
      3. Đếm error rate
    """
    global _request_count, _error_count

    start_time = time.time()
    _request_count += 1

    # Gọi endpoint thực sự và lấy response
    response: Response = await call_next(request)

    # Tính thời gian xử lý
    duration_ms = round((time.time() - start_time) * 1000, 1)

    # Đếm errors (4xx và 5xx)
    if response.status_code >= 400:
        _error_count += 1

    # Log mỗi request theo format JSON
    logger.info(json.dumps({
        "event": "request",
        "method": request.method,
        "path": str(request.url.path),
        "status": response.status_code,
        "latency_ms": duration_ms,
        "client": request.headers.get("X-Forwarded-For", 
                   str(request.client.host) if request.client else "unknown"),
    }))

    # Security Headers — chống các loại tấn công web phổ biến
    # X-Content-Type-Options: ngăn browser "sniff" content type (MIME sniffing attack)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # X-Frame-Options: ngăn site bị nhúng vào iframe (Clickjacking attack)
    response.headers["X-Frame-Options"] = "DENY"
    # Cache-Control: không cache API responses (tránh stale data)
    response.headers["Cache-Control"] = "no-store"
    # Ẩn thông tin server (không để lộ "uvicorn/0.30.0")
    # MutableHeaders không có .pop() → dùng try/except del
    try:
        del response.headers["server"]
    except KeyError:
        pass

    return response


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models — Request/Response Schemas
# ─────────────────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    """Schema cho request body của POST /ask"""
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Câu hỏi gửi cho AI agent",
        json_schema_extra={"example": "Docker là gì và tại sao cần dùng?"},
    )
    session_id: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Session ID để giữ conversation history (optional)",
        json_schema_extra={"example": "user-123-session-456"},
    )

class AskResponse(BaseModel):
    """Schema cho response của POST /ask"""
    question: str
    answer: str
    session_id: Optional[str]
    model: str
    estimated_cost_usd: float
    timestamp: str                        # ISO 8601 format


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    """Endpoint gốc — trả về thông tin app."""
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "docs": "/docs" if settings.environment != "production" else "disabled",
        "endpoints": {
            "POST /ask": "Gửi câu hỏi (cần X-API-Key header)",
            "GET /health": "Liveness probe",
            "GET /ready": "Readiness probe",
            "GET /metrics": "Metrics (cần X-API-Key header)",
        },
    }


@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(
    body: AskRequest,
    request: Request,
    # Các Depends() dưới đây chạy TRƯỚC khi vào function body
    # FastAPI tự gọi chúng theo thứ tự và inject kết quả
    api_key: str = Depends(verify_api_key),      # 1. Kiểm tra auth
    _remaining: int = Depends(check_rate_limit), # 2. Kiểm tra rate limit
    _budget: dict = Depends(check_budget),       # 3. Kiểm tra budget
):
    """
    Gửi câu hỏi cho AI Agent.

    **Authentication required:** Thêm header `X-API-Key: <your-key>`

    **Rate limit:** 10 requests/minute per IP

    **Conversation history:** Truyền `session_id` để giữ context

    **Response:** Câu trả lời từ AI + metadata
    """

    # ── Lấy conversation history ───────────────────────────────────────────
    history = []
    if body.session_id:
        # Lấy lịch sử conversation từ memory (hoặc Redis trong production)
        history = _conversations.get(body.session_id, [])
        logger.debug(f"Loaded {len(history)} messages for session {body.session_id}")

    # ── Gọi LLM ───────────────────────────────────────────────────────────
    logger.info(json.dumps({
        "event": "llm_call",
        "session_id": body.session_id,
        "question_length": len(body.question),
        "history_turns": len(history) // 2,  # mỗi turn = 1 user + 1 assistant msg
    }))

    # Trong production thật, đây sẽ là: openai_client.chat.completions.create(...)
    # Hiện tại dùng mock LLM để không cần API key
    answer = llm_ask(body.question)

    # ── Cập nhật conversation history ─────────────────────────────────────
    if body.session_id:
        if body.session_id not in _conversations:
            _conversations[body.session_id] = []

        # Thêm cặp user/assistant vào history
        _conversations[body.session_id].extend([
            {"role": "user",      "content": body.question},
            {"role": "assistant", "content": answer},
        ])

        # Giữ tối đa 20 messages (~10 turns) để tránh tốn memory
        if len(_conversations[body.session_id]) > 20:
            _conversations[body.session_id] = _conversations[body.session_id][-20:]

    # ── Tính chi phí thực của request này ────────────────────────────────
    actual_cost = estimate_cost(body.question, answer)

    logger.info(json.dumps({
        "event": "llm_success",
        "answer_length": len(answer),
        "estimated_cost_usd": actual_cost,
    }))

    return AskResponse(
        question=body.question,
        answer=answer,
        session_id=body.session_id,
        model=settings.llm_model,
        estimated_cost_usd=actual_cost,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/health", tags=["Operations"])
def health():
    """
    Liveness Probe — "Container còn sống không?"

    Platform (Docker, Kubernetes, Railway) gọi endpoint này định kỳ.
    - Trả về 200 → container OK
    - Trả về 5xx → platform restart container

    Liveness probe KHÔNG nên kiểm tra external dependencies (DB, Redis)
    vì nếu Redis down ta vẫn muốn container sống (chỉ chậm hơn).
    Kiểm tra external deps là việc của /ready (readiness probe).
    """
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["Operations"])
def ready():
    """
    Readiness Probe — "Server đã sẵn sàng nhận traffic chưa?"

    Load balancer dùng endpoint này:
    - 200 → gửi traffic đến instance này
    - 503 → DỪNG gửi traffic (instance đang khởi động hoặc quá tải)

    Điều này cho phép rolling deployment:
    1. Deploy instance mới
    2. Instance mới trả về /ready=503 khi đang khởi động
    3. Khi /ready=200 → load balancer bắt đầu gửi traffic
    4. Instance cũ mới được tắt
    → Zero-downtime deployment!
    """
    if not _is_ready:
        raise HTTPException(
            status_code=503,
            detail="Server đang khởi động, vui lòng thử lại sau"
        )
    return {
        "ready": True,
        "uptime_seconds": round(time.time() - START_TIME, 1),
    }


@app.get("/metrics", tags=["Operations"])
def metrics(api_key: str = Depends(verify_api_key)):
    """
    Basic metrics — chỉ cho admin (cần API key).

    Trong production thật: dùng Prometheus + Grafana.
    Endpoint /metrics này là simplified version.
    """
    total = get_monthly_spending()
    return {
        "server": {
            "uptime_seconds": round(time.time() - START_TIME, 1),
            "environment": settings.environment,
        },
        "traffic": {
            "total_requests": _request_count,
            "total_errors": _error_count,
            "error_rate_pct": round(_error_count / max(_request_count, 1) * 100, 1),
        },
        "budget": {
            # 📌 CHECKLIST: "Cost guard ($10/month)" — tracking theo tháng
            "spent_this_month_usd": round(total, 4),
            "monthly_budget_usd": settings.monthly_budget_usd,
            "remaining_usd": round(settings.monthly_budget_usd - total, 4),
            "used_pct": round(total / settings.monthly_budget_usd * 100, 1),
        },
        "rate_limit": {
            "limit_per_minute": settings.rate_limit_per_minute,
            "scope": "per API key",
        },
        "conversations": {
            "active_sessions": len(_conversations),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Graceful Shutdown — Xử lý SIGTERM
# ─────────────────────────────────────────────────────────────────────────────
def _handle_sigterm(signum, _frame):
    """
    Handler cho SIGTERM signal.

    SIGTERM là signal mà:
    - Docker gửi khi chạy `docker stop`
    - Kubernetes gửi khi terminate pod
    - Railway/Render gửi khi redeploy

    KHÔNG xử lý SIGTERM → Docker đợi 10 giây rồi gửi SIGKILL (force kill)
    → Requests đang xử lý bị ngắt giữa chừng → data loss, error cho user!

    Xử lý SIGTERM đúng cách:
    1. Log để biết reason shutdown
    2. _is_ready = False → /ready trả về 503 → load balancer ngừng gửi traffic
    3. Uvicorn tự hoàn thành các requests đang xử lý (timeout_graceful_shutdown=30s)
    4. Lifespan cleanup (yield phía dưới trong lifespan function)
    """
    global _is_ready
    logger.info(json.dumps({
        "event": "sigterm_received",
        "message": "Bắt đầu graceful shutdown...",
        "pending_requests": "uvicorn sẽ hoàn thành requests hiện tại",
    }))
    _is_ready = False  # Dừng nhận traffic mới qua load balancer

# Đăng ký handler — hệ thống sẽ gọi _handle_sigterm khi nhận SIGTERM
signal.signal(signal.SIGTERM, _handle_sigterm)


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point — chạy trực tiếp bằng `python -m app.main`
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"🚀 Khởi động {settings.app_name} v{settings.app_version}")
    logger.info(f"   Environment : {settings.environment}")
    logger.info(f"   Host:Port   : {settings.host}:{settings.port}")
    logger.info(f"   API Key     : {settings.agent_api_key[:4]}****")
    logger.info(f"   Rate limit  : {settings.rate_limit_per_minute} req/min")
    logger.info(f"   Budget/day  : ${settings.daily_budget_usd}")
    logger.info(f"   Debug mode  : {settings.debug}")

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        # reload=True: tự restart khi sửa code (chỉ dùng dev)
        reload=settings.debug,
        # timeout_graceful_shutdown: chờ tối đa 30s để finish in-flight requests
        timeout_graceful_shutdown=30,
        # access_log=False vì ta đã tự log trong middleware
        access_log=False,
    )
