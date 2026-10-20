# backend/app/core/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

from sqlalchemy.pool import NullPool

# Toàn bộ hệ thống bắt buộc sử dụng Microsoft SQL Server (MSSQL)
if not settings.DATABASE_URL.startswith("mssql"):
    raise RuntimeError(
        f"LỖI CƠ SỞ DỮ LIỆU: Hệ thống được cấu hình chỉ chạy Microsoft SQL Server (mssql+pyodbc). "
        f"URL hiện tại: {settings.DATABASE_URL}"
    )

is_mssql = True
is_sqlite = False

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    fast_executemany=True,
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"timeout": 30},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency cung cấp db session cho FastAPI endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
