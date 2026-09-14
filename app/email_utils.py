# app/email_utils.py
import aiosmtplib
from email.message import EmailMessage
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models import SmtpSettings

async def get_smtp_config(session: AsyncSession) -> SmtpSettings:
    result = await session.execute(select(SmtpSettings).limit(1))
    return result.scalar_one_or_none()

async def check_smtp_configured(session: AsyncSession) -> tuple[bool, str]:
    config = await get_smtp_config(session)
    if not config:
        return False, "Nessuna configurazione SMTP trovata in archivio (Fase 6)."
    if not config.smtp_host or not config.smtp_host.strip():
        return False, "Host del server SMTP non configurato."
    if not config.sender_email or not config.sender_email.strip():
        return False, "Email mittente pubblica non configurata."
    if not config.smtp_port:
        return False, "Porta SMTP non specificata."
    return True, ""

async def send_email_background(
    session: AsyncSession, 
    to_email: str, 
    subject: str, 
    html_body: str,
    attachment_bytes: bytes = None,
    attachment_name: str = None
):
    if not to_email:
        return

    is_configured, err_msg = await check_smtp_configured(session)
    if not is_configured:
        print(f"Invio email ignorato: {err_msg}")
        return

    config = await get_smtp_config(session)

    msg = EmailMessage()
    # Per evitare errori 550 di autenticazione su Aruba, usiamo sender_email come mittente
    sender_email = config.sender_email.strip() if config.sender_email else ""
    sender_name = config.sender_name.strip() if config.sender_name else "AdaptiQ WMS"
    
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("Per visualizzare questo messaggio, abilita la lettura in formato HTML.")
    msg.add_alternative(html_body, subtype="html")

    if attachment_bytes and attachment_name:
        msg.add_attachment(
            attachment_bytes, 
            maintype='application', 
            subtype='pdf', 
            filename=attachment_name
        )

    use_tls = config.use_tls and config.smtp_port == 465
    start_tls = config.use_tls and config.smtp_port != 465

    smtp_host = config.smtp_host.strip() if config.smtp_host else ""
    smtp_user = config.smtp_user.strip() if config.smtp_user else sender_email
    smtp_password = config.smtp_password.strip() if config.smtp_password else ""

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=config.smtp_port,
            username=smtp_user,
            password=smtp_password,
            use_tls=use_tls,
            start_tls=start_tls
        )
        print(f"Email con DDT inviata con successo a {to_email}")
    except Exception as e:
        print(f"Errore SMTP critico durante l'invio a {to_email}: {e}")
