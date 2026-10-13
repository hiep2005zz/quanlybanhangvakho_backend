---
name: safe-merge-guardrail
description: >-
  Quy chuẩn tối cao và checklist kiểm soát toàn diện chống phá hỏng hệ thống khi Merge mã nguồn (Safe Merge Guardrail & Zero-Regression Protocol)
  cho toàn bộ hệ thống Quản lý Bán hàng & Kho (ttcs: FastAPI + React 19 TypeScript + Vite).
  Định nghĩa bản đồ 11 domain nghiệp vụ, các nguyên tắc bất biến (Invariants: RBAC, Session, Audit, COGS, Database),
  kỷ luật phân nhánh & phẫu thuật mã nguồn, cùng quy trình nghiệm thu tự động bắt buộc trước khi merge.
---

# SKILL: SAFE MERGE GUARDRAIL & ZERO-REGRESSION PROTOCOL
## Hệ thống Quản Lý Bán Hàng & Kho (ttcs)
**Kiến trúc:** Dual-Stack (FastAPI Backend + React 19 TypeScript Vite Frontend)  
**Mục tiêu tối thượng:** BẢO VỆ 100% TÍNH NĂNG ĐANG HOẠT ĐỘNG - KHÔNG THOÁI LUI MÃ NGUỒN (ZERO-REGRESSION) KHI BẤT KỲ AI MERGE CODE.

---

## 1. BẢN ĐỒ 11 DOMAIN NGHIỆP VỤ CỐT LÕI (SYSTEM DOMAIN MAP)

Bất kỳ lập trình viên hoặc AI Agent nào khi tham gia codebase này **BẮT BUỘC** phải hiểu rõ 11 phân hệ nghiệp vụ sau để không xóa nhầm hoặc can thiệp sai phạm vi:

