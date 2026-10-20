import logging
import secrets
import smtplib
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header

from app.core.config import settings

logger = logging.getLogger(__name__)


def generate_temporary_password() -> str:
    # Tránh các ký tự dễ nhầm lẫn như l, 1, I, O, 0 và các ký tự đặc biệt gây lỗi copy-paste
    uppercase = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lowercase = "abcdefghijkmnpqrstuvwxyz"
    digits = "23456789"
    specials = "@#$%&"
    
    # Đảm bảo có ít nhất 1 chữ hoa, 1 chữ thường, 1 số, 1 ký tự đặc biệt
    pwd_chars = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(specials),
    ]
    all_chars = uppercase + lowercase + digits + specials
    pwd_chars += [secrets.choice(all_chars) for _ in range(8)]
    secrets.SystemRandom().shuffle(pwd_chars)
    return "".join(pwd_chars)


def send_customer_credentials(
    email: str,
    full_name: str,
    username: str,
    password: str,
    phone: str = "",
    role_title: str = "",
) -> bool:
    login_url = settings.FRONTEND_URL.rstrip('/')
    phone_display = phone.strip() if phone and phone.strip() else "Chưa cập nhật"
    role_display = role_title.strip() if role_title and role_title.strip() else "Người dùng hệ thống"
    
    # In ra terminal server backend de admin co the kiem tra truc tiep (tránh UnicodeEncodeError trên Windows cp1252)
    try:
        print("\n==========================================", flush=True)
        print("[TAO TAI KHOAN MOI - CAP MA DANG NHAP]", flush=True)
        print(f"Ho ten: {full_name}".encode('ascii', errors='backslashreplace').decode('ascii'), flush=True)
        print(f"Vai tro: {role_display}".encode('ascii', errors='backslashreplace').decode('ascii'), flush=True)
        print(f"Username: {username}", flush=True)
        print(f"Email: {email}", flush=True)
        print(f"Phone: {phone_display}", flush=True)
        print(f"Ma dang nhap / Mat khau tam thoi: {password}", flush=True)
        print(f"Dang nhap tai: {login_url}", flush=True)
        print("==========================================\n", flush=True)
    except Exception:
        pass

    if not settings.MAIL_SERVER or not settings.MAIL_USERNAME or not settings.MAIL_PASSWORD:
        logger.warning("SMTP is not configured; cannot send customer credentials to %s. Temporary password: %s", email, password)
        return False

    subject = f"[Hệ Thống Kho & Bán Hàng] Cấp mã đăng nhập & thông tin tài khoản ({role_display})"
    
    text_content = (
        f"Xin chào {full_name},\n\n"
        f"Tài khoản của bạn ({role_display}) đã được khởi tạo thành công trên Hệ Thống Quản Lý Bán Hàng & Kho.\n\n"
        "THÔNG TIN ĐĂNG NHẬP & MÃ KÍCH HOẠT:\n"
        f"- Tên đăng nhập: {username}\n"
        f"- Email đăng nhập: {email}\n"
        f"- Số điện thoại: {phone_display}\n"
        f"- Mã đăng nhập / Mật khẩu tạm thời: {password}\n"
        f"- Link đăng nhập: {login_url}\n\n"
        "Lưu ý: Mật khẩu/mã trên có phân biệt chữ hoa, chữ thường và ký tự đặc biệt. Hãy sao chép chính xác không chứa khoảng trắng thừa.\n"
        "Vui lòng đăng nhập và đổi mật khẩu sau lần sử dụng đầu tiên.\n\n"
        "Trân trọng,\nHệ Thống Quản Lý Bán Hàng & Kho"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0f172a; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0f172a; padding: 30px 10px;">
        <tr>
            <td align="center">
                <table width="100%" max-width="580" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #1e293b; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5); border: 1px solid #334155;">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 26px 30px; text-align: center;">
                            <h1 style="color: #ffffff; font-size: 20px; margin: 0; font-weight: 700; letter-spacing: 0.5px;">HỆ THỐNG QUẢN LÝ KHO & BÁN HÀNG</h1>
                            <p style="color: #d1fae5; font-size: 13px; margin: 6px 0 0 0;">Thông Tin Cấp Tài Khoản & Mã Đăng Nhập ({role_display})</p>
                        </td>
                    </tr>
                    <!-- Body Content -->
                    <tr>
                        <td style="padding: 32px 30px 24px 30px; color: #cbd5e1; font-size: 14.5px; line-height: 1.6;">
                            <p style="margin-top: 0; font-size: 16px; font-weight: 600; color: #f8fafc;">
                                Xin chào <strong>{full_name}</strong>,
                            </p>
                            <p style="margin: 12px 0; color: #94a3b8;">
                                Tài khoản <strong>{role_display}</strong> của bạn đã được khởi tạo thành công trên hệ thống. Dưới đây là thông tin đăng nhập và mã mật khẩu tạm thời của bạn:
                            </p>
                            
                            <!-- Credentials Box -->
                            <div style="background-color: #0f172a; border: 1px solid #334155; border-radius: 10px; padding: 20px; margin: 20px 0;">
                                <table width="100%" border="0" cellspacing="0" cellpadding="6" style="font-size: 14px;">
                                    <tr>
                                        <td width="150" style="color: #94a3b8; font-weight: 500;">Tên đăng nhập:</td>
                                        <td style="color: #38bdf8; font-weight: 700; font-size: 15px; font-family: monospace;">{username}</td>
                                    </tr>
                                    <tr>
                                        <td style="color: #94a3b8; font-weight: 500;">Email tài khoản:</td>
                                        <td style="color: #f8fafc; font-weight: 600;">{email}</td>
                                    </tr>
                                    <tr>
                                        <td style="color: #94a3b8; font-weight: 500;">Số điện thoại:</td>
                                        <td style="color: #f8fafc;">{phone_display}</td>
                                    </tr>
                                    <tr>
                                        <td style="color: #94a3b8; font-weight: 500;">Mã / Mật khẩu tạm thời:</td>
                                        <td>
                                            <span style="display: inline-block; background-color: #1e1b4b; border: 1px solid #6366f1; color: #a5b4fc; font-weight: 800; font-size: 16px; font-family: monospace; padding: 6px 14px; border-radius: 6px; letter-spacing: 1px;">{password}</span>
                                        </td>
                                    </tr>
                                </table>
                            </div>

                            <p style="font-size: 13px; color: #fbbf24; margin: 14px 0; background: rgba(245, 158, 11, 0.1); border-left: 3px solid #f59e0b; padding: 10px 14px; border-radius: 4px;">
                                ⚠️ <strong>Lưu ý quan trọng:</strong> Mật khẩu có phân biệt chữ hoa, chữ thường và ký tự đặc biệt. Khi sao chép, hãy chắc chắn <strong>không chọn thừa khoảng trắng ở đầu hoặc cuối</strong>.
                            </p>

                            <!-- Action Button -->
                            <table border="0" cellspacing="0" cellpadding="0" style="margin: 26px auto 14px auto;">
                                <tr>
                                    <td align="center" style="border-radius: 8px; background: linear-gradient(135deg, #10b981, #059669); box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4);">
                                        <a href="{login_url}" target="_blank" style="display: inline-block; padding: 13px 34px; font-size: 15px; color: #ffffff; text-decoration: none; font-weight: 700; border-radius: 8px;">
                                            Đăng Nhập Ngay
                                        </a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #0b1120; padding: 20px 30px; text-align: center; border-top: 1px solid #1e293b;">
                            <p style="margin: 0; font-size: 12px; color: #64748b;">
                                Hệ Thống Quản Lý Bán Hàng & Kho &copy; 2026. Đây là email tự động, vui lòng không trả lời thư này.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    from email.utils import formataddr
    message = MIMEMultipart("alternative")
    message["Subject"] = Header(subject, "utf-8")
    sender_email = settings.MAIL_FROM or settings.MAIL_USERNAME
    message["From"] = formataddr((str(Header("Hệ Thống Quản Lý Kho & Bán Hàng", "utf-8")), sender_email))
    message["To"] = email
    message.attach(MIMEText(text_content, "plain", "utf-8"))
    message.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if settings.MAIL_PORT == 465:
            server_context = smtplib.SMTP_SSL(settings.MAIL_SERVER, settings.MAIL_PORT, timeout=15)
        else:
            server_context = smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT, timeout=15)
            if settings.MAIL_TLS:
                server_context.starttls()
        
        with server_context as server:
            server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
            server.send_message(message)
        print(f"[GMAIL SENT OK] Da gui email thong tin tai khoan toi: {email}", flush=True)
        logger.info("Customer account credentials email sent to %s", email)
        return True
    except Exception as e:
        print(f"[GMAIL SEND ERROR] Loi gui email tai khoan toi {email}: {e}", flush=True)
        logger.exception("Unable to send customer credentials email to %s: %s", email, e)
        return False
