-- =====================================================================================
-- KỊCH BẢN ĐỒNG BỘ DỮ LIỆU CÔNG NỢ TOÀN DIỆN TRÊN MICROSOFT SQL SERVER
-- Database: quanlybanhang
-- Mục tiêu: 
-- 1. Sử dụng SQL Server làm nguồn chân lý duy nhất (Single Source of Truth).
-- 2. Đồng bộ hóa đơn (invoices) từ đơn hàng (orders).
-- 3. Đồng bộ phiếu thu (receipt_vouchers) và khoản nộp tiền (payment_collections).
-- 4. Đồng bộ chứng từ giảm nợ trả hàng (debt_adjustments).
-- 5. Tự động tính toán lại số dư nợ còn lại (remaining_debt), đã thanh toán (paid_amount),
--    và trạng thái công nợ (UNPAID, PARTIAL, PAID, OVERDUE) trên từng hóa đơn và đại lý.
-- 6. Đồng bộ ngược trạng thái đơn hàng (PAID / PARTIAL / DELIVERED).
-- 7. Cung cấp View v_dealer_debt_summary và Stored Procedure sp_sync_all_dealer_debt.
-- =====================================================================================

USE quanlybanhang;
GO

-- 1. Đảm bảo bảng receipt_vouchers tồn tại trên SQL Server
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'receipt_vouchers')
BEGIN
    CREATE TABLE receipt_vouchers (
        id INT IDENTITY(1,1) PRIMARY KEY,
        receipt_code NVARCHAR(50) NOT NULL UNIQUE,
        dealer_id INT NOT NULL,
        dealer_code NVARCHAR(50) NOT NULL,
        dealer_name NVARCHAR(255) NOT NULL,
        dealer_address NVARCHAR(500) NULL,
        dealer_phone NVARCHAR(50) NULL,
        dealer_tax_code NVARCHAR(50) NULL,
        order_code NVARCHAR(50) NULL,
        amount FLOAT NOT NULL DEFAULT 0.0,
        amount_in_words NVARCHAR(500) NULL,
        payment_method NVARCHAR(50) NOT NULL DEFAULT N'Tiền mặt',
        reason NVARCHAR(500) NULL,
        payer_name NVARCHAR(150) NULL,
        collector_name NVARCHAR(150) NULL,
        collector_role NVARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT GETUTCDATE(),
        note NVARCHAR(MAX) NULL,
        CONSTRAINT FK_receipt_vouchers_dealers FOREIGN KEY (dealer_id) REFERENCES dealers(id)
    );
    CREATE INDEX IX_receipt_vouchers_dealer_id ON receipt_vouchers(dealer_id);
    CREATE INDEX IX_receipt_vouchers_order_code ON receipt_vouchers(order_code);
    PRINT N'Đã tạo bảng receipt_vouchers thành công.';
END
GO

