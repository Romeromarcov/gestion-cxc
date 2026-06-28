# Codebase Fingerprint — `gestion-cxc`
> Generado: 2026-06-28 17:17 UTC | Auto-actualizado por A10 tras cada feature

## Stack detectado
- **Backend:** FastAPI + PostgreSQL

## Estructura del proyecto (SEGUIR ESTA, no una plantilla genérica)
_Replica esta estructura — NO inventes un layout `app/api/v1/...` si el repo no lo usa._
- **Entrypoint:** `backend/main.py`
- **Routers/endpoints en:** `backend/routers/` (replica AHÍ los endpoints nuevos)
- **Modelos en:** `app/models/`
- **Estilo de import:** imports PLANOS dentro del dir del entrypoint (p. ej. `from routers import ...`) — NO uses un paquete `app.` inexistente

## Convenciones detectadas (del código real)
- Logger: `logger = logging.getLogger(__name__)` al inicio de cada módulo

## Variables de entorno referenciadas
`ODOO_HOST`, `ODOO_DB`, `ODOO_USER`, `ODOO_API_KEY`, `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_HOURS`, `ALLOWED_ORIGINS`, `DATABASE_URL`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
