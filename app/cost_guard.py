"""
app/cost_guard.py — Bảo vệ ngân sách hàng tháng (Monthly Cost Guard)

KHÁI NIỆM: Cost Guard = "Tự động dừng khi vượt budget $10/tháng/user"

📌 CHECKLIST YÊU CẦU (DAY12_DELIVERY_CHECKLIST.md line 95):
   "Cost guard ($10/month)" → tracking THEO THÁNG, không phải theo ngày.

VẤN ĐỀ THỰC TẾ:
  - Bạn deploy AI agent lên cloud, có public URL
  - Bot tìm thấy URL và spam 10,000 requests/giờ
  - Mỗi request gọi OpenAI tốn tiền
  - Sáng thức dậy: hóa đơn $5,000 😱

GIẢI PHÁP: Track chi phí theo tháng, dừng khi vượt $10

CÁCH TÍNH CHI PHÍ (approximate):
  - GPT-4o-mini: $0.15/1M input tokens, $0.60/1M output tokens
  - 1 request ≈ 200 input + 100 output tokens
  - Chi phí ≈ $0.000030 + $0.000060 = ~$0.0001/request
  - 100,000 requests = $10 → đó là giới hạn tháng

THIẾT KẾ:
  - Key Redis: "cost:{YYYY-MM}" (ví dụ: "cost:2026-04")
  - Dùng INCRBYFLOAT atomic để tránh race condition
  - Reset tự động đầu tháng (TTL 35 ngày)
  - Fallback in-memory nếu không có Redis
"""
import time
import logging
from fastapi import HTTPException, status
from app.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory fallback (khi không có Redis)
# ─────────────────────────────────────────────────────────────────────────────
# Lưu: {"month": "2026-04", "total": 3.14}
_memory_cost: dict = {"month": "", "total": 0.0}

# Thử kết nối Redis khi module được import
_redis_client = None
if settings.redis_url:
    try:
        import redis as redis_lib
        _redis_client = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,   # trả về str thay vì bytes
            socket_timeout=1,        # không block request quá 1 giây
            socket_connect_timeout=1,
        )
        _redis_client.ping()  # kiểm tra kết nối ngay lập tức
        logger.info("✅ Cost guard: dùng Redis backend (monthly tracking)")
    except Exception as e:
        logger.warning(f"⚠️  Redis không khả dụng ({e}), dùng in-memory fallback")
        _redis_client = None
else:
    logger.info("ℹ️  REDIS_URL chưa set — cost guard dùng in-memory")


def _get_month_key() -> str:
    """
    Tạo Redis key cho tháng hiện tại (UTC).
    Format: "cost:2026-04"

    Tại sao key theo tháng?
    - Checklist yêu cầu $10/month → reset mỗi đầu tháng
    - Dễ query: "tháng này đã tốn bao nhiêu?"
    - TTL 35 ngày = tự cleanup sau khi tháng kết thúc
    """
    return f"cost:{time.strftime('%Y-%m', time.gmtime())}"


def _get_current_spending_redis() -> float:
    """Lấy tổng chi phí tháng này từ Redis."""
    try:
        key = _get_month_key()
        value = _redis_client.get(key)  # type: ignore
        return float(value) if value else 0.0
    except Exception as e:
        logger.error(f"Lỗi đọc cost từ Redis: {e}")
        return 0.0


def _add_cost_redis(amount: float) -> float:
    """
    Cộng thêm chi phí vào Redis và trả về tổng mới.

    INCRBYFLOAT là atomic operation:
    - Đảm bảo an toàn khi 3 instances agent cùng ghi đồng thời
    - GET + SET thông thường có race condition → undercount!

    Ví dụ race condition:
      Instance A: GET cost = 5.0
      Instance B: GET cost = 5.0  ← cùng đọc giá trị cũ!
      Instance A: SET cost = 5.0001
      Instance B: SET cost = 5.0001  ← ghi đè, mất 1 request!
    Với INCRBYFLOAT:
      Instance A: INCRBYFLOAT → 5.0001 (atomic)
      Instance B: INCRBYFLOAT → 5.0002 (atomic, sequential)
    """
    try:
        key = _get_month_key()
        new_total = _redis_client.incrbyfloat(key, amount)  # type: ignore
        # TTL 35 ngày: tự cleanup sau khi tháng kết thúc + buffer
        _redis_client.expire(key, 35 * 24 * 3600)  # type: ignore
        return float(new_total)
    except Exception as e:
        logger.error(f"Lỗi ghi cost vào Redis: {e}")
        return amount