-- 2. Nạp dữ liệu phiếu thu mẫu vào SQL Server nếu bảng receipt_vouchers đang trống
IF NOT EXISTS (SELECT 1 FROM receipt_vouchers)
BEGIN
    INSERT INTO receipt_vouchers (
        receipt_code, dealer_id, dealer_code, dealer_name, dealer_address, dealer_phone, 
        dealer_tax_code, order_code, amount, amount_in_words, payment_method, reason, 
        payer_name, collector_name, collector_role, created_at, note
    )
    VALUES 
    (
        N'PT-DL001-20260318-01', 1, N'DL001', N'Đại Lý Phân Phối Miền Bắc - Sao Mai', 
        N'120 Cầu Giấy, Hà Nội', N'0912345678', N'0108923456', N'DH-2026-0001', 
        20000000.0, N'Hai mươi triệu đồng chẵn.', N'Chuyển khoản', 
        N'Thu tiền thanh toán đợt 1 đơn hàng DH-2026-0001', N'Nguyễn Văn Sao Mai', 
        N'Dang Ke Toan', N'Kế toán công nợ', '2026-03-18 10:00:00', N'Ủy nhiệm chi Vietcombank VCB88291'
    ),
    (
        N'PT-DL001-20260325-02', 1, N'DL001', N'Đại Lý Phân Phối Miền Bắc - Sao Mai', 
        N'120 Cầu Giấy, Hà Nội', N'0912345678', N'0108923456', N'DH-2026-0001', 
        15800000.0, N'Mười lăm triệu tám trăm nghìn đồng chẵn.', N'Chuyển khoản', 
        N'Thu tiền thanh toán dứt điểm đơn hàng DH-2026-0001', N'Nguyễn Văn Sao Mai', 
        N'Dang Ke Toan', N'Kế toán công nợ', '2026-03-25 14:30:00', N'Ủy nhiệm chi Vietcombank VCB89102'
    ),
    (
        N'PT-DL001-20260410-03', 1, N'DL001', N'Đại Lý Phân Phối Miền Bắc - Sao Mai', 
        N'120 Cầu Giấy, Hà Nội', N'0912345678', N'0108923456', N'DH-2026-0002', 
        10000000.0, N'Mười triệu đồng chẵn.', N'Tiền mặt', 
        N'Thu tiền mặt thanh toán tạm ứng đơn DH-2026-0002', N'Trần Thị Mai', 
        N'Dang Ke Toan', N'Kế toán công nợ', '2026-04-10 09:15:00', N'Nộp tiền mặt tại văn phòng kế toán'
    ),
    (
        N'PT-DL002-20260327-01', 2, N'DL002', N'Đại Lý Thời Trang Tân Bình', 
        N'45 Lý Thường Kiệt, TP. HCM', N'0987654321', N'0312456789', N'DH-2026-0003', 
        12000000.0, N'Mười hai triệu đồng chẵn.', N'Chuyển khoản', 
        N'Thu tiền thanh toán đợt 1 đơn hàng DH-2026-0003', N'Lê Văn Bình', 
        N'Dang Ke Toan', N'Kế toán công nợ', '2026-03-27 11:00:00', N'Techcombank TCB55123'
    ),
    (
        N'PT-DL003-20260330-01', 3, N'DL003', N'Đại Lý Tổng Hợp Hải Phòng', 
        N'88 Lạch Tray, Hải Phòng', N'0934567890', N'0201345678', N'DH-2026-0004', 
        14500000.0, N'Mười bốn triệu năm trăm nghìn đồng chẵn.', N'Chuyển khoản', 
        N'Thanh toán trọn gói đơn DH-2026-0004', N'Vũ Văn Hải', 
        N'Dang Ke Toan', N'Kế toán công nợ', '2026-03-30 15:20:00', N'MB Bank MBB99212'
    );
    PRINT N'Đã nạp dữ liệu phiếu thu mẫu vào receipt_vouchers.';
END
GO

