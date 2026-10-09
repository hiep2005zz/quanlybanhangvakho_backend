# -*- coding: utf-8 -*-
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from app.main import app

def test_dealer_api_endpoints():
    client = TestClient(app)
    
    # 1. Login
    login_res = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': '123'})
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    token = login_res.json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    print("[1] Login OK")

    # 2. Stats
    stats_res = client.get('/api/v1/dealers/stats', headers=headers)
    assert stats_res.status_code == 200, f"Stats failed: {stats_res.text}"
    stats = stats_res.json()
    assert 'total' in stats and 'active' in stats and 'stopped' in stats and 'with_transactions' in stats
    print(f"[2] Stats OK: {stats}")

    # 3. Search pagination
    search_res = client.get('/api/v1/dealers/search?page=1&page_size=5', headers=headers)
    assert search_res.status_code == 200
    s_data = search_res.json()
    assert len(s_data['items']) <= 5
    assert 'total' in s_data
    assert 'total_pages' in s_data
    print(f"[3] Pagination OK: {len(s_data['items'])} items, total {s_data['total']}")

    # 4. Search keyword
    kw_res = client.get('/api/v1/dealers/search?keyword=DL-001', headers=headers)
    assert kw_res.status_code == 200
    assert len(kw_res.json()['items']) >= 1
    print(f"[4] Search keyword OK: matched {len(kw_res.json()['items'])} dealer(s)")

    # 5. Filter by customer group
    group_res = client.get('/api/v1/dealers/search?customer_group=Đại lý cấp 1', headers=headers)
    assert group_res.status_code == 200
    for it in group_res.json()['items']:
        assert 'cấp 1' in it['customer_group'].lower() or 'cap_1' in it['customer_group'].lower()
    print(f"[5] Filter customer group OK: {len(group_res.json()['items'])} items")

    # 6. Transactions list
    from app.api.v1.endpoints.orders import ORDERS_DB
    ORDERS_DB[1] = {
        'id': 1,
        'order_code': 'DH-2026-0001',
        'dealer_id': 1,
        'dealer_name': 'Công Ty Cổ Phần Phân Phối Tổng Hợp Sao Mai Toàn Cầu',
        'created_by': 'sales',
        'total_amount': 35800000.0,
        'status': 'COMPLETED',
        'items': [{'product_id': 1, 'quantity': 10, 'price': 250000.0}]
    }
    tx_res = client.get('/api/v1/dealers/1/transactions', headers=headers)
    assert tx_res.status_code == 200
    tx_data = tx_res.json()
    assert 'transactions' in tx_data
    assert tx_data['total_transactions'] > 0
    print(f"[6] Dealer 1 transactions OK: {tx_data['total_transactions']} transactions")

    # 7. Safety check: Cannot delete dealer with transactions
    del_res = client.delete('/api/v1/dealers/1', headers=headers)
    assert del_res.status_code == 400
    assert 'giao dịch' in del_res.json()['detail'].lower()
    print(f"[7] Cannot delete dealer with transactions rule OK: 400 Bad Request with explanation")

    # 8. Unique dealer code check
    dup_res = client.post('/api/v1/dealers', json={
        'code': 'DL-001', # Already exists
        'name': 'Đại lý Trùng Mã',
        'customer_group': 'Đại lý cấp 1',
        'region': 'Miền Bắc',
    }, headers=headers)
    assert dup_res.status_code == 400
    assert 'mã' in dup_res.json()['detail'].lower()
    print(f"[8] Unique dealer code check OK: 400 Bad Request prevented duplicate")

    # 9. Applied Price Book Preview
    pb_res = client.get('/api/v1/dealers/price-book-preview?customer_group=Đại lý cấp 1', headers=headers)
    assert pb_res.status_code == 200
    print("[9] Price book preview OK")

    print("\nALL DEALER UNIT TESTS PASSED SUCCESSFULLY!")

if __name__ == '__main__':
    test_dealer_api_endpoints()
