import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# Definición de la versión de la API
API_VERSION = "1.0.0"

class HealthResponse(BaseModel):
    """Esquema de respuesta para el endpoint de salud."""
    status: str = Field(..., example="ok", description="Estado del servicio")
    version: str = Field(..., example="1.0.0", description="Versión actual de la API")

@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """
    Endpoint de verificación de estado (Health Check).
    
    Retorna el estado del servicio y la versión actual.
    Este endpoint es público y no requiere autenticación para permitir
    monitoreo por orquestadores (K8s, AWS ECS, etc.).
    """
    logger.info("Health check endpoint accessed")
    return HealthResponse(status="ok", version=API_VERSION)