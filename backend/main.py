import sys
import os
import time
import logging
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import init_db, close_pool
from services.tasas_cambio import obtener_tasa_bcv
from services.binance_p2p import actualizar_tasa_binance_p2p
from config import ALLOWED_ORIGINS
from routers import (auth, ventas, descuentos, promociones,
                     pagos, inventario,
                     precios, reportes, config_app, maestro,
                     cobranza, acuerdos_pago, replicas, creditos,
                     cambios_divisa, pagos_fiscales, requisiciones,
                     zelle_terceros, gastos, nomina, compras_odoo,
                     ventas_internas, compras_internas,
                     inventario_interno, cuentas_por_pagar,
                     fraccionamiento)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
logger = logging.getLogger(__name__)


# ── Middleware: logging de requests ──────────────────────────────────────────
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Registra método, path, status y duración de cada request."""
    _skip = {'/health', '/static'}

    async def dispatch(self, request: Request, call_next):
        # No loguear healthchecks ni assets estáticos
        if any(request.url.path.startswith(p) for p in self._skip):
            return await call_next(request)
        t0 = time.monotonic()
        response = await call_next(request)
        ms = round((time.monotonic() - t0) * 1000)
        logger.info('%s %s → %s  (%dms)',
                    request.method, request.url.path,
                    response.status_code, ms)
        return response


# ── Middleware: security headers ──────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Agrega headers de seguridad HTTP en todas las respuestas."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options']    = 'nosniff'
        response.headers['X-Frame-Options']           = 'DENY'
        response.headers['X-XSS-Protection']         = '1; mode=block'
        response.headers['Referrer-Policy']           = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy']        = 'geolocation=(), microphone=()'
        # HSTS: sólo aplica cuando hay TLS (Railway lo provee)
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

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
            except Exception as e:
                logger.warning('sync_pagos_odoo: error en pago %s — %s', pago.get('id'), e)
    except Exception as e:
        logger.error('sync_pagos_odoo: fallo general — %s', e)


async def auto_sync_pagos_clientes():
    """
    Auto-sync cada 15 min: importa pagos de clientes de Odoo al Maestro
    que no vengan de la app. Equivalente a POST /maestro/importar-pagos-cliente.
    """
    try:
        from database import get_con
        from models.schemas import rows_to_list
        from routers.ventas import _odoo_instance
        from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy
        from datetime import date, timedelta
        if not _odoo_instance:
            return

        hoy = date.today()
        fecha_desde = (hoy - timedelta(days=3)).isoformat()  # últimos 3 días
        fecha_hasta = hoy.isoformat()

        pagos_odoo = _odoo_instance.get_pagos_odoo_clientes(limite=200)
        if not pagos_odoo:
            return

        pagos_odoo = [p for p in pagos_odoo
                      if fecha_desde <= (p.get('date') or '') <= fecha_hasta]

        con = get_con()
        ya_ids = {r[0] for r in con.execute(
            "SELECT odoo_payment_id FROM maestro_operaciones WHERE odoo_payment_id IS NOT NULL"
        ).fetchall()}
        app_ids = {r[0] for r in con.execute(
            "SELECT odoo_payment_id FROM pagos WHERE odoo_payment_id IS NOT NULL"
        ).fetchall()}

        tasa_bcv = tasa_bcv_hoy()
        tasa_real = tasa_custom_hoy()

        for p in pagos_odoo:
            pid = p.get('id')
            if pid in ya_ids or pid in app_ids:
                continue
            monto = float(p.get('amount', 0))
            cur = p.get('currency_id')
            moneda = cur[1] if isinstance(cur, (list, tuple)) else (cur or 'USD')
            if moneda not in ('USD', 'VES', 'EUR', 'USDT'):
                moneda = 'USD'
            monto_usd = monto if moneda in ('USD', 'USDT') else (
                round(monto / tasa_bcv, 4) if tasa_bcv else None)
            partner = p.get('partner_id')
            partner_nombre = partner[1] if isinstance(partner, (list, tuple)) else ''
            journal = p.get('journal_id')
            journal_id_val = journal[0] if isinstance(journal, (list, tuple)) else None
            con.execute("""
                INSERT INTO maestro_operaciones
                    (fecha, nro_documento, monto, moneda, tipo, categoria,
                     descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
                     origen, odoo_ref, odoo_payment_id, odoo_journal_id, odoo_partner_id,
                     odoo_conciliado, estado)
                VALUES (?,?,?,?,'ingreso','Cobranza',?,?,?,?,?,'odoo_auto_cliente',?,?,?,?,?,?,'confirmado')
            """, (
                p.get('date', hoy.isoformat()), p.get('name'),
                monto, moneda,
                f"Cobro cliente: {partner_nombre}" if partner_nombre else 'Cobro Odoo',
                tasa_bcv, monto_usd, tasa_real, monto_usd,
                p.get('name'), pid, journal_id_val,
                (partner[0] if isinstance(partner, (list, tuple)) else None),
                1 if p.get('conciliado') else 0,
            ))
        con.commit()
        con.close()
    except Exception as e:
        logger.error('auto_sync_pagos_clientes: fallo general — %s', e)


async def auto_sync_conciliacion():
    """
    Auto-sync cada 30 min: actualiza estado de conciliación en maestro_operaciones
    para entradas que tienen odoo_payment_id pero odoo_conciliado=0.
    """
    try:
        from database import get_con
        from models.schemas import rows_to_list
        from routers.ventas import _odoo_instance
        if not _odoo_instance:
            return
        con = get_con()
        rows = rows_to_list(con.execute("""
            SELECT id, tipo, odoo_payment_id FROM maestro_operaciones
            WHERE odoo_payment_id IS NOT NULL AND odoo_conciliado=0
            LIMIT 100
        """).fetchall())
        if not rows:
            con.close()
            return
        ids_in  = {r['odoo_payment_id'] for r in rows if r['tipo'] == 'ingreso'}
        ids_out = {r['odoo_payment_id'] for r in rows if r['tipo'] == 'egreso'}
        try:
            estado_map = _odoo_instance.verificar_conciliacion_lote(ids_in, ids_out)
        except Exception:
            con.close()
            return
        for r in rows:
            info = estado_map.get(r['odoo_payment_id'])
            if info and info.get('conciliado'):
                con.execute(
                    "UPDATE maestro_operaciones SET odoo_conciliado=1 WHERE id=?", (r['id'],)
                )
        con.commit()
        con.close()
    except Exception as e:
        logger.error('auto_sync_conciliacion: fallo general — %s', e)


async def _probar_odoo():
    """Intenta conectar a Odoo en startup y cachea la instancia.

    Si falla, loguea el error con detalle para que el admin vea exactamente
    qué variable está mal configurada en Railway sin tener que adivinar.
    """
    from config import ODOO_HOST, ODOO_DB, ODOO_USER, ODOO_API_KEY
    if not all([ODOO_HOST, ODOO_DB, ODOO_USER, ODOO_API_KEY]):
        missing = [k for k, v in {
            'ODOO_HOST': ODOO_HOST, 'ODOO_DB': ODOO_DB,
            'ODOO_USER': ODOO_USER, 'ODOO_API_KEY': ODOO_API_KEY,
        }.items() if not v]
        logger.warning('_probar_odoo: variables faltantes → %s — integración Odoo desactivada',
                        ', '.join(missing))
        return
    try:
        from routers.ventas import get_odoo
        odoo = get_odoo()
        logger.info('_probar_odoo: Odoo OK — host=%s db=%s uid=%s', ODOO_HOST, ODOO_DB, odoo.uid)
    except Exception as exc:
        logger.error(
            '_probar_odoo: NO se pudo conectar a Odoo — %s\n'
            '  host=%r  db=%r  user=%r',
            exc, ODOO_HOST, ODOO_DB, ODOO_USER,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    init_db()   # crea el pool y corre migraciones en el startup
    scheduler.add_job(obtener_tasa_bcv, 'cron', hour='8,14', minute=0)
    scheduler.add_job(actualizar_tasa_binance_p2p, 'interval', minutes=30)
    scheduler.add_job(sync_pagos_odoo, 'interval', minutes=15)
    scheduler.add_job(auto_sync_pagos_clientes, 'interval', minutes=15)
    scheduler.add_job(auto_sync_conciliacion, 'interval', minutes=30)
    scheduler.start()
    # Run BCV fetch on startup so we always have today's rate
    asyncio.create_task(obtener_tasa_bcv())
    asyncio.create_task(actualizar_tasa_binance_p2p())
    asyncio.create_task(_probar_odoo())   # ← diagnóstico Odoo en startup
    yield
    # ── Shutdown limpio ───────────────────────────────────────────────────────
    scheduler.shutdown()
    close_pool()   # devuelve todas las conexiones al pool y lo cierra


app = FastAPI(
    title='Gestión CxC — Lubrikca',
    description='Sistema de gestión de cuentas por cobrar con integración Odoo 18',
    version='1.0.0',
    lifespan=lifespan,
)


@app.get('/health', include_in_schema=False)
def health():
    """Healthcheck para Docker / Railway. Verifica conexión a la BD."""
    try:
        from database import get_con
        con = get_con()
        con.execute('SELECT 1')
        con.close()
        return {'status': 'ok', 'db': 'connected'}
    except Exception as e:
        from fastapi import Response
        return Response(
            content=f'{{"status":"error","detail":"{e}"}}',
            status_code=503,
            media_type='application/json',
        )

# Orden importante: el último add_middleware se ejecuta primero.
# SecurityHeaders → RequestLogging → CORS → router
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Registrar todos los routers
for r in [auth, ventas, descuentos, promociones, pagos,
          inventario, precios, reportes, config_app, maestro,
          cobranza, acuerdos_pago, replicas, creditos,
          cambios_divisa, pagos_fiscales, requisiciones,
          zelle_terceros, gastos, nomina, compras_odoo,
          ventas_internas, compras_internas,
          inventario_interno, cuentas_por_pagar,
          fraccionamiento]:
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
