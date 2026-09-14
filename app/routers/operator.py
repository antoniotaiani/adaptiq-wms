from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from app.database import get_db
from app.models import OperatorUser
from app.schemas import OperatorLoginRequest
from app.auth import hash_pin, verify_pin, create_merchant_token
from app.config import JWT_EXPIRATION_HOURS

router = APIRouter(prefix="/api/operator", tags=["Operator"])

class AdminPasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6)

@router.post("/init")
async def init_admin(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(OperatorUser).where(OperatorUser.username == "admin"))
    if res.scalar_one_or_none():
        return {"message": "Admin già esistente."}
    
    admin = OperatorUser(username="admin", password_hash=hash_pin("Adaptiq_Admin_2026!"))
    db.add(admin)
    await db.commit()
    return {"message": "Utente admin creato."}

@router.post("/login")
async def login(cred: OperatorLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    stmt = select(OperatorUser).where(OperatorUser.username == cred.username)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user or not verify_pin(cred.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenziali errate.")

    token = create_merchant_token(user.id, f"OP_{user.username}")
    
    response.set_cookie(
        key="operator_token",
        value=token,
        httponly=True,
        max_age=JWT_EXPIRATION_HOURS * 3600,
        samesite="lax",
        secure=False
    )
    return {"status": "ok", "username": user.username}

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("operator_token")
    return {"status": "ok"}

@router.post("/admin/change-password")
async def change_admin_password(cred: AdminPasswordChangeRequest, db: AsyncSession = Depends(get_db)):
    stmt = select(OperatorUser).where(OperatorUser.username == "admin")
    res = await db.execute(stmt)
    admin_user = res.scalar_one_or_none()

    if not admin_user or not verify_pin(cred.old_password, admin_user.password_hash):
        raise HTTPException(status_code=400, detail="La vecchia password non è corretta.")

    admin_user.password_hash = hash_pin(cred.new_password)
    await db.commit()
    return {"status": "ok", "message": "Password amministratore aggiornata con successo."}
