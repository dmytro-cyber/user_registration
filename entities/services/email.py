import aiosmtplib
from email.message import EmailMessage
from core.config import settings


async def send_email(to_email: str, subject: str, body: str):
    """
    Function for sending email via SMTP.

    :param to_email: Recipient's email address
    :param subject: Subject line
    :param body: Text of the letter
    """
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_SERVER,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )
    except Exception as e:
        pass