def _check_and_add_memory(amount: float) -> float:
    """
    In-memory fallback: cộng cost và reset khi sang tháng mới.
    ⚠️ KHÔNG dùng production khi scale vì:
    - Mỗi instance có counter riêng → undercount spending
    - Data mất khi restart container
    """
    current_month = time.strftime("%Y-%m", time.gmtime())
    # Reset counter nếu sang tháng mới
    if _memory_cost["month"] != current_month:
        logger.info(f"Monthly cost counter reset: {_memory_cost['month']} → {current_month}")
        _memory_cost["month"] = current_month
        _memory_cost["total"] = 0.0

    _memory_cost["total"] += amount
    return _memory_cost["total"]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def estimate_cost(question: str, answer: str = "") -> float:
    """
    Ước tính chi phí (USD) cho 1 request dựa trên độ dài text.

    Quy tắc rough estimate (tiếng Anh): 1 token ≈ 4 ký tự
    GPT-4o-mini pricing (tháng 4/2026):
      - Input:  $0.15  / 1M tokens
      - Output: $0.60  / 1M tokens

    Args:
        question: Câu hỏi của user (input tokens)
        answer:   Câu trả lời của LLM (output tokens)
                  Nếu chưa có answer → estimate 100 tokens output

    Returns:
        Số tiền USD (float, làm tròn 8 chữ số thập phân)
    """
    # Ước tính số tokens từ độ dài text
    input_tokens  = max(len(question) / 4, 10)    # ít nhất 10 tokens
    output_tokens = max(len(answer) / 4, 100) if answer else 100

    # Tính cost theo pricing GPT-4o-mini
    input_cost  = (input_tokens  / 1_000_000) * 0.15
    output_cost = (output_tokens / 1_000_000) * 0.60

    return round(input_cost + output_cost, 8)


def check_budget() -> dict:
    """
    FastAPI Dependency: kiểm tra monthly budget trước mỗi /ask request.

    📌 CHECKLIST: "Cost guard ($10/month)" — giới hạn $10 MỖI THÁNG.

    Flow:
      1. Lấy tổng spending tháng này
      2. Nếu >= $10 (monthly_budget_usd) → raise 503 Service Unavailable
      3. Nếu OK → ghi nhận estimated cost của request này

    Returns:
        dict với thông tin budget hiện tại (inject vào endpoint qua Depends)

    Raises:
        HTTPException 503: Khi đã vượt ngân sách tháng
    """
    # Chi phí ước tính của 1 request (rất nhỏ, từ config)
    estimated_cost = settings.cost_per_request_usd

    # Lấy tổng spending tháng này
    if _redis_client:
        current_spending = _get_current_spending_redis()
    else:
        current_spending = _memory_cost.get("total", 0.0)

    # Kiểm tra có vượt monthly budget không
    if current_spending >= settings.monthly_budget_usd:
        logger.error(
            f"💸 Monthly budget exhausted: "
            f"spent=${current_spending:.4f}, "
            f"budget=${settings.monthly_budget_usd}/month"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Monthly budget exhausted",
                "spent_this_month_usd": round(current_spending, 4),
                "monthly_budget_usd": settings.monthly_budget_usd,
                "message": "Service sẽ tự phục hồi vào đầu tháng sau",
                "resets_on": time.strftime("%Y-%m-01", time.gmtime(
                    # Tính ngày 1 tháng sau
                    time.mktime(time.strptime(
                        time.strftime("%Y-%m", time.gmtime()) + "-28", "%Y-%m-%d"
                    )) + 4 * 86400  # +4 ngày để qua đầu tháng
                )),
            },
        )

    # Ghi nhận chi phí ước tính của request này
    if _redis_client:
        new_total = _add_cost_redis(estimated_cost)
    else:
        new_total = _check_and_add_memory(estimated_cost)

    budget_info = {
        "spent_this_month_usd": round(new_total, 4),
        "monthly_budget_usd": settings.monthly_budget_usd,
        "remaining_usd": round(settings.monthly_budget_usd - new_total, 4),
        "used_pct": round(new_total / settings.monthly_budget_usd * 100, 2),
    }

    logger.debug(f"Budget OK: {budget_info}")
    return budget_info


def get_monthly_spending() -> float:
    """Lấy tổng chi phí tháng này (dùng cho /metrics endpoint)."""
    if _redis_client:
        return _get_current_spending_redis()
    return _memory_cost.get("total", 0.0)


# Backward compat alias (rate_limiter module dùng)
get_today_spending = get_monthly_spending
