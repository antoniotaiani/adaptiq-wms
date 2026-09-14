# app/routers/config.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models import SmtpSettings
from app.schemas import SmtpSettingsSchema

router = APIRouter(prefix="/api/config", tags=["Configuration"])

@router.get("/smtp", response_model=SmtpSettingsSchema)
async def get_smtp_config(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SmtpSettings).where(SmtpSettings.id == 1))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Configurazione SMTP non trovata.")
    return config

@router.post("/smtp")
async def save_smtp_config(settings: SmtpSettingsSchema, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SmtpSettings).where(SmtpSettings.id == 1))
    config = result.scalar_one_or_none()
    
    if config:
        for key, value in settings.model_dump().items():
            setattr(config, key, value)
    else:
        config = SmtpSettings(id=1, **settings.model_dump())
        db.add(config)
        
    await db.commit()
    return {"message": "Configurazione SMTP salvata con successo."}
