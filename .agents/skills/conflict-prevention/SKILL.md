---
name: conflict-prevention
description: >-
  Quy chuẩn và quy trình kiểm soát chống xung đột mã nguồn (Merge Conflict Prevention Guardrail)
  cho nhóm phát triển dự án Quản lý Bán hàng & Kho (ttcs). Thiết lập kỷ luật phân nhánh Git,
  nguyên tắc cô lập module kiến trúc (Decoupled Architecture), quy tắc phẫu thuật code (Surgical Edits),
  và checklist resolve conflict an toàn bảo vệ 100% code của đồng đội.
---

# SKILL: MERGE CONFLICT PREVENTION & TEAM COLLABORATION GUARDRAIL
# Target System: QUẢN LÝ BÁN HÀNG & KHO (Dual-Repo: Backend FastAPI + Frontend React 19)
# Mode: STRICT TEAM DISCIPLINE & ZERO-OVERWRITE

## 1. MỤC TIÊU CỐT LÕI
Skill này cung cấp cơ chế và kỷ luật tối cao nhằm **triệt tiêu 100% nguy cơ xung đột mã nguồn (Merge Conflict)** và **chống việc ghi đè/làm mất code của các thành viên khác** khi cùng làm việc trên hệ thống repository `quanlybanhangvakho_backend` và `quanlybanhangvakho_frontend`.

---

## 2. BẢN ĐỒ CÁC "ĐIỂM NÓNG XUNG ĐỘT" (CONFLICT HOTSPOTS)
Trong dự án này, xung đột 95% xảy ra tại các tập tin điều phối trung tâm sau:

| Vị trí | File điểm nóng | Nguyên nhân xung đột | Giải pháp phòng ngừa bắt buộc |
|---|---|---|---|
| **Frontend** | `src/components/DashboardPage.tsx` | Mọi thành viên cùng chèn menu, import component, gắn URL route, state modal | **Không nhồi code vào file này**. Tạo component độc lập tại `components/XView.tsx`, chỉ gắn 1-2 dòng điều hướng tối giản tại Dashboard. |
| **Frontend** | `src/services/api.ts` | Nhiều thành viên cùng thêm hàm gọi API | **Tách file API theo domain**: `dealerSearchApi.ts`, `supplierApi.ts`, `categoryApi.ts`. |
| **Frontend** | `src/index.css` / CSS chung | Trùng selector, ghi đè class toàn cục | **Module hóa CSS**: Mỗi view có file CSS riêng (`dealer-search.css`, `supplier.css`). |
| **Backend** | `app/db/init_db.py` | Cùng thêm cột migration / seed data | **Viết migration an toàn độc lập (Idempotent)**: Luôn kiểm tra `IF COL_LENGTH(...) IS NULL` trước khi `ALTER TABLE`. |
| **Backend** | `app/models/entities.py` | Cùng sửa cột bảng dữ liệu | Chỉ thêm cột mới, không sửa/đổi tên cột cũ của thành viên khác. |
| **Backend** | `app/api/v1/api.py` / `main.py` | Đăng ký route router mới | Mỗi endpoint nằm ở router riêng trong `endpoints/`, chỉ include router 1 dòng. |

---

## 3. KỶ LUẬT PHÂN NHÁNH & ĐỒNG BỘ GIT 5 BƯỚC (STRICT GIT PROTOCOL)

Tất cả thành viên và Agent hỗ trợ bắt buộc tuân thủ quy trình 5 bước sau:

### Bước 1: Luôn xuất phát từ code mới nhất của nhánh chuẩn (`test`)
Trước khi bắt đầu làm bất kỳ tính năng hay sửa lỗi nào:
```powershell
# Thực hiện riêng trên từng thư mục backend và frontend
cd D:\ttcs\backend   # hoặc cd D:\ttcs\frontend
git checkout test
git pull origin test
```

### Bước 2: Tạo nhánh tính năng ngắn hạn (Short-lived Feature Branch)
Tuyệt đối không dùng một nhánh cá nhân gom hàng chục tính năng kéo dài nhiều tuần. Đặt tên nhánh rõ ràng:
- Tính năng: `feat/ten-chuc-nang` (ví dụ: `feat/supplier-management`)
- Sửa lỗi: `fix/ten-loi` (ví dụ: `fix/dealer-status-encoding`)
```powershell
git checkout -b feat/ten-chuc-nang
```

