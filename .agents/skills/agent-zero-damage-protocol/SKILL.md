---
name: agent-zero-damage-protocol
description: >-
  Quy chuẩn tối cao hướng dẫn và cưỡng chế hành vi của AI Agent (AI Agent Zero-Damage Operating Protocol):
  Phân tích toàn bộ hệ thống TTCS, thiết lập 10 điều răn bất di bất dịch (Iron Commandments),
  quy trình thao tác mã nguồn 5 bước bằng tool calls (Surgical Tooling Protocol),
  bộ rào chắn an toàn RBAC & Session, cùng quy trình nghiệm thu tự động bắt buộc trước khi kết thúc lượt.
---

# SKILL: AI AGENT ZERO-DAMAGE OPERATING PROTOCOL
## Cẩm Nang Vận Hành AI Agent - Bảo Toàn Tuyệt Đối Hệ Thống (TTCS System Guardian)
**Áp dụng cho:** Tất cả các AI Coding Agents (Antigravity Agent, Gemini Agent, Subagents, Pair Programming AI).  
**Triết lý cốt lõi:** *"First, Do No Harm - Trước hết, tuyệt đối không gây tổn hại cho hệ thống hiện có."*

---

## 1. MƯỜI ĐIỀU RĂN BẤT DI BẤT DỊCH DÀNH CHO AGENT (10 IRON COMMANDMENTS)

Khi thao tác trên repository `ttcs`, Agent **BẮT BUỘC TUÂN THỦ 10 ĐIỀU RĂN SAU ĐÂY DƯỚI MỌI HOÀN CẢNH**:

### Điều 1: CẤM Ghi Đè Mù Quáng (Zero Blind Overwrite)
- **TUYỆT ĐỐI KHÔNG** dùng `write_to_file` với `Overwrite: true` lên các file mã nguồn cốt lõi lớn (như `DashboardPage.tsx`, `api.ts`, `UserManagementView.tsx`, `init_db.py`, `ProfileView.tsx`).
- Bắt buộc dùng `replace_file_content` để **phẫu thuật cục bộ (Surgical Edit)**. Chỉ sửa đúng 5-20 dòng cần thiết. Giữ nguyên 100% logic, state, props, và comment xung quanh.

### Điều 2: CẤM Phá Vỡ Cấu Trúc Điều Hướng Clean URL (`DashboardPage.tsx`)
- `DashboardPage.tsx` là bộ điều phối trung tâm. CẤM xóa, cấm đổi tên, cấm làm gãy bất kỳ Clean URL nào:
  `/inventory`, `/users`, `/categories`, `/audit-logs`, `/profile`, `/suppliers`, `/dealers`, `/orders`, `/price-books`, `/delivery-points`.
- Mọi logic dọn query parameter (`params.has('tab')`) phải được duy trì để URL luôn sạch đẹp.

### Điều 3: CẤM Tiết Lộ Giá Vốn & Biên Lợi Nhuận (COGS Confidentiality)
- Dữ liệu `cost_price` (giá vốn) và `profit_margin` (biên lợi nhuận) là bí mật kinh doanh cấp cao.
- **CHỈ ĐƯỢC PHÉP TRẢ VỀ** cho 2 vai trò: `admin` và `sales_manager`.
- Khi viết/sửa bất kỳ API sản phẩm, kho, hay báo cáo nào, Backend **BẮT BUỘC STRIP (GỠ BỎ)** trường này đối với `sales`, `warehouse`, `accountant`, `customer`.
- Frontend cấm hiển thị cột giá vốn cho các vai trò bị cấm.

### Điều 4: CẤM Cấp Quyền Ghi Kho Cho Nhân Viên Kinh Doanh (Sales Inventory Barrier)
- Quyền ghi kho (`inventory:write`: nhập, xuất, kiểm kê, điều chỉnh) **CHỈ CẤP CHO**: `admin`, `warehouse_manager`, `warehouse`.
- Nhân viên kinh doanh (`sales`) **BỊ CHẶN 100% (403 Forbidden)**. Agent tuyệt đối không được mở quyền kho cho sales vì bất kỳ lý do nào.

### Điều 5: CẤM Tạo Endpoint Mở (No Unprotected Endpoints)
- Mọi route Backend mới (trừ login công khai `/api/v1/auth/login`) **BẮT BUỘC CÓ DEPENDENCY BẢO VỆ**:
  `Depends(require_permission(...))` hoặc `Depends(require_roles(...))`.
- Cấm bỏ dependency xác thực chỉ để "tiện test nhanh".

### Điều 6: CẤM Làm Lỗi Tàng Hình Chữ (UI Contrast Rule)
- Nền sáng (`#ffffff`, `#f8fafc`, `#eff6ff`): Màu chữ BẮT BUỘC là màu tối (`#0f172a`, `#1e293b`, `#334155`). CẤM đặt màu chữ `#ffffff` hoặc xám mờ gây lỗi chữ tàng hình trên nền trắng.
- Nền tối / Gradient: Màu chữ là màu sáng (`#ffffff`, `#f8fafc`).

