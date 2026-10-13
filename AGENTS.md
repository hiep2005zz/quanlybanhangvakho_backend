# QUY CHUẨN PHÁT TRIỂN & CHỐNG HỎNG TÍNH NĂNG HỆ THỐNG (MANDATORY AGENT PROTOCOL)

Dự án này là hệ thống Quản lý kho & Bán hàng (FastAPI + React 19 TypeScript + Vite).
TẤT CẢ các Agent khi thao tác trên codebase này **BẮT BUỘC** phải tuân theo các chỉ thị nghiêm ngặt dưới đây:

---

## 1. NGUYÊN TẮC BẢO TOÀN HỆ THỐNG CŨ (ZERO-REGRESSION FIRST)
- **Kiểm tra trước khi code**: Phải đọc kỹ code hiện tại của component/endpoint cần can thiệp. Hiểu rõ luồng dữ liệu (props, state, hook, quyền hạn).
- **Sửa phẫu thuật (Surgical Edits)**: Chỉ sửa đúng điểm cần sửa. Tuyệt đối KHÔNG ghi đè toàn bộ file, KHÔNG xóa props/handlers cũ, KHÔNG làm mất logic xử lý ngoại lệ hay validation đã có.
- **Không phá vỡ cấu trúc điều hướng**:
  - `DashboardPage.tsx` là component điều phối trung tâm với Clean URL (`/inventory`, `/users`, `/categories`, `/audit-logs`, `/profile`, `/suppliers`, `/dealers`).
  - Khi thêm tab/màn hình mới, phải bảo toàn toàn bộ URL mapping, menu sidebar, menu popover và các state modal hiện hữu.

---

## 2. QUY CHUẨN KHI PHÁT TRIỂN TÍNH NĂNG MỚI (NEW FEATURE PROTOCOL)
- **Tách Component độc lập**: Khi tạo tính năng mới cho Frontend, LUÔN tạo file component riêng trong `frontend/src/components/` (ví dụ: `XFeatureModal.tsx`, `XFeatureView.tsx`). Không viết trực tiếp hàng trăm dòng code vào `DashboardPage.tsx`.
- **Ràng buộc phân quyền RBAC**:
  - Frontend: Mọi tính năng, nút bấm, tab mới phải được kiểm tra quyền theo các cờ chuẩn (`isAdmin`, `canManageCategories`, `canWriteInventory`, `canViewDealers`, `canManageSuppliers`, `canConfigUnit`). Nếu người dùng không có quyền truy cập tab, phải bọc bằng `<AccessDeniedView />`.
  - Backend: Mọi endpoint mới BẮT BUỘC có dependency xác thực và phân quyền (`Depends(require_permission(...))` hoặc `Depends(require_roles(...))`). TUYỆT ĐỐI KHÔNG mở endpoint công khai trừ API đăng nhập/khởi tạo ban đầu.
- **Bảo mật giá vốn & kho hàng**:
  - Quyền xem giá vốn (`cost:read`) CHỈ cấp cho `admin` và `sales_manager`.
  - Quyền ghi kho (`inventory:write`) CHỈ cấp cho `admin`, `warehouse_manager`, `warehouse`.
  - Nhân viên kinh doanh (`sales`) TUYỆT ĐỐI KHÔNG được xem giá vốn và KHÔNG được ghi kho.
- **Ghi nhật ký thao tác (Audit Log)**:
  - Mọi thao tác thêm/sửa/xóa/nhập dữ liệu mới trên Backend đều phải gọi `log_audit_event` / `audit_service` để lưu lại vết thao tác cho Quản trị viên.

---

## 3. TIÊU CHUẨN GIAO DIỆN & ĐỘ TƯƠNG PHẢN (UI & ACCESSIBILITY)
- **Quy tắc tương phản màu sắc**:
  - Nền sáng (`#ffffff`, `#f8fafc`, `#eff6ff`): Màu chữ BẮT BUỘC là màu tối (`#0f172a`, `#1e293b`). CẤM đặt màu chữ `#ffffff` hoặc xám nhạt gây lỗi tàng hình chữ.
  - Nền tối / Gradient: Màu chữ là màu sáng (`#ffffff`, `#f8fafc`).
- **An toàn hiển thị thông tin người dùng**:
  - Khi hiển thị họ tên người dùng, LUÔN LUÔN có fallback: `{user.full_name || user.username}`.
  - Luôn dùng Optional Chaining (`user?.prop`) để chống crash màn hình trắng (White Screen of Death).

---

## 4. BẢO TOÀN PHIÊN ĐĂNG NHẬP & STATE REALTIME
- Hệ thống hỗ trợ 7 vai trò chuẩn: `admin`, `sales_manager`, `sales`, `warehouse_manager`, `warehouse`, `accountant`, `customer`.
- Không được can thiệp làm hỏng cơ chế cập nhật người dùng realtime: `onUserUpdated`, `saveClientSession`, `sessionManager`, sự kiện `USER_ROLE_UPDATED`.
- Token Versioning: Tôn trọng cơ chế thu hồi token trên máy chủ (`token_version`).

---

## 5. BẮT BUỘC KIỂM THỬ XÁC MINH TRƯỚC KHI BÀN GIAO (MANDATORY VERIFICATION)
1. **Frontend**: Chạy `npm run build` trong thư mục `frontend` và xác nhận build thành công với 0 lỗi TypeScript (`tsc -b`) và 0 lỗi Vite.
2. **Backend**: Nếu có sửa backend, chạy `pytest` để kiểm tra test suites (đặc biệt là `tests/rbac/`).
3. **Đọc Skill bắt buộc**: Luôn tuân thủ và đọc kỹ 7 Skill bảo vệ cốt lõi:
   - `.agents/skills/sales-order-pricing-discount-guardrail/SKILL.md` (Bảo vệ phân hệ Đơn hàng, Định giá sàn, Chiết khấu vượt trần & Phê duyệt hai cấp)
   - `.agents/skills/system-feature-invariants-guardrail/SKILL.md` (Quy chuẩn tối cao ràng buộc toàn bộ 13 phân hệ & chống thoái lui chức năng khi phát triển/merge)
   - `.agents/skills/agent-zero-damage-protocol/SKILL.md` (Quy chuẩn vận hành AI Agent: Bảo toàn 100% chức năng và chống phá vỡ hệ thống)
   - `.agents/skills/safe-merge-guardrail/SKILL.md` (Quy chuẩn tối cao bảo vệ toàn hệ thống khi Merge mã nguồn)
   - `.agents/skills/regression-prevention/SKILL.md` (Checklist chống thoái lui tính năng)
   - `.agents/skills/sprint-1-preservation-guardrail/SKILL.md` (Bảo vệ kiến trúc Sprint 1: Auth, Session, RBAC/ABAC, Bảo mật Giá vốn & Địa bàn)
   - `.agents/skills/conflict-prevention/SKILL.md` (Kỷ luật phân nhánh Git & chống xung đột mã nguồn giữa các thành viên)

