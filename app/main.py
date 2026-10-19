import os
import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.endpoints import auth, products, inventory, users, orders, categories, audit_logs, profile, suppliers, dealers, delivery_points

from contextlib import asynccontextmanager
from app.db.init_db import init_db

async def daily_credit_policy_evaluation():
    """
    AC3: Scheduled Task chạy tự động đánh giá lại công nợ toàn bộ đại lý mỗi ngày 1 lần.
    """
    # Đợi 10 giây sau khi server khởi động xong trước khi chạy lần đầu
    await asyncio.sleep(10)
    while True:
        try:
            from app.core.database import SessionLocal
            from app.services.credit_policy_service import evaluate_all_dealers_credit_flags, get_credit_policy_config
            with SessionLocal() as db_session:
                cfg = get_credit_policy_config(db_session)
                if getattr(cfg, "auto_eval_enabled", True):
                    evaluate_all_dealers_credit_flags(db_session)
                    print("[Scheduled Job] Đã hoàn thành đánh giá tự động công nợ đại lý hàng ngày (S7-04).")
        except Exception as e:
            print(f"Error in daily credit policy evaluation cron: {e}")
        # Chạy lại sau mỗi 24 giờ
        await asyncio.sleep(86400)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo bảng và dữ liệu mẫu khi ứng dụng khởi động
    init_db()
    cron_task = asyncio.create_task(daily_credit_policy_evaluation())
    try:
        yield
    finally:
        cron_task.cancel()

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
app.include_router(dealers.router, prefix="/api/v1/dealers", tags=["Dealers"])
app.include_router(delivery_points.dealers_router, prefix="/api/v1/dealers", tags=["Dealers"])
app.include_router(delivery_points.router, prefix="/api/v1/dealers", tags=["Delivery Points"])
from app.api.v1.endpoints import (
    price_books, discounts, goods_receipts, warehouses, stock_audits, pricing, batches,
    executive_dashboard, sales_reports, sales_targets,
    inventory_reports, deliveries, stock_adjustments, payment_collections
)
app.include_router(price_books.router, prefix="/api/v1/price-books", tags=["PriceBooks"])
app.include_router(discounts.router, prefix="/api/v1/discounts", tags=["Discounts"])
app.include_router(pricing.router, prefix="/api/v1/pricing", tags=["Pricing"])
app.include_router(pricing.router, prefix="/api/v1/orders/pricing", tags=["Pricing"])
app.include_router(goods_receipts.router, prefix="/api/v1/goods-receipts", tags=["Goods Receipts"])
app.include_router(goods_receipts.router, prefix="/goods-receipts", tags=["Goods Receipts"])
app.include_router(warehouses.router, prefix="/api/v1/warehouses", tags=["Warehouses"])
app.include_router(stock_audits.router, prefix="/api/v1/stock-audits", tags=["Stock Audits"])
from app.api.v1.endpoints import agency_debts, debt_payments
app.include_router(agency_debts.router, prefix="/api/v1/agency-debts", tags=["Agency Debts"])
app.include_router(debt_payments.router, prefix="/api/v1/debt-payments", tags=["Debt Payments"])
app.include_router(batches.router, prefix="/api/v1/batches", tags=["Batches"])
app.include_router(executive_dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(sales_reports.router, prefix="/api/v1/reports", tags=["Reports"])
app.include_router(sales_targets.router, prefix="/api/v1/sales-targets", tags=["Sales Targets"])
app.include_router(inventory_reports.router, prefix="/api/v1/inventory", tags=["Inventory Reports"])
app.include_router(deliveries.router, prefix="/api/v1/deliveries", tags=["Deliveries"])
app.include_router(stock_adjustments.router, prefix="/api/v1/stock-adjustments", tags=["Stock Adjustments"])
app.include_router(payment_collections.router, prefix="/api/v1/payment-collections", tags=["Payment Collections"])
from app.api.v1.endpoints import return_orders
app.include_router(return_orders.router, prefix="/api/v1/return-orders", tags=["Return Orders / RMA"])
from app.api.v1.endpoints import debt_reconciliations
app.include_router(debt_reconciliations.router, prefix="/api/v1/debt-reconciliations", tags=["Debt Reconciliations"])

# Mount static folder for user avatars / media uploads
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
AVATARS_DIR = os.path.join(UPLOAD_DIR, "avatars")
PRODUCTS_DIR = os.path.join(UPLOAD_DIR, "products")
PODS_DIR = os.path.join(UPLOAD_DIR, "pod")
os.makedirs(AVATARS_DIR, exist_ok=True)
os.makedirs(PRODUCTS_DIR, exist_ok=True)
os.makedirs(PODS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Backend API đang hoạt động bình thường",
        "docs_url": "/docs"
    }
