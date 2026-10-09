import sqlite3
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def migrate():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    db_paths = [
        os.path.join(base_dir, 'quanlybanhang.db'),
        os.path.join(base_dir, 'test_quanlybanhang.db'),
    ]

    for db_path in db_paths:
        if not os.path.exists(db_path):
            continue
        print(f"Running migration on {db_path}...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 1. Thêm cột overdue_days_allowed vào dealers
        try:
            cursor.execute("ALTER TABLE dealers ADD COLUMN overdue_days_allowed INTEGER DEFAULT 30")
            print("  Added overdue_days_allowed column to dealers.")
        except Exception as e:
            print("  dealers.overdue_days_allowed note:", e)

        # 2. Thêm cột requires_approval vào orders
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN requires_approval BOOLEAN DEFAULT 0")
            print("  Added requires_approval column to orders.")
        except Exception as e:
            print("  orders.requires_approval note:", e)

        # 3. Thêm cột approval_status vào orders
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN approval_status VARCHAR(50) DEFAULT 'NORMAL'")
            print("  Added approval_status column to orders.")
        except Exception as e:
            print("  orders.approval_status note:", e)

        # 4. Thêm cột approval_reason vào orders
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN approval_reason NVARCHAR(500) NULL")
            print("  Added approval_reason column to orders.")
        except Exception as e:
            print("  orders.approval_reason note:", e)

        conn.commit()
        conn.close()
        print(f"Migration completed on {db_path}.")

if __name__ == "__main__":
    migrate()
