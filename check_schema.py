import sys
sys.stdout.reconfigure(encoding='utf-8')
from app.core.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    cols = db.execute(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_NAME IN ('dealers', 'products', 'users', 'categories', 'orders', 'inventory_transactions', 'audit_logs')
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """)).fetchall()
    for c in cols:
        print(f"{c[0]}.{c[1]}: {c[2]}({c[3]})")
finally:
    db.close()
