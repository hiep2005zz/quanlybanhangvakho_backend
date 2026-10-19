# backend/tests/test_return_orders.py
import pytest
from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.models.entities import OrderEntity, OrderItemEntity, ProductEntity, DealerEntity, WarehouseStockEntity
from app.models.goods_receipt import WarehouseEntity, ProductBatchEntity, InventoryLedgerEntity
from app.models.return_order import ReturnOrderEntity, ReturnOrderItemEntity
from app.schemas.auth import UserResponse
from app.schemas.return_order import ReturnOrderCreate, ReturnOrderItemCreate
from app.services.return_order_service import (
    create_return_order,
    confirm_return_receipt,
    cancel_return_order,
    get_order_returnable_info,
)
from fastapi import HTTPException


def test_return_order_full_flow():
    db = SessionLocal()
    try:
        # 1. Chuẩn bị dữ liệu mẫu
        dealer = db.query(DealerEntity).first()
        if not dealer:
            dealer = DealerEntity(code="DL_TEST_RMA", name="Đại Lý Test RMA")
            db.add(dealer)
            db.commit()

        # Tạo sản phẩm thường và sản phẩm quản lý lô
        prod_normal = ProductEntity(
            code="SP_TEST_NORMAL_RMA",
            name="Sản Phẩm RMA Thường",
            stock=100,
            sell_price=200000.0,
            is_batch_managed=False,
            base_unit="Cái",
        )
        prod_batch = ProductEntity(
            code="SP_TEST_BATCH_RMA",
            name="Sản Phẩm RMA Quản Lý Lô",
            stock=50,
            sell_price=500000.0,
            is_batch_managed=True,
            base_unit="Hộp",
        )
        db.add_all([prod_normal, prod_batch])
        db.commit()

        # Tạo lô hàng mẫu
        wh_good = db.query(WarehouseEntity).filter(WarehouseEntity.code == "KHO_HN").first()
        batch = ProductBatchEntity(
            product_id=prod_batch.id,
            warehouse_id=wh_good.id if wh_good else 1,
            batch_number="LOT-2026-RMA",
            quantity=50.0,
        )
        db.add(batch)
        db.commit()

        # Tạo đơn hàng đã giao
        order = OrderEntity(
            order_code=f"ORD_RMA_{int(datetime.now().timestamp())}",
            dealer_id=dealer.id,
            dealer_name=dealer.name,
            created_by="sales_tester",
            status="DELIVERED",
            total_amount=1600000.0,
        )
        db.add(order)
        db.commit()

        order_item_1 = OrderItemEntity(
            order_id=order.id,
            product_id=prod_normal.id,
            product_name=prod_normal.name,
            quantity=5,
            unit_price=200000.0,
            unit_name="Cái",
        )
        order_item_2 = OrderItemEntity(
            order_id=order.id,
            product_id=prod_batch.id,
            product_name=prod_batch.name,
            quantity=2,
            unit_price=500000.0,
            unit_name="Hộp",
            selected_batch_id=batch.id,
        )
        db.add_all([order_item_1, order_item_2])
        db.commit()

        # 2. Test Quy tắc 1 & lấy thông tin dòng hàng có thể trả
        info = get_order_returnable_info(db, order.id)
        assert info.order_id == order.id
        assert len(info.items) == 2
        assert info.items[0].remaining_quantity == 5
        assert info.items[1].remaining_quantity == 2
        assert info.items[1].is_batch_managed is True

        sales_user = UserResponse(
            id=991,
            username="sales_user",
            full_name="Nhân Viên Kinh Doanh",
            email="sales@test.com",
            role="sales",
            roles=["sales"],
            is_active=True,
        )

        # 3. Test Quy tắc 2: Kiểm tra vượt quá số lượng cho phép
        invalid_data = ReturnOrderCreate(
            order_id=order.id,
            items=[
                ReturnOrderItemCreate(
                    order_item_id=order_item_1.id,
                    product_id=prod_normal.id,
                    return_quantity=6,  # Vượt quá 5!
                    return_reason="Đại lý trả lại hàng tốt",
                )
            ]
        )
        with pytest.raises(HTTPException) as exc_info:
            create_return_order(db, invalid_data, sales_user)
        assert exc_info.value.status_code == 400
        assert "vượt quá" in str(exc_info.value.detail)

        # 4. Test Quy tắc 3: Bắt buộc chọn Lô với sản phẩm có quản lý lô
        invalid_batch_data = ReturnOrderCreate(
            order_id=order.id,
            items=[
                ReturnOrderItemCreate(
                    order_item_id=order_item_2.id,
                    product_id=prod_batch.id,
                    return_quantity=1,
                    return_reason="Hàng lỗi / hỏng bao bì",
                    batch_number=None,  # Thiếu số lô!
                    batch_id=None,
                )
            ]
        )
        # Note: If order had selected_batch_id, service auto-populates it from order item. Let's test with custom item without order_item_id
        no_batch_data = ReturnOrderCreate(
            order_id=order.id,
            items=[
                ReturnOrderItemCreate(
                    order_item_id=None,
                    product_id=prod_batch.id,
                    return_quantity=1,
                    return_reason="Hàng lỗi / hỏng bao bì",
                    batch_number=None,
                    batch_id=None,
                )
            ]
        )
        with pytest.raises(HTTPException) as exc_batch:
            create_return_order(db, no_batch_data, sales_user)
        assert exc_batch.value.status_code == 400
        assert "Lô" in str(exc_batch.value.detail)

        # 5. Test Tạo phiếu trả hàng hợp lệ (Quy tắc 1, 2, 3)
        # Dòng 1: Hàng lỗi -> điều hướng Kho Lỗi KHO_LOI
        # Dòng 2: Hàng tốt -> điều hướng Kho thường KHO_HN
        valid_data = ReturnOrderCreate(
            order_id=order.id,
            items=[
                ReturnOrderItemCreate(
                    order_item_id=order_item_1.id,
                    product_id=prod_normal.id,
                    return_quantity=2,
                    return_reason="Hàng lỗi / móp méo vỏ hộp",
                ),
                ReturnOrderItemCreate(
                    order_item_id=order_item_2.id,
                    product_id=prod_batch.id,
                    return_quantity=1,
                    return_reason="Đại lý không có nhu cầu bán tiếp",
                    batch_number="LOT-2026-RMA",
                ),
            ],
            note="Khách hàng gửi trả 2 món",
        )

        ro = create_return_order(db, valid_data, sales_user)
        assert ro.status == "PENDING_WAREHOUSE"
        assert ro.total_items == 2
        assert ro.total_quantity == 3
        # 2 * 200,000 + 1 * 500,000 = 900,000
        assert ro.total_refund_amount == 900000.0

        # Kiểm tra điều hướng kho:
        item_damaged = [i for i in ro.items if i.product_id == prod_normal.id][0]
        assert item_damaged.is_damaged is True
        assert item_damaged.target_warehouse_code == "KHO_LOI"

        item_good = [i for i in ro.items if i.product_id == prod_batch.id][0]
        assert item_good.is_damaged is False

        # QUY TẮC 4: Ở bước này, TUYỆT ĐỐI CHƯA CỘNG TỒN KHO!
        db.refresh(prod_normal)
        db.refresh(prod_batch)
        assert prod_normal.stock == 100  # Chưa thay đổi
        assert prod_batch.stock == 50    # Chưa thay đổi

        # Test tiếp kiểm tra giới hạn lần 2: Đã lập phiếu 2 cái dòng 1, giờ chỉ còn tối đa 3 cái!
        over_subsequent = ReturnOrderCreate(
            order_id=order.id,
            items=[
                ReturnOrderItemCreate(
                    order_item_id=order_item_1.id,
                    product_id=prod_normal.id,
                    return_quantity=4,  # 2 + 4 = 6 > 5!
                    return_reason="Đại lý trả tiếp",
                )
            ]
        )
        with pytest.raises(HTTPException):
            create_return_order(db, over_subsequent, sales_user)

        # 6. Test Quy tắc 4: Quản lý kho xác nhận nhận hàng (Confirm Receipt)
        wh_user = UserResponse(
            id=992,
            username="warehouse_mgr",
            full_name="Quản Lý Kho Tổng",
            email="wh@test.com",
            role="warehouse_manager",
            roles=["warehouse_manager"],
            is_active=True,
        )

        ro_confirmed = confirm_return_receipt(db, ro.id, wh_user, note="Đã kiểm đếm đủ")
        assert ro_confirmed.status == "COMPLETED"
        assert ro_confirmed.confirmed_by == "Quản Lý Kho Tổng"
        assert ro_confirmed.confirmed_at is not None

        # Kiểm tra tồn kho đã được cộng sau khi xác nhận
        db.refresh(prod_normal)
        db.refresh(prod_batch)
        assert prod_normal.stock == 102  # 100 + 2
        assert prod_batch.stock == 51    # 50 + 1

        # Kiểm tra tồn kho tại Kho Lỗi (KHO_LOI)
        ws_damaged = db.query(WarehouseStockEntity).filter(
            WarehouseStockEntity.product_id == prod_normal.id,
            WarehouseStockEntity.warehouse_id == "KHO_LOI"
        ).first()
        assert ws_damaged is not None
        assert ws_damaged.actual_stock == 2

        # Kiểm tra tồn Lô đã được cộng
        batch_updated = db.query(ProductBatchEntity).filter(
            ProductBatchEntity.product_id == prod_batch.id,
            ProductBatchEntity.batch_number == "LOT-2026-RMA"
        ).first()
        assert batch_updated.quantity >= 51.0

        # Kiểm tra thẻ kho (InventoryLedgerEntity)
        ledger_entries = db.query(InventoryLedgerEntity).filter(
            InventoryLedgerEntity.reference_code == ro.code
        ).all()
        assert len(ledger_entries) == 2

        print("TẤT CẢ 4 QUY TẮC NGHIỆP VỤ ĐÃ VƯỢT QUA TEST THÀNH CÔNG RỰC RỠ!")

    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    test_return_order_full_flow()
