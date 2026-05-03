from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import date, datetime
from typing import Optional
from database import get_con
from routers.auth import get_current_user, require_roles
from models.schemas import row_to_dict, rows_to_list
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/maestro', tags=['maestro'])


# ── CATEGORÍAS ────────────────────────────────────────────────────────────────

@router.get('/categorias')
def listar_categorias(tipo: str = None, user=Depends(get_current_user)):
    con = get_con()
    if tipo:
        rows = rows_to_list(con.execute(
            "SELECT * FROM categorias_operacion WHERE activa=1 AND (tipo=? OR tipo='ambos') ORDER BY categoria, subcategoria",
            (tipo,)
        ).fetchall())
    else:
        rows = rows_to_list(con.execute(
            "SELECT * FROM categorias_operacion WHERE activa=1 ORDER BY tipo, categoria, subcategoria"
        ).fetchall())
    con.close()
    return rows


@router.post('/categorias')
def crear_categoria(body: dict, user=Depends(require_roles('admin', 'gerente'))):
    tipo = body.get('tipo')
    categoria = body.get('categoria')
    if not tipo or not categoria:
        raise HTTPException(status_code=400, detail='tipo y categoria son requeridos')
    con = get_con()
    cur = con.execute(
        "INSERT INTO categorias_operacion(tipo, categoria, subcategoria, cuenta_odoo) VALUES(?,?,?,?)",
        (tipo, categoria, body.get('subcategoria'), body.get('cuenta_odoo'))
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return {'id': new_id, 'mensaje': 'Categoría creada'}


@router.delete('/categorias/{cat_id}')
def desactivar_categoria(cat_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    con.execute("UPDATE categorias_operacion SET activa=0 WHERE id=?", (cat_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Categoría desactivada'}


# ── OPERACIONES ───────────────────────────────────────────────────────────────

@router.get('')
def listar_operaciones(
    tipo: Optional[str] = None,
    categoria: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    moneda: Optional[str] = None,
    origen: Optional[str] = None,
    limit: int = Query(200, le=1000),
    user=Depends(get_current_user)
):
    con = get_con()
    q = """
        SELECT m.*, u.nombre as creado_por_nombre
        FROM maestro_operaciones m
        LEFT JOIN usuarios u ON u.id = m.creado_por
        WHERE 1=1
    """
    params = []
    if tipo:
        q += " AND m.tipo=?"
        params.append(tipo)
    if categoria:
        q += " AND m.categoria=?"
        params.append(categoria)
    if fecha_desde:
        q += " AND m.fecha >= ?"
        params.append(fecha_desde)
    if fecha_hasta:
        q += " AND m.fecha <= ?"
        params.append(fecha_hasta)
    if moneda:
        q += " AND m.moneda=?"
        params.append(moneda)
    if origen:
        q += " AND m.origen=?"
        params.append(origen)
    q += " ORDER BY m.fecha DESC, m.id DESC LIMIT ?"
    params.append(limit)
    rows = rows_to_list(con.execute(q, params).fetchall())
    con.close()
    return rows


@router.post('')
def crear_operacion(body: dict, user=Depends(get_current_user)):
    fecha = body.get('fecha') or date.today().isoformat()
    monto = body.get('monto')
    moneda = body.get('moneda')
    tipo = body.get('tipo')  # 'ingreso' | 'egreso'

    if not all([monto, moneda, tipo]):
        raise HTTPException(status_code=400, detail='monto, moneda y tipo son requeridos')
    if tipo not in ('ingreso', 'egreso'):
        raise HTTPException(status_code=400, detail='tipo debe ser ingreso o egreso')

    tasa_bcv = body.get('tasa_bcv') or tasa_bcv_hoy()
    tasa_real = body.get('tasa_real') or tasa_custom_hoy()

    monto_usd_bcv = None
    monto_real_usd = None
    if moneda == 'VES':
        if tasa_bcv:
            monto_usd_bcv = float(monto) / tasa_bcv
        if tasa_real:
            monto_real_usd = float(monto) / tasa_real
    elif moneda in ('USD', 'USDT'):
        monto_usd_bcv = float(monto)
        monto_real_usd = float(monto)
    elif moneda == 'EUR':
        tasa_eur = tasa_bcv_hoy('EUR_VES')
        if tasa_eur:
            ves = float(monto) * tasa_eur
            if tasa_bcv:
                monto_usd_bcv = ves / tasa_bcv
            if tasa_real:
                monto_real_usd = ves / tasa_real

    con = get_con()
    cur = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, nro_documento, monto, moneda, metodo, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, pago_id, odoo_ref, estado, journal_nombre, creado_por)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        fecha,
        body.get('nro_documento'),
        float(monto),
        moneda,
        body.get('metodo'),
        tipo,
        body.get('categoria'),
        body.get('subcategoria'),
        body.get('descripcion'),
        tasa_bcv,
        monto_usd_bcv,
        tasa_real,
        monto_real_usd,
        body.get('origen', 'manual'),
        body.get('pago_id'),
        body.get('odoo_ref'),
        body.get('estado', 'confirmado'),
        body.get('journal_nombre'),
        user['id']
    ))
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return {'id': new_id, 'mensaje': 'Operación registrada'}


@router.put('/{op_id}')
def actualizar_operacion(op_id: int, body: dict,
                          user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    op = row_to_dict(con.execute(
        "SELECT * FROM maestro_operaciones WHERE id=?", (op_id,)
    ).fetchone())
    if not op:
        con.close()
        raise HTTPException(status_code=404, detail='Operación no encontrada')
    if op.get('origen') != 'manual':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo se pueden editar operaciones manuales')

    campos = ['fecha', 'nro_documento', 'monto', 'moneda', 'metodo', 'tipo',
              'categoria', 'subcategoria', 'descripcion', 'tasa_bcv',
              'monto_usd_bcv', 'tasa_real', 'monto_real_usd', 'estado']
    sets = []
    vals = []
    for c in campos:
        if c in body:
            sets.append(f"{c}=?")
            vals.append(body[c])
    if not sets:
        con.close()
        return {'mensaje': 'Sin cambios'}
    vals.append(op_id)
    con.execute(f"UPDATE maestro_operaciones SET {', '.join(sets)} WHERE id=?", vals)
    con.commit()
    con.close()
    return {'mensaje': 'Operación actualizada'}


@router.delete('/{op_id}')
def eliminar_operacion(op_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    op = row_to_dict(con.execute(
        "SELECT * FROM maestro_operaciones WHERE id=?", (op_id,)
    ).fetchone())
    if not op:
        con.close()
        raise HTTPException(status_code=404, detail='Operación no encontrada')
    if op.get('origen') != 'manual':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo se pueden eliminar operaciones manuales')
    con.execute("DELETE FROM maestro_operaciones WHERE id=?", (op_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Operación eliminada'}


# ── REPORTES ─────────────────────────────────────────────────────────────────

@router.get('/reportes/resumen')
def resumen_operaciones(
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    moneda: str = 'USD',
    user=Depends(get_current_user)
):
    """Totales agrupados por tipo+categoría para el período."""
    con = get_con()
    hoy = date.today().isoformat()
    f_desde = fecha_desde or hoy[:8] + '01'  # primer día del mes
    f_hasta = fecha_hasta or hoy

    campo_monto = 'monto_usd_bcv' if moneda == 'USD' else 'monto'

    rows = rows_to_list(con.execute(f"""
        SELECT tipo, categoria, subcategoria,
               COUNT(*) as cant,
               SUM({campo_monto}) as total
        FROM maestro_operaciones
        WHERE fecha BETWEEN ? AND ?
          AND estado != 'anulado'
        GROUP BY tipo, categoria, subcategoria
        ORDER BY tipo, categoria, subcategoria
    """, (f_desde, f_hasta)).fetchall())

    # Totales generales
    totales = row_to_dict(con.execute(f"""
        SELECT
            SUM(CASE WHEN tipo='ingreso' THEN {campo_monto} ELSE 0 END) as total_ingresos,
            SUM(CASE WHEN tipo='egreso'  THEN {campo_monto} ELSE 0 END) as total_egresos
        FROM maestro_operaciones
        WHERE fecha BETWEEN ? AND ? AND estado != 'anulado'
    """, (f_desde, f_hasta)).fetchone())

    con.close()

    total_ing = totales.get('total_ingresos') or 0
    total_egr = totales.get('total_egresos') or 0

    return {
        'fecha_desde': f_desde,
        'fecha_hasta': f_hasta,
        'moneda_base': moneda,
        'total_ingresos': total_ing,
        'total_egresos': total_egr,
        'saldo': total_ing - total_egr,
        'detalle': rows
    }


@router.get('/reportes/por-dia')
def operaciones_por_dia(
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    tipo: Optional[str] = None,
    user=Depends(get_current_user)
):
    """Serie temporal agrupada por día."""
    con = get_con()
    hoy = date.today().isoformat()
    f_desde = fecha_desde or hoy[:8] + '01'
    f_hasta = fecha_hasta or hoy

    q = """
        SELECT fecha,
               SUM(CASE WHEN tipo='ingreso' THEN monto_usd_bcv ELSE 0 END) as ingresos_usd,
               SUM(CASE WHEN tipo='egreso'  THEN monto_usd_bcv ELSE 0 END) as egresos_usd,
               SUM(CASE WHEN tipo='ingreso' THEN monto ELSE 0 END) as ingresos_orig,
               SUM(CASE WHEN tipo='egreso'  THEN monto ELSE 0 END) as egresos_orig,
               COUNT(*) as cant
        FROM maestro_operaciones
        WHERE fecha BETWEEN ? AND ? AND estado != 'anulado'
    """
    params = [f_desde, f_hasta]
    if tipo:
        q += " AND tipo=?"
        params.append(tipo)
    q += " GROUP BY fecha ORDER BY fecha ASC"

    rows = rows_to_list(con.execute(q, params).fetchall())
    con.close()
    return rows


@router.get('/reportes/gastos-categoria')
def gastos_por_categoria(
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    user=Depends(get_current_user)
):
    """Egresos agrupados para gráfico de torta."""
    con = get_con()
    hoy = date.today().isoformat()
    f_desde = fecha_desde or hoy[:8] + '01'
    f_hasta = fecha_hasta or hoy

    rows = rows_to_list(con.execute("""
        SELECT
            COALESCE(subcategoria, categoria) as etiqueta,
            categoria,
            subcategoria,
            COUNT(*) as cant,
            SUM(monto_usd_bcv) as total_usd
        FROM maestro_operaciones
        WHERE tipo='egreso' AND fecha BETWEEN ? AND ? AND estado != 'anulado'
        GROUP BY categoria, subcategoria
        ORDER BY total_usd DESC
    """, (f_desde, f_hasta)).fetchall())
    con.close()
    return rows


# ── ENVIAR EGRESO A ODOO ──────────────────────────────────────────────────────

@router.post('/{op_id}/enviar-odoo')
def enviar_egreso_odoo(op_id: int, body: dict,
                        user=Depends(require_roles('admin', 'gerente'))):
    """
    Crea el pago de proveedor en Odoo para un egreso manual del maestro.
    body: {journal_id: int, partner_id: int (opcional)}
    """
    from routers.ventas import get_odoo
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

    odoo = get_odoo()
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
    return {'mensaje': 'Egreso enviado a Odoo',
            'odoo_payment_id': odoo_payment_id}


# ── IMPORTAR PAGOS DE PROVEEDOR DESDE ODOO ────────────────────────────────────

@router.post('/importar-pagos-proveedor')
def importar_pagos_proveedor(body: dict,
                              user=Depends(require_roles('admin', 'gerente'))):
    """
    Importa pagos de proveedor (outbound) de Odoo que NO estén registrados
    en el maestro. Igual que con cobranza: si ya existe, se omite.
    """
    from routers.ventas import get_odoo
    hoy = date.today().isoformat()
    fecha_desde = body.get('fecha_desde', hoy[:8] + '01')
    fecha_hasta = body.get('fecha_hasta', hoy)

    odoo = get_odoo()
    try:
        pagos_odoo = odoo.get_pagos_proveedor(fecha_desde, fecha_hasta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    if not pagos_odoo:
        return {'importados': 0, 'omitidos': 0, 'mensaje': 'Sin pagos en ese período'}

    con = get_con()
    # IDs ya registrados
    ya_reg = {r[0] for r in con.execute(
        "SELECT odoo_payment_id FROM maestro_operaciones "
        "WHERE odoo_payment_id IS NOT NULL"
    ).fetchall()}

    tasa_bcv = tasa_bcv_hoy()
    tasa_real = tasa_custom_hoy()
    importados = 0
    omitidos   = 0

    for p in pagos_odoo:
        pid = p.get('id')
        if pid in ya_reg:
            omitidos += 1
            continue

        monto = float(p.get('amount', 0))
        cur = p.get('currency_id')
        moneda = cur[1] if isinstance(cur, (list, tuple)) else (cur or 'USD')
        if moneda not in ('USD', 'VES', 'EUR', 'USDT'):
            moneda = 'USD'

        monto_usd_bcv = None
        monto_real_usd = None
        if moneda in ('USD', 'USDT'):
            monto_usd_bcv = monto
            monto_real_usd = monto
        elif moneda == 'VES':
            if tasa_bcv:
                monto_usd_bcv = monto / tasa_bcv
            if tasa_real:
                monto_real_usd = monto / tasa_real

        partner = p.get('partner_id')
        partner_id_val = partner[0] if isinstance(partner, (list, tuple)) else None
        partner_nombre = partner[1] if isinstance(partner, (list, tuple)) else ''
        journal = p.get('journal_id')
        journal_id_val = journal[0] if isinstance(journal, (list, tuple)) else None

        descripcion = f"Pago proveedor: {partner_nombre}" if partner_nombre else 'Pago proveedor Odoo'

        con.execute("""
            INSERT INTO maestro_operaciones
                (fecha, nro_documento, monto, moneda, tipo, categoria,
                 descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
                 origen, odoo_ref, odoo_payment_id, odoo_journal_id, odoo_partner_id,
                 estado, creado_por)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p.get('date', hoy),
            p.get('name'),
            monto,
            moneda,
            'egreso',
            'Compra',
            descripcion,
            tasa_bcv,
            monto_usd_bcv,
            tasa_real,
            monto_real_usd,
            'odoo_pago_proveedor',
            p.get('name'),
            pid,
            journal_id_val,
            partner_id_val,
            'confirmado',
            user['id']
        ))
        importados += 1

    con.commit()
    con.close()
    return {'importados': importados, 'omitidos': omitidos,
            'mensaje': f'{importados} pagos importados, {omitidos} ya existían'}


# ── SINCRONIZAR CONCILIACIÓN ──────────────────────────────────────────────────

@router.post('/sync-conciliacion')
def sync_conciliacion(user=Depends(require_roles('admin', 'gerente'))):
    """
    Consulta Odoo para actualizar el campo odoo_conciliado en todas las
    entradas del maestro que tengan odoo_payment_id.
    Inbound = ingresos de cliente / Outbound = pagos a proveedor.
    """
    from routers.ventas import get_odoo
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

    odoo = get_odoo()
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


# ── CUENTAS CONTABLES ODOO ────────────────────────────────────────────────────

@router.get('/cuentas-odoo')
def listar_cuentas_odoo(user=Depends(require_roles('admin', 'gerente'))):
    """Lista de cuentas de gasto de Odoo para mapeo de categorías."""
    from routers.ventas import get_odoo
    try:
        odoo = get_odoo()
        cuentas = odoo.get_cuentas_gasto()
        return cuentas or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')


@router.get('/journals-odoo')
def listar_journals_odoo(user=Depends(get_current_user)):
    """Diarios de banco/caja de Odoo para registrar pagos."""
    from routers.ventas import get_odoo
    try:
        odoo = get_odoo()
        return odoo.get_journals() or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')


@router.get('/buscar-proveedores')
def buscar_proveedores(q: str = '', user=Depends(get_current_user)):
    from routers.ventas import get_odoo
    if len(q) < 2:
        return []
    try:
        odoo = get_odoo()
        return odoo.buscar_proveedores(q) or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')


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


# ── ACTUALIZAR CATEGORÍA (mapeo Odoo) ─────────────────────────────────────────

@router.put('/categorias/{cat_id}')
def actualizar_categoria(cat_id: int, body: dict,
                          user=Depends(require_roles('admin', 'gerente'))):
    """Actualiza campos de una categoría incluyendo el mapeo a cuenta/journal Odoo."""
    con = get_con()
    campos = ['categoria', 'subcategoria', 'tipo', 'cuenta_odoo',
              'odoo_journal_id', 'odoo_account_id', 'odoo_account_code', 'activa']
    sets = []
    vals = []
    for c in campos:
        if c in body:
            sets.append(f"{c}=?")
            vals.append(body[c])
    if not sets:
        con.close()
        return {'mensaje': 'Sin cambios'}
    vals.append(cat_id)
    con.execute(f"UPDATE categorias_operacion SET {', '.join(sets)} WHERE id=?", vals)
    con.commit()
    con.close()
    return {'mensaje': 'Categoría actualizada'}
