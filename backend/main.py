import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# Import plano según convención del proyecto (desde el dir del entrypoint)
from routers import health 

logger = logging.getLogger(__name__)

# Configuración básica de la aplicación
app = FastAPI(
    title="Gestión CxC API",
    description="API para la gestión de cuentas por cobrar",
    version="1.0.0"
)

# Configuración de CORS
# Nota: En producción, ALLOWED_ORIGINS debería cargarse desde variables de entorno
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclusión de Routers
app.include_router(health.router)

# Eventos de startup y shutdown
@app.on_event("startup")
async def startup_event():
    logger.info("Starting application...")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application...")