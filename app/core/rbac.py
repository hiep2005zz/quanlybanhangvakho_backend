# backend/app/core/rbac.py
"""
Hệ thống Phân quyền theo Vai trò (Role-Based Access Control - RBAC)
Nguyên tắc: Zero-Trust & Default Deny
"""
from enum import Enum
from typing import Dict, Set, List, Optional


class Role(str, Enum):
    SYSTEM_ADMIN = "admin"                    # Quản trị hệ thống
    SALES_MANAGER = "sales_manager"          # Quản lý kinh doanh
    SALES = "sales"                          # Nhân viên kinh doanh
    WAREHOUSE_STAFF = "warehouse"            # Thủ kho
    WAREHOUSE_MANAGER = "warehouse_manager"  # Quản lý kho
    ACCOUNTANT = "accountant"                # Kế toán
    PURCHASING_STAFF = "purchasing"          # Nhân viên mua hàng
    CUSTOMER = "customer"                    # Nhân viên kinh doanh chờ cấp quyền


class Permission(str, Enum):
    # Tài nguyên: Sản phẩm
    PRODUCT_READ = "product:read"
    PRODUCT_WRITE = "product:write"

    # Dữ liệu nhạy cảm: Giá vốn & Lợi nhuận (Chỉ Quản lý kinh doanh & Admin)
    COST_READ = "cost:read"

    # Tài nguyên: Kho & Tồn kho
    INVENTORY_READ = "inventory:read"
    INVENTORY_WRITE = "inventory:write"      # Nhập/xuất/điều chỉnh kho (Sales bị chặn hoàn toàn!)

    # Tài nguyên: Đơn hàng bán
    ORDER_READ = "order:read"
    ORDER_WRITE = "order:write"

    # Tài nguyên: Báo cáo
    REPORT_READ = "report:read"

    # Tài nguyên: Mua hàng
    PURCHASE_READ = "purchase:read"
    PURCHASE_WRITE = "purchase:write"

    # Tài nguyên: Quản trị hệ thống & Phân quyền
    USER_MANAGE = "user:manage"


# Ma trận phân quyền cho 7 vai trò nghiệp vụ (RBAC Matrix)
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    # 1. Quản trị hệ thống: Toàn quyền hệ thống
    Role.SYSTEM_ADMIN.value: {
        "*"  # Wildcard superuser
    },

    # 2. Quản lý kinh doanh: Xem/sửa sản phẩm, xem đơn hàng, xem báo cáo VÀ ĐƯỢC XEM GIÁ VỐN / LỢI NHUẬN
    # Tuyệt đối KHÔNG có quyền can thiệp ghi kho (inventory:write)
    Role.SALES_MANAGER.value: {
        Permission.PRODUCT_READ.value,
        Permission.PRODUCT_WRITE.value,
        Permission.COST_READ.value,          # Được xem giá vốn & biên lợi nhuận
        Permission.INVENTORY_READ.value,     # Chỉ xem tồn kho để điều phối
        Permission.ORDER_READ.value,
        Permission.ORDER_WRITE.value,
        Permission.REPORT_READ.value,
    },

    # 3. Nhân viên kinh doanh: Xem SP, tạo đơn hàng, xem tồn kho bán.
    # KHÔNG được xem giá vốn (cost:read) và TUYỆT ĐỐI CHẶN can thiệp kho (inventory:write)
    Role.SALES.value: {
        Permission.PRODUCT_READ.value,
        Permission.ORDER_READ.value,
        Permission.ORDER_WRITE.value,
        Permission.INVENTORY_READ.value,     # Xem số lượng tồn để bán hàng
    },

    # 4. Thủ kho: Thao tác kho toàn diện (nhập, xuất, kiểm kê, điều chỉnh).
    # TUYỆT ĐỐI KHÔNG ĐƯỢC XEM GIÁ VỐN VÀ BIÊN LỢI NHUẬN (cost:read)
    Role.WAREHOUSE_STAFF.value: {
        Permission.PRODUCT_READ.value,
        Permission.INVENTORY_READ.value,
        Permission.INVENTORY_WRITE.value,    # Có quyền ghi kho
        Permission.ORDER_READ.value,        # Xem đơn hàng để soạn/xuất hàng
    },

    # 5. Quản lý kho: Quản lý kho hàng & xem phiếu mua hàng.
    # Không có quyền xem giá vốn / biên lợi nhuận
    Role.WAREHOUSE_MANAGER.value: {
        Permission.PRODUCT_READ.value,
        Permission.INVENTORY_READ.value,
        Permission.INVENTORY_WRITE.value,
        Permission.PURCHASE_READ.value,
        Permission.REPORT_READ.value,
        Permission.ORDER_READ.value,        # Xem đơn hàng để điều phối xuất kho
    },

    # 6. Kế toán: Xem chứng từ, đơn hàng, mua hàng, báo cáo chung.
    # Không có quyền can thiệp kho (inventory:write)
    Role.ACCOUNTANT.value: {
        Permission.PRODUCT_READ.value,
        Permission.ORDER_READ.value,
        Permission.PURCHASE_READ.value,
        Permission.REPORT_READ.value,
        Permission.INVENTORY_READ.value,
    },

    # 7. Nhân viên mua hàng: Tạo và theo dõi phiếu mua hàng, xem tồn kho.
    # Không can thiệp kho trực tiếp
    Role.PURCHASING_STAFF.value: {
        Permission.PRODUCT_READ.value,
        Permission.PURCHASE_READ.value,
        Permission.PURCHASE_WRITE.value,
        Permission.INVENTORY_READ.value,
    },

    # 8. Đại lý (Customer / Dealer): Xem danh mục sản phẩm, xem tồn kho để đặt hàng sỉ, tự tạo đơn hàng
    Role.CUSTOMER.value: {
        Permission.PRODUCT_READ.value,
        Permission.ORDER_READ.value,
        Permission.ORDER_WRITE.value,
    },
}