-- 3. Tạo hoặc thay thế Stored Procedure sp_sync_all_dealer_debt
CREATE OR ALTER PROCEDURE sp_sync_all_dealer_debt
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    TRY_SYNC:
    BEGIN TRY
        PRINT N'-- BƯỚC 1: CẬP NHẬT CẤU HÌNH CÔNG NỢ ĐẠI LÝ MẶC ĐỊNH --';
        UPDATE dealers
        SET max_debt_days = COALESCE(max_debt_days, 30),
            overdue_days_allowed = COALESCE(overdue_days_allowed, 30),
            credit_limit = COALESCE(credit_limit, 50000000.0)
        WHERE max_debt_days IS NULL 
           OR overdue_days_allowed IS NULL 
           OR credit_limit IS NULL;

        PRINT N'-- BƯỚC 2: TỰ ĐỘNG SINH HÓA ĐƠN CHO CÁC ĐƠN HÀNG HỢP LỆ CHƯA CÓ HÓA ĐƠN --';
        INSERT INTO invoices (
            invoice_code,
            order_id,
            order_code,
            dealer_id,
            dealer_name,
            issue_date,
            due_date,
            total_amount,
            paid_amount,
            remaining_debt,
            status,
            items_json,
            note,
            created_at,
            updated_at
        )
        SELECT
            'HD-' + o.order_code AS invoice_code,
            o.id AS order_id,
            o.order_code,
            o.dealer_id,
            o.dealer_name,
            COALESCE(o.created_at, GETUTCDATE()) AS issue_date,
            DATEADD(DAY, COALESCE(d.overdue_days_allowed, d.max_debt_days, 30), COALESCE(o.created_at, GETUTCDATE())) AS due_date,
            COALESCE(o.total_amount, 0.0) AS total_amount,
            0.0 AS paid_amount,
            COALESCE(o.total_amount, 0.0) AS remaining_debt,
            'UNPAID' AS status,
            o.items_json,
            COALESCE(o.note, N'Hóa đơn phát hành tự động theo đơn hàng ' + o.order_code) AS note,
            COALESCE(o.created_at, GETUTCDATE()) AS created_at,
            GETUTCDATE() AS updated_at
        FROM orders o
        JOIN dealers d ON o.dealer_id = d.id
        WHERE o.status NOT IN ('CANCELLED', 'REJECTED', 'DRAFT')
          AND NOT EXISTS (
              SELECT 1 FROM invoices inv WHERE inv.invoice_code = ('HD-' + o.order_code)
          );

        PRINT N'-- BƯỚC 3: XỬ LÝ HÓA ĐƠN ĐỐI VỚI ĐƠN HÀNG BỊ HỦY / TỪ CHỐI --';
        UPDATE inv
        SET inv.remaining_debt = 0.0,
            inv.status = 'CANCELLED',
            inv.note = COALESCE(inv.note, N'') + N' [Đơn hàng gốc đã hủy]',
            inv.updated_at = GETUTCDATE()
        FROM invoices inv
        JOIN orders o ON inv.order_code = o.order_code
        WHERE o.status IN ('CANCELLED', 'REJECTED');

        PRINT N'-- BƯỚC 4: TÍNH TOÁN LẠI TIỀN ĐÃ THU VÀ GIẢM TRỪ CÔNG NỢ THEO TỪNG HÓA ĐƠN --';
        -- Sử dụng CTE tổng hợp các khoản thu và điều chỉnh giảm trừ
        ;WITH OrderPayments AS (
            -- Khoản thu từ receipt_vouchers
            SELECT 
                order_code, 
                SUM(amount) AS total_voucher_amount
            FROM receipt_vouchers
            WHERE order_code IS NOT NULL AND order_code != ''
            GROUP BY order_code
        ),
        OrderCollections AS (
            -- Khoản thu từ payment_collections đã xác nhận
            SELECT 
                order_code, 
                SUM(amount) AS total_collected_amount
            FROM payment_collections
            WHERE order_code IS NOT NULL 
              AND order_code != ''
              AND status = 'CONFIRMED'
            GROUP BY order_code
        ),
        OrderAllocations AS (
            -- Khoản thu từ payment_allocations (Thu tiền & đối trừ nợ - S7-02)
            SELECT 
                pa.invoice_id, 
                SUM(pa.current_allocated_amount) AS total_allocated_amount
            FROM payment_allocations pa
            JOIN payment_receipts pr ON pr.id = pa.payment_id
            WHERE pr.status = 'CONFIRMED'
            GROUP BY pa.invoice_id
        ),
        InvoiceAdjustments AS (
            -- Khoản giảm nợ do trả hàng từ debt_adjustments
            SELECT 
                original_invoice_code, 
                SUM(total_adjusted_amount) AS total_adjusted_amount
            FROM debt_adjustments
            WHERE status = 'POSTED'
            GROUP BY original_invoice_code
        )
        UPDATE inv
        SET 
            -- Cập nhật tổng số tiền đã thanh toán
            inv.paid_amount = ROUND(
                COALESCE(p.total_voucher_amount, 0.0) + COALESCE(c.total_collected_amount, 0.0) + COALESCE(a.total_allocated_amount, 0.0), 
                2
            ),
            -- Cập nhật nợ còn lại = Tổng tiền hàng - Đã thanh toán - Giảm trừ trả hàng
            inv.remaining_debt = CASE 
                WHEN inv.status = 'CANCELLED' THEN 0.0
                ELSE CASE 
                    WHEN (inv.total_amount - (COALESCE(p.total_voucher_amount, 0.0) + COALESCE(c.total_collected_amount, 0.0) + COALESCE(a.total_allocated_amount, 0.0)) - COALESCE(adj.total_adjusted_amount, 0.0)) <= 0.0 
                    THEN 0.0
                    ELSE ROUND(inv.total_amount - (COALESCE(p.total_voucher_amount, 0.0) + COALESCE(c.total_collected_amount, 0.0) + COALESCE(a.total_allocated_amount, 0.0)) - COALESCE(adj.total_adjusted_amount, 0.0), 2)
                END
            END,
            -- Cập nhật trạng thái hóa đơn chuẩn xác theo chuẩn kế toán
            inv.status = CASE 
                WHEN inv.status = 'CANCELLED' THEN 'CANCELLED'
                WHEN (inv.total_amount - (COALESCE(p.total_voucher_amount, 0.0) + COALESCE(c.total_collected_amount, 0.0) + COALESCE(a.total_allocated_amount, 0.0)) - COALESCE(adj.total_adjusted_amount, 0.0)) <= 0.0 
                THEN 'PAID'
                WHEN inv.due_date < GETUTCDATE() 
                THEN 'OVERDUE'
                WHEN (COALESCE(p.total_voucher_amount, 0.0) + COALESCE(c.total_collected_amount, 0.0) + COALESCE(a.total_allocated_amount, 0.0) > 0.0) 
                  OR (COALESCE(adj.total_adjusted_amount, 0.0) > 0.0)
                THEN 'PARTIAL'
                ELSE 'UNPAID'
            END,
            inv.updated_at = GETUTCDATE()
        FROM invoices inv
        LEFT JOIN OrderPayments p ON inv.order_code = p.order_code
        LEFT JOIN OrderCollections c ON inv.order_code = c.order_code
        LEFT JOIN OrderAllocations a ON inv.id = a.invoice_id
        LEFT JOIN InvoiceAdjustments adj ON inv.invoice_code = adj.original_invoice_code
        WHERE inv.status != 'CANCELLED';

        PRINT N'-- BƯỚC 5: ĐỒNG BỘ TRẠNG THÁI NGƯỢC VÀO BẢNG ORDERS --';
        -- Nếu hóa đơn đã tất toán công nợ (remaining_debt = 0), đơn hàng chuyển PAID
        UPDATE o
        SET o.status = 'PAID'
        FROM orders o
        JOIN invoices inv ON o.order_code = inv.order_code
        WHERE inv.remaining_debt <= 0.0 
          AND inv.status = 'PAID'
          AND o.status NOT IN ('CANCELLED', 'REJECTED', 'PAID');

        -- Nếu hóa đơn chưa trả hết nhưng đã thanh toán một phần, cập nhật PARTIAL nếu cần
        UPDATE o
        SET o.status = 'CONFIRMED'
        FROM orders o
        JOIN invoices inv ON o.order_code = inv.order_code
        WHERE inv.remaining_debt > 0.0
          AND o.status = 'PAID';

        COMMIT TRANSACTION;
        PRINT N'>>> ĐỒNG BỘ DỮ LIỆU CÔNG NỢ TRÊN SQL SERVER HOÀN TẤT THÀNH CÔNG! <<<';
    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        DECLARE @ErrMsg NVARCHAR(4000) = ERROR_MESSAGE();
        RAISERROR(@ErrMsg, 16, 1);
    END CATCH
