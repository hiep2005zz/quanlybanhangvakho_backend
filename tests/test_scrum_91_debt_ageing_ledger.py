import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.entities import DealerEntity, OrderEntity, UserEntity
from app.services.debt_ledger_service import compute_debt_ledger_data, export_debt_ledger_excel

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    try:
        # Seed Sales user
        sale_user = UserEntity(
            id=10,
            username="saleman1",
            full_name="Nguyễn Văn Kinh Doanh",
            email="sale1@example.com",
            role="salesman",
            hashed_password="hash",
            is_active=True,
        )
        db.add(sale_user)

        # Seed Dealers
        dealer1 = DealerEntity(
            id=1,
            code="DL001",
            name="Đại Lý Miền Bắc",
            region="Miền Bắc",
            assigned_sale_id=10,
            credit_limit=100000000.0,
            max_debt_days=30,
            overdue_days_allowed=0,
            phone="0911222333",
            status="Đang hoạt động",
        )
        dealer2 = DealerEntity(
            id=2,
            code="DL002",
            name="Đại Lý Miền Nam",
            region="Miền Nam",
            assigned_sale_id=10,
            credit_limit=50000000.0,
            max_debt_days=30,
            overdue_days_allowed=0,
            phone="0988777666",
            status="Đang hoạt động",
        )
        db.add_all([dealer1, dealer2])
        db.commit()

        # Seed Orders with different ages
        now = datetime.now(timezone.utc)

        # Dealer 1 orders:
        # Order 1: 15 days ago -> Bucket 0-30 days: 10,000,000
        o1 = OrderEntity(
            id=101,
            order_code="ORD-01",
            dealer_id=1,
            dealer_name="Đại Lý Miền Bắc",
            created_by=10,
            total_amount=10000000.0,
            status="DELIVERED", # Unpaid debt
            created_at=now - timedelta(days=15),
        )
        # Order 2: 45 days ago -> Bucket 31-60 days: 20,000,000
        o2 = OrderEntity(
            id=102,
            order_code="ORD-02",
            dealer_id=1,
            dealer_name="Đại Lý Miền Bắc",
            created_by=10,
            total_amount=20000000.0,
            status="DELIVERED", # Unpaid debt
            created_at=now - timedelta(days=45),
        )
        # Order 3: 75 days ago -> Bucket 61-90 days: 15,000,000
        o3 = OrderEntity(
            id=103,
            order_code="ORD-03",
            dealer_id=1,
            dealer_name="Đại Lý Miền Bắc",
            created_by=10,
            total_amount=15000000.0,
            status="DELIVERED", # Unpaid debt
            created_at=now - timedelta(days=75),
        )
        # Order 4: 120 days ago -> Bucket >90 days: 5,000,000
        o4 = OrderEntity(
            id=104,
            order_code="ORD-04",
            dealer_id=1,
            dealer_name="Đại Lý Miền Bắc",
            created_by=10,
            total_amount=5000000.0,
            status="DELIVERED", # Unpaid debt
            created_at=now - timedelta(days=120),
        )
        # Order 5: Paid order: 8,000,000
        o5 = OrderEntity(
            id=105,
            order_code="ORD-05",
            dealer_id=1,
            dealer_name="Đại Lý Miền Bắc",
            created_by=10,
            total_amount=8000000.0,
            status="PAID",
            created_at=now - timedelta(days=10),
        )

        # Dealer 2 orders:
        # Order 6: Only fresh debt within 10 days (Bucket 0-30: 5,000,000)
        o6 = OrderEntity(
            id=106,
            order_code="ORD-06",
            dealer_id=2,
            dealer_name="Đại Lý Miền Nam",
            created_by=10,
            total_amount=5000000.0,
            status="CONFIRMED",
            created_at=now - timedelta(days=10),
        )

        db.add_all([o1, o2, o3, o4, o5, o6])
        db.commit()

        yield db
    finally:
        db.close()


