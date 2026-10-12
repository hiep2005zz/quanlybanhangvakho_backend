---
name: regression-prevention
description: >-
  Quy chuẩn bắt buộc và checklist kiểm soát nghiêm ngặt khi bảo trì, sửa lỗi hoặc phát triển tính năng mới tại dự án ttcs.
  Bảo đảm Zero-Regression: Không làm hỏng tính năng cũ, không làm vỡ giao diện (UI contrast, responsive),
  bảo vệ phân quyền RBAC Zero-Trust, và quy trình nghiệm thu tự động trước khi bàn giao.
---

# QUY TRÌNH KIỂM SOÁT AN TOÀN KHI PHÁT TRIỂN & CHỐNG HỎNG HỆ THỐNG (TTCS SAFE PROTOCOL)

Khi Agent nhận bất kỳ tác vụ nào (sửa bug, thêm modal, thêm trang mới, thêm API mới) trong dự án này, Agent **BẮT BUỘC** phải rà soát qua checklist sau đây:

---

## I. BẢN ĐỒ KIẾN TRÚC HỆ THỐNG CẦN BẢO VỆ
1. **Frontend Hub (`DashboardPage.tsx`)**:
   - Quản lý Clean URL: `/inventory`, `/users`, `/categories`, `/audit-logs`, `/profile`, `/suppliers`, `/dealers`.
   - Quản lý State Modal: `isUserMenuOpen`, `isCreateCustomerModalOpen`, `isCreateAccountModalOpen`, `isMoveCategoryModalOpen`, `isBulkImportOpen`, `isStockActionModalOpen`, `isUnitModalOpen`, `isOrderCreateModalOpen`.
   - Cờ quyền chuẩn: `isAdmin`, `canManageCategories`, `canWriteInventory`, `canViewDealers`, `canManageSuppliers`, `canConfigUnit`.
2. **Backend RBAC Zero-Trust (`app/core/rbac.py`)**:
   - 7 Vai trò: `admin`, `sales_manager`, `sales`, `warehouse_manager`, `warehouse`, `accountant`, `customer`.
   - 2 Vùng dữ liệu tuyệt mật:
     - Giá vốn (`cost:read`): CHỈ `admin` và `sales_manager` được xem.
     - Thao tác kho (`inventory:write`): CHỈ `admin`, `warehouse_manager`, `warehouse` được ghi.
     - Nhân viên kinh doanh (`sales`): TUYỆT ĐỐI BỊ CHẶN xem giá vốn và ghi kho.

---

## II. CHECKLIST KHI PHÁT TRIỂN TÍNH NĂNG MỚI (NEW FEATURE CHECKLIST)

### 1. Phía Frontend (Giao Diện & Trải Nghiệm)
- [ ] **Tách Component độc lập**: Tạo file riêng tại `frontend/src/components/` (ví dụ `NewFeatureView.tsx` hoặc `NewFeatureModal.tsx`). Không nhồi nhét logic hàng trăm dòng trực tiếp vào `DashboardPage.tsx`.
- [ ] **Kiểm tra màu sắc & Độ tương phản (UI Contrast)**:
  - Nếu container có nền trắng (`#ffffff`, `#f8fafc`, `#eff6ff`): Màu chữ BẮT BUỘC là màu tối (`#0f172a`, `#1e293b`). CẤM đặt màu chữ `#ffffff` (trắng trên nền trắng).
  - Nếu container nền tối: Màu chữ BẮT BUỘC là màu sáng (`#ffffff`, `#f8fafc`).
- [ ] **Hiển thị thông tin người dùng an toàn**:
  - Luôn dùng fallback: `{user.full_name || user.username}`.
  - Luôn dùng Optional Chaining: `user?.role`, `user?.permissions`.
- [ ] **Nếu thêm Tab/Trang mới vào Dashboard**:
  - Bổ sung định tuyến Clean URL an toàn trong `useState<...>(() => { ... })` và `handleCleanNavigation`.
  - Bổ sung nút bấm trong Sidebar Drawer và Popover Menu với hiệu ứng hover và active state chuẩn.
  - Phải bọc View bằng `<AccessDeniedView />` nếu người dùng không đủ quyền.
- [ ] **Nếu thêm Modal/Drawer mới**:
  - Đặt `z-index` chuẩn: Modal Overlay (`z-index: 1100`), Drawer (`z-index: 1050`), Popover (`z-index: 1000`).
  - Hỗ trợ đóng modal khi bấm phím `Esc` hoặc bấm ra ngoài overlay.
  - Sử dụng hệ thống thông báo chuẩn `emitStatusToast({ title: '...', message: '...' })`.

### 2. Phía Backend (API & Cơ Sở Dữ Liệu)
- [ ] **Ràng buộc phân quyền RBAC trên từng Endpoint**:
  - Dùng decorator `Depends(require_permission(Permission.XYZ))` hoặc `Depends(require_roles([...]))`.
  - CẤM để lộ endpoint mở mà không có xác thực ngoại trừ endpoint login công khai.
- [ ] **Bảo vệ Schema Pydantic**:
  - Mọi trường mới thêm vào model request/response phải có giá trị mặc định (`Optional[T] = None`) để không làm gãy các client cũ hoặc data JSON cũ.
- [ ] **Ghi nhận Audit Log**:
  - Mọi hành động Tạo mới (POST), Chỉnh sửa (PUT/PATCH), Xóa (DELETE), Nhập dữ liệu (IMPORT) đều phải gọi `log_audit_event` để lưu lại vết thao tác.

---

## III. QUY TRÌNH 4 BƯỚC THỰC HIỆN "ZERO REGRESSION"
1. **Bước 1 - Khảo sát (Inspect)**:
   Đọc kỹ file hiện tại trước khi chạm vào mã nguồn. Xác định rõ các props, state và logic phụ thuộc.
2. **Bước 2 - Phẫu thuật tối thiểu (Surgical Edits)**:
   Chỉ thay đổi đúng khối mã cần thiết. Giữ nguyên tất cả các hàm, sự kiện, props và comment xung quanh.
3. **Bước 3 - Xác minh tự động (Verification)**:
   - Frontend: Bắt buộc chạy `npm run build` trong `frontend/` (phải đạt 0 lỗi `tsc` và 0 lỗi `vite`).
   - Backend: Nếu sửa backend, chạy `pytest backend/tests/` để bảo đảm toàn bộ test suites đều PASS.
4. **Bước 4 - Rà soát giao diện (Sanity Check)**:
   Tự đặt câu hỏi: "Màu chữ có bị chìm vào màu nền không?", "Tính năng cũ bên cạnh có bị mất hoặc lệch layout không?".
