import asyncio
import logging
from datetime import datetime, timedelta

from app.modules.payment_reminders.config import FEATURE_ENABLED, RUN_TIME, TIME_ZONE
from app.modules.payment_reminders.service import run_daily_reminders

logger = logging.getLogger(__name__)
_scheduler_running = False
_last_run_at: datetime | None = None
_last_run_summary: dict[str, int | bool] | None = None
_last_run_failed = False


def next_run_at(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("Scheduler time must include a timezone")

    local_now = now.astimezone(TIME_ZONE)
    scheduled = datetime.combine(local_now.date(), RUN_TIME, tzinfo=TIME_ZONE)
    if scheduled <= local_now:
        scheduled += timedelta(days=1)
    return scheduled


def scheduler_status(now: datetime | None = None) -> dict[str, object]:
    current_time = now or datetime.now(TIME_ZONE)
    return {
        "enabled": FEATURE_ENABLED,
        "running": _scheduler_running,
        "run_time": RUN_TIME.isoformat(timespec="minutes"),
        "timezone": "Asia/Ho_Chi_Minh",
        "next_run_at": next_run_at(current_time).isoformat(),
        "last_run_at": _last_run_at.isoformat() if _last_run_at else None,
        "last_run_summary": _last_run_summary,
        "last_run_failed": _last_run_failed,
    }


async def run_daily_scheduler() -> None:
    global _last_run_at, _last_run_summary, _last_run_failed, _scheduler_running
    _scheduler_running = True
    logger.info("Payment reminder scheduler started; run time is %s ICT", RUN_TIME)
    try:
        while True:
            now = datetime.now(TIME_ZONE)
            scheduled = next_run_at(now)
            delay = max(0.0, (scheduled - now).total_seconds())
            logger.info("Next payment reminder run scheduled at %s", scheduled.isoformat())
            await asyncio.sleep(delay)
            _last_run_at = datetime.now(TIME_ZONE)
            try:
                _last_run_summary = await asyncio.to_thread(run_daily_reminders)
                _last_run_failed = int(_last_run_summary["failed"]) > 0
                logger.info("Scheduled payment reminder run completed: %s", _last_run_summary)
            except Exception:
                _last_run_failed = True
                logger.exception("Scheduled payment reminder run failed")
    finally:
        _scheduler_running = False