### Bước 3: "Sửa phẫu thuật" (Surgical Commits) - Cấm Auto-format cả file
- Chỉ chỉnh sửa đúng những dòng phục vụ chức năng được giao.
- **CẤM auto-format toàn bộ file chung**: Hành động nhấn format toàn bộ file 1000 dòng sẽ biến mọi dòng code thành thay đổi của bạn, gây xung đột 100% với đồng đội.
- Commit với thông điệp rõ ràng theo chuẩn: `feat: ...`, `fix: ...`, `refactor: ...`.

### Bước 4: Đồng bộ nhánh trước khi tạo Pull Request (Sync before PR)
Trước khi đẩy lên GitHub hoặc tạo PR:
```powershell
git fetch origin
git merge origin/test   # Hoặc git rebase origin/test
```
- Nếu có conflict: Xử lý conflict tại máy mình (xem Phần 4 bên dưới).
- Chạy kiểm thử xác nhận thành công: `npm run build` (frontend) hoặc `pytest` (backend).

### Bước 5: Mở Pull Request & KHÔNG MERGE CHÉO BỪA BÃI
- Mở PR từ nhánh feature vào nhánh `test` trên GitHub để Team Lead kiểm duyệt.
- **TUYỆT ĐỐI CẤM:** Không tự tiện gõ `git merge <nhanh-khac>` thẳng vào `test` tại máy local rồi force push.
- **CẤM VÒNG LẶP MERGE:** Không merge `test` vào `nhánh A` rồi lại merge `nhánh A` vào `test` mà không có PR kiểm soát.

---

## 4. QUY TẮC GIẢI QUYẾT XUNG ĐỘT AN TOÀN (SAFE CONFLICT RESOLUTION)

Khi xảy ra Merge Conflict, Agent và Developer phải tuân theo nguyên tắc **BẢO TOÀN CODE ĐỒNG ĐỘI**:

1. **Không chọn mù quáng**: TUYỆT ĐỐI KHÔNG dùng tính năng "Accept All Current" hoặc "Accept All Incoming" vì sẽ xóa sạch công sức của người khác.
2. **Đọc hiểu cả 2 vế conflict**:
   - `<<<<<<< HEAD` (Code của mình)
   - `=======`
   - `>>>>>>> origin/test` (Code của đồng đội vừa được merge vào nhánh chính)
3. **Ghép nối logic (Harmonious Integration)**:
   - Nếu cả 2 cùng thêm import -> Giữ lại cả 2 import.
   - Nếu cả 2 cùng thêm state/route trong `DashboardPage.tsx` -> Xếp cả 2 state/route cạnh nhau.
   - Nếu cả 2 cùng bổ sung cột DB -> Giữ lại tất cả các cột mới.
4. **Nghiệm thu bắt buộc sau khi gỡ conflict**:
   - Frontend: `npm run build` (phải ra 0 lỗi TypeScript và Vite).
   - Backend: `.\venv\Scripts\python.exe -m pytest tests/rbac -v` (37/37 tests pass).
   - Chỉ khi mọi kiểm thử PASS mới được `git add .` và `git commit`.

---

## 5. MÔ HÌNH KIẾN TRÚC TÁCH RỜI (DECOUPLED ARCHITECTURE GUIDELINES)

Để nhiều người code cùng lúc mà không chạm mặt nhau, áp dụng triệt để nguyên lý:

```
frontend/src/
├── components/
│   ├── DealerSearchView.tsx        <-- Developer A toàn quyền
│   ├── dealer-search.css           <-- Developer A toàn quyền
│   ├── SupplierManagementView.tsx  <-- Developer B toàn quyền
│   ├── supplier.css                <-- Developer B toàn quyền
│   └── DashboardPage.tsx           <-- CHỈ CHỨA SHELL & ĐIỀU PHỐI (Hạn chế tối đa sửa đổi)
```

1. **Component độc lập**: Mỗi màn hình lớn là 1 file component riêng biệt, chứa toàn bộ state nội bộ, drawer, modal con của màn hình đó.
2. **Service API độc lập**: Mỗi màn hình tự import file API riêng của mình.
3. **Styles độc lập**: Sử dụng tiền tố riêng cho class CSS (ví dụ: `.dealer-...`, `.supplier-...`) để tránh đè style lẫn nhau.
4. **Backend Endpoint độc lập**: Mỗi nhóm API nằm riêng trong 1 file router `app/api/v1/endpoints/<tên_chức_năng>.py`.