| STT | Domain Nghiệp Vụ | File Backend Đầu Não | File Frontend Đầu Não | Điểm Quan Trọng Cấm Phá Vỡ |
|---|---|---|---|---|
| **1** | **Xác thực & Phiên (Auth & Session)** | `app/api/v1/endpoints/auth.py`<br>`app/core/security.py` | `src/services/sessionManager.ts`<br>`src/services/auth.ts`<br>`src/components/SecurityModal.tsx` | Chống User Enumeration; Khóa 15p sau 5 lần login sai; Thu hồi `token_version` khi logout/đổi pass; Sự kiện `USER_ROLE_UPDATED` realtime. |
| **2** | **Quản trị Người dùng & Phân quyền (RBAC)** | `app/api/v1/endpoints/users.py`<br>`app/api/v1/endpoints/user_import.py`<br>`app/core/rbac.py` | `src/components/UserManagementView.tsx`<br>`src/components/UserBulkImportModal.tsx` | 7 vai trò chuẩn; Chặn Self-Lockout của Admin; Khóa tài khoản bắt buộc nhập lý do (`reason`) và kích hoạt cờ cảnh báo bàn giao đại lý. |
| **3** | **Hồ sơ Cá nhân (Profile & Account)** | `app/api/v1/endpoints/profile.py` | `src/components/ProfileView.tsx` | Cấu trúc **1 Bảng duy nhất** gọn gàng; Cho phép đổi ảnh đại diện (avatar cropper), đổi họ tên, số điện thoại; Các trường hệ thống là Read-Only. |
| **4** | **Quản lý Đại lý & Khách hàng (Dealers)** | `app/api/v1/endpoints/dealers.py` | `src/components/DealerSearchView.tsx`<br>`src/components/CreateCustomerModal.tsx` | Tìm kiếm theo địa bàn phụ trách; Phân bổ đại lý cho Sales Rep; Quản lý trạng thái đại lý (Active / Inactive). |
| **5** | **Điểm Giao Hàng (Delivery Points)** | `app/api/v1/endpoints/delivery_points.py` | `src/components/DeliveryPointsView.tsx`<br>`src/components/DeliveryPointManager.tsx` | 1 đại lý có nhiều điểm nhận hàng; Kiểm tra tính hợp lệ địa chỉ, tọa độ và người liên hệ nhận hàng. |
| **6** | **Quản lý Nhà Cung Cấp (Suppliers)** | `app/api/v1/endpoints/suppliers.py` | `src/components/SupplierManagementView.tsx`<br>`src/components/SupplierModal.tsx` | Danh bạ nhà cung cấp, mã số thuế, công nợ, đánh giá đối tác cung ứng hàng hóa. |
| **7** | **Cây Danh Mục Hàng Hóa (Categories)** | `app/api/v1/endpoints/categories.py` | `src/components/CategoryManagementView.tsx`<br>`src/components/MoveCategoryModal.tsx` | Cấu trúc danh mục đa cấp (Cha - Con); Chức năng di chuyển danh mục an toàn không tạo vòng lặp vô tận (Cyclic Loop Prevention). |
| **8** | **Sản Phẩm, ĐVT & Tồn Kho (Inventory & Units)** | `app/api/v1/endpoints/products.py`<br>`app/api/v1/endpoints/inventory.py` | `src/components/ProductUnitModal.tsx`<br>`src/components/StockActionModal.tsx`<br>`src/components/ProductBulkImportModal.tsx` | Quy đổi đơn vị tính (Unit Conversion); Giao dịch Nhập/Xuất/Kiểm kê; **Quyền ghi kho chỉ dành cho Admin & Kho**; Import sản phẩm Excel hàng loạt. |
| **9** | **Bảng Giá & Chính Sách Giá (Price Books)** | `app/api/v1/endpoints/price_books.py` | `src/components/PriceBookManagementView.tsx`<br>`src/components/PriceUpdateModal.tsx` | Bảng giá theo kênh/đại lý; Quy trình phê duyệt điều chỉnh giá bán; Lịch sử hiệu lực giá. |
| **10** | **Đơn Hàng & Vòng Đời Bán Hàng (Sales Orders)** | `app/api/v1/endpoints/orders.py` | `src/components/OrderManagementView.tsx`<br>`src/components/SalesOrderEntry.tsx`<br>`src/components/ApproveOrderModal.tsx`<br>`src/components/RejectOrderModal.tsx` | Vòng đời đơn: `Pending` -> `Approved` / `Rejected` -> `Delivered` / `Cancelled`; Khóa giá tại thời điểm đặt đơn; Kiểm tra hạn mức nợ đại lý. |
| **11** | **Nhật Ký Kiểm Toán (Audit Logs)** | `app/api/v1/endpoints/audit_logs.py`<br>`app/services/audit_service.py` | `src/components/AuditLogView.tsx`<br>`src/components/AuditDetailModal.tsx`<br>`src/components/ProductAuditDrawer.tsx` | Bắt buộc ghi lại mọi vết thao tác nhạy cảm (Ai, Làm gì, Vào lúc nào, Dữ liệu cũ -> Dữ liệu mới, IP/UserAgent). |

---

## 2. BẢY NGUYÊN TẮC BẤT BIẾN TOÀN HỆ THỐNG (SYSTEM INVARIANTS)

Khi merge code, nếu bất kỳ file nào vi phạm 1 trong 7 nguyên tắc dưới đây thì **BẮT BUỘC TỪ CHỐI (REJECT PR NGAY LẬP TỨC)**:

### Bất biến 1: RBAC Zero-Trust & Bảo Mật Tuyệt Mật Giá Vốn (COGS Guard)
1. **Server-Side Default Deny:** Mọi route Backend (trừ login công khai) bắt buộc có dependency:
   - `Depends(require_permission(Permission.XYZ))` hoặc `Depends(require_roles([...]))`.
2. **Quy định Giá vốn (`cost_price`) và Biên lợi nhuận (`profit_margin`):**
   - **CHỈ ĐƯỢC PHÉP TRẢ VỀ** cho 2 vai trò: `admin` và `sales_manager`.
   - Backend **BẮT BUỘC STRIP (GỠ BỎ)** trường này trong JSON response khi người gọi là `sales`, `warehouse`, `accountant` hoặc `customer`.
   - Frontend không được hiển thị cột giá vốn cho các vai trò không có quyền xem giá vốn.
