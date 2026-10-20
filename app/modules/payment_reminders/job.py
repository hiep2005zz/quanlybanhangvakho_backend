from __future__ import annotations
import argparse
import logging
from datetime import date

from app.modules.payment_reminders.config import DRY_RUN, FEATURE_ENABLED
from app.modules.payment_reminders.migration import upgrade
from app.modules.payment_reminders.service import run_daily_reminders


def main() -> None:
    parser = argparse.ArgumentParser(description="Send daily payment reminders")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        help="Override the ICT run date (YYYY-MM-DD), primarily for verification",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=DRY_RUN,
        help="Report eligible invoices without sending notifications",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if not FEATURE_ENABLED:
        logging.getLogger(__name__).info("Payment reminders are disabled")
        return
    upgrade()
    run_daily_reminders(today=args.today, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