def test_ageing_buckets_calculation(db_session):
    """
    Test calculating debt into 4 buckets: 0-30, 31-60, 61-90, >90 days.
    """
    res = compute_debt_ledger_data(db=db_session)
    assert len(res.items) == 2

    d1_item = next(x for x in res.items if x.dealer_id == 1)
    # Total receivable: 10M + 20M + 15M + 5M + 8M = 58M
    assert d1_item.total_receivable == 58000000.0
    # Total paid: 8M
    assert d1_item.total_paid == 8000000.0
    # Remaining debt: 50M
    assert d1_item.remaining_debt == 50000000.0

    # Ageing buckets:
    assert d1_item.ageing.bucket_0_30 == 10000000.0
    assert d1_item.ageing.bucket_31_60 == 20000000.0
    assert d1_item.ageing.bucket_61_90 == 15000000.0
    assert d1_item.ageing.bucket_over_90 == 5000000.0
    assert d1_item.is_overdue is True
    assert d1_item.oldest_debt_days >= 120

    # Dealer 2: only bucket 0-30
    d2_item = next(x for x in res.items if x.dealer_id == 2)
    assert d2_item.remaining_debt == 5000000.0
    assert d2_item.ageing.bucket_0_30 == 5000000.0
    assert d2_item.ageing.bucket_31_60 == 0.0
    assert d2_item.ageing.bucket_61_90 == 0.0
    assert d2_item.ageing.bucket_over_90 == 0.0
    assert d2_item.is_overdue is False

    # Check Summary totals
    assert res.summary.total_receivable == 63000000.0
    assert res.summary.total_paid == 8000000.0
    assert res.summary.total_remaining_debt == 55000000.0
    assert res.summary.total_bucket_0_30 == 15000000.0
    assert res.summary.total_bucket_31_60 == 20000000.0
    assert res.summary.total_bucket_61_90 == 15000000.0
    assert res.summary.total_bucket_over_90 == 5000000.0


def test_overdue_filter(db_session):
    """
    Test filtering only overdue dealers.
    """
    res = compute_debt_ledger_data(db=db_session, overdue_only=True)
    assert len(res.items) == 1
    assert res.items[0].dealer_id == 1
    assert res.items[0].is_overdue is True


def test_region_and_search_filters(db_session):
    """
    Test filtering by region and keyword search.
    """
    # Filter region
    res_north = compute_debt_ledger_data(db=db_session, region="Miền Bắc")
    assert len(res_north.items) == 1
    assert res_north.items[0].dealer_code == "DL001"

    # Search keyword
    res_search = compute_debt_ledger_data(db=db_session, search="Miền Nam")
    assert len(res_search.items) == 1
    assert res_search.items[0].dealer_code == "DL002"


def test_excel_export_generation(db_session):
    """
    Test generating Excel report.
    """
    res = compute_debt_ledger_data(db=db_session)
    excel_bytes = export_debt_ledger_excel(res)
    assert isinstance(excel_bytes, bytes)
    assert len(excel_bytes) > 1000  # Valid non-empty workbook
    assert excel_bytes.startswith(b"PK")  # Standard zip/xlsx magic bytes


def test_ageing_bucket_filter(db_session):
    """
    Test filtering by ageing_bucket (0-30, 31-60, 61-90, over_90, has_debt, settled, overdue).
    """
    # 0-30: cả 2 đại lý đều có nợ 0-30 ngày (DL1 có 10M, DL2 có 5M)
    res_0_30 = compute_debt_ledger_data(db=db_session, ageing_bucket="0-30")
    assert len(res_0_30.items) == 2

    # 31-60: chỉ DL1 có (20M)
    res_31_60 = compute_debt_ledger_data(db=db_session, ageing_bucket="31-60")
    assert len(res_31_60.items) == 1
    assert res_31_60.items[0].dealer_code == "DL001"

    # >90: chỉ DL1 có (5M)
    res_over_90 = compute_debt_ledger_data(db=db_session, ageing_bucket="over_90")
    assert len(res_over_90.items) == 1
    assert res_over_90.items[0].dealer_code == "DL001"

    # settled: không có đại lý nào đã tất toán (cả 2 đều còn nợ)
    res_settled = compute_debt_ledger_data(db=db_session, ageing_bucket="settled")
    assert len(res_settled.items) == 0

    # has_debt: cả 2 đều còn nợ
    res_has_debt = compute_debt_ledger_data(db=db_session, ageing_bucket="has_debt")
    assert len(res_has_debt.items) == 2