### Điều 7: CẤM Phá Vỡ Tính Toàn Khối Trang Profile (`ProfileView.tsx`)
- Trang Profile của hệ thống đã được quy chuẩn hóa thành **1 BẢNG DUY NHẤT (Single Compact Card)**.
- Khi chỉnh sửa trang Profile, Agent cấm tách vụn thành 4 box/bảng rời rạc trôi nổi trên màn hình.

### Điều 8: CẤM Gây Crash Màn Hình Trắng (No White Screen of Death)
- Khi render tên người dùng trên Frontend, **LUÔN LUÔN CÓ FALLBACK**:
  `{user.full_name || user.username}`
- Luôn sử dụng Optional Chaining (`user?.role`, `profile?.branch`, `order?.items?.length`).

### Điều 9: CẤM Phá Hỏng Phiên Đăng Nhập Realtime
- Không được can thiệp làm hỏng cơ chế trong `frontend/src/services/sessionManager.ts`.
- Bảo toàn sự kiện `USER_ROLE_UPDATED`, broadcast channel đa tab, cơ chế thu hồi token theo phiên bản `token_version`.

### Điều 10: CẤM Báo Xong Khi Chưa Chạy Kiểm Thử Xác Minh
- Agent **CẤM NÓI HOÀN THÀNH** nếu chưa chạy và xác nhận:
  1. `npm run build` trong `frontend` -> **0 lỗi TypeScript, 0 lỗi Vite**.
  2. `pytest tests/rbac` trong `backend` -> **100% Passed**.

---

## 2. QUY TRÌNH 5 BƯỚC PHẪU THUẬT MÃ NGUỒN CỦA AGENT (SURGICAL PROTOCOL)

Mọi thao tác chỉnh sửa mã nguồn của Agent phải tuân theo chuỗi hành động nghiêm ngặt sau:

```
    [Nhận Nhiệm Vụ]
           │
           ▼
  ┌──────────────────────────────────┐
  │ Bước 1: Khảo sát mã nguồn        │
  │ Dùng view_file đọc đủ ngữ cảnh   │
  └────────────────┬─────────────────┘
           │
           ▼
  ┌──────────────────────────────────┐
  │ Bước 2: Lập phạm vi phẫu thuật   │
  │ Xác định chính xác Start/EndLine │
  └────────────────┬─────────────────┘
           │
           ▼
  ┌──────────────────────────────────┐
  │ Bước 3: Thực thi thay thế        │
  │ Dùng replace_file_content        │
  │ Kiểm tra đủ thẻ đóng JSX / HTML  │
  └────────────────┬─────────────────┘
           │
           ▼
  ┌──────────────────────────────────┐
  │ Bước 4: Chạy kiểm thử xác minh   │
  │ npm run build & pytest           │
  └────────────────┬─────────────────┘
           │
           ▼
  ┌──────────────────────────────────┐
  │ Bước 5: Phân tích log & Bàn giao │
  │ Chỉ bàn giao khi Exit Code = 0   │
  └──────────────────────────────────┘
```

### Hướng dẫn chi tiết từng bước:
1. **Bước 1 (Khảo sát):** Đọc trước tối thiểu 30-50 dòng xung quanh điểm cần sửa. Nắm chắc tên biến, tên hook, kiểu dữ liệu props.
2. **Bước 2 (Phạm vi):** Đặt `StartLine` và `EndLine` bao bọc đúng khối cần thay thế. `TargetContent` phải khớp 100% từng dấu cách và thụt lề (indentation).
3. **Bước 3 (Kiểm tra thẻ):** Khi sửa JSX/TSX, **luôn đếm số thẻ mở `<div>` và thẻ đóng `</div>`**. Thiếu 1 thẻ đóng sẽ lập tức gây lỗi build `TS17008`.
4. **Bước 4 (Kiểm thử bắt buộc):** Chạy lệnh kiểm thử bằng `run_command`:
   - Frontend: `npm run build` tại `d:\ttcs\frontend`
   - Backend: `.\venv\Scripts\python.exe -m pytest tests/rbac -v` tại `d:\ttcs\backend`
5. **Bước 5 (Bàn giao):** Đọc kết quả log trả về. Nếu có lỗi, sửa ngay lập tức trong phiên làm việc. Tuyệt đối không để user phát hiện lỗi biên dịch.

---

## 3. CẨM NANG AN TOÀN THEO TỪNG PHÂN HỆ NGHIỆP VỤ (MODULE PLAYBOOK)

### A. Phân hệ Frontend Hub (`DashboardPage.tsx`)
- **Nguyên tắc:** File này dài hơn 2900 dòng, là xương sống của toàn bộ ứng dụng.
- **Khi thêm Tab/Màn hình mới:**
  1. Tạo component riêng tại `frontend/src/components/TenTinhNangView.tsx`.
  2. Tại `DashboardPage.tsx`: Chỉ chèn tối đa 3 chỗ:
     - Import component mới.
     - Khai báo Clean URL trong logic router (`handleCleanNavigation` và hàm khởi tạo `useState`).
     - Render thẻ view trong khối switch/case hoặc điều kiện hiển thị, bọc `<AccessDeniedView />` nếu không có quyền.
  3. Tuyệt đối không xóa bất kỳ modal state nào: `isUserMenuOpen`, `isCreateCustomerModalOpen`, `isCreateAccountModalOpen`, `isMoveCategoryModalOpen`, `isBulkImportOpen`, `isStockActionModalOpen`, `isUnitModalOpen`, `isOrderCreateModalOpen`.

