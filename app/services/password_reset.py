import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header

from fastapi import HTTPException, status
from app.models.user import USERS_DB, save_users_db, load_users_db
from app.core.config import settings
from app.core.security import get_password_hash

logger = logging.getLogger(__name__)

RESET_TOKEN_TTL_MINUTES = 30
RESET_MESSAGE = 'Thông tin lấy lại quyền truy cập đã được gửi về email của bạn.'


class PasswordResetService:
    def __init__(self) -> None:
        # Lưu token hash -> (username, expires_at)
        self._tokens: dict[str, tuple[str, datetime]] = {}

    def request_reset(self, email: str) -> str:
        # Nạp lại dữ liệu mới nhất từ file JSON để đảm bảo email vừa sửa/tạo được nhận ngay
        load_users_db()
        normalized_email = email.strip().lower()

        # Kiểm tra định dạng nếu người dùng nhập email (có chứa @)
        import re
        if '@' in normalized_email:
            email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
            if not re.match(email_regex, normalized_email):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Vui lòng xem lại thông tin tài khoản!"
                )
        else:
            # Kiểm tra định dạng cơ bản nếu là username (chỉ chữ, số, _, -)
            username_regex = r"^[a-zA-Z0-9_-]+$"
            if not re.match(username_regex, normalized_email):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Vui lòng xem lại thông tin tài khoản!"
                )

        
        # Tìm người dùng tương ứng trong USERS_DB (hỗ trợ nhập email hoặc username)
        matched_user = None
        for u in USERS_DB.values():
            if (u.email and u.email.lower() == normalized_email) or (u.username.lower() == normalized_email):
                matched_user = u
                break

        if not matched_user or not matched_user.is_active:
            print(f"[FORGOT PASSWORD] Không tìm thấy user hoặc tài khoản bị khóa cho input: '{normalized_email}'", flush=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vui lòng xem lại thông tin tài khoản!")
            
        if not matched_user.email:
            print(f"[FORGOT PASSWORD] Tài khoản không có email: '{normalized_email}'", flush=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vui lòng xem lại thông tin tài khoản!")

        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
        self._tokens[token_hash] = (matched_user.username, expires_at)

        reset_token = token
        link_str = f"http://localhost:5173/reset-password?token={reset_token}"
        # In link ra terminal backend an toan cho Windows Console
        print("\n==========================================", flush=True)
        print(f"[RESET PASSWORD LINK]: {link_str}", flush=True)
        print("==========================================\n", flush=True)

        recipient_email = matched_user.email

        # Gửi email qua Gmail SMTP bằng thư viện smtplib và email.mime
        try:
            self._send_email(
                to_email=recipient_email,
                token=reset_token,
                recipient_name=matched_user.full_name or matched_user.username
            )
            print(f"[GMAIL SENT OK] Da gui email dat lai mat khau thanh cong toi: {recipient_email}", flush=True)
        except Exception as e:
            # Ghi log lỗi rõ ràng trên server nhưng không để lộ exception ra ngoài frontend
            print(f"[GMAIL SEND ERROR] Loi gui email toi {recipient_email}: {e}", flush=True)
            logger.error("Loi khi gui email dat lai mat khau toi %s: %s", recipient_email, e, exc_info=True)

        return RESET_MESSAGE

    def reset_password(self, token: str, new_password: str) -> str:
        token_hash = self._hash_token(token)
        # Liên kết chỉ dùng được 1 lần: pop token ra khỏi bộ nhớ ngay khi sử dụng
        stored_token = self._tokens.pop(token_hash, None)
        if stored_token is None or stored_token[1] <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Token đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.',
            )

        username, _ = stored_token
        user = USERS_DB.get(username)
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Người dùng không tồn tại hoặc đã bị vô hiệu hóa.',
            )

        # Cập nhật mật khẩu mã hóa mới và tăng token_version để thu hồi các phiên cũ
        user.hashed_password = get_password_hash(new_password)
        user.token_version = getattr(user, 'token_version', 1) + 1
        save_users_db()
        return 'Đặt lại mật khẩu thành công. Vui lòng đăng nhập với mật khẩu mới.'

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @staticmethod
    def _send_email(to_email: str, token: str, recipient_name: str = "Quý khách") -> None:
        mail_server = settings.MAIL_SERVER
        mail_port = settings.MAIL_PORT
        mail_username = settings.MAIL_USERNAME
        mail_password = settings.MAIL_PASSWORD
        mail_from = settings.MAIL_FROM or mail_username

        if not mail_server or not mail_username or not mail_password:
            logger.warning("SMTP chưa được cấu hình đầy đủ trong file .env. Bỏ qua gửi email thực tế.")
            return

        reset_link = f"http://localhost:5173/reset-password?token={token}"

        # Tiêu đề email
        subject = "[Hệ Thống Kho & Bán Hàng] Hướng dẫn đặt lại mật khẩu"

        # Bản nội dung Text thuần
        text_content = (
            f"Xin chào {recipient_name},\n\n"
            "Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn tại Hệ Thống Kho & Bán Hàng.\n\n"
            f"Vui lòng truy cập đường dẫn sau để đặt lại mật khẩu:\n{reset_link}\n\n"
            "Lưu ý: Liên kết có hiệu lực trong vòng 30 phút và chỉ sử dụng được 1 lần duy nhất.\n"
            "Nếu bạn không yêu cầu điều này, xin vui lòng bỏ qua thư này.\n\n"
            "Trân trọng,\nHệ Thống Quản Lý Bán Hàng & Kho"
        )

        # Bản nội dung HTML giao diện cao cấp, chuẩn hoá UTF-8
        html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{subject}</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    </style>
