import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://adaptiq_user:adaptiq_secure_password_2026@localhost:5432/adaptiq_wms"
)
JWT_SECRET = os.getenv("JWT_SECRET", "adaptiq_jwt_dev_secret_key_2026_xyz")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 12

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
