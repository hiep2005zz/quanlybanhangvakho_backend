import os
from datetime import time, timedelta, timezone


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


REMINDER_DAYS_BEFORE_DUE = 3
OVERDUE_REPEAT_DAILY = _env_bool("PAYMENT_REMINDERS_OVERDUE_REPEAT_DAILY", False)
MAX_SEND_ATTEMPTS = int(os.getenv("PAYMENT_REMINDERS_MAX_ATTEMPTS", "3"))
TIMEZONE = "Asia/Ho_Chi_Minh"
TIME_ZONE = timezone(timedelta(hours=7), name="ICT")
FEATURE_ENABLED = _env_bool("PAYMENT_REMINDERS_ENABLED", True)
DRY_RUN = _env_bool("PAYMENT_REMINDERS_DRY_RUN", False)
RUN_TIME = time.fromisoformat(os.getenv("PAYMENT_REMINDERS_RUN_TIME", "09:00"))

if MAX_SEND_ATTEMPTS < 1:
    raise ValueError("PAYMENT_REMINDERS_MAX_ATTEMPTS must be at least 1")
