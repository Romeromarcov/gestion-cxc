from fastapi import APIRouter
from app.api.v1.endpoints import health

api_router = APIRouter()

# Incluir el router de salud
api_router.include_router(health.router, tags=["Health"])

# Nota: Aquí se deberían incluir otros routers existentes (tags, accounts, etc.)
# api_router.include_router(tags.router, prefix="/tags", tags=["Tags"])