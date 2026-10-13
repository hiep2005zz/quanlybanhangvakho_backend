---
name: system-feature-invariants-guardrail
description: >-
  Quy chuẩn tối cao và tài liệu ràng buộc toàn diện các chức năng hệ thống Quản lý Bán hàng & Kho (ttcs: FastAPI + React 19 TypeScript + Vite).
  Định nghĩa chi tiết bản đồ 13 phân hệ nghiệp vụ, các ràng buộc bất biến liên module (Cross-Module Invariants),
  ma trận phân quyền 8 vai trò (RBAC/ABAC), bảng điều phối Clean URL & Modal State Registry, cùng quy trình nghiệm thu tự động bắt buộc
  để đảm bảo 100% không làm hỏng tính năng cũ khi phát triển mới hoặc giải quyết xung đột khi Merge code.
---

# SKILL: SYSTEM FEATURE INVARIANTS & ANTI-REGRESSION GUARDRAIL
## Hệ Thống Quản Lý Bán Hàng & Kho Hàng (ttcs Enterprise)
**Kiến trúc:** Dual-Stack Decoupled (FastAPI Backend + React 19 TypeScript Vite Frontend)  
**Tôn chỉ cốt lõi:** BẢO TOÀN 100% TÍNH NĂNG ĐÃ CÓ — ZERO-REGRESSION TUYỆT ĐỐI — CẤM LÀM HỎNG TÍNH NĂNG LIÊN ĐỚI KHI THÊM MỚI HOẶC MERGE CODE.

---

## 1. MỤC ĐÍCH & PHẠM VI ÁP DỤNG

Tài liệu Skill này là **QUY CHUẨN TỐI CAO BẮT BUỘC** áp dụng cho:
1. Tất cả các lập trình viên trong nhóm phát triển dự án `quanlybanhangvakho_frontend` và `quanlybanhangvakho_backend`.
2. Mọi AI Agent (Gemini, Claude, Antigravity...) khi thực hiện đọc hiểu, viết code, sửa lỗi, refactor hoặc merge mã nguồn trên codebase này.

Bất kỳ hành vi nào làm mất tính năng đã có, xóa nhầm prop/handler, xóa endpoint, làm vỡ phân quyền, hoặc gây lỗi trắng trang (White Screen of Death) đều bị coi là **VI PHẠM NGHIÊM TRỌNG NGUYÊN TẮC BẢO TOÀN HỆ THỐNG**.

---

## 2. BẢN ĐỒ CHI TIẾT 13 PHÂN HỆ NGHIỆP VỤ & DANH MỤC CHỨC NĂNG

