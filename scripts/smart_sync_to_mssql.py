import sqlite3
import pyodbc
import sys
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

backend_dir = Path(r"c:\Users\klqdo\Downloads\hu\quanlybanhangvakho_backend")
SQLITE_DB = backend_dir / "quanlybanhang.db"

MSSQL_CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=quanlybanhang;"
    "Trusted_Connection=yes;"
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

def sync_data():
    if not SQLITE_DB.exists():
        print("SQLite db not found.")
        return

    print("Connecting to SQLite and SQL Server...")
    lite = sqlite3.connect(str(SQLITE_DB))
    c_lite = lite.cursor()

    sql = pyodbc.connect(MSSQL_CONN_STR, autocommit=False)
    c_sql = sql.cursor()

    # Get column metadata and identity columns from SQL Server
    c_sql.execute("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS")
    table_col_types = {}
    for t_name, c_name, d_type in c_sql.fetchall():
        table_col_types.setdefault(t_name, {})[c_name] = d_type

    c_sql.execute("""
        SELECT t.name, c.name 
        FROM sys.tables t 
        JOIN sys.columns c ON t.object_id = c.object_id 
        WHERE c.is_identity = 1
    """)
    identity_tables = {r[0]: r[1] for r in c_sql.fetchall()}

    # Disable FK constraints
    print("Disabling FK constraints...")
    c_sql.execute('EXEC sp_MSforeachtable "ALTER TABLE ? NOCHECK CONSTRAINT all"')
    sql.commit()

    c_lite.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    sqlite_tables = [r[0] for r in c_lite.fetchall()]

    for t in sorted(sqlite_tables):
        if t not in table_col_types:
            continue

        c_lite.execute(f"PRAGMA table_info([{t}])")
        lite_cols = [r[1] for r in c_lite.fetchall()]
        common_cols = [c for c in lite_cols if c in table_col_types[t]]
        if not common_cols:
            continue

        cols_str = ", ".join(f"[{c}]" for c in common_cols)
        c_lite.execute(f"SELECT {cols_str} FROM [{t}]")
        lite_rows = c_lite.fetchall()
        if not lite_rows:
            continue

        # Check existing keys in SQL Server
        has_id = "id" in common_cols
        existing_ids = set()
        if has_id:
            c_sql.execute(f"SELECT [id] FROM [{t}]")
            existing_ids = {r[0] for r in c_sql.fetchall()}

        # Check additional unique keys
        code_col = None
        for cand in ("code", "order_code", "invoice_code", "username", "sku"):
            if cand in common_cols:
                code_col = cand
                break

        existing_codes = set()
        if code_col:
            c_sql.execute(f"SELECT [{code_col}] FROM [{t}] WHERE [{code_col}] IS NOT NULL")
            existing_codes = {str(r[0]).strip() for r in c_sql.fetchall()}

        rows_to_insert = []
        id_index = common_cols.index("id") if has_id else -1
        code_index = common_cols.index(code_col) if code_col else -1
        for r in lite_rows:
            if has_id and r[id_index] in existing_ids:
                continue
            if code_col and code_index != -1 and str(r[code_index]).strip() in existing_codes:
                continue
            
            new_r = []
            for val, c_name in zip(r, common_cols):
                c_type = table_col_types[t][c_name]
                parsed = parse_val(val, c_type)
                if t == "categories" and c_name == "code" and (not parsed or str(parsed).strip() == ""):
                    cat_id = r[id_index] if has_id else 1
                    parsed = f"CAT{cat_id:04d}"
                new_r.append(parsed)
            rows_to_insert.append(tuple(new_r))

        if not rows_to_insert:
            print(f"Bảng {t:30}: SQL Server đã có đủ dữ liệu ({len(existing_ids)} dòng).")
            continue

        has_identity = t in identity_tables and identity_tables[t] in common_cols
        if has_identity:
            c_sql.execute(f"SET IDENTITY_INSERT [{t}] ON")

        placeholders = ", ".join("?" for _ in common_cols)
        insert_sql = f"INSERT INTO [{t}] ({cols_str}) VALUES ({placeholders})"

        inserted_count = 0
        try:
            c_sql.fast_executemany = True
            batch_size = 500
            for i in range(0, len(rows_to_insert), batch_size):
                batch = rows_to_insert[i:i + batch_size]
                c_sql.executemany(insert_sql, batch)
                inserted_count += len(batch)
            sql.commit()
        except Exception:
            sql.rollback()
            if has_identity:
                c_sql.execute(f"SET IDENTITY_INSERT [{t}] ON")
            c_sql.fast_executemany = False
            for r in rows_to_insert:
                try:
                    c_sql.execute(insert_sql, r)
                    sql.commit()
                    inserted_count += 1
                except Exception:
                    sql.rollback()

        if has_identity:
            c_sql.execute(f"SET IDENTITY_INSERT [{t}] OFF")

        print(f"Bảng {t:30}: Đã thêm {inserted_count:4} dòng mới vào SQL Server (Tổng: {len(existing_ids) + inserted_count}).")

    # Re-enable FK constraints
    print("Re-enabling FK constraints...")
    c_sql.execute('EXEC sp_MSforeachtable "ALTER TABLE ? WITH CHECK CHECK CONSTRAINT all"')
    sql.commit()

    # Reseed identity
    print("Reseeding identity columns...")
    for t, id_col in identity_tables.items():
        if t in table_col_types:
            c_sql.execute(f"SELECT ISNULL(MAX([{id_col}]), 0) FROM [{t}]")
            max_id = c_sql.fetchone()[0]
            if max_id > 0:
                c_sql.execute(f"DBCC CHECKIDENT ('{t}', RESEED, {max_id})")
    sql.commit()

    lite.close()
    sql.close()
    print(" Đồng bộ toàn bộ dữ liệu SQLite sang SQL Server thành công!")

if __name__ == "__main__":
    sync_data()
