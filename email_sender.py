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

import html
import smtplib
from email.mime.multipart import MIMEMultipart
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
    #
    # Sent as HTML with a real <a href> anchor, not a bare URL in plain
    # text -- a long bare URL is a real risk of being visually (and
    # sometimes literally) broken across a line wrap by the mail client,
    # which silently turns the link into a dead/partial one. An href
    # attribute isn't affected by how the visible text wraps.
    escaped_link = html.escape(reset_link, quote=True)
    plain_body = (
        "تلقّينا طلباً لإعادة تعيين كلمة المرور لحسابك في مساعد البحث العلمي العربي.\n\n"
        "لإعادة التعيين، افتح هذا الرابط خلال ساعة واحدة (انسخه بالكامل إلى المتصفح إذا لم يعمل النقر عليه):\n"
        f"{reset_link}\n\n"
        "إذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة بأمان."
    )
    html_body = f"""\
<html dir="rtl" lang="ar"><body style="font-family: Arial, sans-serif; text-align: right;">
<p>تلقّينا طلباً لإعادة تعيين كلمة المرور لحسابك في مساعد البحث العلمي العربي.</p>
<p>لإعادة التعيين، اضغط على الرابط التالي خلال ساعة واحدة:</p>
<p><a href="{escaped_link}">إعادة تعيين كلمة المرور</a></p>
<p style="color: #666; font-size: 0.9em;">إذا لم يعمل الزر أعلاه، انسخ هذا الرابط بالكامل إلى المتصفح:<br>{escaped_link}</p>
<p>إذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة بأمان.</p>
</body></html>"""

    message = MIMEMultipart("alternative")
    message["Subject"] = "إعادة تعيين كلمة المرور"
    message["From"] = gmail_address
    message["To"] = to_email
    # Plain-text part first, HTML second -- per the multipart/alternative
    # convention, clients use the LAST part they're able to render, so the
    # HTML version (with the real clickable link) is what's actually shown
    # in any client that supports HTML, with plain text as the fallback.
    message.attach(MIMEText(plain_body, "plain", _charset="utf-8"))
    message.attach(MIMEText(html_body, "html", _charset="utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.login(gmail_address, gmail_app_password)
            server.sendmail(gmail_address, [to_email], message.as_string())
    except Exception as error:
        raise EmailSendError(f"Failed to send password reset email: {error}") from error