# Thông tin mô tả 7 vai trò nghiệp vụ
ROLE_DETAILS: Dict[str, dict] = {
    Role.SYSTEM_ADMIN.value: {
        "title": "Quản trị hệ thống",
        "badge_color": "#ef4444",
        "description": "Toàn quyền quản trị tài khoản, cấu hình và giám sát dữ liệu.",
        "can_view_cost": True,
        "can_write_inventory": True,
    },
    Role.SALES_MANAGER.value: {
        "title": "Quản lý kinh doanh",
        "badge_color": "#8b5cf6",
        "description": "Quản lý bán hàng, xem báo cáo doanh thu, giá vốn và biên lợi nhuận. Không can thiệp kho.",
        "can_view_cost": True,
        "can_write_inventory": False,
    },
    Role.SALES.value: {
        "title": "Nhân viên kinh doanh",
        "badge_color": "#3b82f6",
        "description": "Tạo đơn hàng, tra cứu tồn kho bán hàng. Bị chặn xem giá vốn và bị chặn can thiệp kho.",
        "can_view_cost": False,
        "can_write_inventory": False,
    },
    Role.WAREHOUSE_STAFF.value: {
        "title": "Thủ kho",
        "badge_color": "#10b981",
        "description": "Thực hiện nhập, xuất, kiểm kê kho. Tuyệt đối không xem giá vốn và lợi nhuận.",
        "can_view_cost": False,
        "can_write_inventory": True,
    },
    Role.WAREHOUSE_MANAGER.value: {
        "title": "Quản lý kho",
        "badge_color": "#059669",
        "description": "Giám sát quy trình kho vận, theo dõi đơn mua hàng. Không xem giá vốn tài chính.",
        "can_view_cost": False,
        "can_write_inventory": True,
    },
    Role.ACCOUNTANT.value: {
        "title": "Kế toán",
        "badge_color": "#f59e0b",
        "description": "Đối soát sổ sách hóa đơn, chứng từ đơn hàng và mua hàng.",
        "can_view_cost": False,
        "can_write_inventory": False,
    },
    Role.PURCHASING_STAFF.value: {
        "title": "Nhân viên mua hàng",
        "badge_color": "#06b6d4",
        "description": "Lập phiếu mua hàng, theo dõi đơn nhập từ nhà cung cấp. Không can thiệp kho trực tiếp.",
        "can_view_cost": False,
        "can_write_inventory": False,
    },
    Role.CUSTOMER.value: {
        "title": "Đại lý",
        "badge_color": "#0284c7",
        "description": "Cửa hàng hoặc đại lý mua sỉ, tự đặt hàng, theo dõi đơn và công nợ của mình.",
        "can_view_cost": False,
        "can_write_inventory": False,
    },
}

# Danh sách các vai trò thuộc nghiệp vụ Kho
WAREHOUSE_ROLES: Set[str] = {
    Role.WAREHOUSE_STAFF.value,
    Role.WAREHOUSE_MANAGER.value,
}

# Danh sách các kho thực tế (Địa điểm kho cụ thể, không tính địa bàn tổng quát như Toàn quốc)
SPECIFIC_WAREHOUSES: List[str] = [
    "Kho Tổng Hà Nội",
    "Kho Chi Nhánh Đà Nẵng",
    "Kho Chi Nhánh TP. Hồ Chí Minh",
]


def is_warehouse_role(roles: List[str]) -> bool:
    """Kiểm tra danh sách vai trò có chứa vai trò Kho nào không."""
    return any(r in WAREHOUSE_ROLES for r in roles)


def is_specific_warehouse(branch: Optional[str]) -> bool:
    """Kiểm tra tên kho/địa bàn có phải là một kho cụ thể hay không."""
    if not branch or not branch.strip():
        return False
    b = branch.strip()
    return b.startswith("Kho ") or b in SPECIFIC_WAREHOUSES


def get_roles_permissions(roles: List[str]) -> List[str]:
    """Hợp nhất (union) toàn bộ quyền hạn từ tất cả các vai trò mà người dùng nắm giữ."""
    if not roles:
        return []
    
    # Nếu sở hữu vai trò Quản trị hệ thống -> Toàn quyền
    if Role.SYSTEM_ADMIN.value in roles or any("*" in ROLE_PERMISSIONS.get(r, set()) for r in roles):
        return [p.value for p in Permission]

    effective_perms: Set[str] = set()
    for r in roles:
        effective_perms.update(ROLE_PERMISSIONS.get(r, set()))

    return sorted(list(effective_perms))


def get_role_permissions(role: str) -> List[str]:
    """Lấy danh sách các quyền hạn được cấp cho vai trò (tương thích ngược)."""
    return get_roles_permissions([role] if role else [])


def has_permission(role: str, permission: str) -> bool:
    """
    Kiểm tra xem vai trò có quyền thực thi thao tác hay không (tương thích ngược).
    """
    return has_roles_permission([role] if role else [], permission)


def has_roles_permission(roles: List[str], permission: str) -> bool:
    """
    Kiểm tra xem người dùng (với 1 hoặc nhiều vai trò) có quyền thực thi thao tác hay không.
    Nguyên tắc Default Deny:
    - Nếu danh sách vai trò rỗng -> False
    - Nếu có bất kỳ vai trò nào có quyền (hoặc Superuser *) -> True
    """
    if not roles:
        return False
    for r in roles:
        perms = ROLE_PERMISSIONS.get(r)
        if perms is not None:
            if "*" in perms or permission in perms:
                return True
    return False
