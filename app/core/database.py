# backend/app/core/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

from sqlalchemy.pool import NullPool

# Engine kết nối cơ sở dữ liệu
is_sqlite = "sqlite" in settings.DATABASE_URL
is_mssql = "mssql" in settings.DATABASE_URL

engine_kwargs = {
    "echo": False,
    "connect_args": {"check_same_thread": False} if is_sqlite else ({"timeout": 30} if is_mssql else {}),
    "poolclass": NullPool if is_sqlite else None,
    "pool_pre_ping": True,
    "pool_recycle": 300,
}
if is_mssql:
    engine_kwargs["fast_executemany"] = True
    engine_kwargs["pool_size"] = 15
    engine_kwargs["max_overflow"] = 25
    engine_kwargs["pool_timeout"] = 30

engine = create_engine(settings.DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency cung cấp db session cho FastAPI endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
