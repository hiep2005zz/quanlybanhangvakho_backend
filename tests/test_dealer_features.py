# -*- coding: utf-8 -*-
import requests
import json
import sys

BASE_URL = 'http://127.0.0.1:8000/api/v1'

def run_tests():
    # 1. Login
    login_res = requests.post(f'{BASE_URL}/auth/login', json={'username': 'admin', 'password': '123'})
    assert login_res.status_code == 200, f'Login failed: {login_res.text}'
    token = login_res.json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    print('[TEST 1] Login successful')

    # 2. Stats
    stats_res = requests.get(f'{BASE_URL}/dealers/stats', headers=headers)
    assert stats_res.status_code == 200, f'Stats failed: {stats_res.text}'
    stats = stats_res.json()
    print('[TEST 2] Real stats loaded:', json.dumps(stats, ensure_ascii=False))
    assert 'total' in stats and 'active' in stats and 'stopped' in stats and 'with_transactions' in stats

    # 3. Search & Pagination
    search_res = requests.get(f'{BASE_URL}/dealers/search?page=1&page_size=5', headers=headers)
    assert search_res.status_code == 200, f'Search failed: {search_res.text}'
    data = search_res.json()
    dealers_cnt = len(data.get('dealers', []))
    total_cnt = data.get('total', 0)
    total_pages = data.get('total_pages', 0)
    print(f'[TEST 3] Search page 1 returned {dealers_cnt} items, total: {total_cnt}, pages: {total_pages}')

    # 4. Search by keyword
    kw_res = requests.get(f'{BASE_URL}/dealers/search?keyword=DL-001', headers=headers)
    assert kw_res.status_code == 200 and len(kw_res.json().get('dealers', [])) >= 1
    print('[TEST 4] Keyword search works for DL-001')

    # 5. Filter by has_transactions=true
    tx_res = requests.get(f'{BASE_URL}/dealers/search?has_transactions=true', headers=headers)
    assert tx_res.status_code == 200
    for d in tx_res.json().get('dealers', []):
        assert d['transaction_count'] > 0
    print(f'[TEST 5] Filter has_transactions=true returned {len(tx_res.json().get("dealers", []))} dealers, all have count > 0')

    # 6. Filter by has_transactions=false
    no_tx_res = requests.get(f'{BASE_URL}/dealers/search?has_transactions=false', headers=headers)
    assert no_tx_res.status_code == 200
    for d in no_tx_res.json().get('dealers', []):
        assert d['transaction_count'] == 0
    print(f'[TEST 6] Filter has_transactions=false returned {len(no_tx_res.json().get("dealers", []))} dealers, all have count == 0')

    # 7. Get transactions for a dealer with transactions
    tx_list_res = requests.get(f'{BASE_URL}/dealers/1/transactions', headers=headers)
    assert tx_list_res.status_code == 200
    tx_data = tx_list_res.json()
    print(f'[TEST 7] Dealer ID 1 has {tx_data.get("total_transactions")} transactions')

    # 8. Business Rule: Cannot delete dealer with transactions
    del_tx_res = requests.delete(f'{BASE_URL}/dealers/1', headers=headers)
    print(f'[TEST 8] Delete dealer with transactions status: {del_tx_res.status_code}')
    assert del_tx_res.status_code == 400
    print('   Detail:', del_tx_res.json().get('detail'))

    # 9. Business Rule: Unique code check on create
    dup_create = requests.post(f'{BASE_URL}/dealers/', headers=headers, json={
        'code': 'DL-001',
        'name': 'Trung ma',
        'customer_group': 'Đại lý Cấp 1',
        'region': 'Miền Bắc',
        'status': 'Đang hoạt động'
    })
    assert dup_create.status_code == 400
    print('[TEST 9] Unique dealer code check enforced (returned 400 for DL-001 duplicate)')

    # 10. Create dealer with 0 transactions, verify deletion allowed
    new_code = 'DL-TEST-DELETE'
    create_res = requests.post(f'{BASE_URL}/dealers/', headers=headers, json={
        'code': new_code,
        'name': 'Đại lý Test Xóa',
        'customer_group': 'Đại lý Cấp 2',
        'region': 'Miền Trung',
        'status': 'Chưa kích hoạt'
    })
    assert create_res.status_code in (200, 201), f'Create failed: {create_res.text}'
    created_id = create_res.json()['id']
    print(f'[TEST 10a] Created temporary dealer ID {created_id} with code {new_code}')

    del_res = requests.delete(f'{BASE_URL}/dealers/{created_id}', headers=headers)
    assert del_res.status_code == 200, f'Delete 0-tx dealer failed: {del_res.text}'
    print(f'[TEST 10b] Successfully deleted 0-transaction dealer ID {created_id}')

    # 11. Status update test: allows all 4 statuses
    for st in ['Tạm ngừng', 'Chưa kích hoạt', 'Ngừng giao dịch', 'Đang hoạt động']:
        patch_res = requests.patch(f'{BASE_URL}/dealers/1/status', headers=headers, json={'status': st})
        assert patch_res.status_code == 200, f'Status patch failed for {st}'
    print('[TEST 11] Status patch works seamlessly for all 4 statuses')

    print('\n=============================================')
    print('ALL 11 BACKEND API & BUSINESS RULE TESTS PASSED!')
    print('=============================================')

if __name__ == '__main__':
    run_tests()
