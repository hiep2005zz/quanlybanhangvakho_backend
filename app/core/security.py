from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid
import bcrypt
import jwt
from app.core.config import settings

# In-memory Token Blacklist: stores identifier (token or jti) -> expiry timestamp (float)
BLACKLISTED_TOKENS: dict[str, float] = {}

def cleanup_blacklist() -> None:
    """Xóa các token đã hết hạn tự nhiên ra khỏi blacklist để tránh đầy bộ nhớ."""
    now_ts = datetime.now(timezone.utc).timestamp()
    expired_keys = [k for k, exp in BLACKLISTED_TOKENS.items() if exp < now_ts]
    for k in expired_keys:
        BLACKLISTED_TOKENS.pop(k, None)

def revoke_token(token: str) -> bool:
    """
    Thu hồi (hủy) hiệu lực của token ngay lập tức phía server khi đăng xuất.
    Thêm JTI hoặc chuỗi token vào blacklist.
    """
    try:
        cleanup_blacklist()
        # Decode không kiểm tra thời hạn để lấy jti và exp
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_exp": False}
        )
        jti = payload.get("jti")
        exp = payload.get("exp") or (datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()
        if jti:
            BLACKLISTED_TOKENS[jti] = float(exp)
        BLACKLISTED_TOKENS[token] = float(exp)
        return True
    except Exception:
        # Nếu token không giải mã được thì lưu chuỗi thô để chặn
        BLACKLISTED_TOKENS[token] = (datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()
        return True

def is_token_revoked(token: str, payload: Optional[dict[str, Any]] = None) -> bool:
    """Kiểm tra token có nằm trong blacklist (đã bị thu hồi do đăng xuất) hay không."""
    cleanup_blacklist()
    if token in BLACKLISTED_TOKENS:
        return True
    if payload:
        jti = payload.get("jti")
        if jti and jti in BLACKLISTED_TOKENS:
            return True
    return False

def get_password_hash(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against hashed password."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False

def create_access_token(
    subject: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
    jti: Optional[str] = None,
    token_version: int = 1
) -> str:
    """Generate JWT Access Token with username, role, unique jti, token_version, and expiration."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "token_version": token_version,
        "jti": jti or uuid.uuid4().hex,
        "exp": expire,
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_access_token(
    token: str,
    allow_expired_grace: bool = False,
    grace_seconds: int = 1800
) -> Optional[dict[str, Any]]:
    """Decode and validate JWT Access Token. Rejects blacklisted tokens.
    Khi allow_expired_grace=True, cho phép ân hạn thời gian hết hạn (leeway) để làm mới phiên."""
    try:
        leeway = grace_seconds if allow_expired_grace else 0
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            leeway=leeway
        )
        if is_token_revoked(token, payload):
            return None
        return payload
    except jwt.PyJWTError:
        return None
