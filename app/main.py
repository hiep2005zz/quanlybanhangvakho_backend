# backend/app/main.py - Fresh Reset
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.endpoints import auth, products, inventory, users, orders, categories, audit_logs, profile, suppliers, dealers, delivery_points

from contextlib import asynccontextmanager
from app.db.init_db import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo bảng và dữ liệu mẫu trên SQL Server khi ứng dụng khởi động
    init_db()
    yield

app = FastAPI(
    title="Quan Ly Ban Hang & Kho API",
    description="Hệ thống API quản lý bán hàng và kho với cơ chế RBAC Zero-Trust bảo vệ giá vốn và kho hàng",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware to allow Frontend (Vite on any localhost port: 5173, 5174, 5175, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(products.router, prefix="/api/v1/products", tags=["Products"])
app.include_router(inventory.router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
from app.api.v1.endpoints import user_import
app.include_router(user_import.router, prefix="/api/v1/users/import", tags=["User Import"])
app.include_router(orders.router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(categories.router, prefix="/api/v1/categories", tags=["Categories"])
app.include_router(audit_logs.router, prefix="/api/v1/audit-logs", tags=["AuditLogs"])
app.include_router(profile.router, prefix="/api/v1/me", tags=["Profile"])
app.include_router(profile.router, prefix="/api/v1/profile", tags=["Profile"])
app.include_router(suppliers.router, prefix="/api/v1/suppliers", tags=["Suppliers"])
app.include_router(delivery_points.dealers_router, prefix="/api/v1/dealers", tags=["Dealers"])
app.include_router(delivery_points.router, prefix="/api/v1/dealers", tags=["Delivery Points"])
app.include_router(dealers.router, prefix="/api/v1/dealers", tags=["Dealers"])
from app.api.v1.endpoints import price_books
app.include_router(price_books.router, prefix="/api/v1/price-books", tags=["PriceBooks"])

# Mount static folder for user avatars / media uploads
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
AVATARS_DIR = os.path.join(UPLOAD_DIR, "avatars")
os.makedirs(AVATARS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Backend API đang hoạt động bình thường",
        "docs_url": "/docs"
    }
