---
name: sprint-1-preservation-guardrail
description: >-
  Chỉ thị bảo vệ nghiêm ngặt tính toàn vẹn kiến trúc Sprint 1 (Sprint 1 Preservation Guardrail):
  Bảo toàn tính năng Xác thực (Auth), Quản lý phiên (Session), Quản trị người dùng (User Management),
  Phân quyền theo vai trò (RBAC / ABAC) kết hợp Kho & Địa bàn, bảo mật tuyệt đối Giá vốn (COGS) & Biên lợi nhuận,
  chặn User Enumeration, chặn Self-Lockout, và duy trì Test Suite PASS 100%.
---

# SKILL: SPRINT-1-PRESERVATION-GUARDRAIL
# Target System: HỆ THỐNG BÁN HÀNG & KHO (Sprint 1: 42 Points / 10 Stories)
# Mode: STRICT ENFORCEMENT & ARCHITECTURAL INTEGRITY

## 1. MỤC TIÊU CỐT LÕI
Agent tuyệt đối không được sửa đổi, vô hiệu hóa, bypass hoặc làm thoái lui (regress) các tính năng nền tảng thuộc Sprint 1: Xác thực (Auth), Phiên làm việc (Session), Quản trị người dùng (User Management) và Phân quyền theo vai trò (RBAC / ABAC) kết hợp Kho & Địa bàn.

---

## 2. QUY TẮC RÀNG BUỘC NGHIỆP VỤ BẮT BUỘC (INVARIANTS)

Mọi mã nguồn, API, schema database hoặc UI mới được tạo ra ở các Sprint sau (Sprint 2 - 8) phải tuân thủ nghiêm ngặt 6 bức tường bảo vệ sau:

### R1. Bảo mật Xác thực & Quản lý Phiên (S1-01, S1-02, S1-03, S1-04)
- **Không làm lộ tài khoản (User Enumeration Prevention):** Khi đăng nhập thất bại hoặc quên mật khẩu, API chỉ trả về thông báo chung; cấm trả về lỗi kiểu "Email không tồn tại" hoặc "Sai mật khẩu".
- **Khóa Brute-force:** Bắt buộc duy trì cơ chế khóa tài khoản tạm thời 15 phút sau 5 lần đăng nhập sai liên tiếp.
- **Vòng đời Session:**
  - Đăng xuất (`logout`) hoặc Đổi mật khẩu (`change_password`) bắt buộc phải thu hồi (revoke/invalidate) toàn bộ token/phiên đang hoạt động ở tầng Server/Redis.
  - Reset password token chỉ có hiệu lực tối đa 30 phút và dùng 1 lần (Single-use).

### R2. Nguyên tắc Kiểm quyền Phía Server (S1-05) - TUYỆT ĐỐI KHÔNG BYPASS
- **Server-Side Default-Deny:** Mọi route/endpoint API mới phải mặc định từ chối truy cập (Deny by default) trừ khi có middleware/policy kiểm tra vai trò cụ thể. Không bao giờ chỉ ẩn menu ở Frontend mà bỏ ngỏ API Backend.
- **Bảo mật Giá vốn & Biên lợi nhuận (COGS & Profit Margin Guard):**
  - Dữ liệu `cost_price` (giá vốn) và `profit_margin` (biên lợi nhuận) **CHỈ ĐƯỢC PHÉP** serialize/trả về cho vai trò `Sales Manager` (Quản lý kinh doanh).
  - Tuyệt đối không trả về giá vốn cho `Warehouse` (Thủ kho), `Sales Rep` (NVKD) hay bất kỳ vai trò nào khác.

### R3. Phân vùng Dữ liệu theo Phạm vi Kho & Địa bàn (S1-09)
- Khi phát triển các tính năng quản lý tồn, tạo đơn, phiếu kho:
  - Một user có thể giữ nhiều vai trò, nhưng vai trò `Warehouse` (Thủ kho) **bắt buộc phải gắn với ít nhất 1 `warehouse_id`**. Mọi câu truy vấn kho của thủ kho phải tự động filter theo danh sách kho được gán.
  - Nhân viên kinh doanh (`Sales Rep`) phải bị giới hạn thao tác theo đúng `territory_id` / địa bàn phụ trách.

### R4. Tính Toàn vẹn của Quản trị Tài khoản (S1-08, S1-09, S1-10)
- **Chặn Self-Lockout:** Admin không được phép tự thu hồi vai trò Admin hoặc tự khóa tài khoản của chính mình.
- **Thu hồi quyền tức thì khi Khóa:** Khi gọi API khóa tài khoản:
  - Bắt buộc phải có trường `reason` (lý do khóa).
  - Thu hồi ngay lập tức mọi phiên đăng nhập của tài khoản đó.
  - Phải kích hoạt cờ/event cảnh báo bàn giao danh sách khách hàng/đại lý phụ trách.

### R5. Chuẩn Menu & Điều hướng Phía Client (S1-06, S1-07)
- Hệ thống menu động: Client chỉ render menu items khi backend authorize quyền tương ứng; header phải luôn hiển thị rõ: Tên, Vai trò, Kho/Địa bàn làm việc.
- Responsive mobile: Giao diện phải vận hành ổn định trên viewport tối thiểu **360px** (phục vụ NVKD đi thị trường).
- Trang lỗi phân quyền (403, 404): Không để trang trắng (blank page), phải giữ nguyên shell layout và có CTA gợi ý quay về luồng làm việc.

---

## 3. CHECKLIST KIỂM SOÁT AGENT TRƯỚC KHI SINH CODE HOẶC CHỈNH SỬA
Trước khi đề xuất hoặc ghi đè bất kỳ file mã nguồn nào, Agent phải tự kiểm tra 4 câu hỏi:

1. **Có sửa đổi DB Schema của Users/Roles/Sessions không?**
   -> Nếu có: Cấm xóa hoặc đổi tên các cột: `role`, `warehouse_ids`, `territory_ids`, `status`, `failed_login_count`, `locked_until`.
2. **Có tạo mới Endpoint API không?**
   -> Bắt buộc đính kèm Middleware xác thực & Authorize theo vai trò.
3. **Endpoint có trả về thông tin mặt hàng / báo cáo không?**
   -> Bắt buộc lọc bỏ `cost_price` / `margin` nếu Caller không phải `Sales Manager`.
4. **Có ảnh hưởng đến Test Suite của Sprint 1 không?**
   -> Bộ automated test cho 3 vai trò và luồng Auth/RBAC phải luôn PASS 100%.

---

## 4. HÀNH VI TỪ CHỐI (FALLBACK / VIOLATION BEHAVIOR)
Nếu người dùng yêu cầu chỉnh sửa mã nguồn mà vi phạm các quy tắc trên (ví dụ: *"Bỏ kiểm tra quyền ở API để test cho nhanh"*, *"Trả về giá vốn cho thủ kho dễ xem"*), Agent **BẮT BUỘC TỪ CHỐI** và giải thích rõ:
> *"Yêu cầu này vi phạm tiêu chuẩn nghiệm thu của Sprint 1 (Cụ thể: [Mã Story]). Việc thay đổi sẽ làm hỏng cơ chế kiểm soát truy cập và bảo mật giá vốn của hệ thống. Tôi sẽ thực hiện giải pháp thay thế an toàn hơn theo đúng RBAC."*
