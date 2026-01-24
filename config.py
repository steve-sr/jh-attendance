import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    CA_PATH = os.getenv("DB_SSL_CA", str(BASE_DIR / "certs" / "ca.pem"))
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {
            "ssl": {"ca": CA_PATH}
        }
    }
