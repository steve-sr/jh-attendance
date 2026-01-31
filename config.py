import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev")

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- Cookies de sesión (seguridad) ----
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # En Render (https) = True. En local (http) = False.
    SESSION_COOKIE_SECURE = os.getenv("FLASK_ENV") == "production" or os.getenv("RENDER") == "true"

    # ---- SSL para MySQL (solo si hay CA) ----
    CA_PATH = os.getenv("DB_SSL_CA", str(BASE_DIR / "certs" / "ca.pem"))
    if os.path.exists(CA_PATH):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "connect_args": {"ssl": {"ca": CA_PATH}}
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {}
