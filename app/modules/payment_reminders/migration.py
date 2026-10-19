import argparse

from sqlalchemy.engine import Engine

from app.core.database import engine
from app.modules.payment_reminders.models import (
    PaymentReminderLog,
    PaymentReminderNotification,
)


def upgrade(bind: Engine = engine) -> None:
    PaymentReminderLog.__table__.create(bind=bind, checkfirst=True)
    PaymentReminderNotification.__table__.create(bind=bind, checkfirst=True)


def downgrade(bind: Engine = engine) -> None:
    PaymentReminderNotification.__table__.drop(bind=bind, checkfirst=True)
    PaymentReminderLog.__table__.drop(bind=bind, checkfirst=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage payment reminder tables")
    parser.add_argument("action", choices=("upgrade", "downgrade"), nargs="?", default="upgrade")
    args = parser.parse_args()
    if args.action == "upgrade":
        upgrade()
    else:
        downgrade()


if __name__ == "__main__":
    main()