3. **Quy định Quyền ghi kho (`inventory:write`):**
   - CHỈ cấp cho: `admin`, `warehouse_manager`, `warehouse`.
   - Nhân viên kinh doanh (`sales`) **BỊ CHẶN 100% (403 Forbidden)** đối với mọi hành động ghi/nhập/xuất/điều chỉnh kho.

### Bất biến 2: Bảo Vệ Trục Điều Phối Clean URL (`DashboardPage.tsx`)
`DashboardPage.tsx` là bộ não điều phối giao diện. Khi merge:
1. **Bảo tồn toàn bộ Clean URL mapping hiện hữu:**
   - `/inventory`, `/users`, `/categories`, `/audit-logs`, `/profile`, `/suppliers`, `/dealers`, `/orders`, `/price-books`, `/delivery-points`.
2. **Không ghi đè hoặc xóa các State Modal dùng chung:**
   - `isUserMenuOpen`, `isCreateCustomerModalOpen`, `isCreateAccountModalOpen`, `isMoveCategoryModalOpen`, `isBulkImportOpen`, `isStockActionModalOpen`, `isUnitModalOpen`, `isOrderCreateModalOpen`.
3. **Kiểm tra phân quyền trước khi render Tab:**
   - Sử dụng các cờ chuẩn: `isAdmin`, `canManageCategories`, `canWriteInventory`, `canViewDealers`, `canManageSuppliers`, `canConfigUnit`.
   - Nếu user không có quyền vào tab, bắt buộc hiển thị `<AccessDeniedView />` thay vì màn hình trắng.

### Bất biến 3: Session Realtime & Chống Mất Trạng Thái Đăng Nhập
1. Hệ thống sử dụng token versioning (`token_version`). Khi tài khoản bị đổi mật khẩu hoặc bị Admin khóa, toàn bộ token cũ bị vô hiệu hóa ngay lập tức.
2. Frontend lắng nghe sự kiện `USER_ROLE_UPDATED` để đồng bộ quyền tức thì mà không cần người dùng đăng xuất thủ công.
3. Luôn bảo tồn cơ chế heartbeat và lưu trữ an toàn trong `sessionManager.ts`.

### Bất biến 4: Audit Trail Không Được Bị Bỏ Quên
Mọi endpoint thực hiện các hành động:
- `POST` (Tạo mới)
- `PUT` / `PATCH` (Cập nhật)
- `DELETE` (Xóa)
- `POST /import` (Nạp hàng loạt)
- `POST /lock`, `POST /unlock`, `POST /approve`, `POST /reject`
**BẮT BUỘC PHẢI GỌI** `log_audit_event` / `audit_service` để lưu lại vết thao tác. Cấm tuyệt đối thao tác ngầm vào DB mà không ghi audit log.

### Bất biến 5: An Toàn Schema Database & Tương Thích Ngược
1. **Cấm đổi tên (Rename) hoặc Xóa (Drop) cột/bảng đang chạy** trong cơ sở dữ liệu.
2. Khi thêm cột mới vào bảng:
   - Trong SQL: Luôn có giá trị mặc định (`DEFAULT ...`) hoặc cho phép `NULL`.
   - Trong Pydantic: Khai báo dạng tùy chọn `Optional[T] = None` để dữ liệu cũ không bị lỗi validate 422.
3. Trong migration (`init_db.py`): Viết theo cơ chế **Idempotent** (chỉ thêm cột nếu cột chưa tồn tại).