Hệ thống hiện tại gồm **13 phân hệ nghiệp vụ hoàn chỉnh**. Dưới đây là danh mục chi tiết từng chức năng con, các file phụ trách và các điểm kỹ thuật bất biến:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 BẢN ĐỒ 13 PHÂN HỆ HỆ THỐNG TTCS ENTERPRISE                              │
├───────────────────────────────┬───────────────────────────────┬────────────────────────────────────────┤
│ 1. Xác thực & Quản lý Phiên   │ 2. Quản trị User & RBAC/ABAC  │ 3. Hồ sơ Cá nhân (Profile)             │
│ 4. Danh bạ Đại lý & Khách hàng│ 5. Điểm Giao Hàng Chi nhánh   │ 6. Quản lý Nhà Cung Cấp                │
│ 7. Cây Danh mục Hàng hóa      │ 8. Sản phẩm, ĐVT & Tồn kho    │ 9. Nhập kho & Điều chỉnh từ chứng từ   │
│ 10. Kiểm kê & Biên bản kho    │ 11. Bảng giá & Duyệt giá bán  │ 12. Chính sách Chiết khấu Thương mại   │
│ 13. Đơn hàng & Vòng đời Bán   │ 14. Nhật ký Kiểm toán (Audit) │ 15. Điều phối Giao diện (Dashboard)    │
└───────────────────────────────┴───────────────────────────────┴────────────────────────────────────────┘
```

---

### Phân hệ 1: Xác thực, Bảo mật & Quản lý Phiên (Auth & Session Lifecycle)
- **Backend:** `backend/app/api/v1/endpoints/auth.py`, `backend/app/core/security.py`, `backend/app/models/user.py`
- **Frontend:** `frontend/src/services/auth.ts`, `frontend/src/services/sessionManager.ts`, `frontend/src/components/SecurityModal.tsx`, `frontend/src/features/auth/`
- **Chức năng đã có:**
  1. **Đăng nhập an toàn:** JWT Access Token (HS256), băm mật khẩu chuẩn Bcrypt.
  2. **Chống Brute-force:** Khóa tài khoản 15 phút nếu nhập sai mật khẩu liên tiếp 5 lần (`failed_attempts >= 5`).
  3. **Chống dò quét tài khoản (User Enumeration Defense):** Phản hồi thông báo lỗi đồng nhất khi sai tài khoản hoặc sai mật khẩu (`"Tài khoản hoặc mật khẩu không chính xác."`).
  4. **Thu hồi phiên đăng nhập tức thì (Token Versioning):** Mỗi user có `token_version`. Khi đổi mật khẩu, hoặc khi Admin khóa tài khoản, `token_version` tự động tăng -> toàn bộ token cũ lập tức bị từ chối 401 trên Backend.
  5. **Cảnh báo phiên hết hạn & Heartbeat:** `sessionManager.ts` tính toán thời gian rảnh (idle timeout), hiển thị popup cảnh báo gia hạn phiên trước khi tự động đăng xuất.
  6. **Đồng bộ quyền Realtime:** Bắn và lắng nghe sự kiện `USER_ROLE_UPDATED` để cập nhật giao diện ngay lập tức mà không cần F5 hay đăng xuất.

---

### Phân hệ 2: Quản trị Người Dùng & Phân Quyền (RBAC / ABAC)
- **Backend:** `backend/app/api/v1/endpoints/users.py`, `backend/app/api/v1/endpoints/user_import.py`, `backend/app/core/rbac.py`
- **Frontend:** `frontend/src/components/UserManagementView.tsx`, `frontend/src/components/UserBulkImportModal.tsx`, `frontend/src/components/CreateCustomerModal.tsx`
- **Chức năng đã có:**
  1. **Hệ thống 8 vai trò chuẩn:**
     - `admin`: Quản trị hệ thống (Toàn quyền tối cao).
     - `sales_manager`: Quản lý kinh doanh (Xem báo cáo, xem giá vốn, duyệt giá).
     - `sales`: Nhân viên kinh doanh (Tạo đơn hàng, tra cứu tồn bán hàng; CHẶN giá vốn, CHẶN ghi kho).
     - `warehouse`: Thủ kho (Nhập/xuất kho; CHẶN giá vốn).
     - `warehouse_manager`: Quản lý kho (Điều phối kho, duyệt phiếu kho; CHẶN giá vốn).
     - `accountant`: Kế toán công nợ (Hóa đơn, công nợ đại lý; CHẶN tự ý sửa kho).
     - `purchasing`: Nhân viên mua hàng (Đơn mua NCC, theo dõi nhập kho).
     - `customer`: Đại lý / Khách sỉ (Đặt hàng sỉ, xem công nợ bản thân).
  2. **Bảo vệ tài khoản Quản trị tối cao (Admin Protection):** Cấm tự thu hồi quyền Admin của chính mình; Cấm tự khóa tài khoản Admin của chính mình.
  3. **Ràng buộc vai trò Kho:** Nhân viên có vai trò `warehouse` hoặc `warehouse_manager` bắt buộc phải gắn với ít nhất một kho cụ thể (`Kho Tổng Hà Nội`, `Kho Chi Nhánh Đà Nẵng`, `Kho Chi Nhánh TP. Hồ Chí Minh`). Cấm chọn "Toàn quốc" cho thủ kho.
  4. **Khóa tài khoản bắt buộc nhập lý do (Mandatory Lock Reason):** Khi chuyển sang Tạm khóa / Nghỉ việc, bắt buộc nhập `lock_reason` (tối thiểu thông tin bàn giao).
  5. **Bàn giao đại lý khi nhân viên bị khóa (Handover Protocol):** Khi nhân viên Sales bị khóa, cờ `dealers_needing_handover` bật lên. Toàn bộ tính năng lên đơn mới cho đại lý đó bị phong tỏa cho tới khi hoàn tất bàn giao cho Sales cùng địa bàn (`checkRegionMatch`).
  6. **Nhập hàng loạt tài khoản qua Excel:** Kiểm tra trùng email, phone, username, validate định dạng và cấp mật khẩu ban đầu.

---

### Phân hệ 3: Hồ sơ Cá nhân (Profile & Account Info)
- **Backend:** `backend/app/api/v1/endpoints/profile.py`
- **Frontend:** `frontend/src/components/ProfileView.tsx`
- **Chức năng đã có:**
  1. **Thiết kế Single Compact Card:** Toàn bộ thông tin hiển thị trong 1 khối duy nhất, không xé vụn giao diện.
  2. **Cập nhật thông tin:** Cập nhật họ tên, số điện thoại, đổi ảnh đại diện (avatar cropper hỗ trợ zoom/xoay hình).
  3. **Bảo vệ trường hệ thống:** Tên đăng nhập, email, vai trò, kho/chi nhánh phụ trách là Read-Only đối với người dùng thông thường (chỉ Admin mới có quyền đổi qua User Management).

---

### Phân hệ 4: Quản lý Đại lý & Khách Hàng (Dealers & Accounts)
- **Backend:** `backend/app/api/v1/endpoints/dealers.py`
- **Frontend:** `frontend/src/components/DealerSearchView.tsx`, `frontend/src/components/DealerProfileManagementView.tsx`, `frontend/src/components/DealerCreditBadge.tsx`
- **Chức năng đã có:**
  1. **Hồ sơ đại lý:** Mã đại lý (`DLxxx`), Tên, Số điện thoại, Email, Địa chỉ, Khu vực/Địa bàn (`region`).
  2. **Phân nhóm khách hàng:** `dai_ly_cap_1`, `dai_ly_cap_2`, `khach_le`, `Khách sỉ`.
  3. **Quản lý Hạn mức & Công nợ:** Hạn mức tín dụng (`credit_limit`), Dư nợ hiện tại (`current_debt`), Nợ quá hạn (`overdue_debt`). Huy hiệu cảnh báo màu sắc theo tỷ lệ nợ (Xanh < 70%, Vàng 70-90%, Đỏ >= 90% hoặc có nợ quá hạn).
  4. **Trạng thái giao dịch:** Đang hoạt động / Bị khóa giao dịch. Khi đại lý bị khóa giao dịch, hệ thống chặn 100% không cho tạo đơn hàng mới.
  5. **Tự động cấp tài khoản Đại lý:** Khi tạo đại lý, hệ thống hỗ trợ sinh tài khoản `customer` tương ứng và gửi thông tin qua mail.

---

### Phân hệ 5: Điểm Giao Hàng Chi Nhánh (Delivery Points)
- **Backend:** `backend/app/api/v1/endpoints/delivery_points.py`
- **Frontend:** `frontend/src/components/DeliveryPointsView.tsx`, `frontend/src/components/DeliveryPointManager.tsx`
- **Chức năng đã có:**
  1. Một đại lý có thể gắn nhiều điểm giao hàng (Kho trung tâm, Cửa hàng 1, Cửa hàng 2...).
  2. Quản lý người liên hệ nhận hàng, số điện thoại nhận hàng, địa chỉ giao hàng chi tiết, gắn cờ điểm mặc định.

---

### Phân hệ 6: Quản lý Nhà Cung Cấp (Suppliers)
- **Backend:** `backend/app/api/v1/endpoints/suppliers.py`
- **Frontend:** `frontend/src/components/SupplierManagementView.tsx`, `frontend/src/components/SupplierModal.tsx`
- **Chức năng đã có:**
  1. Danh bạ nhà cung cấp, Mã số thuế, Địa chỉ, Người liên hệ, Công nợ phải trả NCC.
  2. Phục vụ trực tiếp cho tính năng Nhập kho và Mua hàng.

---

### Phân hệ 7: Cây Danh Mục Hàng Hóa (Categories)
- **Backend:** `backend/app/api/v1/endpoints/categories.py`
- **Frontend:** `frontend/src/components/CategoryManagementView.tsx`, `frontend/src/components/MoveCategoryModal.tsx`
- **Chức năng đã có:**
  1. Cấu trúc cây đa tầng (Danh mục cha - con).
  2. Di chuyển danh mục (`MoveCategoryModal`): Kiểm tra thuật toán chống tạo vòng lặp vô tận (Cyclic Loop Prevention) — cấm di chuyển một danh mục cha vào chính cây con của nó.
  3. Phân quyền: Chỉ `admin` và `sales_manager` mới có quyền can thiệp danh mục.

---

### Phân hệ 8: Sản Phẩm, Đơn Vị Tính Quy Đổi & Tồn Kho (Products, Units & Inventory)
- **Backend:** `backend/app/api/v1/endpoints/products.py`, `backend/app/api/v1/endpoints/inventory.py`
- **Frontend:** `frontend/src/components/ProductFormView.tsx`, `frontend/src/components/ProductUnitModal.tsx`, `frontend/src/components/ProductBulkImportModal.tsx`, `frontend/src/components/StockThresholdModal.tsx`, `frontend/src/components/WarehouseStockBookView.tsx`
- **Chức năng đã có:**
  1. **Thông tin mặt hàng:** Mã sản phẩm, tên, ảnh, danh mục, giá bán niêm yết, tồn kho tổng, tồn kho theo chi nhánh.
  2. **Bảo mật giá vốn (COGS Guard):** Giá vốn (`cost_price`) chỉ trả về cho Admin & Quản lý kinh doanh; Bị strip hoàn toàn với Sales và Kho.
  3. **Đơn vị tính quy đổi (Unit Conversion):** ĐVT cơ bản (Ví dụ: Chai, Cái) và ĐVT quy đổi (Thùng, Lốc) kèm hệ số (`conversion_factor`) và mã vạch riêng. Mọi giao dịch xuất/nhập theo ĐVT phụ đều tự động quy đổi chính xác về ĐVT cơ bản.
  4. **Cảnh báo tồn kho tối thiểu (Low Stock Alerts):** Thiết lập ngưỡng cảnh báo cho từng sản phẩm; tự động báo động khi tồn kho thực tế chạm ngưỡng an toàn.
  5. **Sổ kho & Thẻ kho điện tử (Stock Book):** Ghi nhận chi tiết từng dòng biến động Xuất - Nhập - Tồn - Điều chỉnh kèm số chứng từ tham chiếu.
  6. **Import sản phẩm hàng loạt bằng Excel.**
  7. **Chống xóa sản phẩm đã có lịch sử:** Cấm xóa cứng sản phẩm nếu đã phát sinh phiếu xuất/nhập/đơn hàng.

---

### Phân hệ 9: Nhập Kho & Điều Chỉnh Tồn Kho từ Chứng Từ (Goods Receipts)
- **Backend:** `backend/app/api/v1/endpoints/goods_receipts.py`
- **Frontend:** `frontend/src/components/GoodsReceiptModal.tsx`
- **Chức năng đã có:**
  1. Lập phiếu nhập kho từ nhà cung cấp: chọn NCC, kho nhận, chọn sản phẩm, số lượng, đơn giá nhập.
  2. Xác nhận nhập kho: Tự động cộng tồn kho tức thì, cập nhật giá vốn bình quân, ghi sổ kế toán kho.
  3. Lập phiếu điều chỉnh kho trực tiếp từ chứng từ nhập đã xác nhận: Xử lý các trường hợp hàng thừa/thiếu/hư hỏng sau khi nhập.

---

### Phân hệ 10: Kiểm Kê Kho & Biên Bản Chênh Lệch (Stock Audits)
- **Backend:** `backend/app/api/v1/endpoints/stock_audits.py`
- **Frontend:** `frontend/src/components/StockAuditManagementView.tsx`
- **Chức năng đã có:**
  1. Tạo đợt kiểm kê theo kho cụ thể.
  2. Ghi nhận số lượng thực tế đếm được vs số lượng trên hệ thống -> Tự động tính chênh lệch thừa/thiếu.
  3. Duyệt biên bản kiểm kê & Điều chỉnh tồn kho: Cân đối lại tồn kho thực tế vào hệ thống và ghi nhận nguyên nhân chênh lệch.

---

### Phân hệ 11: Bảng Giá & Phê Duyệt Giá Bán (Price Books & Price Approval)
- **Backend:** `backend/app/api/v1/endpoints/price_books.py`
- **Frontend:** `frontend/src/components/PriceBookManagementView.tsx`, `frontend/src/components/PriceUpdateModal.tsx`
- **Chức năng đã có:**
  1. Tạo bảng giá theo nhóm khách hàng (`customer_group`) và thời hạn áp dụng (`valid_from` -> `valid_to`).
  2. API `resolve-price`: Tự động tìm kiếm mức giá bán chính xác nhất cho từng cặp `(customer_id, product_id)`.
  3. **Kiểm soát giá sàn (Floor Price Guard):** Khi nhân viên kinh doanh bán dưới giá quy định hoặc dưới giá sàn (`unit_price < floor_price`), hệ thống giữ nguyên giá thỏa thuận và chuyển đơn sang trạng thái Chờ duyệt (`PENDING_APPROVAL`), gắn cờ `requires_approval = True`, ghi rõ `approval_reason`. Chỉ Quản lý kinh doanh hoặc Admin mới có quyền duyệt.
  4. Chi tiết xem thêm: `.agents/skills/sales-order-pricing-discount-guardrail/SKILL.md`.

---

### Phân hệ 12: Chính Sách Chiết Khấu Thương Mại (Discount Policies)
- **Backend:** `backend/app/api/v1/endpoints/discounts.py`
- **Frontend:** `frontend/src/components/DiscountPolicyView.tsx`, `frontend/src/utils/discountEngine.ts`
- **Chức năng đã có:**
  1. Thiết lập chiết khấu phần trăm (%) hoặc số tiền cố định theo sản phẩm cụ thể, theo danh mục ngành hàng hoặc toàn hệ thống.
  2. **Thứ tự ưu tiên chiết khấu (Priority Invariant):** Luôn so khớp Sản phẩm cụ thể (Mã SKU/Tên) trước, sau đó mới tới Danh mục ngành hàng (`category`), cuối cùng là Toàn hệ thống (`ALL`).
  3. **Kiểm soát chiết khấu vượt mức (Discount Ceiling):** Khi chiết khấu thực tế vượt mức chính sách (`if effective_pct > best_pct`, kể cả khi chính sách là 0% mà nhập > 0%), đơn hàng BẮT BUỘC chuyển sang `PENDING_APPROVAL`, ghi rõ `approval_reason` và hiển thị ngay trong tab "Chờ duyệt".
  4. Chi tiết xem thêm: `.agents/skills/sales-order-pricing-discount-guardrail/SKILL.md`.

---

### Phân hệ 13: Quản Lý Đơn Hàng & Vòng Đời Bán Hàng (Sales Orders Lifecycle)
- **Backend:** `backend/app/api/v1/endpoints/orders.py`
- **Frontend:** `frontend/src/components/OrderManagementView.tsx`, `frontend/src/components/OrdersView.tsx`, `frontend/src/components/SalesOrderEntry.tsx`, `frontend/src/components/OrderDetailsModal.tsx`, `frontend/src/components/ApproveOrderModal.tsx`, `frontend/src/components/RejectOrderModal.tsx`, `frontend/src/components/OrderCancelModal.tsx`, `frontend/src/components/OrderPrintModal.tsx`
- **Chức năng đã có:**
  1. **Tạo đơn hàng bán (Đồng bộ 2 luồng: `OrderCreateModal` & `SalesOrderEntry`):**
     - Chọn đại lý -> Kiểm tra hạn mức nợ & nợ quá hạn.
     - Lọc tồn kho khả dụng (`available_stock`) của kho phụ trách đại lý.
     - Chọn sản phẩm, chọn ĐVT quy đổi, tự động áp bảng giá & chiết khấu.
     - Khóa giá (Price Locking) tại thời điểm đặt đơn.
     - Gắn địa chỉ giao hàng đại lý (`delivery_point_id`).
  2. **Phê duyệt hai cấp (Sales -> Manager/Admin):**
     - Nhân viên Sales chỉ tạo đơn và xem đơn trong địa bàn. Tuyệt đối KHÔNG có quyền tự duyệt đơn (`/approve`) hoặc từ chối (`/reject`).
     - Quản lý / Admin có quyền Duyệt hoặc Từ chối đơn kèm lý do.
  3. **Vòng đời trạng thái đơn:**
     - `PENDING_APPROVAL` (Chờ duyệt) -> `APPROVED`/`CONFIRMED` (Đã duyệt/Đã xác nhận) -> `PROCESSING` -> `SHIPPED` -> `COMPLETED` (Hoàn thành) hoặc `REJECTED`/`CANCELLED` (Từ chối/Đã hủy).
  4. **Hủy đơn & Từ chối đơn hoàn tồn (Reserved Stock Rollback):** Khi hủy đơn hoặc quản lý từ chối đơn, số lượng hàng đã giữ chỗ tự động được hoàn trả vào tồn khả dụng.
  5. **Sao chép đơn cũ (Order Clone - SCRUM-63):** Sao chép nhanh đơn hàng lịch sử thành đơn mới, tự động lấy giá bán hiện hành tại thời điểm sao chép.
  6. **In ấn chứng từ & Hóa đơn:** In nhiệt K80, in phiếu A4, in mã vạch Code128 theo mẫu chuyên nghiệp.

---

### Phân hệ 14: Nhật Ký Kiểm Toán Toàn Diện (System Audit Logs)
- **Backend:** `backend/app/api/v1/endpoints/audit_logs.py`, `backend/app/services/audit_service.py`
- **Frontend:** `frontend/src/components/AuditLogView.tsx`, `frontend/src/components/AuditDetailModal.tsx`
- **Chức năng đã có:**
  1. Bắt buộc ghi nhận mọi hành vi: Tạo, sửa, xóa, khóa/mở khóa tài khoản, duyệt đơn, điều chỉnh giá, xuất nhập kho.
  2. Lưu trữ đầy đủ: Người thực hiện, IP, User-Agent, Thời gian UTC, Diff chi tiết (Before vs After).

---

### Phân hệ 15: Bộ Điều Phối Giao Diện Trung Tâm (`DashboardPage.tsx`)
- **Frontend:** `frontend/src/components/DashboardPage.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/components/DashboardHeader.tsx`
- **Chức năng đã có:**
  1. **Clean URL Routing:** Điều hướng không tải lại trang dựa trên History API:
     - `/inventory`, `/users`, `/categories`, `/audit-logs`, `/profile`, `/suppliers`, `/dealers`, `/orders`, `/create-order`, `/price-books`, `/delivery-points`, `/discounts`, `/product-history`.
  2. **Bảo vệ màn hình (Access Denied):** Người dùng không có quyền truy cập tab sẽ hiển thị `<AccessDeniedView />`, không bao giờ được để crash màn hình trắng.
  3. **Registry State Modals Dùng Chung:** Quản lý tập trung toàn bộ state đóng/mở của các modal popup hệ thống.

---

## 3. MƯỜI ĐIỀU RĂN BẤT BIẾN TOÀN HỆ THỐNG (SYSTEM INVARIANTS)

Khi lập trình tính năng mới hoặc giải quyết xung đột khi Merge, **BẮT BUỘC TUÂN THỦ 10 ĐIỀU RĂN SAU ĐÂY**:

### Điều răn 1: Bảo mật Tuyệt đối Giá Vốn (COGS Invariant)
- **Quy tắc:** Quyền xem giá vốn (`cost:read`) **CHỈ CẤP CHO** `admin` và `sales_manager`.
- **Cấm:** Backend TUYỆT ĐỐI KHÔNG được trả trường `cost_price` cho `sales`, `warehouse`, `accountant` hoặc `customer`.
- **Frontend:** Cột giá vốn chỉ render khi cờ `isCostVisible` hoặc quyền cho phép.

### Điều răn 2: Phân quyền Ghi Kho Zero-Trust (Warehouse Write Invariant)
- **Quy tắc:** Quyền ghi kho (`inventory:write`: nhập, xuất, điều chỉnh, kiểm kê) **CHỈ CẤP CHO** `admin`, `warehouse_manager`, `warehouse`.
- **Cấm:** Nhân viên kinh doanh (`sales`) và Đại lý (`customer`) **BỊ CHẶN 100% (HTTP 403)** đối với mọi API ghi kho.

### Điều răn 3: Bảo Toàn Trục Điều Phối Clean URL (`DashboardPage.tsx`)
- **Cấm ghi đè:** Không được ghi đè toàn bộ file `DashboardPage.tsx`.
- **Cấm xóa Route:** Mọi URL route hiện hữu (`/inventory`, `/users`, `/categories`, `/audit-logs`, `/profile`, `/suppliers`, `/dealers`, `/orders`, `/price-books`, `/delivery-points`, `/discounts`, `/product-history`) bắt buộc phải được bảo toàn 100%.
- **Tách Component:** Khi có view mới, tạo file riêng trong `components/XView.tsx` rồi import vào, không viết hàng trăm dòng code thẳng vào DashboardPage.

### Điều răn 4: Bảo Toàn 8 Vai Trò Chuẩn Trong ROLES_LIST & RBAC Matrix
- Bất kỳ chỗ nào khai báo danh sách vai trò (`ROLES_LIST`, `availableRoles`, dropdown chọn vai trò, bộ lọc người dùng) bắt buộc phải có đủ **8 vai trò**:
  1. `admin` (Quản trị hệ thống)
  2. `sales_manager` (Quản lý kinh doanh)
  3. `sales` (Nhân viên kinh doanh)
  4. `warehouse` (Thủ kho)
  5. `warehouse_manager` (Quản lý kho)
  6. `accountant` (Kế toán công nợ)
  7. `purchasing` (Nhân viên mua hàng)
  8. `customer` (Đại lý)
- **Cấm tuyệt đối:** Không được bỏ sót vai trò `customer` ("Đại lý") gây lỗi mất quyền và nhảy sai vai trò mặc định như sự cố trước đây.

### Điều răn 5: Ràng Buộc Khóa Tài Khoản & Bàn Giao Địa Bàn (Handover Invariant)
- Khi khóa tài khoản nhân viên kinh doanh:
  1. Bắt buộc có lý do khóa (`lock_reason`).
  2. Thu hồi token ngay lập tức (`token_version + 1`).
  3. Kích hoạt cờ cảnh báo bàn giao đại lý (`dealers_needing_handover`).
  4. Đơn hàng mới của đại lý đó bị phong tỏa cho đến khi bàn giao xong cho Sales khác cùng địa bàn.

### Điều răn 6: Chống Gãy Vỡ Cơ Sở Dữ Liệu & Tương Thích Ngược Schema
- **Cấm Drop / Rename:** Tuyệt đối không xóa hoặc đổi tên các cột DB đang hoạt động.
- **Thêm cột mới:** Luôn có `DEFAULT` hoặc `NULL` trong SQL, và `Optional[...] = None` trong Pydantic schemas.
- **Migration Idempotent:** Trong `init_db.py`, luôn kiểm tra cột đã tồn tại chưa trước khi `ALTER TABLE`.

### Điều răn 7: Bắt Buộc Ghi Nhật Ký Kiểm Toán (Audit Logging Invariant)
- Mọi thao tác làm thay đổi dữ liệu (`POST`, `PUT`, `DELETE`, duyệt đơn, đổi giá, khóa tài khoản, xuất nhập kho) **BẮT BUỘC PHẢI GỌI** `log_audit_event` / `audit_service`.
- Cấm thực hiện các truy vấn cập nhật ngầm mà không để lại vết kiểm toán cho Quản trị viên.

### Điều răn 8: Bảo Toàn Giao Diện & Tránh Lỗi Tàng Hình Chữ (UI Contrast Rule)
- Nền sáng (`#ffffff`, `#f8fafc`): Màu chữ BẮT BUỘC là màu tối (`#0f172a`, `#1e293b`). CẤM đặt màu chữ trắng `#ffffff` hoặc xám nhạt gây lỗi tàng hình chữ.
- Hiển thị tên người dùng luôn có fallback: `{user.full_name || user.username}`.
- Luôn sử dụng Optional Chaining (`user?.prop`, `dealer?.prop`) để chống crash màn hình trắng (White Screen of Death).

