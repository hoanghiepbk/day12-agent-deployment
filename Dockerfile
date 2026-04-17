# =============================================================================
# Multi-stage Dockerfile — Production AI Agent
# =============================================================================
#
# TẠI SAO MULTI-STAGE BUILD?
#   - Stage 1 (builder): cài đặt tools biên dịch (gcc, rustc...) để build deps
#   - Stage 2 (runtime): chỉ copy kết quả, KHÔNG copy tools
#   → Image nhỏ hơn nhiều (500MB → 150MB) vì không có build tools thừa
#   → Bảo mật hơn: ít package = ít attack surface
#
# BEST PRACTICES ÁP DỤNG:
#   ✅ Multi-stage build
#   ✅ Non-root user (không chạy app bằng root)
#   ✅ Tận dụng Docker layer cache (COPY requirements trước khi COPY code)
#   ✅ HEALTHCHECK built-in
#   ✅ ENV rõ ràng
#   ✅ Dùng python:slim thay vì python:full
#
# CÁCH BUILD:
#   docker build -t my-agent:latest .
#   docker run -p 8000:8000 -e AGENT_API_KEY=secret my-agent:latest
#
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: Builder — cài đặt dependencies
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder
# Đặt tên stage là "builder" để stage 2 có thể COPY --from=builder

WORKDIR /build

# Cài build tools cần thiết để compile một số Python packages (e.g. pydantic-core)
# --no-install-recommends: không cài package gợi ý (giảm size)
# rm -rf /var/lib/apt/lists/*: xóa apt cache sau khi cài (giảm layer size)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements TRƯỚC khi copy code
# TẠI SAO? Docker layer cache:
#   - Nếu requirements.txt không đổi → Docker dùng cache layer này
#   - Chỉ khi code thay đổi → rebuild từ COPY app/ trở xuống
#   - Nếu COPY code trước → mỗi lần sửa code đều phải pip install lại → chậm!
COPY requirements.txt .

# --no-cache-dir: không lưu pip cache (giảm size)
# --user: cài vào ~/.local (không cần root, dễ copy sang stage 2)
RUN pip install --no-cache-dir --user -r requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: Runtime — image cuối cùng, nhỏ gọn
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
# Bắt đầu từ base image sạch (không có gcc, không có apt cache từ stage 1)

# Tạo non-root user để chạy app
# TẠI SAO KHÔNG DÙNG ROOT?
#   - Nếu app bị compromise, attacker chỉ có quyền user "agent", không phải root
#   - Không thể sửa system files, không thể cài thêm tools nguy hiểm
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

WORKDIR /app

# Copy installed packages từ builder stage
# /root/.local vì pip --user cài vào home của user hiện tại (root trong builder)
COPY --from=builder /root/.local /home/agent/.local

# Copy source code (thứ tự: ít thay đổi trước, hay thay đổi sau)
COPY utils/ ./utils/
COPY app/   ./app/

# Đổi owner toàn bộ /app về user "agent"
# Cần làm trước khi switch user (sau USER agent thì không có quyền chown)
RUN chown -R agent:agent /app /home/agent/.local

# Switch sang non-root user cho tất cả lệnh tiếp theo
USER agent

# Environment variables mặc định
ENV PATH=/home/agent/.local/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random

EXPOSE 8000

# HEALTHCHECK: Docker tự gọi lệnh này mỗi 30 giây
#   - Exit 0 → healthy (container tiếp tục chạy)
#   - Exit 1 → unhealthy (Docker restart container nếu --restart=always)
# --start-period=15s: chờ 15s sau khi start trước khi check lần đầu
HEALTHCHECK \
    --interval=30s \
    --timeout=10s  \
    --start-period=15s \
    --retries=3 \
    CMD python -c "import urllib.request, os; port = os.environ.get('PORT', '8000'); urllib.request.urlopen('http://localhost:' + port + '/health')" \
    || exit 1

# CMD: Lệnh khởi chạy server
# Chạy thông qua python module để bắt được khối lệnh if __name__ == "__main__":
# Qua đó ứng dụng sẽ tự động chọn PORT do Railway cấp.
CMD ["python", "-m", "app.main"]
