"""
Maestro — agregador de sub-routers.

Sub-módulos:
  maestro_operaciones.py  →  CRUD categorías + operaciones + reportes internos
  maestro_sync.py         →  Sync con Odoo, importación de pagos, saldos

El prefijo '/maestro' se define aquí; los sub-routers no llevan prefijo propio.
"""
from fastapi import APIRouter
from routers.maestro_operaciones import router as _ops_router
from routers.maestro_sync import router as _sync_router

router = APIRouter(prefix='/maestro', tags=['maestro'])
router.include_router(_ops_router)
router.include_router(_sync_router)