### Điều răn 9: Bảo Toàn Đơn Vị Tính Quy Đổi Khi Lên Đơn & Xuất Kho
- Khi người dùng chọn ĐVT quy đổi (ví dụ: Thùng thay vì Chai), hệ thống bắt buộc nhân chính xác với `conversion_factor` để trừ đúng tồn kho cơ bản. Cấm trừ 1:1 làm thất thoát hàng hóa.

### Điều răn 10: Giữ Chỗ Tồn Kho & Hoàn Tồn Khi Hủy Đơn (Inventory Reservation Rule)
- Đơn hàng khi được tạo/duyệt sẽ chiếm giữ tồn khả dụng (`available_stock`).
- Khi đơn hàng bị HỦY (`CANCELLED`) hoặc Từ chối (`REJECTED`), hệ thống bắt buộc phải giải phóng số lượng giữ chỗ và hoàn trả lại tồn khả dụng.

### Điều răn 11: Kiểm Soát Bán Dưới Giá Sàn & Chiết Khấu Vượt Trần (Pricing & Discount Approval Invariant)
- **Bán dưới giá sàn:** Khi `unit_price < floor_price` hoặc thấp hơn bảng giá quy định, hệ thống **bắt buộc** chuyển đơn sang `PENDING_APPROVAL`, bật `requires_approval = True`, ghi rõ `approval_reason`. CẤM tự ý reset giá của người dùng.
- **Chiết khấu vượt mức:** Khi `effective_pct > best_pct` (kể cả khi chính sách là 0% mà nhập > 0%), hệ thống **bắt buộc** chuyển đơn sang `PENDING_APPROVAL`, bật `requires_approval = True`, ghi rõ `approval_reason`. CẤM dùng điều kiện lọt `if best_pct > 0 and ...`.
- **Hiển thị tab Chờ duyệt:** Đơn có trạng thái `PENDING_APPROVAL` bắt buộc phải xuất hiện ngay lập tức trong tab "Chờ duyệt" của màn hình Quản lý đơn hàng.

