import os
import re
import logging
import warnings
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ODOO_HOST    = os.getenv('ODOO_HOST', '')
ODOO_DB      = os.getenv('ODOO_DB', '')
ODOO_USER    = os.getenv('ODOO_USER', '')
ODOO_API_KEY = os.getenv('ODOO_API_KEY', '')

# ── Validación de SECRET_KEY ──────────────────────────────────────────────────
_DEFAULT_SECRET = 'cambiar_en_produccion_clave_muy_larga_123456789'

# Palabras que delatan un secreto de placeholder / desarrollo
_WEAK_PATTERNS = re.compile(
    r'cambiar|aqui|placeholder|ejemplo|test|dev|secret|password|1234|'
    r'clave|aqui|sample|demo|default|replace|your[-_]?secret',
    re.IGNORECASE,
)

SECRET_KEY = os.getenv('SECRET_KEY', _DEFAULT_SECRET)

def _validate_secret_key(key: str) -> None:
    """Emite advertencia o lanza error si el SECRET_KEY es inseguro."""
    weak = (
        key == _DEFAULT_SECRET
        or len(key) < 32
        or bool(_WEAK_PATTERNS.search(key))
    )
    if not weak:
        return

    msg = (
        'SECRET_KEY insegura detectada. '
        'Genera un secreto real con: '
        'python -c "import secrets; print(secrets.token_hex(32))" '
        'y configúralo en las variables de entorno del servidor.'
    )

    # En producción (Railway inyecta RAILWAY_ENVIRONMENT o PORT), error fatal.
    # En desarrollo local se permite con warning para no bloquear el arranque.
    is_prod = bool(os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_PROJECT_ID'))
    if is_prod:
        raise RuntimeError(f'[SEGURIDAD] {msg}')
    else:
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

_validate_secret_key(SECRET_KEY)

ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv('ACCESS_TOKEN_EXPIRE_HOURS', '8'))

# Orígenes CORS permitidos (separados por coma). En producción limitar a tu dominio.
_raw_origins = os.getenv('ALLOWED_ORIGINS', '*')
ALLOWED_ORIGINS: list[str] = (
    ['*'] if _raw_origins.strip() == '*'
    else [o.strip() for o in _raw_origins.split(',') if o.strip()]
)

GOOGLE_SHEETS_CRED = os.getenv('GOOGLE_SHEETS_CRED', 'credentials.json')
GOOGLE_SHEET_ID    = os.getenv('GOOGLE_SHEET_ID', '')

# ── Base de datos ─────────────────────────────────────────────────────────────
# En desarrollo local apunta a PostgreSQL local (o el servicio 'db' de Docker).
# En docker-compose el valor se sobreescribe vía la sección `environment:`.
_DEFAULT_DB = 'postgresql://gestion_user:gestion_pass@localhost:5432/gestion_cxc'
DATABASE_URL = os.getenv('DATABASE_URL', _DEFAULT_DB)
if not DATABASE_URL.startswith('postgresql'):
    warnings.warn(
        'DATABASE_URL no parece una URL de PostgreSQL. '
        'Formato esperado: postgresql://usuario:clave@host:5432/base',
        RuntimeWarning,
        stacklevel=1,
    )
