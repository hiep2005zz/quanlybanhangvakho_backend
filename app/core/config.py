import os
from pathlib import Path
from dotenv import load_dotenv

# Nạp file .env từ thư mục backend
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings:
    PROJECT_NAME: str = "Backend API - Quan Ly Kho & Ban Hang"
    API_V1_STR: str = "/api/v1"
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    
    # JWT Settings (Secret key 32 bytes securely generated or constant for dev)
    SECRET_KEY: str = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # Token hiệu lực 60 phút, kết hợp gia hạn ngầm (Sliding Expiration) và Idle Timeout 15 phút

    # Security Lockout Settings
    MAX_FAILED_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15

    # Gmail SMTP Settings
    MAIL_USERNAME: str = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD", "")
    MAIL_FROM: str = os.getenv("MAIL_FROM", os.getenv("MAIL_USERNAME", ""))
    MAIL_PORT: int = int(os.getenv("MAIL_PORT", "587"))
    MAIL_SERVER: str = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_TLS: bool = os.getenv("MAIL_TLS", "True").lower() in ("true", "1", "yes")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./quanlybanhang.db"
    )

settings = Settings()
