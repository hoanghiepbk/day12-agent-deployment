"""
app/auth.py — Xác thực API Key

KHÁI NIỆM: Authentication = "Bạn là ai?"
  - Client gửi X-API-Key trong HTTP header
  - Server kiểm tra key có đúng không
  - Nếu sai → trả về 401 Unauthorized

TẠI SAO CẦN?
  - Không có auth → ai cũng gọi được API của bạn
  - Bot có thể spam → hết budget OpenAI → mất tiền!

CÁCH DÙNG TRONG FASTAPI:
  @app.post("/ask")
  def ask(user_id: str = Depends(verify_api_key)):  # FastAPI tự inject
      ...

LƯU Ý: Đây là API Key auth (đơn giản).
  Nếu cần user-level auth → dùng JWT (xem jwt_auth.py)
"""
from fastapi import Header, HTTPException, status
from app.config import settings
import logging

logger = logging.getLogger(__name__)


def verify_api_key(x_api_key: str = Header(None, alias="X-API-Key")) -> str:
    """
    Dependency function: FastAPI gọi hàm này trước mỗi request cần auth.

    Cách FastAPI hoạt động:
      1. Client gửi request với header: X-API-Key: abc123
      2. FastAPI tự extract header và truyền vào tham số `x_api_key`
      3. Ta kiểm tra xem key có đúng không

    Args:
        x_api_key: Giá trị của HTTP header "X-API-Key". None nếu không có.

    Returns:
        API key (chuỗi) nếu hợp lệ — FastAPI inject vào endpoint dưới dạng `user_id`

    Raises:
        HTTPException(401): Nếu key thiếu hoặc sai
    """

    # Trường hợp 1: Client không gửi header X-API-Key
    if not x_api_key:
        logger.warning("Request bị từ chối: thiếu API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "Missing API key",
                "hint": "Thêm header: X-API-Key: <your-key>",
            },
            # WWW-Authenticate header: chuẩn HTTP để báo client biết cần auth
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Trường hợp 2: Client gửi key nhưng sai
    # So sánh bằng == thông thường (đủ cho lab)
    # Production nâng cao: dùng secrets.compare_digest() để tránh timing attack
    if x_api_key != settings.agent_api_key:
        logger.warning(f"Request bị từ chối: API key sai (prefix: {x_api_key[:4]}****)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "Invalid API key",
                "hint": "Kiểm tra lại AGENT_API_KEY",
            },
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Trường hợp 3: Key hợp lệ → cho phép request đi tiếp
    logger.debug(f"Auth thành công: key prefix {x_api_key[:4]}****")
    return x_api_key  # Return key để endpoint dùng làm user identifier
