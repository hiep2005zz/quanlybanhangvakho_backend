import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.entities import DealerEntity, OrderEntity
from app.models.invoice import InvoiceEntity
from app.api.v1.endpoints.payment_reminders import (
    list_payment_reminders,
    mark_payment_reminder_read,
)
from app.modules.payment_reminders import migration, notifiers, service
from app.modules.payment_reminders.scheduler import next_run_at, scheduler_status
from app import main
from app.modules.payment_reminders.models import (
    PaymentReminderLog,
    PaymentReminderNotification,
)
from app.schemas.auth import UserResponse
from fastapi import HTTPException


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def add_dealer(
    db: Session,
    *,
    code: str = "DL001",
    email: str | None = "dealer@example.com",
    locked: bool = False,
) -> DealerEntity:
    dealer = DealerEntity(
        code=code,
        name="Đại lý kiểm thử",
        email=email,
        status="ACTIVE",
        locked_at=datetime(2026, 1, 1) if locked else None,
    )
    db.add(dealer)
    db.flush()
    return dealer


def add_invoice(
    db: Session,
    dealer: DealerEntity,
    *,
    invoice_code: str,
    due_date: date,
    status: str = "UNPAID",
    total_amount: float = 1_000_000,
    paid_amount: float = 0,
    order_status: str | None = None,
) -> InvoiceEntity:
    order_id = None
    if order_status:
        order = OrderEntity(
            order_code=f"ORD-{invoice_code}",
            dealer_id=dealer.id,
            dealer_name=dealer.name,
            created_by="test",
            status=order_status,
        )
        db.add(order)
        db.flush()
        order_id = order.id

    invoice = InvoiceEntity(
        invoice_code=invoice_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        order_id=order_id,
        due_date=datetime.combine(due_date, time.min, tzinfo=timezone.utc),
        status=status,
        total_amount=total_amount,
        paid_amount=paid_amount,
        remaining_debt=max(0, total_amount - paid_amount),
    )
    db.add(invoice)
    db.flush()
    return invoice


def test_selects_due_and_first_overdue_invoices_only(db_session):
    today = date(2026, 10, 16)
    dealer = add_dealer(db_session)
    due_soon = add_invoice(
        db_session, dealer, invoice_code="DUE-SOON", due_date=today + timedelta(days=3)
    )
    first_overdue = add_invoice(
        db_session,
        dealer,
        invoice_code="OVERDUE-1D",
        due_date=today - timedelta(days=1),
        status="OVERDUE",
    )
    add_invoice(
        db_session,
        dealer,
        invoice_code="OVERDUE-2D",
        due_date=today - timedelta(days=2),
        status="OVERDUE",
    )
    add_invoice(
        db_session,
        dealer,
        invoice_code="PAID",
        due_date=today + timedelta(days=3),
        status="PAID",
        paid_amount=1_000_000,
    )
    locked_dealer = add_dealer(db_session, code="DL002", locked=True)
    add_invoice(
        db_session,
        locked_dealer,
        invoice_code="LOCKED",
        due_date=today + timedelta(days=3),
    )
    add_invoice(
        db_session,
        dealer,
        invoice_code="CANCELLED-ORDER",
        due_date=today + timedelta(days=3),
        order_status="CANCELLED",
    )

    selected = service.repository.select_due_invoices(db_session, today)

    assert {(invoice.id, kind) for invoice, kind in selected} == {
        (due_soon.id, "DUE_SOON_3D"),
        (first_overdue.id, "OVERDUE"),
    }


def test_scheduler_selects_next_ict_run_time():
    from app.modules.payment_reminders.config import RUN_TIME, TIME_ZONE

    before_run = datetime(2026, 10, 18, 8, 59, tzinfo=TIME_ZONE)
    at_run_time = datetime.combine(before_run.date(), RUN_TIME, tzinfo=TIME_ZONE)
    after_run = at_run_time + timedelta(minutes=1)

    assert next_run_at(before_run) == at_run_time
    assert next_run_at(at_run_time) == at_run_time + timedelta(days=1)
    assert next_run_at(after_run) == at_run_time + timedelta(days=1)


def test_scheduler_rejects_timezone_naive_datetime():
    with pytest.raises(ValueError, match="timezone"):
        next_run_at(datetime(2026, 10, 18, 8, 59))


