---
name: sales-order-pricing-discount-guardrail
description: >-
  Quy chuẩn tối cao và tài liệu ràng buộc bảo vệ toàn diện phân hệ Đơn hàng, Định giá Bán, Giá sàn,
  Chính sách Chiết khấu & Quy trình Phê duyệt hai cấp (ttcs Enterprise: FastAPI + React 19 TypeScript + Vite).
  Bảo vệ 100% các tính năng cốt lõi: Phê duyệt khi bán dưới giá sàn, Phê duyệt khi chiết khấu vượt mức chính sách,
  Khóa giá khi tạo đơn, Chặn lọt đơn sang Đã xác nhận, Hiển thị tức thì trong tab Chờ duyệt, Giữ chỗ và hoàn tồn kho,
  cùng bộ checklist kiểm thử tự động bắt buộc trước khi thêm tính năng mới hoặc merge code.
---

# SKILL: SALES ORDER, PRICING, DISCOUNT & APPROVAL GUARDRAIL
## Quy Chuẩn Ràng Buộc Bảo Toàn Phân Hệ Đơn Hàng & Phê Duyệt (ttcs Enterprise)

**Kiến trúc:** Dual-Stack Decoupled (FastAPI Backend + React 19 TypeScript Vite Frontend)  
**Tôn chỉ cốt lõi:** BẢO TOÀN 100% NGHIỆP VỤ ĐƠN HÀNG, ĐỊNH GIÁ & CHIẾT KHẤU — ZERO-REGRESSION — CHẶN ĐỨNG NGUY CƠ LÀM HỎNG TÍNH NĂNG KHI PHÁT TRIỂN TIẾP HOẶC MERGE CODE.

---

## 1. MỤC ĐÍCH & BỐI CẢNH ÁP DỤNG

Hệ thống vừa hoàn thiện và tinh chỉnh sâu các chức năng nghiệp vụ bán hàng trọng yếu:
1. **Kiểm soát giá bán & Giá sàn:** Cho phép đàm phán giá, tự động phát hiện bán dưới giá sàn quy định để chuyển sang quy trình chờ quản lý duyệt.
2. **Động cơ chiết khấu đa tầng:** Tính chiết khấu theo sản phẩm (SKU), theo danh mục hàng hóa, hoặc toàn hệ thống.
3. **Kiểm soát chiết khấu vượt mức (Discount Ceiling Enforcement):** Phát hiện mọi trường hợp chiết khấu nhập tay vượt mức chính sách cho phép (kể cả chính sách 0% mà nhập > 0%), tự động kích hoạt phê duyệt hai cấp.
4. **Vòng đời phê duyệt hai cấp (Sales -> Manager):** Phân định ranh giới rõ ràng giữa Nhân viên kinh doanh (lập đơn) và Quản lý/Admin (duyệt/từ chối).
5. **Đồng bộ song song 2 luồng tạo đơn:** `OrderCreateModal.tsx` (tạo nhanh tại danh sách) và `SalesOrderEntry.tsx` (lên đơn chuyên sâu).

Khi tiếp tục mở rộng hệ thống (như xuất kho, thanh toán công nợ, giao hàng, khuyến mại bậc thang...), **BẮT BUỘC** phải tuân theo tài liệu Skill này để không phá vỡ bất kỳ quy tắc nào trong số các quy tắc trên.

---

