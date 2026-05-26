"""
Maestro — sincronización con Odoo e importaciones de pagos.

Rutas (sin prefijo; el prefijo '/maestro' lo asigna el agregador maestro.py):
  POST  /{op_id}/enviar-odoo
  POST  /importar-pagos-proveedor      (versión original — período libre)
  POST  /sync-conciliacion
  GET   /cuentas-odoo
  GET   /journals-odoo
  GET   /buscar-proveedores
  GET   /saldos-banco
  GET   /saldos-vs-odoo
  POST  /importar-pagos-cliente
  POST  /importar-pagos-proveedor-auto (versión extendida con exclusión de origen app)
  POST  /importar-comisiones
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import date
from typing import Optional
from database import get_con
from routers.auth import get_current_user, require_roles
from models.schemas import row_to_dict, rows_to_list
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter()


def _get_odoo():
    from routers.ventas import get_odoo
    return get_odoo()


# ── ENVIAR EGRESO A ODOO ──────────────────────────────────────────────────────

@router.post('/{op_id}/enviar-odoo')
def enviar_egreso_odoo(op_id: int, body: dict,
                       user=Depends(require_roles('admin', 'gerente'))):
    """
    Crea el pago de proveedor en Odoo para un egreso manual del maestro.
    body: {journal_id: int, partner_id: int (opcional)}
    """
    journal_id = body.get('journal_id')
    if not journal_id:
        raise HTTPException(status_code=400, detail='journal_id es requerido')

    con = get_con()
    op = row_to_dict(con.execute(
        "SELECT * FROM maestro_operaciones WHERE id=?", (op_id,)
    ).fetchone())
    if not op:
        con.close()
        raise HTTPException(status_code=404, detail='Operación no encontrada')
    if op.get('tipo') != 'egreso':
        con.close()
        raise HTTPException(status_code=400, detail='Solo aplica para egresos')
    if op.get('odoo_payment_id'):
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'Ya fue enviado a Odoo (ID {op["odoo_payment_id"]})')

    odoo = _get_odoo()
    try:
        odoo_payment_id = odoo.crear_pago_proveedor(
            monto=op['monto'],
            fecha=op['fecha'],
            journal_id=int(journal_id),
            ref=op.get('nro_documento') or op.get('descripcion') or '',
            partner_id=body.get('partner_id'),
        )
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    con.execute("""
        UPDATE maestro_operaciones
        SET odoo_payment_id=?, odoo_journal_id=?, odoo_partner_id=?, estado='confirmado'
        WHERE id=?
    """, (odoo_payment_id, int(journal_id), body.get('partner_id'), op_id))
    con.commit()
    con.close()
    return {'mensaje': 'Egreso enviado a Odoo', 'odoo_payment_id': odoo_payment_id}


# ── IMPORTAR PAGOS DE PROVEEDOR DESDE ODOO ───────────────────────────────────

@router.post('/importar-pagos-proveedor')
def importar_pagos_proveedor(body: dict,
                             user=Depends(require_roles('admin', 'gerente'))):
    """
    Importa pagos de proveedores (outbound) de Odoo que NO estén ya en el maestro.
    Excluye los generados desde la app (zelle_terceros, nomina_terceros).

    body: { "fecha_desde": "YYYY-MM-DD", "fecha_hasta": "YYYY-MM-DD" }
    """
    hoy = date.today().isoformat()
    fecha_desde = body.get('fecha_desde', hoy[:8] + '01')
    fecha_hasta = body.get('fecha_hasta', hoy)

    odoo = _get_odoo()
    try:
        pagos_odoo = odoo.get_pagos_proveedor(fecha_desde, fecha_hasta, limite=500)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    if not pagos_odoo:
        return {'importados': 0, 'omitidos': 0, 'mensaje': 'Sin pagos de proveedor en ese período'}

    con = get_con()
    ya_ids = {r[0] for r in con.execute(
        "SELECT odoo_payment_id FROM maestro_operaciones WHERE odoo_payment_id IS NOT NULL"
    ).fetchall()}
    # Excluir pagos generados desde la app
    app_odoo_ids = {r[0] for r in con.execute(
        "SELECT odoo_payment_id FROM zelle_terceros WHERE odoo_payment_id IS NOT NULL"
    ).fetchall()}
    app_odoo_ids |= {r[0] for r in con.execute(
        "SELECT odoo_payment_id FROM nomina_terceros WHERE odoo_payment_id IS NOT NULL"
    ).fetchall()}

    tasa_bcv = tasa_bcv_hoy()
    tasa_real = tasa_custom_hoy()
    importados = omitidos = app_origin = 0

    for p in pagos_odoo:
        pid = p.get('id')
        if pid in ya_ids:
            omitidos += 1
            continue
        if pid in app_odoo_ids:
            app_origin += 1
            continue

        monto = float(p.get('amount', 0))
        cur = p.get('currency_id')
        moneda = cur[1] if isinstance(cur, (list, tuple)) else (cur or 'USD')
        if moneda not in ('USD', 'VES', 'EUR', 'USDT'):
            moneda = 'USD'
        monto_usd = monto if moneda in ('USD', 'USDT') else (
            round(monto / tasa_bcv, 4) if tasa_bcv else None)
        partner = p.get('partner_id')
        partner_nom = partner[1] if isinstance(partner, (list, tuple)) else ''
        journal = p.get('journal_id')
        journal_id_val = journal[0] if isinstance(journal, (list, tuple)) else None
        journal_nom = journal[1] if isinstance(journal, (list, tuple)) else ''

        con.execute("""
            INSERT INTO maestro_operaciones
                (fecha, nro_documento, monto, moneda, tipo, categoria,
                 descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
                 origen, odoo_ref, odoo_payment_id, odoo_journal_id, odoo_partner_id,
                 journal_nombre, odoo_conciliado, estado)
            VALUES (?,?,?,?,'egreso','Pago Proveedor',?,?,?,?,?,'odoo_auto_proveedor',?,?,?,?,?,?,?,'confirmado')
        """, (
            p.get('date', hoy), p.get('name'),
            monto, moneda,
            f"Pago proveedor: {partner_nom}" if partner_nom else 'Pago Odoo',
            tasa_bcv, monto_usd, tasa_real, monto_usd,
            p.get('name'), pid, journal_id_val,
            int(partner[0]) if isinstance(partner, (list, tuple)) else None,
            journal_nom,
            1 if p.get('conciliado') else 0,
        ))
        importados += 1

    con.commit()
    con.close()
    return {
        'importados': importados,
        'omitidos': omitidos,
        'app_origin': app_origin,
        'mensaje': (f'{importados} pagos de proveedor importados '
                    f'({omitidos} ya existían, {app_origin} generados desde la app)'),
    }


# ── SINCRONIZAR CONCILIACIÓN ──────────────────────────────────────────────────

@router.post('/sync-conciliacion')
def sync_conciliacion(user=Depends(require_roles('admin', 'gerente'))):
    """Actualiza odoo_conciliado en entradas del maestro que tienen odoo_payment_id."""
    con = get_con()
    rows = rows_to_list(con.execute("""
        SELECT id, tipo, odoo_payment_id
        FROM maestro_operaciones
        WHERE odoo_payment_id IS NOT NULL AND odoo_conciliado = 0
    """).fetchall())

    if not rows:
        con.close()
        return {'actualizados': 0, 'mensaje': 'Sin entradas pendientes de verificar'}

    ids_in  = {r['odoo_payment_id'] for r in rows if r['tipo'] == 'ingreso'}
    ids_out = {r['odoo_payment_id'] for r in rows if r['tipo'] == 'egreso'}

    odoo = _get_odoo()
    try:
        estado_map = odoo.verificar_conciliacion_lote(ids_in, ids_out)
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    actualizados = 0
    for r in rows:
        info = estado_map.get(r['odoo_payment_id'])
        if info and info.get('conciliado'):
            con.execute(
                "UPDATE maestro_operaciones SET odoo_conciliado=1 WHERE id=?",
                (r['id'],)
            )
            actualizados += 1

    con.commit()
    con.close()
    return {'actualizados': actualizados,
            'mensaje': f'{actualizados} entradas marcadas como conciliadas'}


# ── CUENTAS CONTABLES / JOURNALS / PROVEEDORES ────────────────────────────────

@router.get('/cuentas-odoo')
def listar_cuentas_odoo(user=Depends(require_roles('admin', 'gerente'))):
    """Lista de cuentas de gasto de Odoo para mapeo de categorías."""
    try:
        return _get_odoo().get_cuentas_gasto() or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')


@router.get('/journals-odoo')
def listar_journals_odoo(user=Depends(get_current_user)):
    """Diarios de banco/caja de Odoo para registrar pagos."""
    try:
        return _get_odoo().get_journals() or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')


@router.get('/buscar-proveedores')
def buscar_proveedores(q: str = '', user=Depends(get_current_user)):
    if len(q) < 2:
        return []
    try:
        return _get_odoo().buscar_proveedores(q) or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')


# ── SALDOS ────────────────────────────────────────────────────────────────────

@router.get('/saldos-banco')
def saldos_por_banco(desde: str = None, hasta: str = None,
                     user=Depends(require_roles('gerente', 'admin'))):
    """Saldo neto por diario/banco (ingresos - egresos) en USD."""
    con = get_con()
    q = """
        SELECT
            COALESCE(journal_nombre, 'Sin diario') as banco,
            SUM(CASE WHEN tipo='ingreso' THEN COALESCE(monto_real_usd, monto_usd_bcv, monto) ELSE 0 END) as total_ingresos,
            SUM(CASE WHEN tipo='egreso'  THEN COALESCE(monto_real_usd, monto_usd_bcv, monto) ELSE 0 END) as total_egresos,
            COUNT(*) as operaciones
        FROM maestro_operaciones
        WHERE 1=1
    """
    params = []
    if desde:
        q += " AND fecha>=?"; params.append(desde)
    if hasta:
        q += " AND fecha<=?"; params.append(hasta)
    q += " GROUP BY COALESCE(journal_nombre, 'Sin diario') ORDER BY banco"
    rows = rows_to_list(con.execute(q, params).fetchall())
    con.close()
    for r in rows:
        r['saldo'] = round((r['total_ingresos'] or 0) - (r['total_egresos'] or 0), 4)
    return rows


@router.get('/saldos-vs-odoo')
def saldos_vs_odoo(user=Depends(require_roles('gerente', 'admin'))):
    """Compara saldo de cada journal en la app vs. saldo real en Odoo."""
    con = get_con()
    rows = rows_to_list(con.execute("""
        SELECT odoo_journal_id,
               COALESCE(journal_nombre, 'Sin diario') as nombre,
               SUM(CASE WHEN tipo='ingreso' THEN monto ELSE 0 END) as total_ingresos,
               SUM(CASE WHEN tipo='egreso'  THEN monto ELSE 0 END) as total_egresos
        FROM maestro_operaciones
        WHERE odoo_journal_id IS NOT NULL AND estado != 'anulado'
        GROUP BY odoo_journal_id, journal_nombre
    """).fetchall())
    con.close()

    odoo = _get_odoo()
    resultado = []
    for r in rows:
        saldo_app = round((r['total_ingresos'] or 0) - (r['total_egresos'] or 0), 2)
        saldo_odoo = diferencia = None
        estado = 'sin_datos'
        if r.get('odoo_journal_id'):
            try:
                saldo_odoo = round(odoo.get_saldo_journal(int(r['odoo_journal_id'])), 2)
                diferencia = round(saldo_app - saldo_odoo, 2)
                if abs(diferencia) < 0.05:
                    estado = 'ok'
                elif abs(diferencia) < 50:
                    estado = 'advertencia'
                else:
                    estado = 'discrepancia'
            except Exception:
                estado = 'error_odoo'
        resultado.append({
            'journal_id': r['odoo_journal_id'],
            'nombre': r['nombre'],
            'saldo_app': saldo_app,
            'saldo_odoo': saldo_odoo,
            'diferencia': diferencia,
            'estado': estado,
        })
    return resultado


# ── IMPORTAR PAGOS CLIENTES DESDE ODOO ───────────────────────────────────────

@router.post('/importar-pagos-cliente')
def importar_pagos_cliente(body: dict,
                           user=Depends(require_roles('admin', 'gerente'))):
    """
    Importa pagos de clientes (inbound) de Odoo que NO estén ya en el maestro.
    Excluye pagos generados desde la app (tienen odoo_payment_id propio).
    body: { "fecha_desde": "YYYY-MM-DD", "fecha_hasta": "YYYY-MM-DD" }
    """
    hoy = date.today().isoformat()
    fecha_desde = body.get('fecha_desde', hoy[:8] + '01')
    fecha_hasta = body.get('fecha_hasta', hoy)

    odoo = _get_odoo()
    try:
        pagos_odoo = odoo.get_pagos_odoo_clientes(limite=500)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    if not pagos_odoo:
        return {'importados': 0, 'omitidos': 0, 'mensaje': 'Sin pagos en ese período'}

    pagos_odoo = [p for p in pagos_odoo
                  if fecha_desde <= (p.get('date') or '') <= fecha_hasta]

    con = get_con()
    ya_ids = {r[0] for r in con.execute(
        "SELECT odoo_payment_id FROM maestro_operaciones WHERE odoo_payment_id IS NOT NULL"
    ).fetchall()}
    app_odoo_ids = {r[0] for r in con.execute(
        "SELECT odoo_payment_id FROM pagos WHERE odoo_payment_id IS NOT NULL"
    ).fetchall()}

    tasa_bcv = tasa_bcv_hoy()
    tasa_real = tasa_custom_hoy()
    importados = omitidos = app_origin = 0

    for p in pagos_odoo:
        pid = p.get('id')
        if pid in ya_ids:
            omitidos += 1
            continue
        if pid in app_odoo_ids:
            app_origin += 1
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
                 odoo_conciliado, estado, creado_por)
            VALUES (?,?,?,?,'ingreso','Cobranza',?,?,?,?,?,'odoo_auto_cliente',?,?,?,?,?,?,'confirmado',?)
        """, (
            p.get('date', hoy), p.get('name'),
            monto, moneda,
            f"Cobro cliente: {partner_nombre}" if partner_nombre else 'Cobro Odoo',
            tasa_bcv, monto_usd, tasa_real, monto_usd,
            p.get('name'), pid, journal_id_val,
            (partner[0] if isinstance(partner, (list, tuple)) else None),
            1 if p.get('conciliado') else 0,
            user['id'],
        ))
        importados += 1

    con.commit()
    con.close()
    return {
        'importados': importados, 'omitidos': omitidos,
        'origen_app': app_origin,
        'mensaje': (f'{importados} pagos importados de Odoo. '
                    f'{omitidos} ya existían. '
                    f'{app_origin} generados desde la app (excluidos).')
    }


