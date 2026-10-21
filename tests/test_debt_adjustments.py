# backend/tests/test_debt_adjustments.py
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import SessionLocal, engine, Base
from app.core.security import create_access_token
from app.models.entities import UserEntity, DealerEntity, OrderEntity, OrderItemEntity, ProductEntity
from app.models.invoice import InvoiceEntity
from app.models.debt_adjustment import (
    DebtAdjustmentEntity,
    DebtAdjustmentItemEntity,
    ReturnOrderEntity,
    ReturnOrderItemEntity,
)


@pytest.fixture(scope="module")
def client():
    # Ensure tables are created
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def accountant_token():
    return create_access_token("ketoan", "accountant")


@pytest.fixture
def sales_token():
    return create_access_token("sales", "sales")


@pytest.fixture
def setup_test_data(db: Session):
    now = datetime.now(timezone.utc)
    # 1. Product with current price 180,000 (higher than original sold price 150,000)
    prod = db.query(ProductEntity).filter(ProductEntity.id == 999).first()
    if not prod:
        prod = ProductEntity(
            id=999,
            code="SP-TEST-999",
            name="Sản Phẩm Test Giá Gốc",
            sell_price=180000.0,  # Current listed price has increased!
            base_unit="Cái",
            stock=100,
        )
        db.add(prod)
    else:
        prod.sell_price = 180000.0


    # 2. Dealer
    dealer = db.query(DealerEntity).filter(DealerEntity.id == 999).first()
    if not dealer:
        dealer = DealerEntity(
            id=999,
            code="DL-TEST-999",
            name="Đại Lý Test Trả Hàng",
            credit_limit=100000000.0,
            status="Đang hoạt động",
        )
        db.add(dealer)
    else:
        dealer.credit_limit = 100000000.0

    import uuid
    uid = uuid.uuid4().hex[:8]
    # 3. Order sold at ORIGINAL price 150,000 (quantity = 10 -> total = 1,500,000)
    order_code = f"ORD-TEST-{uid}"
    order = OrderEntity(
        order_code=order_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        created_by="sales_test",
        total_amount=1500000.0,
        status="CONFIRMED",
        created_at=now,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    ord_item = OrderItemEntity(
        order_id=order.id,
        product_id=prod.id,
        sku_id=prod.code,
        product_name=prod.name,
        unit_name="Cái",
        quantity=10,
        unit_price=150000.0,  # Sold at 150,000
    )
    db.add(ord_item)

    # 4. Invoice for this order
    inv_code = f"HD-{order_code}"
    invoice = InvoiceEntity(
        invoice_code=inv_code,
        order_id=order.id,
        order_code=order.order_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        issue_date=now,
        due_date=now,
        total_amount=1500000.0,
        paid_amount=0.0,
        remaining_debt=1500000.0,
        status="UNPAID",
        items_json=f'[{{"product_id": {prod.id}, "product_code": "{prod.code}", "product_name": "{prod.name}", "quantity": 10, "unit_name": "Cái", "unit_price": 150000.0, "total": 1500000.0}}]',
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    # 5. Return Order: 3 items returned (3 * 150,000 = 450,000 reduction expected)
    ret_code = f"TH-TEST-{uid}"
    ret_order = ReturnOrderEntity(
        return_code=ret_code,
        order_id=order.id,
        order_code=order.order_code,
        original_invoice_id=invoice.id,
        original_invoice_code=invoice.invoice_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        warehouse_id="WH01",
        warehouse_name="Kho Tổng Hà Nội",
        status="CONFIRMED",
        reason="Lỗi bao bì khi giao hàng",
        subtotal_amount=450000.0,
        tax_amount=0.0,
        total_amount=450000.0,
        confirmed_at=now,
    )
    db.add(ret_order)
    db.commit()
    db.refresh(ret_order)

    ret_item = ReturnOrderItemEntity(
        return_order_id=ret_order.id,
        product_id=prod.id,
        product_code=prod.code,
        product_name=prod.name,
        unit_name="Cái",
        quantity=3,
        unit_price=150000.0,
        line_total=450000.0,
        reason="Móp hộp sản phẩm",
    )
    db.add(ret_item)
    db.commit()

    return {
        "product": prod,
        "dealer": dealer,
        "order": order,
        "invoice": invoice,
        "return_order": ret_order,
    }


def test_rbac_permission_for_debt_adjustments(client: TestClient, accountant_token: str, sales_token: str):
    headers_sales = {"Authorization": f"Bearer {sales_token}"}
    headers_accountant = {"Authorization": f"Bearer {accountant_token}"}

    # Sales cannot create or view pending returns
    res_pending = client.get("/api/v1/debt-adjustments/pending-returns", headers=headers_sales)
    assert res_pending.status_code == 403

    # Accountant can view pending returns
    res_accountant = client.get("/api/v1/debt-adjustments/pending-returns", headers=headers_accountant)
    assert res_accountant.status_code == 200
    assert isinstance(res_accountant.json(), list)


def test_preview_from_return_maps_original_price_not_current_price(
    client: TestClient,
    accountant_token: str,
    setup_test_data: dict,
):
    """
    CRITICAL BUSINESS RULE:
    Đơn giá xuất bán gốc (150,000) BẮT BUỘC phải được áp dụng,
    dù giá niêm yết hiện hành của sản phẩm đã tăng lên 180,000!
    """
    headers = {"Authorization": f"Bearer {accountant_token}"}
    ret_id = setup_test_data["return_order"].id

    res = client.get(f"/api/v1/debt-adjustments/preview/{ret_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()

    assert data["return_order_id"] == ret_id
    assert data["original_invoice_code"] == setup_test_data["invoice"].invoice_code
    assert len(data["items"]) == 1
    item = data["items"][0]

    # Verify original price 150,000, NOT 180,000!
    assert item["original_unit_price"] == 150000.0
    assert item["return_quantity"] == 3
    assert item["line_total"] == 450000.0
    assert data["subtotal_amount"] == 450000.0
    assert data["total_adjusted_amount"] == 450000.0

    # Test alias endpoint as specified in workflow diagram
    res_alias = client.get(f"/api/v1/debt-adjustments/preview-from-return/{ret_id}", headers=headers)
    assert res_alias.status_code == 200
    assert res_alias.json()["total_adjusted_amount"] == 450000.0


def test_post_debt_adjustment_acid_transaction(
    client: TestClient,
    accountant_token: str,
    setup_test_data: dict,
    db: Session,
):
    headers = {"Authorization": f"Bearer {accountant_token}"}
    ret = setup_test_data["return_order"]
    inv = setup_test_data["invoice"]

    payload = {
        "return_order_id": ret.id,
        "reason": "Hàng trả lại do móp méo vỏ hộp khi vận chuyển",
        "note": "Kế toán công nợ đã kiểm tra hóa đơn gốc",
    }

    res = client.post("/api/v1/debt-adjustments", json=payload, headers=headers)
    assert res.status_code == 201
    created_adj = res.json()

    assert created_adj["adjustment_code"].startswith("DCG-")
    assert created_adj["status"] == "POSTED"
    assert created_adj["total_adjusted_amount"] == 450000.0
    assert len(created_adj["items"]) == 1
    assert created_adj["items"][0]["original_unit_price"] == 150000.0

    # 1. Verify Immutability of original invoice total_amount
    db.refresh(inv)
    assert inv.total_amount == 1500000.0  # TOTAL AMOUNT UNCHANGED!
    # remaining_debt reduced: 1,500,000 - 450,000 = 1,050,000
    assert inv.remaining_debt == 1050000.0

    # 2. Verify Idempotency: Attempting to create again for the same return order MUST fail
    res_duplicate = client.post("/api/v1/debt-adjustments", json=payload, headers=headers)
    assert res_duplicate.status_code == 400
    assert "đã được lập chứng từ" in res_duplicate.json()["detail"]

    # 3. Verify Debt Statement S31-DN includes DEBT_ADJUSTMENT in Phát sinh Có
    dealer_id = setup_test_data["dealer"].id
    res_statement = client.get(f"/api/v1/dealers/{dealer_id}/debt-statement", headers=headers)
    assert res_statement.status_code == 200
    st_data = res_statement.json()

    adj_txs = [tx for tx in st_data["transactions"] if tx["doc_type"] == "DEBT_ADJUSTMENT"]
    assert len(adj_txs) >= 1
    target_tx = next((tx for tx in adj_txs if tx["doc_code"] == created_adj["adjustment_code"]), None)
    assert target_tx is not None
    assert target_tx["decrease_amount"] == 450000.0

    # 4. Verify Export PDF endpoint returns formatted accounting data
    adj_id = created_adj["id"]
    res_pdf = client.get(f"/api/v1/debt-adjustments/{adj_id}/export-pdf", headers=headers)
    assert res_pdf.status_code == 200
    pdf_data = res_pdf.json()
    assert pdf_data["adjustment_code"] == created_adj["adjustment_code"]
    assert pdf_data["company"]["name"]
    assert pdf_data["financials"]["total_adjusted_amount"] == 450000.0


def test_debt_adjustment_full_settlement_updates_invoice_to_paid(
    client: TestClient,
    accountant_token: str,
    setup_test_data: dict,
    db: Session,
):
    """
    CRITICAL BUSINESS RULE:
    Nếu số nợ còn lại sau điều chỉnh bằng 0, chuyển trạng thái hóa đơn sang PAID (kèm cờ ghi chú đã đối trừ hàng trả).
    """
    import uuid
    headers = {"Authorization": f"Bearer {accountant_token}"}
    now = datetime.now(timezone.utc)
    uid = uuid.uuid4().hex[:8]

    dealer = setup_test_data["dealer"]
    prod = setup_test_data["product"]

    order = OrderEntity(
        order_code=f"ORD-FULL-{uid}",
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        created_by="sales_test",
        total_amount=300000.0,
        status="CONFIRMED",
        created_at=now,
    )
    db.add(order)
    db.commit()

    inv = InvoiceEntity(
        invoice_code=f"HD-ORD-FULL-{uid}",
        order_id=order.id,
        order_code=order.order_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        issue_date=now,
        due_date=now,
        total_amount=300000.0,
        paid_amount=0.0,
        remaining_debt=300000.0,
        status="UNPAID",
        items_json=f'[{{"product_id": {prod.id}, "product_code": "{prod.code}", "product_name": "{prod.name}", "quantity": 2, "unit_name": "Cái", "unit_price": 150000.0, "total": 300000.0}}]',
    )
    db.add(inv)
    db.commit()

    ret = ReturnOrderEntity(
        return_code=f"TH-FULL-{uid}",
        order_id=order.id,
        order_code=order.order_code,
        original_invoice_id=inv.id,
        original_invoice_code=inv.invoice_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        warehouse_id="WH01",
        warehouse_name="Kho Tổng Hà Nội",
        status="CONFIRMED",
        reason="Đại lý trả toàn bộ đơn",
        subtotal_amount=300000.0,
        tax_amount=0.0,
        total_amount=300000.0,
        confirmed_at=now,
        items=[
            ReturnOrderItemEntity(
                product_id=prod.id,
                product_code=prod.code,
                product_name=prod.name,
                unit_name="Cái",
                quantity=2,
                unit_price=150000.0,
                line_total=300000.0,
            )
        ],
    )
    db.add(ret)
    db.commit()

    res = client.post("/api/v1/debt-adjustments", json={"return_order_id": ret.id, "reason": "Trả hàng toàn bộ"}, headers=headers)
    assert res.status_code == 201

    db.refresh(inv)
    assert inv.remaining_debt == 0.0
    assert inv.status == "PAID"
    assert "đối trừ" in inv.note or "giảm trừ" in inv.note


def test_debt_adjustment_quantity_exceeds_sold_fails(
    client: TestClient,
    accountant_token: str,
    setup_test_data: dict,
    db: Session,
):
    """
    CRITICAL BUSINESS RULE:
    Tổng số lượng hàng trả không vượt quá số lượng hàng đã xuất bán ban đầu.
    """
    import uuid
    headers = {"Authorization": f"Bearer {accountant_token}"}
    now = datetime.now(timezone.utc)
    uid = uuid.uuid4().hex[:8]

    dealer = setup_test_data["dealer"]
    prod = setup_test_data["product"]

    order = OrderEntity(
        order_code=f"ORD-EXCEED-{uid}",
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        created_by="sales_test",
        total_amount=150000.0,
        status="CONFIRMED",
        created_at=now,
    )
    db.add(order)
    db.commit()

    inv = InvoiceEntity(
        invoice_code=f"HD-ORD-EXCEED-{uid}",
        order_id=order.id,
        order_code=order.order_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        issue_date=now,
        due_date=now,
        total_amount=150000.0,
        paid_amount=0.0,
        remaining_debt=150000.0,
        status="UNPAID",
        items_json=f'[{{"product_id": {prod.id}, "product_code": "{prod.code}", "product_name": "{prod.name}", "quantity": 1, "unit_name": "Cái", "unit_price": 150000.0, "total": 150000.0}}]',
    )
    db.add(inv)
    db.commit()

    ret = ReturnOrderEntity(
        return_code=f"TH-EXCEED-{uid}",
        order_id=order.id,
        order_code=order.order_code,
        original_invoice_id=inv.id,
        original_invoice_code=inv.invoice_code,
        dealer_id=dealer.id,
        dealer_name=dealer.name,
        status="CONFIRMED",
        reason="Trả lố số lượng",
        subtotal_amount=750000.0,
        tax_amount=0.0,
        total_amount=750000.0,
        confirmed_at=now,
        items=[
            ReturnOrderItemEntity(
                product_id=prod.id,
                product_code=prod.code,
                product_name=prod.name,
                unit_name="Cái",
                quantity=5,  # Exceeds 1 sold!
                unit_price=150000.0,
                line_total=750000.0,
            )
        ],
    )
    db.add(ret)
    db.commit()

    res = client.post("/api/v1/debt-adjustments", json={"return_order_id": ret.id, "reason": "Cố tình trả lố"}, headers=headers)
    assert res.status_code == 400
    assert "vượt quá số lượng xuất bán" in res.json()["detail"]


def test_list_debt_adjustments_filtering_and_pagination(
    client: TestClient,
    accountant_token: str,
):
    headers = {"Authorization": f"Bearer {accountant_token}"}
    res = client.get("/api/v1/debt-adjustments?page=1&page_size=10", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "total" in data
    assert "items" in data
    assert isinstance(data["items"], list)
