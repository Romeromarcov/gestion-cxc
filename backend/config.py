import os
import logging
import warnings
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ODOO_HOST    = os.getenv('ODOO_HOST', '')
ODOO_DB      = os.getenv('ODOO_DB', '')
ODOO_USER    = os.getenv('ODOO_USER', '')
ODOO_API_KEY = os.getenv('ODOO_API_KEY', '')

_DEFAULT_SECRET = 'cambiar_en_produccion_clave_muy_larga_123456789'
SECRET_KEY = os.getenv('SECRET_KEY', _DEFAULT_SECRET)
if SECRET_KEY == _DEFAULT_SECRET or len(SECRET_KEY) < 32:
    warnings.warn(
        'SECRET_KEY no configurada o demasiado corta. '
        'Establece SECRET_KEY en .env con al menos 32 caracteres aleatorios.',
        RuntimeWarning,
        stacklevel=1,
    )

ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv('ACCESS_TOKEN_EXPIRE_HOURS', '8'))

# Orígenes CORS permitidos (separados por coma). En producción limitar a tu dominio.
_raw_origins = os.getenv('ALLOWED_ORIGINS', '*')
ALLOWED_ORIGINS: list[str] = (
    ['*'] if _raw_origins.strip() == '*'
    else [o.strip() for o in _raw_origins.split(',') if o.strip()]
)

GOOGLE_SHEETS_CRED = os.getenv('GOOGLE_SHEETS_CRED', 'credentials.json')
GOOGLE_SHEET_ID    = os.getenv('GOOGLE_SHEET_ID', '')
