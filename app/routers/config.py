# app/routers/config.py
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import select as sa_select
from app.database import get_db
from app.models import SmtpSettings, AuditLog
from app.schemas import SmtpSettingsSchema
from app.auth import get_current_operator_payload
from app.audit import log_action, actor_from_payload
from app.config import STATIC_DIR

router = APIRouter(prefix="/api/config", tags=["Configuration"], dependencies=[Depends(get_current_operator_payload)])

@router.get("/smtp", response_model=SmtpSettingsSchema)
async def get_smtp_config(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SmtpSettings).where(SmtpSettings.id == 1))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configurazione SMTP non trovata.")
    return config

@router.post("/smtp")
async def save_smtp_config(
    settings: SmtpSettingsSchema,
    op: dict = Depends(get_current_operator_payload),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(SmtpSettings).where(SmtpSettings.id == 1))
    config = result.scalar_one_or_none()
    
    if config:
        for key, value in settings.model_dump().items():
            setattr(config, key, value)
    else:
        config = SmtpSettings(id=1, **settings.model_dump())
        db.add(config)

    await log_action(db, actor_from_payload(op), "MODIFICA_CONFIG_SMTP", "smtp_settings", "Parametri server email aggiornati.")
    await db.commit()
    return {"message": "Configurazione SMTP salvata con successo."}


# ==========================================
# PERSONALIZZAZIONE - LOGHI
# ==========================================
ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
LOGO_TARGETS = {
    "adaptiq": "logo.png",
    "logistics": "logo_logistics.png",
}

@router.post("/logo/{target}")
async def upload_logo(
    target: str,
    file: UploadFile = File(...),
    op: dict = Depends(get_current_operator_payload),
    db: AsyncSession = Depends(get_db)
):
    if target not in LOGO_TARGETS:
        raise HTTPException(status_code=400, detail="Target logo non valido. Usa 'adaptiq' o 'logistics'.")
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(status_code=400, detail="Formato non supportato. Carica un'immagine PNG, JPEG o WEBP.")

    dest_path = STATIC_DIR / LOGO_TARGETS[target]
    contents = await file.read()
    if len(contents) > 3 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File troppo grande (max 3MB).")

    with open(dest_path, "wb") as f:
        f.write(contents)

    await log_action(db, actor_from_payload(op), "AGGIORNAMENTO_LOGO", f"static:{LOGO_TARGETS[target]}", f"Logo '{target}' sostituito da interfaccia.")
    await db.commit()
    return {"status": "ok", "message": "Logo aggiornato con successo.", "path": f"/static/{LOGO_TARGETS[target]}"}


# ==========================================
# LOG OPERAZIONI (AUDIT TRAIL)
# ==========================================
@router.get("/audit-log")
async def get_audit_log(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = sa_select(AuditLog)
    if date_from and date_from.strip():
        stmt = stmt.where(AuditLog.timestamp >= f"{date_from.strip()} 00:00:00")
    if date_to and date_to.strip():
        stmt = stmt.where(AuditLog.timestamp <= f"{date_to.strip()} 23:59:59")
    stmt = stmt.order_by(AuditLog.id.desc()).limit(300)

    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        {"id": r.id, "timestamp": r.timestamp, "actor": r.actor, "action": r.action, "target": r.target, "details": r.details}
        for r in rows
    ]
