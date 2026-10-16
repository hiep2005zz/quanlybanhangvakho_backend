import sqlite3
import sys

conn = sqlite3.connect('quanlybanhang.db')
c = conn.cursor()

c.execute("SELECT id, code, name FROM products WHERE id BETWEEN 1 AND 20")
print("=== PRODUCTS 1..20 ===")
for row in c.fetchall():
    print(row)

c.execute("SELECT count(*) FROM products")
total = c.fetchone()[0]
print(f"Total products: {total}")

c.execute("SELECT count(*) FROM products WHERE code LIKE 'SP0%' OR code LIKE 'SP10000'")
mock_count = c.fetchone()[0]
print(f"Mock count (SP0... or SP10000): {mock_count}")

c.execute("SELECT id, code, name FROM products WHERE NOT (code LIKE 'SP0%' OR code LIKE 'SP10000')")
print("=== ORIGINAL / REAL PRODUCTS ===")
for row in c.fetchall():
    print(row)

conn.close()
