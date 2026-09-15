from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AuditLog


def actor_from_payload(payload: dict) -> str:
    """Estrae uno username leggibile dal payload del token operatore (formato 'OP_<username>')."""
    code = str(payload.get("code", "")) if payload else ""
    return code[3:] if code.startswith("OP_") else (code or "operatore")


async def log_action(db: AsyncSession, actor: str, action: str, target: str, details: str = None):
    """Registra una voce di audit. Non esegue il commit: si appoggia alla transazione
    già aperta dal chiamante, così l'evento viene salvato solo se l'operazione principale va a buon fine."""
    entry = AuditLog(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        actor=actor,
        action=action,
        target=target,
        details=details
    )
    db.add(entry)
