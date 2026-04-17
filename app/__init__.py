"""
app/__init__.py — Đánh dấu thư mục `app/` là Python package.

File này có thể để trống. Sự tồn tại của nó cho phép:
  from app.config import settings
  from app.auth import verify_api_key
  ... (import như module)

Không có file này → Python không nhận ra `app` là package → ImportError!
"""