### Điều răn 12: Phân Quyền Duyệt Đơn Hai Cấp & Đồng Bộ Song Song (Order RBAC & Parity Invariant)
- **Phân quyền duyệt:** Nhân viên kinh doanh (`sales`) chỉ tạo đơn và xem đơn; tuyệt đối không có quyền tự duyệt đơn (`/approve`) hoặc từ chối (`/reject`). Quyền duyệt/từ chối chỉ cấp cho `admin` và `sales_manager`.
- **Đồng bộ song song:** Mọi quy tắc kiểm tra (công nợ đại lý, điểm giao hàng, ĐVT quy đổi, chiết khấu, giá sàn) phải được áp dụng đồng nhất 100% giữa `OrderCreateModal.tsx` và `SalesOrderEntry.tsx`.
- Xem chi tiết tại: `.agents/skills/sales-order-pricing-discount-guardrail/SKILL.md`.

---

## 4. QUY TRÌNH PHẪU THUẬT MÃ NGUỒN (SURGICAL EDIT PROTOCOL)

Khi chỉnh sửa code hoặc resolve merge conflict:
1. **Kiểm tra trước khi sửa:** Đọc kỹ 50 dòng trước và 50 dòng sau điểm cần can thiệp. Hiểu rõ luồng props, handlers, state.
2. **Sửa đúng điểm cần sửa (Surgical Precision):** Chỉ thay đổi đúng khối mã phục vụ yêu cầu. CẤM format lại toàn bộ file 1000 dòng vì sẽ làm bẩn git diff và gây conflict 100% với đồng đội.
3. **Khi Resolve Conflict (`git merge` / `git pull`):**
   - Đọc hiểu cả 2 vế: `<<<<<<< HEAD` (Code của mình) và `>>>>>>> origin/test` (Code của đồng đội).
   - **CẤM CHỌN MÙ QUÁNG:** Không được ấn "Accept All Current" hay "Accept All Incoming" vì sẽ xóa sạch tính năng của người khác.
   - **Ghép nối hài hòa:** Giữ lại toàn bộ các hàm mới, route mới, import mới của cả hai bên.