def test_scheduler_status_reports_enabled_state_and_next_run():
    from app.modules.payment_reminders.config import RUN_TIME, TIME_ZONE

    current_time = datetime(2026, 10, 18, 10, 0, tzinfo=TIME_ZONE)

    status = scheduler_status(current_time)

    assert status["enabled"] is True
    assert status["run_time"] == RUN_TIME.isoformat(timespec="minutes")
    assert status["next_run_at"] == datetime.combine(
        date(2026, 10, 19), RUN_TIME, tzinfo=TIME_ZONE
    ).isoformat()
    assert status["last_run_at"] is None


def test_application_lifespan_starts_and_stops_reminder_scheduler(monkeypatch):
    events = []

    async def fake_scheduler():
        events.append("started")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("stopped")

    monkeypatch.setattr(main, "init_db", lambda: events.append("database"))
    monkeypatch.setattr(main, "run_daily_scheduler", fake_scheduler)
    monkeypatch.setattr(main, "FEATURE_ENABLED", True)

    async def verify_lifespan():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            assert events == ["database", "started"]
        assert events == ["database", "started", "stopped"]

    asyncio.run(verify_lifespan())


def test_daily_overdue_repeat_option_includes_older_overdue(db_session):
    today = date(2026, 10, 16)
    dealer = add_dealer(db_session)
    invoice = add_invoice(
        db_session,
        dealer,
        invoice_code="OVERDUE-OLD",
        due_date=today - timedelta(days=8),
        status="OVERDUE",
    )

    selected = service.repository.select_due_invoices(
        db_session, today, overdue_repeat_daily=True
    )

    assert [(item.id, kind) for item, kind in selected] == [(invoice.id, "OVERDUE")]


def test_run_is_idempotent_and_retries_only_failed_channel(db_session, monkeypatch):
    today = date(2026, 10, 16)
    dealer = add_dealer(db_session)
    add_invoice(
        db_session,
        dealer,
        invoice_code="RETRY",
        due_date=today + timedelta(days=3),
    )
    email_attempts = 0

    def send_email(*_args):
        nonlocal email_attempts
        email_attempts += 1
        if email_attempts == 1:
            raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(notifiers, "send_email_reminder", send_email)

    first = service.run_daily_reminders(db_session, today=today)
    second = service.run_daily_reminders(db_session, today=today)

    assert first["sent"] == 1
    assert first["failed"] == 1
    assert second["sent"] == 1
    assert second["failed"] == 0
    assert email_attempts == 2
    assert db_session.query(PaymentReminderLog).count() == 2
    assert db_session.query(PaymentReminderNotification).count() == 1


def test_missing_email_skips_email_but_keeps_in_app(db_session, monkeypatch):
    today = date(2026, 10, 16)
    dealer = add_dealer(db_session, email=None)
    add_invoice(
        db_session,
        dealer,
        invoice_code="NO-EMAIL",
        due_date=today + timedelta(days=3),
    )
    monkeypatch.setattr(
        notifiers,
        "send_email_reminder",
        lambda *_args: pytest.fail("Must not attempt email without a recipient"),
    )

    result = service.run_daily_reminders(db_session, today=today)

    statuses = {
        (log.channel, log.status)
        for log in db_session.query(PaymentReminderLog).all()
    }
    assert result["sent"] == 1
    assert result["skipped"] == 1
    assert statuses == {("in_app", "sent"), ("email", "skipped")}
    assert db_session.query(PaymentReminderNotification).count() == 1


def test_dry_run_does_not_send_or_write_logs(db_session, monkeypatch):
    today = date(2026, 10, 16)
    dealer = add_dealer(db_session)
    add_invoice(
        db_session,
        dealer,
        invoice_code="DRY-RUN",
        due_date=today + timedelta(days=3),
    )
    monkeypatch.setattr(
        notifiers,
        "send_email_reminder",
        lambda *_args: pytest.fail("Dry run must not send email"),
    )
    monkeypatch.setattr(
        notifiers,
        "create_in_app_notification",
        lambda *_args: pytest.fail("Dry run must not create notifications"),
    )

    result = service.run_daily_reminders(db_session, today=today, dry_run=True)

    assert result["dry_run"] is True
    assert result["candidates"] == 1
    assert db_session.query(PaymentReminderLog).count() == 0
    assert db_session.query(PaymentReminderNotification).count() == 0


def test_migration_preserves_existing_tables(db_session):
    engine = db_session.get_bind()
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE existing_business_data (id INTEGER PRIMARY KEY)")
        )
        connection.execute(text("INSERT INTO existing_business_data (id) VALUES (1)"))

    migration.upgrade(engine)
    names = set(inspect(engine).get_table_names())
    assert {"payment_reminder_logs", "payment_reminder_notifications"} <= names
    assert "existing_business_data" in names

    migration.downgrade(engine)
    names = set(inspect(engine).get_table_names())
    assert "payment_reminder_logs" not in names
    assert "payment_reminder_notifications" not in names
    assert "existing_business_data" in names


