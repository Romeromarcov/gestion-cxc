import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import init_db
from services.tasas_cambio import obtener_tasa_bcv
from routers import (auth, ventas, descuentos, promociones,
                     pagos, ventas_internas, inventario,
                     precios, reportes, config_app, maestro,
                     cobranza, acuerdos_pago)

scheduler = AsyncIOScheduler()


async def sync_pagos_odoo():
    """Polling cada 15 min: actualiza estado de pagos enviados a Odoo."""
    try:
        from database import get_con
        from models.schemas import rows_to_list
        from routers.ventas import _odoo_instance
        if not _odoo_instance:
            return
        con = get_con()
        pagos_pendientes = rows_to_list(con.execute(
            "SELECT * FROM pagos WHERE estado='enviado_odoo' AND odoo_payment_id IS NOT NULL"
        ).fetchall())
        con.close()

        for pago in pagos_pendientes:
            try:
                estado = _odoo_instance.get_pago(pago['odoo_payment_id'])
                if estado and estado[0].get('state') == 'posted':
                    con = get_con()
                    con.execute(
                        "UPDATE pagos SET estado='confirmado_odoo' WHERE id=?",
                        (pago['id'],)
                    )
                    con.commit()
                    con.close()
            except Exception:
                pass
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(obtener_tasa_bcv, 'cron', hour=8, minute=0)
    scheduler.add_job(sync_pagos_odoo, 'interval', minutes=15)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(
    title='Gestión CxC — Lubrikca',
    description='Sistema de gestión de cuentas por cobrar con integración Odoo 18',
    version='1.0.0',
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Registrar todos los routers
for r in [auth, ventas, descuentos, promociones, pagos,
          ventas_internas, inventario, precios, reportes, config_app, maestro,
          cobranza, acuerdos_pago]:
    app.include_router(r.router)

# Frontend estático
frontend_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend')
if os.path.isdir(frontend_dir):
    app.mount('/static', StaticFiles(directory=frontend_dir), name='static')

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(os.path.join(frontend_dir, 'index.html'))

    @app.get('/mobile', include_in_schema=False)
    @app.get('/mobile.html', include_in_schema=False)
    def mobile():
        return FileResponse(os.path.join(frontend_dir, 'mobile.html'))
