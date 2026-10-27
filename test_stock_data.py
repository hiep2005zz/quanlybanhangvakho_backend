import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.db.init_db import SessionLocal
from app.services.stock_card_service import get_stock_card_for_product
from app.models.entities import ProductEntity, InventoryTransactionEntity, WarehouseStockEntity
from app.models.goods_receipt import GoodsReceiptNoteItemEntity, InventoryLedgerEntity, WarehouseEntity

db = SessionLocal()

# Check warehouses table
whs = db.query(WarehouseEntity).all()
print(f"WarehouseEntity count: {len(whs)}")
for w in whs:
    print(f"  wh: id={w.id}, name={w.name}, is_active={getattr(w, 'is_active', None)}")

p = db.query(ProductEntity).filter(ProductEntity.id == 4).first()
if p:
    print(f"Product 4: id={p.id}, code={p.code}, stock={p.stock}")

res = get_stock_card_for_product(db, 4)
print(f"Warehouses in response: {len(res.warehouses)}")
for w in res.warehouses:
    print(f"  - {w.warehouse_id}: {w.warehouse_name}")

print(f"Entries in response: {len(res.entries)}")
for e in res.entries[:5]:
    print(f"  Entry: {e.created_at} | {e.transaction_type} | {e.reference_code} | qty={e.quantity} | bal={e.balance_stock}")

print(f"Summary: {res.summary.dict()}")

# Check which products have entries in any stock table
all_tx_products = db.query(InventoryTransactionEntity.product_id).distinct().all()
print(f"Products with InventoryTransactionEntity: {[x[0] for x in all_tx_products]}")

all_ledger_products = db.query(InventoryLedgerEntity.product_id).distinct().all()
print(f"Products with InventoryLedgerEntity: {[x[0] for x in all_ledger_products]}")

all_grn_products = db.query(GoodsReceiptNoteItemEntity.product_id).distinct().all()
print(f"Products with GoodsReceiptNoteItemEntity: {[x[0] for x in all_grn_products]}")

