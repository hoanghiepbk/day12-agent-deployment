"""
app/config.py — Quản lý cấu hình theo 12-Factor App (Factor III: Config)

NGUYÊN TẮC: KHÔNG bao giờ hardcode giá trị config trong code!
Tất cả config phải đọc từ environment variables.

TẠI SAO?
  - Cùng code chạy được ở dev/staging/production chỉ cần đổi env vars
  - Không lộ secrets khi push code lên GitHub
  - Dễ thay đổi mà không cần redeploy code (Factor XI: Logs)

CÁCH ĐỌC CONFIG:
  from app.config import settings
  port = settings.port          # 8000
  key  = settings.agent_api_key # "abc123"
"""
import os
import logging

logger = logging.getLogger(__name__)


class Settings:
    """
    Singleton chứa toàn bộ config của app.
    Đọc từ environment variables với giá trị mặc định cho development.

    CÁCH SET ENV VARS:
      Linux/Mac:  export AGENT_API_KEY=my-secret-key
      Windows PS: $env:AGENT_API_KEY="my-secret-key"
      Docker:     environment: [AGENT_API_KEY=my-secret-key]
      Railway:    railway variables set AGENT_API_KEY=my-secret-key
    """

    # ── Server ────────────────────────────────────────────────────────────────
    # HOST: luôn dùng "0.0.0.0" trong container
    # "localhost" chỉ nhận traffic từ BÊN TRONG container → app không reach được!
    host: str = os.getenv("HOST", "0.0.0.0")

    # PORT: Railway/Render inject PORT tự động khi deploy
    # Phải đọc từ env — PORT có thể là 3000, 8080, ... tùy platform
    port: int = int(os.getenv("PORT", "8000"))

    # ENVIRONMENT: "development" | "staging" | "production"
    # Ảnh hưởng đến: /docs URL, log level, validation strictness
    environment: str = os.getenv("ENVIRONMENT", "development")

    # DEBUG: bật auto-reload và verbose logging
    # ⚠️ KHÔNG bật trong production — gây memory leak + lộ stack trace
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── App Identity ──────────────────────────────────────────────────────────
    app_name: str    = os.getenv("APP_NAME", "Production AI Agent")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")

    # ── LLM ───────────────────────────────────────────────────────────────────
    # OPENAI_API_KEY: bỏ trống → tự động dùng Mock LLM
    # Mock LLM không gọi API thật → không tốn tiền → OK cho lab
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # Tên model hiển thị trong response (metadata only, mock LLM không dùng)
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # ── Security ──────────────────────────────────────────────────────────────
    # AGENT_API_KEY: client phải gửi kèm trong header "X-API-Key"
    # ⚠️ PHẢI đổi trong production! Tạo strong key:
    #   Linux/Mac: openssl rand -hex 32
    #   Windows:   -join ((48..57+65..90+97..122) | Get-Random -Count 32 | %{[char]$_})
    agent_api_key: str = os.getenv("AGENT_API_KEY", "dev-key-change-me")

    # JWT_SECRET: dùng để ký JWT tokens (nếu enable JWT auth ở tương lai)
    # ⚠️ PHẢI đổi trong production!
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-jwt-secret")

    # ALLOWED_ORIGINS: danh sách domain được phép CORS, ngăn cách bằng dấu phẩy
    # "*" = mọi domain (dev only!), production: "https://yourapp.com"
    allowed_origins: list = os.getenv("ALLOWED_ORIGINS", "*").split(",")

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    # 📌 CHECKLIST: "Rate limiting (10 req/min)"
    # Rate limit áp dụng per API key (mỗi key có quota riêng)
    # Tại sao per key thay vì per IP?
    #   - IP có thể bị share (NAT, VPN, corporate proxy)
    #   - API key chính xác hơn trong việc identify user
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

    # ── Budget (Cost Guard) ───────────────────────────────────────────────────
    # 📌 CHECKLIST line 95: "Cost guard ($10/month)" → phải là MONTHLY tracking
    # Mỗi tháng được phép tốn tối đa $10 cho tất cả requests
    monthly_budget_usd: float = float(os.getenv("MONTHLY_BUDGET_USD", "10.0"))

    # Chi phí ước tính mỗi request (USD)
    # GPT-4o-mini: ~$0.0001 per request (200 input + 100 output tokens)
    cost_per_request_usd: float = float(os.getenv("COST_PER_REQUEST_USD", "0.0001"))

    # Alias cho backward compatibility
    @property
    def daily_budget_usd(self) -> float:
        """Deprecated: dùng monthly_budget_usd. Giữ lại để không break code cũ."""
        return self.monthly_budget_usd

    # ── Redis ─────────────────────────────────────────────────────────────────
    # Redis dùng cho 3 mục đích:
    #   1. Rate limiting counter (per API key, sliding window)
    #   2. Monthly cost tracking (INCRBYFLOAT, atomic)
    #   3. Conversation history (optional, TTL tự cleanup)
    #
    # Format: redis://host:port/db-number
    # Ví dụ: redis://localhost:6379/0
    #         redis://user:pass@redis.example.com:6379/0
    # Để trống → app dùng in-memory fallback (không scale được)
    redis_url: str = os.getenv("REDIS_URL", "")

    def validate(self) -> "Settings":
        """
        Kiểm tra config hợp lệ khi khởi động.
        Fail fast: báo lỗi ngay khi start thay vì fail giữa production.

        Returns:
            self (để dùng chain: settings = Settings().validate())

        Raises:
            ValueError: Nếu config thiếu/sai trong môi trường production
        """
        if self.environment == "production":
            # Không chấp nhận default keys trong production
            if self.agent_api_key == "dev-key-change-me":
                raise ValueError(
                    "❌ AGENT_API_KEY chưa được set!\n"
                    "   Tạo key mạnh: openssl rand -hex 32\n"
                    "   Set: AGENT_API_KEY=<kết quả>"
                )
            if self.jwt_secret == "dev-jwt-secret":
                raise ValueError(
                    "❌ JWT_SECRET chưa được set trong production!\n"
                    "   Tạo: openssl rand -hex 64"
                )

        # Cảnh báo nếu không có OpenAI key (sẽ dùng mock LLM)
        if not self.openai_api_key:
            logger.warning(
                "⚠️  OPENAI_API_KEY chưa set — "
                "sẽ dùng Mock LLM (không gọi OpenAI API thật)"
            )

        return self


# Singleton: tạo 1 lần khi module được import, dùng ở khắp app
settings = Settings()
settings.validate()
