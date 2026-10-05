import sqlite3
import os

def migrate():
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'quanlybanhang.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE price_book_items ADD COLUMN sale_price FLOAT DEFAULT 0.0")
        print("Added sale_price column.")
    except Exception as e:
        print("sale_price might already exist:", e)
        
    try:
        cursor.execute("ALTER TABLE price_book_items ADD COLUMN floor_price FLOAT DEFAULT 0.0")
        print("Added floor_price column.")
    except Exception as e:
        print("floor_price might already exist:", e)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()
