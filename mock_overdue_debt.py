"""
Tiện ích giả lập khoản nợ quá hạn (>30 ngày) cho Đại lý để kiểm tra Tiêu chí 3.
Sử dụng:
  - Bật nợ quá hạn 35 ngày: python mock_overdue_debt.py enable --dealer-id 1 --days 35
  - Tắt nợ quá hạn (hoàn tác): python mock_overdue_debt.py disable --dealer-id 1
  - Kiểm tra trạng thái:      python mock_overdue_debt.py status --dealer-id 1
"""
import sys
import argparse
from datetime import datetime, timezone, timedelta

# Dam bao in tieng Viet tren Windows console khong loi encode
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
from app.core.database import SessionLocal
from app.models.entities import OrderEntity
from app.models.dealer import DEALERS_DB, load_dealers_db

MOCK_ORDER_CODE = "ORD-MOCK-OVERDUE"

def enable_overdue(dealer_id: int, days_old: int = 35, amount: float = 15000000.0):
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    dealer_name = dealer.name if dealer else f"Đại lý #{dealer_id}"
    
    past_date = datetime.now(timezone.utc) - timedelta(days=days_old)
    
    with SessionLocal() as db:
        # Xóa mock cũ nếu có
        db.query(OrderEntity).filter(OrderEntity.order_code == MOCK_ORDER_CODE).delete()
        
        # Thêm đơn hàng giả lập nợ quá hạn
        mock_order = OrderEntity(
            order_code=MOCK_ORDER_CODE,
            dealer_id=dealer_id,
            dealer_name=dealer_name,
            created_by="system_mock",
            total_amount=amount,
            status="CONFIRMED",
            created_at=past_date,
            note=f"Đơn hàng giả lập nợ quá hạn {days_old} ngày để test Tiêu chí 3",
        )
        db.add(mock_order)
        db.commit()
    
    print(f"✅ ĐÃ BẬT GIẢ LẬP NỢ QUÁ HẠN:")
    print(f"   - Đại lý ID: {dealer_id} ({dealer_name})")
    print(f"   - Mã đơn: {MOCK_ORDER_CODE}")
    print(f"   - Số tiền nợ: {amount:,.0f} VNĐ")
    print(f"   - Ngày tạo đơn: {past_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"   - Tuổi nợ thực tế: {days_old} ngày (> 30 ngày cho phép)")
    print(f"👉 Bây giờ bạn có thể mở web http://localhost:5173 và bấm 'Tạo đơn' cho đại lý này để xem hệ thống chặn!")

def disable_overdue(dealer_id: int):
    with SessionLocal() as db:
        deleted = db.query(OrderEntity).filter(
            OrderEntity.order_code == MOCK_ORDER_CODE,
            OrderEntity.dealer_id == dealer_id
        ).delete()
        db.commit()
    
    if deleted:
        print(f"✅ ĐÃ TẮT GIẢ LẬP NỢ QUÁ HẠN cho Đại lý ID {dealer_id}. Đại lý trở lại trạng thái bình thường.")
    else:
        print(f"ℹ️ Không có bản ghi giả lập nào đang bật cho Đại lý ID {dealer_id}.")

def check_status(dealer_id: int):
    from app.api.v1.endpoints.orders import ORDERS_DB, calculate_dealer_debt_summary
    load_dealers_db()
    dealer = DEALERS_DB.get(dealer_id)
    if not dealer:
        print(f"❌ Không tìm thấy đại lý ID {dealer_id}")
        return
    summary = calculate_dealer_debt_summary(dealer, ORDERS_DB)
    print(f"📊 THÔNG TIN CÔNG NỢ ĐẠI LÝ [{dealer.name}]:")
    print(f"   - Công nợ hiện tại: {summary['current_debt']:,.0f} VNĐ")
    print(f"   - Hạn mức nợ: {summary['credit_limit']:,.0f} VNĐ")
    print(f"   - Tuổi nợ tối đa: {summary['max_debt_age']} ngày")
    print(f"   - Số ngày cho phép: {summary['overdue_days_allowed']} ngày")
    print(f"   - Có nợ quá hạn không: {'🔴 CÓ (ĐANG BỊ CHẶN LÊN ĐƠN)' if summary['is_overdue'] else '🟢 KHÔNG (BÌNH THƯỜNG)'}")

def test_create_order(dealer_id: int):
    import urllib.request
    import json
    
    print(f"🚀 Đang gửi yêu cầu tạo đơn hàng cho đại lý ID {dealer_id} qua API...")
    # Login as sales
    req_login = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/auth/login",
        data=json.dumps({"username": "sales", "password": "123"}).encode(),
        headers={"Content-Type": "application/json"}
    )
    res_login = json.loads(urllib.request.urlopen(req_login).read().decode())
    token = res_login["access_token"]
    
    # Try creating order
    order_payload = {
        "dealer_id": dealer_id,
        "items": [{"product_id": 1, "quantity": 1, "price": 100000.0}],
    }
    req_order = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/orders",
        data=json.dumps(order_payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
    )
    try:
        urllib.request.urlopen(req_order)
        print("🟢 KẾT QUẢ: Tạo đơn THÀNH CÔNG (Không bị chặn).")
    except urllib.error.HTTPError as err:
        err_body = json.loads(err.read().decode())
        print(f"🔴 KẾT QUẢ: BỊ CHẶN TUYỆT ĐỐI (HTTP {err.code})!")
        print(f"   Thông báo từ máy chủ: \"{err_body.get('detail')}\"")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quản lý giả lập nợ quá hạn")
    parser.add_argument("action", choices=["enable", "disable", "status", "test"], help="Hành động: enable, disable, status, hoặc test")
    parser.add_argument("--dealer-id", type=int, default=1, help="ID của đại lý (mặc định 1: Sao Mai)")
    parser.add_argument("--days", type=int, default=35, help="Số ngày quá hạn giả lập (mặc định 35 ngày)")
    parser.add_argument("--amount", type=float, default=15000000.0, help="Số tiền nợ giả lập (mặc định 15,000,000 đ)")
    args = parser.parse_args()

    if args.action == "enable":
        enable_overdue(args.dealer_id, args.days, args.amount)
    elif args.action == "disable":
        disable_overdue(args.dealer_id)
    elif args.action == "status":
        check_status(args.dealer_id)
    elif args.action == "test":
        test_create_order(args.dealer_id)