### Bất biến 6: Chuẩn Giao Diện & Tránh Lỗi Tàng Hình Chữ (UI Contrast & Accessibility)
1. **Quy tắc tương phản bắt buộc:**
   - Nền sáng (`#ffffff`, `#f8fafc`, `#eff6ff`): Màu chữ BẮT BUỘC là màu tối (`#0f172a`, `#1e293b`, `#334155`). CẤM đặt màu chữ `#ffffff` hoặc xám nhạt gây lỗi tàng hình chữ.
   - Nền tối / Gradient: Màu chữ là màu sáng (`#ffffff`, `#f8fafc`).
2. **Hiển thị thông tin người dùng an toàn:**
   - Luôn dùng fallback: `{user.full_name || user.username}`.
   - Luôn dùng Optional Chaining: `user?.role`, `user?.branch`.
3. **Responsive Mobile:** Viewport hỗ trợ tối thiểu `360px` cho nhân viên thị trường.

### Bất biến 7: Tính Toàn Khối Của Trang Profile (`ProfileView.tsx`)
- Trang Profile được thiết kế theo chuẩn **1 Bảng duy nhất (Single Compact Card)**.
- Khi cập nhật profile, không được tách vụn thành các box/bảng rời rạc trôi nổi trên màn hình.

---

## 3. KỸ THUẬT PHẪU THUẬT CODE & GIẢI QUYẾT CONFLICT KHI MERGE (SURGICAL MERGE PROTOCOL)

Khi có xung đột mã nguồn (Merge Conflict), lập trình viên và Agent phải xử lý theo **Quy trình Phẫu thuật 4 bước**:

```
      [Xung Đột Code (Conflict Detected)]
                      │
                      ▼
        ┌───────────────────────────┐
        │  1. Đọc hiểu cả 2 vế     │
        │  HEAD (mình) vs INCOMING  │
        └─────────────┬─────────────┘
                      │
                      ▼
        ┌───────────────────────────┐
        │  2. Phẫu thuật ghép nối   │
        │  (KHÔNG chọn Accept All!) │
        └─────────────┬─────────────┘
                      │
                      ▼
        ┌───────────────────────────┐
        │  3. Biên dịch kiểm tra    │
        │  npm run build + pytest   │
        └─────────────┬─────────────┘
                      │
                      ▼
        ┌───────────────────────────┐
        │  4. Commit & Đẩy nhánh    │
        └───────────────────────────┘
```

### Chi tiết cách resolve các file "Điểm Nóng":
1. **Tại `DashboardPage.tsx`:**
   - Nếu cả 2 nhánh cùng thêm Tab mới: **Giữ cả 2 Tab**. Gom định tuyến Clean URL của cả hai vào chung switch-case hoặc map.
   - Nếu cả 2 nhánh cùng thêm Modal: Giữ cả 2 modal state và đặt cạnh nhau.
2. **Tại `api.ts`:**
   - Nếu cả 2 nhánh cùng thêm endpoint: **Giữ lại cả hai**. Tốt nhất là tách thành các file API riêng (`dealerSearchApi.ts`, `supplierApi.ts`).
3. **Tại `schema.sql` / `init_db.py`:**
   - Giữ lại tất cả các cột mới của cả 2 bên. Không được bỏ cột của đồng đội.
4. **Tại CSS (`index.css` hoặc CSS module):**
   - Không ghi đè class toàn cục. Đặt tên class có prefix theo module (ví dụ `.dealer-search-card`, `.supplier-modal-btn`).

---

## 4. BỘ KIỂM TRA NGHIỆM THU TỰ ĐỘNG BẮT BUỘC (PRE-MERGE ACCEPTANCE GATES)

Bất kỳ Pull Request nào trước khi merge vào nhánh chính (`test` hoặc `main`) đều **BẮT BUỘC VƯỢT QUA 2 CỔNG TỰ ĐỘNG NÀY**:

### Cổng 1: Kiểm tra Frontend Build (0 Lỗi TypeScript & 0 Lỗi Vite)
Mở terminal tại thư mục `frontend/` và chạy:
```powershell
npm run build
```
- **Tiêu chuẩn đạt:** Lệnh trả về Exit Code `0`.
- **Lỗi phổ biến cần chặn:**
  - TypeScript thiếu type definition (`tsc -b` báo lỗi TS2322, TS2339).
  - Thiếu thẻ đóng JSX (`error TS17008`).
  - Import đường dẫn file sai hoặc file đã bị xóa.

