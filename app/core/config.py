from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Existing settings
    DATABASE_URL: str
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24
    ALLOWED_ORIGINS: str = "*"
    
    # Odoo Settings
    ODOO_HOST: Optional[str] = None
    ODOO_DB: Optional[str] = None
    ODOO_USER: Optional[str] = None
    ODOO_API_KEY: Optional[str] = None
    ODOO_PORT: int = 8069

    # App Version
    APP_VERSION: str = "1.0.0"

    class Config:
        env_file = ".env"

settings = Settings()