### B. Phân hệ Backend Endpoints (`backend/app/api/v1/endpoints/`)
- **Khi thêm endpoint mới:**
  ```python
  @router.post("/new-feature", response_model=FeatureResponse)
  def create_feature(
      data: FeatureCreate,
      db: Session = Depends(get_db),
      current_user: User = Depends(require_permission(Permission.FEATURE_WRITE))
  ):
      # 1. Xử lý nghiệp vụ
      result = service.create(db, data, current_user)
      
      # 2. BẮT BUỘC GHI NHẬN AUDIT LOG
      log_audit_event(
          db=db,
          user_id=current_user.id,
          action="CREATE_FEATURE",
          entity="feature",
          entity_id=result.id,
          details=f"Tạo mới bởi {current_user.username}"
      )
      return result
  ```

### C. Phân hệ Cơ Sở Dữ Liệu & Schema
- Khi thêm trường dữ liệu vào model Pydantic:
  - **Bắt buộc có giá trị mặc định:** `field_name: Optional[str] = None`.
  - Không bao giờ thêm trường bắt buộc (`field_name: str`) vào model đang phục vụ dữ liệu cũ, vì sẽ gây lỗi 422 Unprocessable Entity với các bản ghi JSON/DB hiện hành.

### D. Phân hệ Hồ sơ cá nhân (`ProfileView.tsx`)
- Duy trì kiến trúc **1 Bảng duy nhất (Single Card)**:
  - Khung bao ngoài: Nền trắng, viền `#e2e8f0`, bo góc `16px`.
  - Bên trong: Avatar + Thông tin tài khoản ở trên; Lưới 2 cột các trường dữ liệu ở giữa; Nút Hủy & Lưu thay đổi ở chân bảng.
  - Không chèn thêm các hộp ghi chú rườm rà.

---

## 4. KỊCH BẢN TỰ SỬA LỖI CẤP TỐC KHI GẶP SỰ CỐ (TROUBLESHOOTING RUNBOOK)

| Triệu Chứng Lỗi | Nguyên Nhân Gốc Rễ | Hành Động Khắc Phục Ngay Của Agent |
|---|---|---|
| `error TS17008: JSX element 'div' has no corresponding closing tag` | Agent thay thế code làm thừa hoặc thiếu thẻ đóng `</div>`. | Dùng `view_file` đếm lại các cặp thẻ `<div> ... </div>` trong phạm vi vừa sửa và dùng `replace_file_content` bổ sung thẻ đóng. |
| `TS2322: Type '...' is not assignable to type '...'` | Truyền thiếu prop bắt buộc hoặc sai kiểu dữ liệu khi gọi component con. | Kiểm tra interface Props của component con, bổ sung prop còn thiếu hoặc thêm kiểu tùy chọn `?`. |
| Giao diện bị chữ trắng trên nền trắng | Style inline hoặc CSS class dùng màu chữ `#ffffff` hoặc biến theme không tương phản. | Đổi ngay màu chữ sang `#0f172a` hoặc `#1e293b`. |
| 403 Forbidden khi user thao tác | Endpoint backend yêu cầu quyền mà role của user không có, hoặc thiếu decorator bypass. | Kiểm tra `ROLE_PERMISSIONS` trong `rbac.py` xem vai trò người dùng đã được cấp cờ quyền đó chưa. |
| Màn hình trắng xóa (White Screen) khi tải trang | Code truy cập `user.full_name` mà `user` bị `null`/`undefined`. | Bọc bằng Optional Chaining: `user?.full_name` và thêm fallback: `{user?.full_name || user?.username || 'Người dùng'}`. |
| Test suite RBAC bị fail | Response schema trả về có chứa trường `cost_price` cho vai trò không phải Admin/Sales Manager. | Thêm bộ lọc serializer gỡ bỏ `cost_price` trước khi return JSON. |

---

## 5. CAM KẾT CHẤT LƯỢNG CỦA AGENT TRƯỚC KHI TRẢ LỜI NGƯỜI DÙNG

Trước khi đưa ra câu trả lời cuối cùng cho người dùng, Agent phải tự xác nhận trong suy nghĩ:
- [x] Ta đã đọc kỹ code cũ trước khi sửa chưa? -> **ĐÃ ĐỌC.**
- [x] Ta có dùng phương pháp phẫu thuật tối thiểu (Surgical Edit) không? -> **CÓ.**
- [x] Clean URL và các tính năng của 11 Domain nghiệp vụ có nguyên vẹn không? -> **NGUYÊN VẸN.**
- [x] Đã chạy `npm run build` và ra 0 lỗi chưa? -> **ĐÃ CHẠY VÀ 0 LỖI.**
- [x] Đã chạy `pytest tests/rbac` và 100% xanh chưa? -> **ĐÃ CHẠY VÀ 100% XANH.**
