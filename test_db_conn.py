import pyodbc
import os
from dotenv import load_dotenv
from pathlib import Path

# Load .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

db_url = os.getenv("DATABASE_URL", "")
print(f"Testing connection to: {db_url}")

# Extract connection string parts for pyodbc
# mssql+pyodbc://localhost/quanlybanhang?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes
try:
    if "mssql+pyodbc" in db_url:
        # Simplify for pyodbc direct test
        conn_str = db_url.replace("mssql+pyodbc://", "")
        server_part, params_part = conn_str.split("?")
        
        # Parse server and database
        if "/" in server_part:
            server, database = server_part.split("/")
        else:
            server = server_part
            database = "master"
            
        # Parse params
        params = dict(p.split("=") for p in params_part.split("&"))
        
        driver = params.get("driver", "ODBC Driver 17 for SQL Server").replace("+", " ")
        trusted = params.get("trusted_connection", "no")
        
        final_conn_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
        if trusted == "yes":
            final_conn_str += "Trusted_Connection=yes;"
        else:
            final_conn_str += f"UID={params.get('user')};PWD={params.get('password')};"
            
        print(f"Attempting to connect with: {final_conn_str}")
        conn = pyodbc.connect(final_conn_str, timeout=5)
        print("✅ KẾT NỐI THÀNH CÔNG!")
        
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION")
        row = cursor.fetchone()
        print(f"Phiên bản SQL Server: {row[0]}")
        
        conn.close()
    else:
        print("❌ DATABASE_URL không phải là SQL Server (mssql+pyodbc)")
except Exception as e:
    print("❌ LỖI KẾT NỐI:")
    print(str(e))
    print("\n--- Gợi ý ---")
    if "driver" in str(e).lower():
        print("1. Kiểm tra xem bạn đã cài 'ODBC Driver 17 for SQL Server' chưa.")
    if "login failed" in str(e).lower():
        print("1. Kiểm tra database 'quanlybanhang' đã tồn tại chưa.")
        print("2. Kiểm tra quyền truy cập của user.")
    if "server not found" in str(e).lower() or "08001" in str(e):
        print("1. Kiểm tra SQL Server có đang chạy không.")
        print("2. Nếu là Instance (ví dụ SQLEXPRESS), hãy thử đổi localhost thành localhost\\SQLEXPRESS")
