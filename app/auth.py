import secrets
import hashlib
from datetime import datetime, timedelta
from fastapi import Request, HTTPException, status
from jose import JWTError, jwt
from app.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS

def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 100_000)
    return f"{salt}:{key.hex()}"

def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        salt, key_hex = stored_hash.split(":")
        calc_key = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 100_000)
        return secrets.compare_digest(calc_key.hex(), key_hex)
    except Exception:
        return False

def create_merchant_token(merchant_id: int, account_code: str) -> str:
    exp = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    payload = {"sub": str(merchant_id), "code": account_code, "exp": exp}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_merchant_payload(request: Request) -> dict:
    token = request.cookies.get("adaptiq_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or not authenticated.")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization token.")
