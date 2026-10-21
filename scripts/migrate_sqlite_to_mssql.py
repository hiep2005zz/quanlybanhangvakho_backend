# scripts/migrate_sqlite_to_mssql.py
import sys
import os
import sqlite3
import pyodbc
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_DB_PATH = BASE_DIR / "quanlybanhang.db"

MSSQL_SERVER = os.getenv("MSSQL_SERVER", "localhost")
MSSQL_CONN_STR = os.getenv(
    "MSSQL_CONN_STR",
    (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={MSSQL_SERVER};"
        "DATABASE=quanlybanhang;"
        "Trusted_Connection=yes;"
    )
)

def parse_val(val, col_type):
    if val is None:
        return None
    col_type = col_type.lower()
    if col_type in ("datetime", "datetime2", "date", "smalldatetime"):
        if isinstance(val, str):
            val_clean = val.strip().replace("Z", "+00:00")
            if not val_clean:
                return None
            try:
                dt = datetime.fromisoformat(val_clean)
                return dt.replace(tzinfo=None)
            except Exception:
                for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(val_clean, fmt)
                    except Exception:
                        pass
                return None
        return val
    elif col_type == "bit":
        if isinstance(val, bool):
            return 1 if val else 0
        if isinstance(val, (int, float)):
            return 1 if val else 0
        if isinstance(val, str):
            return 1 if val.lower() in ("true", "1", "t", "yes", "y") else 0
        return None
    elif col_type in ("int", "bigint", "smallint", "tinyint"):
        if isinstance(val, str):
            val_clean = val.strip()
            if not val_clean:
                return None
            try:
                return int(float(val_clean))
            except Exception:
                return None
        elif isinstance(val, float):
            return int(val)
        return val
    elif col_type in ("float", "real", "decimal", "numeric"):
        if isinstance(val, str):
            val_clean = val.strip()
            if not val_clean:
                return None
            try:
                return float(val_clean)
            except Exception:
                return None
        return val
    return val

def migrate():
    print(f"Bắt đầu chuyển dữ liệu từ SQLite ({SQLITE_DB_PATH}) sang SQL Server...")
    
    if not SQLITE_DB_PATH.exists():
        print(f"❌ Không tìm thấy file SQLite: {SQLITE_DB_PATH}")
        return

    conn_lite = sqlite3.connect(str(SQLITE_DB_PATH))
    cur_lite = conn_lite.cursor()

    conn_sql = pyodbc.connect(MSSQL_CONN_STR, autocommit=False)
    cur_sql = conn_sql.cursor()

    # 1. Lấy thông tin cột và loại dữ liệu từ SQL Server
    cur_sql.execute("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE 
        FROM INFORMATION_SCHEMA.COLUMNS
    """)
    table_col_types = {}
    for t_name, c_name, d_type in cur_sql.fetchall():
        table_col_types.setdefault(t_name, {})[c_name] = d_type

    # 2. Lấy danh sách bảng có IDENTITY column
    cur_sql.execute("""
        SELECT t.name, c.name 
        FROM sys.tables t 
        JOIN sys.columns c ON t.object_id = c.object_id 
        WHERE c.is_identity = 1
    """)
    identity_tables = {r[0]: r[1] for r in cur_sql.fetchall()}

    # 3. Lấy danh sách bảng trong SQLite
    cur_lite.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [r[0] for r in cur_lite.fetchall()]

    # 4. Tạm thời vô hiệu hóa tất cả foreign key constraints
    print("Vô hiệu hóa toàn bộ ràng buộc khóa ngoại (Foreign Keys)...")
    cur_sql.execute('EXEC sp_MSforeachtable "ALTER TABLE ? NOCHECK CONSTRAINT all"')
    conn_sql.commit()

    # 5. Xóa dữ liệu cũ trong SQL Server để tránh trùng khóa chính
    print("Xóa dữ liệu cũ trên các bảng SQL Server...")
    for t in tables:
        if t in table_col_types:
            cur_sql.execute(f"DELETE FROM [{t}]")
    conn_sql.commit()
    print("Đã làm sạch dữ liệu cũ trên SQL Server.")

    # 6. Sao chép dữ liệu từng bảng
    total_migrated = {}
    for t in sorted(tables):
        if t not in table_col_types:
            print(f"⚠️ Bỏ qua bảng {t} (không tồn tại trong SQL Server)")
            continue

        cur_lite.execute(f"PRAGMA table_info([{t}])")
        lite_cols = [r[1] for r in cur_lite.fetchall()]
        
        # Chỉ lấy các cột có mặt ở cả hai bên
        common_cols = [c for c in lite_cols if c in table_col_types[t]]
        if not common_cols:
            continue

        cols_str = ", ".join(f"[{c}]" for c in common_cols)
        cur_lite.execute(f"SELECT {cols_str} FROM [{t}]")
        rows = cur_lite.fetchall()
        
        row_count = len(rows)
        if row_count == 0:
            print(f"ℹ️  Bảng {t}: 0 dòng")
            total_migrated[t] = 0
            continue

        has_identity = t in identity_tables and identity_tables[t] in common_cols

        if has_identity:
            cur_sql.execute(f"SET IDENTITY_INSERT [{t}] ON")

        placeholders = ", ".join("?" for _ in common_cols)
        insert_sql = f"INSERT INTO [{t}] ({cols_str}) VALUES ({placeholders})"

        # Convert values
        converted_rows = []
        for r in rows:
            new_r = []
            for val, c_name in zip(r, common_cols):
                c_type = table_col_types[t][c_name]
                parsed = parse_val(val, c_type)
                if t == "categories" and c_name == "code" and (not parsed or str(parsed).strip() == ""):
                    cat_id = r[common_cols.index("id")] if "id" in common_cols else 1
                    parsed = f"CAT{cat_id:04d}"
                new_r.append(parsed)
            converted_rows.append(tuple(new_r))

        # Batch insert
        cur_sql.fast_executemany = True
        batch_size = 1000
        for i in range(0, len(converted_rows), batch_size):
            batch = converted_rows[i:i + batch_size]
            cur_sql.executemany(insert_sql, batch)

        if has_identity:
            cur_sql.execute(f"SET IDENTITY_INSERT [{t}] OFF")

        conn_sql.commit()
        print(f"✅ Bảng {t}: {row_count} dòng")
        total_migrated[t] = row_count

    # 7. Kích hoạt lại toàn bộ foreign key constraints
    print("Kích hoạt lại ràng buộc khóa ngoại (Foreign Keys)...")
    cur_sql.execute('EXEC sp_MSforeachtable "ALTER TABLE ? WITH CHECK CHECK CONSTRAINT all"')
    conn_sql.commit()

    # 8. Cập nhật lại giá trị IDENTITY hiện tại cho các bảng
    print("Cập nhật chỉ mục IDENTITY cho các bảng...")
    for t, id_col in identity_tables.items():
        if t in table_col_types:
            cur_sql.execute(f"SELECT ISNULL(MAX([{id_col}]), 0) FROM [{t}]")
            max_id = cur_sql.fetchone()[0]
            if max_id > 0:
                cur_sql.execute(f"DBCC CHECKIDENT ('{t}', RESEED, {max_id})")
    conn_sql.commit()

    conn_lite.close()
    conn_sql.close()
    print("🎉 Hoàn tất chuyển dữ liệu sang SQL Server thành công!")

if __name__ == "__main__":
    migrate()
