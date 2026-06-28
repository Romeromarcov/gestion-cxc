import logging
import socket
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import get_db
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

def check_db_connection(db: Session) -> str:
    """
    Verifica la conectividad con la base de datos PostgreSQL ejecutando una query simple.
    """
    try:
        # Usamos text() para ejecutar SQL raw (SELECT 1)
        db.execute(text("SELECT 1"))
        return "connected"
    except Exception as e:
        logger.error(f"Error de conexión a la base de datos: {e}")
        return "disconnected"

def check_odoo_connection() -> str:
    """
    Verifica la conectividad de red con el host de Odoo.
    Nota: Esto solo verifica que el host y puerto son alcanzables, no la autenticación API.
    """
    if not settings.ODOO_HOST:
        return "not_configured"
    
    try:
        # Timeout corto para no bloquear el health check
        sock = socket.create_connection((settings.ODOO_HOST, settings.ODOO_PORT), timeout=2)
        sock.close()
        return "connected"
    except socket.timeout:
        logger.warning(f"Timeout intentando conectar a Odoo en {settings.ODOO_HOST}:{settings.ODOO_PORT}")
        return "timeout"
    except Exception as e:
        logger.error(f"Error de conexión con Odoo: {e}")
        return "disconnected"

@router.get("/health", status_code=status.HTTP_200_OK)
def health_check(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Endpoint de Health Check.
    
    Devuelve el estado del servicio, versión y conectividad con dependencias críticas.
    Si la base de datos no responde, retorna un 503 Service Unavailable.
    """
    db_status = check_db_connection(db)
    odoo_status = check_odoo_connection()
    
    health_data = {
        "status": "ok" if db_status == "connected" else "error",
        "version": settings.APP_VERSION,
        "database": db_status,
        "odoo": odoo_status
    }

    # Determinar el código de estado HTTP
    # Si falla la DB, es un error crítico (503). Si falla Odoo, podemos seguir vivos (200) pero reportar el estado.
    if db_status != "connected":
        logger.critical("Health Check fallo: Base de datos desconectada.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "version": settings.APP_VERSION,
                "database": db_status,
                "odoo": odoo_status,
                "message": "Service Unavailable: Database connection failed"
            }
        )
    
    logger.info("Health Check exitoso.")
    return health_data