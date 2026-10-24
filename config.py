"""Environment-backed application settings."""

import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "")
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
    ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    DATABASE_BACKEND = os.getenv("DATABASE_BACKEND", "").strip().lower() or "auto"
    SQLITE_DATABASE_PATH = os.getenv(
        "SQLITE_DATABASE_PATH", os.path.join("instance", "jee_exam.sqlite3")
    )
    SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv(
        "SESSION_COOKIE_SECURE", "true" if APP_ENV == "production" else "false"
    ).lower() == "true"