def test_notification_api_only_returns_the_authenticated_dealers_items(db_session):
    own_dealer = add_dealer(db_session, email="own@example.com")
    other_dealer = add_dealer(
        db_session, code="DL002", email="other@example.com"
    )
    for dealer, invoice_code in (
        (own_dealer, "OWN-INVOICE"),
        (other_dealer, "OTHER-INVOICE"),
    ):
        invoice = add_invoice(
            db_session,
            dealer,
            invoice_code=invoice_code,
            due_date=date(2026, 10, 19),
        )
        db_session.add(
            PaymentReminderNotification(
                agent_id=dealer.id,
                invoice_id=invoice.id,
                reminder_type="DUE_SOON_3D",
                reminder_date=date(2026, 10, 16),
                title=invoice_code,
                body="Reminder",
            )
        )
    db_session.commit()
    current_user = UserResponse(
        id=own_dealer.id,
        username="own@example.com",
        full_name="Own dealer",
        email=own_dealer.email,
        role="customer",
        roles=["customer"],
    )

    response = list_payment_reminders(current_user, db_session)

    assert len(response["items"]) == 1
    assert response["items"][0]["invoice_code"] == "OWN-INVOICE"
    with pytest.raises(HTTPException) as error:
        mark_payment_reminder_read(2, current_user, db_session)
    assert error.value.status_code == 404


def test_due_date_is_compared_in_indochina_time(db_session):
    today = date(2026, 10, 16)
    dealer = add_dealer(db_session)
    invoice = add_invoice(
        db_session,
        dealer,
        invoice_code="ICT-BOUNDARY",
        due_date=today + timedelta(days=3),
    )
    invoice.due_date = datetime(2026, 10, 19, 17, 30, tzinfo=timezone.utc)
    db_session.flush()

    selected = service.repository.select_due_invoices(db_session, today)

    assert all(item.id != invoice.id for item, _ in selected)


def test_claim_retries_failed_log_but_does_not_retry_after_attempt_limit(db_session):
    today = date(2026, 10, 16)
    dealer = add_dealer(db_session)
    invoice = add_invoice(
        db_session,
        dealer,
        invoice_code="MAX-ATTEMPTS",
        due_date=today + timedelta(days=3),
    )
    log_id = service.repository.claim_reminder(
        db_session,
        invoice.id,
        dealer.id,
        "DUE_SOON_3D",
        "email",
        today,
    )
    service.repository.set_log_status(
        db_session, log_id, "failed", error_message="SMTP failed"
    )
    db_session.query(PaymentReminderLog).filter(
        PaymentReminderLog.id == log_id
    ).update({PaymentReminderLog.attempts: 3})
    db_session.commit()

    retry_id = service.repository.claim_reminder(
        db_session,
        invoice.id,
        dealer.id,
        "DUE_SOON_3D",
        "email",
        today,
    )

    assert retry_id is None


def test_email_uses_existing_smtp_settings_and_invoice_template(db_session, monkeypatch):
    dealer = add_dealer(db_session)
    invoice = add_invoice(
        db_session,
        dealer,
        invoice_code="EMAIL-TEMPLATE",
        due_date=date(2026, 10, 19),
    )
    sent_messages = []

    class FakeSMTP:
        def __init__(self, server, port, timeout):
            assert (server, port, timeout) == ("smtp.test", 587, 15)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self, context):
            assert context is not None

        def login(self, username, password):
            assert (username, password) == ("sender@example.com", "secret")

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(notifiers.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notifiers.settings, "MAIL_SERVER", "smtp.test")
    monkeypatch.setattr(notifiers.settings, "MAIL_PORT", 587)
    monkeypatch.setattr(notifiers.settings, "MAIL_USERNAME", "sender@example.com")
    monkeypatch.setattr(notifiers.settings, "MAIL_PASSWORD", "secret")
    monkeypatch.setattr(notifiers.settings, "MAIL_FROM", "sender@example.com")
    monkeypatch.setattr(notifiers.settings, "MAIL_TLS", True)

    notifiers.send_email_reminder(dealer, invoice, "DUE_SOON_3D")

    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "dealer@example.com"
    assert "EMAIL-TEMPLATE" in sent_messages[0]["Subject"]
    assert "Xem hóa đơn:" in sent_messages[0].get_body(preferencelist=("plain",)).get_content()
    assert "EMAIL-TEMPLATE" in sent_messages[0].get_body(preferencelist=("html",)).get_content()
