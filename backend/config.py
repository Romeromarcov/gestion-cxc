import os
from dotenv import load_dotenv

load_dotenv()

ODOO_HOST    = os.getenv('ODOO_HOST', '')
ODOO_DB      = os.getenv('ODOO_DB', '')
ODOO_USER    = os.getenv('ODOO_USER', '')
ODOO_API_KEY = os.getenv('ODOO_API_KEY', '')

SECRET_KEY       = os.getenv('SECRET_KEY', 'cambiar_en_produccion_clave_muy_larga_123456789')
ACCESS_TOKEN_EXPIRE_HOURS = 8

GOOGLE_SHEETS_CRED = os.getenv('GOOGLE_SHEETS_CRED', 'credentials.json')
GOOGLE_SHEET_ID    = os.getenv('GOOGLE_SHEET_ID', '')
