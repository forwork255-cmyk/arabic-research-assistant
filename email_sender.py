"""
Sends the password-reset email via Gmail SMTP -- Python's built-in smtplib
and email modules, no new dependency, no new paid vendor. Uses the owner's
own Gmail account (an "App Password", not the real Gmail password) to send.

Required Streamlit secrets:
    GMAIL_ADDRESS      -- the sending Gmail address, e.g. "you@gmail.com"
    GMAIL_APP_PASSWORD -- a Gmail "App Password" (Google Account -> Security
                          -> 2-Step Verification -> App passwords), NOT the
                          real account password.
"""

import smtplib
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class EmailSendError(Exception):
    """Raised when the reset email could not be sent -- callers show a
    generic friendly message, never the raw SMTP error, to the user."""


def send_password_reset_email(gmail_address: str, gmail_app_password: str, to_email: str, reset_link: str) -> None:
    # Deliberately doesn't include the password hint here -- it's shown on
    # the reset page itself (reached via this link), not in the email body,
    # so it's not sitting in plaintext in an inbox/forwarded copy any longer
    # than the reset link itself already is.
    body_lines = [
        "تلقّينا طلباً لإعادة تعيين كلمة المرور لحسابك في مساعد البحث العلمي العربي.",
        "",
        f"لإعادة التعيين، افتح هذا الرابط خلال ساعة واحدة:",
        reset_link,
        "",
        "إذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة بأمان.",
    ]
    message = MIMEText("\n".join(body_lines), _charset="utf-8")
    message["Subject"] = "إعادة تعيين كلمة المرور"
    message["From"] = gmail_address
    message["To"] = to_email

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.login(gmail_address, gmail_app_password)
            server.sendmail(gmail_address, [to_email], message.as_string())
    except Exception as error:
        raise EmailSendError(f"Failed to send password reset email: {error}") from error