### Cổng 2: Kiểm tra Bộ Test Suites Backend (100% Pass)
Mở terminal tại thư mục `backend/` và chạy:
```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```
*(Hoặc chạy riêng suite RBAC cốt lõi:* `pytest tests/rbac/ -v`*)*
- **Tiêu chuẩn đạt:** Tất cả các test cases phải hiển thị xanh `PASSED` (đặc biệt là 37+ test case RBAC, password invalidation, account locking, dealer handover).
- **Lỗi phổ biến cần chặn:**
  - Test bị fail do xóa nhầm check permission trong decorator.
  - Test bị fail do response JSON làm lộ trường giá vốn `cost_price`.

---

## 5. CHECKLIST TRƯỚC KHI BẤM "CONFIRM MERGE" (DÀNH CHO REVIEWER / LEAD)

Trước khi bấm Merge trên GitHub hoặc Git CLI, Reviewer phải tích đủ 10 ô sau:

- [ ] **1. Không làm hỏng routing:** Truy cập trực tiếp các Clean URL (`/inventory`, `/dealers`, `/suppliers`, `/categories`, `/profile`, `/orders`) vẫn hiển thị đúng.
- [ ] **2. Không bypass quyền:** Kiểm tra code Backend xem có endpoint nào thiếu decorator xác thực/phân quyền không.
- [ ] **3. Bảo mật giá vốn:** Thử đăng nhập bằng tài khoản `sales` hoặc `kho`, kiểm tra API sản phẩm có bị lọt `cost_price` không.
- [ ] **4. Bảo vệ chức năng khóa tài khoản:** Khóa tài khoản có bắt buộc nhập `reason` và có thu hồi phiên đăng nhập ngay lập tức không.
- [ ] **5. Không hỏng giao diện Profile:** Trang hồ sơ vẫn nằm gọn gàng trong 1 bảng duy nhất, đổi avatar và lưu tên/SĐT hoạt động tốt.
- [ ] **6. Không tạo lỗi giao diện tàng hình:** Nền sáng chữ đen, nền tối chữ sáng. Không có chữ trắng trên nền trắng.
- [ ] **7. Ghi nhận Audit Log đầy đủ:** Các thao tác thêm/sửa/xóa mới đều gọi `log_audit_event`.
- [ ] **8. Độc lập Component:** Tính năng mới được viết trong file riêng ở `frontend/src/components/`, không nhồi nhét trực tiếp hàng trăm dòng vào `DashboardPage.tsx`.
- [ ] **9. Build Frontend PASS:** `npm run build` kết thúc với 0 lỗi.
- [ ] **10. Test Backend PASS:** `pytest` kết thúc với 100% test passed.

---

## 6. HÀNH VI TỪ CHỐI & XỬ LÝ KHỦNG HOẢNG KHI MERGE LỖI (EMERGENCY ROLLBACK)

### Nếu phát hiện PR vi phạm bất biến:
Người review hoặc Agent **BẮT BUỘC ĐÓNG PR** kèm thông báo:
> *"PR bị từ chối do vi phạm Quy chuẩn An toàn Hệ thống (Skill: safe-merge-guardrail). Lý do: [Ghi rõ lý do, ví dụ: làm hỏng Clean URL / bỏ quên check quyền RBAC / gây lỗi tàng hình chữ]. Vui lòng sửa lại theo chuẩn trước khi yêu cầu merge."*

### Nếu lỡ merge nhầm gây hỏng hệ thống:
Lập tức hoàn tác an toàn tại nhánh chính:
```powershell
git checkout test
git pull origin test
git revert -m 1 <commit-hash-vua-merge>
git push origin test
```
Sau đó kiểm tra lại bằng `npm run build` và `pytest` để khôi phục trạng thái ổn định cho toàn đội.
