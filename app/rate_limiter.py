"""
app/rate_limiter.py — Giới hạn số request (Rate Limiting)

KHÁI NIỆM: Rate Limiting = "Mỗi API key chỉ được gọi X lần mỗi phút"

📌 CHECKLIST YÊU CẦU: "Rate limiting (10 req/min)"
   Áp dụng PER API KEY (không phải per IP) vì:
   - IP có thể bị share (NAT, VPN, corporate network → nhiều user cùng IP)
   - API key chính xác hơn: identify đúng từng user

TẠI SAO CẦN RATE LIMITING?
  - Không có rate limit → bot spam 1000 requests/giây → hết budget OpenAI
  - Bảo vệ server khỏi bị overload
  - Fair usage: user A không chiếm hết quota của hệ thống

THUẬT TOÁN: Sliding Window Counter
  - Với mỗi API key: lưu deque (double-ended queue) các timestamps
  - Mỗi request: xóa timestamps cũ hơn 60s, đếm còn lại
  - Nếu count >= LIMIT → từ chối với HTTP 429
  - "Sliding": cửa sổ 60 giây trượt theo thời gian thực (không phải fixed window)

VÍ DỤ (limit=3 req/min):
  t=0s  → [0]        → OK (1/3)
  t=10s → [0, 10]    → OK (2/3)
  t=20s → [0,10,20]  → OK (3/3)
  t=30s → ❌ 429 (3 req trong 60s qua)
  t=61s → [10,20,61] → OK (t=0 đã expired)

BACKEND:
  - In-memory: nhanh, đơn giản, nhưng KHÔNG scale (mỗi instance có dict riêng)
  - Redis: shared state giữa 3 instances → đúng với stateless design
"""
import time
import logging
from collections import defaultdict, deque
from fastapi import HTTPException, status, Request
from app.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory store (fallback khi không có Redis)
# key = user identifier (api_key prefix hoặc IP)
# value = deque (hàng đợi 2 đầu) chứa timestamps của các request
# ─────────────────────────────────────────────────────────────────────────────
_memory_windows: dict[str, deque] = defaultdict(deque)

# Thử kết nối Redis nếu có cấu hình
_redis_client = None
if settings.redis_url:
    try:
        import redis as redis_lib
        _redis_client = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,   # trả về str thay vì bytes
            socket_timeout=1,        # timeout nhanh để không block request
            socket_connect_timeout=1,
        )
        _redis_client.ping()  # test kết nối ngay khi khởi động
        logger.info("✅ Rate limiter: dùng Redis backend")
    except Exception as e:
        logger.warning(f"⚠️  Không kết nối được Redis ({e}), dùng in-memory fallback")
        _redis_client = None
else:
    logger.info("ℹ️  REDIS_URL chưa set — rate limiter dùng in-memory")