END;
GO

-- 4. Tạo View tổng hợp công nợ đại lý phục vụ báo cáo trực tiếp từ SQL Server
CREATE OR ALTER VIEW v_dealer_debt_summary
AS
SELECT 
    d.id AS dealer_id,
    d.code AS dealer_code,
    d.name AS dealer_name,
    d.phone,
    d.region,
    d.assigned_sale_id,
    COALESCE(d.credit_limit, 50000000.0) AS credit_limit,
    COALESCE(d.max_debt_days, 30) AS max_debt_days,
    COALESCE(d.overdue_days_allowed, 30) AS overdue_days_allowed,
    
    -- Tổng giá trị hóa đơn đã phát hành
    COALESCE(SUM(CASE WHEN inv.status != 'CANCELLED' THEN inv.total_amount ELSE 0.0 END), 0.0) AS total_invoiced,
    
    -- Tổng số tiền đã thanh toán
    COALESCE(SUM(CASE WHEN inv.status != 'CANCELLED' THEN inv.paid_amount ELSE 0.0 END), 0.0) AS total_paid,
    
    -- Tổng công nợ còn lại hiện tại
    COALESCE(SUM(CASE WHEN inv.status != 'CANCELLED' THEN inv.remaining_debt ELSE 0.0 END), 0.0) AS current_debt,
    
    -- Tổng nợ quá hạn
    COALESCE(SUM(CASE WHEN inv.status = 'OVERDUE' THEN inv.remaining_debt ELSE 0.0 END), 0.0) AS overdue_debt,
    
    -- Số lượng hóa đơn quá hạn
    COUNT(CASE WHEN inv.status = 'OVERDUE' THEN 1 ELSE NULL END) AS overdue_invoices_count,
    
    -- Hạn mức khả dụng còn lại = Hạn mức - Tổng nợ hiện tại
    CASE 
        WHEN COALESCE(d.credit_limit, 50000000.0) - COALESCE(SUM(CASE WHEN inv.status != 'CANCELLED' THEN inv.remaining_debt ELSE 0.0 END), 0.0) < 0.0 
        THEN 0.0
        ELSE COALESCE(d.credit_limit, 50000000.0) - COALESCE(SUM(CASE WHEN inv.status != 'CANCELLED' THEN inv.remaining_debt ELSE 0.0 END), 0.0)
    END AS remaining_credit,
    
    -- Cờ cảnh báo
    CASE 
        WHEN COALESCE(SUM(CASE WHEN inv.status = 'OVERDUE' THEN inv.remaining_debt ELSE 0.0 END), 0.0) > 0.0 
        THEN 1 ELSE 0 
    END AS is_overdue,
    
    CASE 
        WHEN COALESCE(SUM(CASE WHEN inv.status != 'CANCELLED' THEN inv.remaining_debt ELSE 0.0 END), 0.0) > COALESCE(d.credit_limit, 50000000.0) 
        THEN 1 ELSE 0 
    END AS is_over_limit

FROM dealers d
LEFT JOIN invoices inv ON d.id = inv.dealer_id
GROUP BY 
    d.id, d.code, d.name, d.phone, d.region, d.assigned_sale_id,
    d.credit_limit, d.max_debt_days, d.overdue_days_allowed;
GO

-- 5. Thực thi ngay Stored Procedure để chuẩn hóa dữ liệu
EXEC sp_sync_all_dealer_debt;
GO

-- 6. Hiển thị kết quả đồng bộ dữ liệu công nợ trên SQL Server
SELECT * FROM v_dealer_debt_summary;
GO
SELECT id, invoice_code, order_code, dealer_name, total_amount, paid_amount, remaining_debt, status, due_date FROM invoices;
GO