## 2. BẢN ĐỒ TẬP TRUNG PHÂN HỆ BÁN HÀNG & PHÊ DUYỆT

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              BẢN ĐỒ PHÂN HỆ ĐƠN HÀNG, ĐỊNH GIÁ & PHÊ DUYỆT                             │
├────────────────────────────────┬───────────────────────────────────────┬───────────────────────────────┤
│ THÀNH PHẦN FRONTEND            │ THÀNH PHẦN BACKEND                    │ NHIỆM VỤ CỐT LÕI              │
├────────────────────────────────┼───────────────────────────────────────┼───────────────────────────────┤
│ OrderCreateModal.tsx           │ orders.py (/api/v1/orders)            │ Tạo đơn nhanh, validate tức thì│
│ SalesOrderEntry.tsx            │ orders.py (create_sales_entry_order)  │ Lên đơn đầy đủ, tính thuế/ĐVT │
│ OrderManagementView.tsx        │ orders.py (/api/v1/orders GET)        │ Quản lý tab đơn, bộ lọc trạng thái│
│ OrdersView.tsx                 │ orders.py (get_orders)                │ Danh sách đơn cho sales/đại lý │
│ ApproveOrderModal.tsx          │ orders.py (/approve)                  │ Quản lý duyệt đơn vượt giá/CK │
│ RejectOrderModal.tsx           │ orders.py (/reject)                   │ Từ chối đơn, hoàn tồn khả dụng │
│ OrderCancelModal.tsx           │ orders.py (/cancel)                   │ Hủy đơn hợp lệ, hoàn tồn kho  │
│ OrderDetailsModal.tsx          │ orders.py (/{order_id})               │ Chi tiết mặt hàng, lý do duyệt│
│ DiscountPolicyView.tsx         │ discounts.py (/api/v1/discounts)      │ Cấu hình chính sách chiết khấu│
│ PriceBookManagementView.tsx    │ price_books.py (/api/v1/price-books)  │ Cấu hình bảng giá & giá sàn   │
└────────────────────────────────┴───────────────────────────────────────┴───────────────────────────────┘
```

---

## 3. TÁM ĐIỀU RĂN BẤT BIẾN NGHIỆP VỤ BÁN HÀNG (THE 8 INVARIANTS)

### Điều răn 1: Bất biến Giá Sàn & Bán Dưới Giá Quy Định (Floor Price Invariant)
1. **Ghi nhận giá đàm phán:** Khi Nhân viên kinh doanh nhập đơn giá bán thực tế, hệ thống **PHẢI** ghi nhận giá do người dùng nhập vào. CẤM tự ý reset hoặc ép ngược về giá niêm yết/giá sàn mà không có sự đồng ý của người dùng.
2. **Kích hoạt phê duyệt:** Nếu có bất kỳ mặt hàng nào trong đơn có `unit_price < floor_price` (hoặc thấp hơn giá chuẩn trong bảng giá áp dụng cho đại lý):
   - `requires_approval = True`
   - `order_status = "PENDING_APPROVAL"`
   - `approval_reason` bắt buộc ghi rõ: *"Sản phẩm [Tên/Mã SP] có giá bán [X] đ thấp hơn giá sàn [Y] đ quy định"* (kèm danh sách nếu nhiều sản phẩm).
3. **Cấm bỏ lọt:** Cấm tuyệt đối việc lưu đơn dưới giá sàn trực tiếp thành trạng thái `CONFIRMED` hay `APPROVED`.

### Điều răn 2: Bất biến Chiết Khấu Vượt Trần & Thứ Tự Ưu Tiên (Discount Ceiling Invariant)
1. **Thứ tự ưu tiên đối soát chính sách chiết khấu:**
   - **Ưu tiên 1:** Khớp đích danh Sản phẩm cụ thể (Tên sản phẩm hoặc Mã SKU trong `pol_title`).
   - **Ưu tiên 2:** Khớp theo Danh mục ngành hàng (`category`).
   - **Ưu tiên 3:** Áp dụng toàn hệ thống (`ALL`).
   - *Quy tắc kỹ thuật:* Trong `discounts.py`, logic so khớp sản phẩm cụ thể **BẮT BUỘC** nằm trước logic so khớp danh mục để tránh hiện tượng *Shadowing* làm mất mức chiết khấu của sản phẩm.
2. **Điều kiện kích hoạt duyệt chiết khấu:**
   - Luôn sử dụng điều kiện: `if effective_pct > best_pct:`
   - **TUYỆT ĐỐI CẤM** viết điều kiện dạng: `if best_pct > 0 and effective_pct > best_pct:`. Điều kiện này sẽ bỏ lọt trường hợp khách hàng chưa có chính sách (`best_pct = 0.0%`) nhưng Sales nhập chiết khấu tay > 0%.
   - Khi `effective_pct > best_pct`:
     - `requires_approval = True`
     - `order_status = "PENDING_APPROVAL"`
     - `approval_reason` ghi rõ: *"Chiết khấu (X%) vượt mức chính sách [Mã_CS] (Y%) cần Quản lý duyệt"*
3. **Hiển thị tức thì trong danh sách Chờ duyệt:**
   - Đơn hàng sau khi tạo với trạng thái `PENDING_APPROVAL` **PHẢI** xuất hiện ngay lập tức trong tab **"Chờ duyệt"** của cả Nhân viên kinh doanh và Quản lý kinh doanh.

### Điều răn 3: Bất biến Phân Quyền Phê Duyệt Hai Cấp (Two-Tier RBAC Invariant)
1. **Quyền của Nhân viên kinh doanh (`sales`):**
   - Được tạo đơn hàng mới, tra cứu đơn thuộc địa bàn hoặc do mình tạo.
   - **TUYỆT ĐỐI BỊ CHẶN (HTTP 403 Forbidden):** Không được tự duyệt đơn của chính mình (`/approve`), không được tự duyệt đơn của người khác, không được từ chối duyệt đơn (`/reject`).
2. **Quyền của Quản lý kinh doanh (`sales_manager`) & Quản trị viên (`admin`):**
   - Có toàn quyền xem tất cả đơn cần duyệt.
   - Có quyền Duyệt đơn (`POST /api/v1/orders/{order_id}/approve`) kèm ghi chú duyệt.
   - Có quyền Từ chối đơn (`POST /api/v1/orders/{order_id}/reject`) kèm **bắt buộc nhập lý do từ chối** (`reject_reason`).

### Điều răn 4: Bất biến Vòng Đời Đơn Hàng & Giữ Chỗ Tồn Kho (Order Lifecycle & Stock Invariant)
1. **Khóa xuất kho khi chưa duyệt:** Đơn hàng ở trạng thái `PENDING_APPROVAL` chỉ giữ chỗ tạm thời tồn kho khả dụng (`available_stock`), **CẤM** cho phép thủ kho xuất kho thực tế (`COMPLETED`/`SHIPPED`) khi đơn chưa chuyển sang `CONFIRMED`/`APPROVED`.
2. **Hoàn tồn khi Từ chối hoặc Hủy:**
   - Khi Quản lý từ chối đơn (`REJECTED`) hoặc khi Hủy đơn (`CANCELLED`): Hệ thống bắt buộc giải phóng số lượng giữ chỗ và hoàn trả ngay lập tức vào tồn kho khả dụng (`available_stock`).

### Điều răn 5: Bất biến Đồng Bộ Song Song Giữa 2 Luồng Tạo Đơn (Dual Entry Parity)
Mọi quy chuẩn nghiệp vụ phải được áp dụng **100% đồng nhất** trên cả 2 màn hình:
1. `OrderCreateModal.tsx` (Popup tạo đơn nhanh tại màn hình danh sách đơn).
2. `SalesOrderEntry.tsx` (Màn hình lập đơn hàng chi tiết).
- **Các điểm bắt buộc đồng bộ:**
  - Kiểm tra Hạn mức tín dụng (`credit_limit`) và Nợ quá hạn (`overdue_debt`) của đại lý.
  - Chọn và gắn đúng Điểm giao hàng (`delivery_point_id`) của đại lý.
  - Quy đổi Đơn vị tính (`conversion_factor`) khi người dùng chọn ĐVT phụ.
  - Truyền đầy đủ `category`, `unit`, `unit_price`, `discount_percent`, `db=db` vào backend.

### Điều răn 6: Bất biến Bảo Vệ Giá Vốn (COGS Shield Invariant)
1. Trong toàn bộ các API liên quan đến đơn hàng (`/api/v1/orders`, `create_order`, `get_order_details`):
   - Trường giá vốn (`cost_price`, `cogs`, `profit_margin`) **CHỈ ĐƯỢC PHÉP TRẢ VỀ** cho `admin` và `sales_manager`.
   - Với `sales`, `warehouse`, `accountant`, `customer`: Toàn bộ thông tin giá vốn phải bị ẩn hoàn toàn (strip fields).
2. Frontend: Cột giá vốn và tỷ suất lợi nhuận chỉ render khi cờ `isCostVisible` hợp lệ.

### Điều răn 7: Bất biến Giao Diện & Trải Nghiệm Nhập Liệu (UI/UX Invariants)
1. **Trải nghiệm gõ số (Number Input Caret Preservation):**
   - Các ô nhập: Số lượng (`quantity`), Đơn giá (`unit_price`), Phần trăm chiết khấu (`discount_percent`), Tiền chiết khấu (`discount_amount`) **PHẢI** cho phép người dùng gõ số tự nhiên, liên tục.
   - CẤM ép kiểu `Number(e.target.value)` trực tiếp trong `onChange` làm reset con trỏ chuột, nhảy về 0 hoặc bắt buộc người dùng phải bôi đen toàn bộ mới gõ được.
2. **Chống nhấp nháy Dropdown & Popover:**
   - Các popover lọc trạng thái, chọn đại lý, chọn điểm giao hàng phải có handler `mousedown` / `click-outside` chuẩn xác, không bị flickering hoặc tự đóng khi click bên trong.
3. **100% Thuần Tiếng Việt Thân Thiện:**
   - CẤM hiển thị mã enum kỹ thuật tiếng Anh trần trụi ra giao diện:
     - ❌ `PENDING_APPROVAL` ➔  **Chờ quản lý duyệt**
     - ❌ `CONFIRMED` ➔  **Đã xác nhận**
     - ❌ `APPROVED` ➔  **Đã duyệt**
     - ❌ `REJECTED` ➔  **Từ chối duyệt**
     - ❌ `PROCESSING` ➔  **Đang xử lý**
     - ❌ `SHIPPED` ➔  **Đang giao hàng**
     - ❌ `COMPLETED` ➔  **Hoàn thành**
     - ❌ `CANCELLED` ➔  **Đã hủy**

### Điều răn 8: Bất biến Ghi Nhật Ký Kiểm Toán (Sales Audit Invariant)
- Mọi thao tác: Tạo đơn, Sửa đơn, Duyệt đơn, Từ chối duyệt, Hủy đơn, Thay đổi chiết khấu **BẮT BUỘC PHẢI GỌI** `log_audit_event` / `audit_service`.
- Log phải ghi nhận rõ: `order_id`, `actor_username`, `action_type`, `approval_reason` hoặc `reject_reason`, giá trị đơn hàng trước và sau khi thay đổi.

---

## 4. BẢNG ĐỐI CHIẾU MẪU MÃ NGUỒN (ANTI-PATTERNS VS BEST PRACTICES)

| Tình huống | Cách làm SAI (Nguy cơ lỗi cao) | Cách làm ĐÚNG (Chuẩn TTCS) |
| :--- | :--- | :--- |
| **Kiểm tra chiết khấu vượt mức** | `if best_pct > 0 and effective_pct > best_pct:` *(Bỏ lọt trường hợp best_pct = 0)* | `if effective_pct > best_pct:` *(Bắt trọn mọi trường hợp nhập chiết khấu trái phép)* |
| **Khớp chính sách chiết khấu** | `if pol.category != "ALL": ... elif pol.name == item.name: ...` *(Category che khuất sản phẩm)* | `if pol_title_matches_sku: ... elif pol.category != "ALL": ...` *(Ưu tiên SKU cụ thể trước)* |
| **Duyệt đơn hàng** | Cho phép Sales tự duyệt đơn bằng cách gọi API không check role | Kiểm tra nghiêm ngặt `current_user.role in ["admin", "sales_manager"]` |
| **Input đơn giá trên UI** | `<input value={price} onChange={e => setPrice(Number(e.target.value))} />` | Lưu chuỗi thô `rawPrice`, format khi blur, hoặc dùng custom numeric input component |
| **Hiển thị nhãn trạng thái** | `<span>{order.status}</span>` *(Ra PENDING_APPROVAL)* | `<span>{STATUS_LABELS[order.status] || order.status}</span>` *(Ra Chờ quản lý duyệt)* |

---

## 5. BỘ CHECKLIST NGHIỆM THU TỰ ĐỘNG BẮT BUỘC (MANDATORY ACCEPTANCE)

Trước khi bàn giao bất kỳ tính năng mới hoặc giải quyết xung đột khi merge, Agent/Lập trình viên **BẮT BUỘC** kiểm tra 6 kịch bản sau:

- [ ] **Kịch bản 1 (Đơn chuẩn):** Tạo đơn hàng với đơn giá chuẩn, chiết khấu <= chính sách -> Trạng thái phải là `CONFIRMED`, `requires_approval = False`.
- [ ] **Kịch bản 2 (Dưới giá sàn):** Tạo đơn hàng có đơn giá < giá sàn -> Trạng thái phải là `PENDING_APPROVAL`, `requires_approval = True`, có `approval_reason` về giá sàn.
- [ ] **Kịch bản 3 (Chiết khấu vượt mức chính sách):** Tạo đơn hàng có chiết khấu 5.6% khi chính sách là 5.0% -> Trạng thái phải là `PENDING_APPROVAL`, `requires_approval = True`, có `approval_reason` nêu rõ mức 5.6% > 5.0%.
- [ ] **Kịch bản 4 (Chiết khấu tự do khi chính sách 0%):** Tạo đơn hàng nhập chiết khấu 3.0% khi không có chính sách (0%) -> Trạng thái phải là `PENDING_APPROVAL`, không được lọt sang `CONFIRMED`.
- [ ] **Kịch bản 5 (Phân quyền duyệt):**
  - Đăng nhập tài khoản Sales -> Không nhìn thấy nút Duyệt đơn / Gọi API duyệt bị trả về HTTP 403 Forbidden.
  - Đăng nhập Quản lý kinh doanh / Admin -> Duyệt đơn thành công sang `APPROVED` / Từ chối đơn thành công sang `REJECTED`.
- [ ] **Kịch bản 6 (Hiển thị tab Chờ duyệt):** Các đơn có `PENDING_APPROVAL` phải lập tức xuất hiện trong tab "Chờ duyệt" của `OrderManagementView.tsx`.
- [ ] **Cổng kỹ thuật:**
  1. `npm run build` trong `frontend`: Mã thoát `0`, **0 lỗi TypeScript**, **0 lỗi Vite**.
  2. `pytest tests/rbac/` trong `backend`: **100% test cases PASS**.