</head>
<body style="margin: 0; padding: 0; background-color: #0b1120; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0b1120; padding: 40px 15px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 540px; background-color: #0f172a; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 40px rgba(0, 0, 0, 0.45); border: 1px solid rgba(255, 255, 255, 0.1);">
                    <!-- Brand Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); padding: 32px 30px; text-align: center; border-bottom: 1px solid rgba(255, 255, 255, 0.08);">
                            <div style="display: inline-block; width: 48px; height: 48px; background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%); border-radius: 14px; margin-bottom: 12px; line-height: 48px; text-align: center; box-shadow: 0 8px 20px rgba(99, 102, 241, 0.35);">
                                <span style="font-size: 22px; color: #ffffff;">📦</span>
                            </div>
                            <h1 style="color: #ffffff; font-size: 19px; margin: 0; font-weight: 700; letter-spacing: 0.5px;">HỆ THỐNG QUẢN LÝ KHO & BÁN HÀNG</h1>
                            <p style="color: #94a3b8; font-size: 13px; margin: 6px 0 0 0; font-weight: 500;">Yêu cầu cấp lại mật khẩu truy cập hệ thống</p>
                        </td>
                    </tr>
                    <!-- Main Body -->
                    <tr>
                        <td style="padding: 36px 32px 28px 32px; color: #cbd5e1; font-size: 15px; line-height: 1.65;">
                            <p style="margin-top: 0; font-size: 17px; font-weight: 600; color: #f8fafc;">
                                Xin chào <span style="color: #818cf8;">{recipient_name}</span>,
                            </p>
                            <p style="margin: 14px 0; color: #94a3b8;">
                                Chúng tôi đã nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn tại hệ thống quản lý. Để tiếp tục, bạn vui lòng nhấp vào nút xác nhận bên dưới:
                            </p>

                            <!-- Primary Action Button -->
                            <table border="0" cellspacing="0" cellpadding="0" style="margin: 32px auto; width: 100%;">
                                <tr>
                                    <td align="center">
                                        <a href="{reset_link}" target="_blank" style="display: inline-block; padding: 14px 38px; font-size: 15px; color: #ffffff; text-decoration: none; font-weight: 600; border-radius: 10px; background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%); box-shadow: 0 10px 22px rgba(79, 70, 229, 0.4); letter-spacing: 0.3px;">
                                            🔒 Đặt Lại Mật Khẩu Mới
                                        </a>
                                    </td>
                                </tr>
                            </table>

                            <div style="background-color: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 16px; margin: 26px 0 16px 0;">
                                <p style="margin: 0 0 8px 0; font-size: 12.5px; color: #94a3b8; font-weight: 500;">
                                    Hoặc sao chép liên kết bên dưới vào trình duyệt của bạn:
                                </p>
                                <p style="margin: 0; font-size: 12.5px; word-break: break-all;">
                                    <a href="{reset_link}" target="_blank" style="color: #818cf8; text-decoration: none; border-bottom: 1px dotted #818cf8;">{reset_link}</a>
                                </p>
                            </div>

                            <!-- Security Warning Alert -->
                            <div style="background-color: rgba(245, 158, 11, 0.1); border-left: 4px solid #f59e0b; padding: 13px 16px; margin-top: 24px; border-radius: 6px;">
                                <p style="margin: 0; font-size: 13px; color: #fde68a; line-height: 1.5;">
                                    ⏱️ <strong>Bảo mật:</strong> Liên kết có hiệu lực trong vòng <strong>30 phút</strong> và chỉ sử dụng được <strong>01 lần duy nhất</strong>. Nếu bạn không gửi yêu cầu này, xin vui lòng bỏ qua thư và tài khoản vẫn an toàn tuyệt đối.
                                </p>
                            </div>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #0b1120; padding: 22px 30px; text-align: center; border-top: 1px solid rgba(255, 255, 255, 0.06); font-size: 12px; color: #64748b; line-height: 1.5;">
                            Email tự động gửi từ <strong>Hệ Thống Quản Lý Kho & Bán Hàng</strong>.<br />
                            Vui lòng không phản hồi thư này.
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = mail_from
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP(mail_server, mail_port, timeout=15) as server:
            if settings.MAIL_TLS:
                server.starttls()
            server.login(mail_username, mail_password)
            server.send_message(msg)
            logger.info("Đã gửi email đặt lại mật khẩu thành công tới %s", to_email)


password_reset_service = PasswordResetService()
