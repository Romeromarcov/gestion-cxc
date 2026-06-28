import logging
from fastapi import APIRouter
from app.api.v1.endpoints import tags

logger = logging.getLogger(__name__)

api_router = APIRouter()

# Registro del router de etiquetas
api_router.include_router(tags.router, prefix="/tags", tags=["tags"])

# Nota: Asegúrese de incluir otros routers existentes (ej: login, accounts) aquí
# api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])