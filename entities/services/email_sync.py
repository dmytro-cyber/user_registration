import smtplib
from email.message import EmailMessage

from core.config import settings


def send_email_sync(to_email: str, subject: str, body: str, attachment_path: str | None = None):
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment_path:
        with open(attachment_path, "rb") as f:
            data = f.read()
            msg.add_attachment(
                data,
                maintype="application",
                subtype="json",
                filename=attachment_path.split("/")[-1],
            )

    with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(msg)