# ─────────────────────────────────────────────────────────────────────────────
# Sliding Window với In-memory
# ─────────────────────────────────────────────────────────────────────────────
def _check_rate_limit_memory(key: str, limit: int, window_seconds: int = 60) -> int:
    """
    Kiểm tra rate limit bằng in-memory deque.

    Args:
        key: Định danh user (api key prefix, IP, ...)
        limit: Số request tối đa trong window_seconds giây
        window_seconds: Kích thước cửa sổ thời gian (mặc định 60s)

    Returns:
        Số request còn lại (remaining)

    Raises:
        HTTPException 429: Nếu đã vượt giới hạn
    """
    now = time.time()
    window = _memory_windows[key]

    # Xóa các timestamps cũ hơn window_seconds giây (đã "expired")
    # Ví dụ: window=60s, now=100s → xóa timestamps < 40s
    while window and window[0] < now - window_seconds:
        window.popleft()

    # Kiểm tra xem còn slot không
    if len(window) >= limit:
        oldest = window[0]
        retry_after = int(oldest + window_seconds - now) + 1

        logger.warning(f"Rate limit exceeded: key={key[:8]}, count={len(window)}/{limit}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "window": f"{window_seconds}s",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # Thêm timestamp của request hiện tại vào window
    window.append(now)
    remaining = limit - len(window)
    return remaining


# ─────────────────────────────────────────────────────────────────────────────
# Sliding Window với Redis (production-ready, stateless)
# ─────────────────────────────────────────────────────────────────────────────
def _check_rate_limit_redis(key: str, limit: int, window_seconds: int = 60) -> int:
    """
    Kiểm tra rate limit bằng Redis sorted set.

    Redis sorted set lưu {member: score} trong đó:
    - member = timestamp (làm unique)
    - score  = timestamp (để sort và range query)

    Cách hoạt động:
    1. ZREMRANGEBYSCORE: xóa entries cũ hơn 60 giây
    2. ZCARD: đếm số entries còn lại
    3. Nếu count >= limit → reject
    4. ZADD: thêm entry mới
    5. EXPIRE: key tự xóa sau 70 giây (1 phút + buffer)
    """
    now = time.time()
    redis_key = f"ratelimit:{key}"

    try:
        pipe = _redis_client.pipeline()  # type: ignore
        # Bước 1: Xóa entries cũ
        pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
        # Bước 2: Đếm entries còn lại
        pipe.zcard(redis_key)
        # Bước 3: Thêm entry mới (score=timestamp, member=timestamp+random để unique)
        pipe.zadd(redis_key, {str(now): now})
        # Bước 4: Set TTL để key tự cleanup
        pipe.expire(redis_key, window_seconds + 10)
        results = pipe.execute()

        count = results[1]  # kết quả của ZCARD (trước khi ZADD)

        if count >= limit:
            logger.warning(f"Rate limit exceeded (Redis): key={key[:8]}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": limit,
                    "window": f"{window_seconds}s",
                    "retry_after_seconds": window_seconds,
                },
                headers={"Retry-After": str(window_seconds)},
            )

        return limit - count - 1  # remaining sau khi thêm request này

    except HTTPException:
        raise  # re-raise rate limit exception
    except Exception as e:
        # Nếu Redis lỗi → fallback về in-memory (graceful degradation)
        logger.error(f"Redis rate limit error: {e}, falling back to memory")
        return _check_rate_limit_memory(key, limit, window_seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — FastAPI Dependency
# ─────────────────────────────────────────────────────────────────────────────
def check_rate_limit(
    request: Request,
    x_api_key: str = None,  # type: ignore
) -> int:
    """
    FastAPI Dependency: kiểm tra rate limit PER API KEY.

    📌 CHECKLIST: "Rate limiting (10 req/min)" — áp dụng cho từng API key.

    Cách FastAPI inject:
        @app.post("/ask")
        async def ask(
            api_key: str = Depends(verify_api_key),
            remaining: int = Depends(check_rate_limit),
        ): ...

    Tại sao per API key thay vì per IP?
      - IP bị share: 100 employees cùng công ty dùng chung IP public
      - API key chính xác: mỗi key = 1 user = 10 req/min riêng

    Rate limit key format: "ratelimit:apikey:{first-8-chars-of-key}"
    Dùng prefix 8 ký tự đầu để:
      - Không lưu key đầy đủ trong Redis (bảo mật)
      - Đủ unique để phân biệt các keys

    Returns:
        Số requests còn lại trong window hiện tại

    Raises:
        HTTPException 429: khi vượt rate limit
    """
    # Lấy API key từ header X-API-Key
    # (auth.py đã verify trước, đây chỉ cần lấy giá trị)
    api_key_value = request.headers.get("X-API-Key", "anonymous")

    # Dùng 8 ký tự đầu làm rate limit bucket key
    # Tránh lưu full key vào Redis log
    key_bucket = api_key_value[:8] if len(api_key_value) >= 8 else api_key_value

    # Chọn backend: Redis (production) hoặc in-memory (dev fallback)
    check_fn = _check_rate_limit_redis if _redis_client else _check_rate_limit_memory

    remaining = check_fn(
        key=f"ratelimit:apikey:{key_bucket}",
        limit=settings.rate_limit_per_minute,
        window_seconds=60,
    )

    logger.debug(
        f"Rate limit OK: key_prefix={key_bucket}, "
        f"remaining={remaining}/{settings.rate_limit_per_minute} req/min"
    )
    return remaining