# ── IMPORTAR COMISIONES BANCARIAS DESDE ODOO ─────────────────────────────────

@router.post('/importar-comisiones')
def importar_comisiones_bancarias(body: dict,
                                  user=Depends(require_roles('admin', 'gerente'))):
    """
    Importa comisiones bancarias de Odoo que NO estén ya registradas en el maestro.
    body: { "fecha_desde": "YYYY-MM-DD", "fecha_hasta": "YYYY-MM-DD" }
    """
    hoy = date.today().isoformat()
    fecha_desde = body.get('fecha_desde', hoy[:8] + '01')
    fecha_hasta = body.get('fecha_hasta', hoy)

    odoo = _get_odoo()
    try:
        comisiones = odoo.get_comisiones_bancarias(fecha_desde, fecha_hasta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    if not comisiones:
        return {'importados': 0, 'omitidos': 0, 'mensaje': 'Sin comisiones en ese período'}

    con = get_con()
    ya_refs = {r[0] for r in con.execute(
        "SELECT odoo_ref FROM maestro_operaciones "
        "WHERE odoo_ref IS NOT NULL AND origen='odoo_comision'"
    ).fetchall()}

    tasa_bcv = tasa_bcv_hoy()
    tasa_real = tasa_custom_hoy()
    importados = omitidos = 0

    for c in comisiones:
        ref = c.get('name') or str(c.get('id'))
        if ref in ya_refs:
            omitidos += 1
            continue

        monto = float(c.get('monto_comision', 0) or 0)
        if monto <= 0:
            continue

        journal_nombre = c.get('journal_nombre', '')
        desc = c.get('ref') or c.get('name') or 'Comisión bancaria Odoo'

        con.execute("""
            INSERT INTO maestro_operaciones
                (fecha, nro_documento, monto, moneda, tipo, categoria, subcategoria,
                 descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
                 origen, odoo_ref, journal_nombre, estado, creado_por)
            VALUES (?,?,?,?,'egreso','Gasto','Comisión Bancaria',?,?,?,?,?,'odoo_comision',?,?,'confirmado',?)
        """, (
            c.get('date', hoy), c.get('name'),
            monto, 'USD',
            desc,
            tasa_bcv, monto, tasa_real, monto,
            ref, journal_nombre,
            user['id']
        ))
        importados += 1

    con.commit()
    con.close()
    return {'importados': importados, 'omitidos': omitidos,
            'mensaje': f'{importados} comisiones importadas, {omitidos} ya existían'}