---

## 5. BỘ LỌC NGHIỆM THU TỰ ĐỘNG BẮT BUỘC TRƯỚC KHI BÀN GIAO / PUSH

Bất kỳ lượt chỉnh sửa hoặc merge nào **BẮT BUỘC PHẢI VƯỢT QUA 3 CỔNG KIỂM SOÁT** sau:

### Cổng 1: Kiểm thử Backend (Pytest Suite)
Chạy trong thư mục gốc hoặc thư mục `backend`:
```powershell
pytest backend/tests/rbac/test_rbac.py -v
pytest backend/tests/ -v
```
- **Tiêu chí:** 100% test cases PASS (đặc biệt là 16/16 tests trong `test_rbac.py` và các test vòng đời đơn hàng, giá vốn).

### Cổng 2: Kiểm thử Frontend Build (TypeScript & Vite)
Chạy trong thư mục `frontend`:
```powershell
npm run build
```
- **Tiêu chí:** Kết thúc với mã thoát `0`, **0 lỗi TypeScript (`tsc -b`)**, **0 lỗi đóng gói Vite**.

### Cổng 3: Kiểm tra Trạng thái Git (Git Status Clean)
```powershell
git status
```
- **Tiêu chí:** Không sót các file rác build tạm thời (`dist/`, `tsbuildinfo`), nhánh làm việc sạch sẽ trước khi commit/push.

---
**KẾT LUẬN:** Tuân thủ triệt để Skill này là cam kết danh dự của mọi kỹ sư và AI Agent để hệ thống ttcs Enterprise luôn vững chắc, an toàn tuyệt đối và không bao giờ gặp sự cố thoái lui chức năng.
