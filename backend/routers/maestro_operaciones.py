"""
Maestro de Operaciones — CRUD de categorías y operaciones, más reportes internos.

Rutas (sin prefijo; el prefijo '/maestro' lo asigna el agregador maestro.py):
  GET    /categorias
  POST   /categorias
  DELETE /categorias/{cat_id}
  PUT    /categorias/{cat_id}
  GET    /                   (lista con filtros)
  POST   /                   (crear operación manual)
  PUT    /{op_id}
  DELETE /{op_id}
  GET    /reportes/resumen
  GET    /reportes/por-dia
  GET    /reportes/gastos-categoria
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import date
from typing import Optional
from database import get_con
from routers.auth import get_current_user, require_roles
from models.schemas import row_to_dict, rows_to_list
from models.schemas_input import MaestroOperacionCreate
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter()


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


@router.put('/categorias/{cat_id}')
def actualizar_categoria(cat_id: int, body: dict,
                         user=Depends(require_roles('admin', 'gerente'))):
    """Actualiza campos de una categoría incluyendo el mapeo a cuenta/journal Odoo."""
    con = get_con()
    campos = ['categoria', 'subcategoria', 'tipo', 'cuenta_odoo',
              'odoo_journal_id', 'odoo_account_id', 'odoo_account_code', 'activa']
    sets, vals = [], []
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


# ── OPERACIONES ───────────────────────────────────────────────────────────────

@router.get('/')
def listar_operaciones(
    tipo: Optional[str] = None,
    categoria: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    moneda: Optional[str] = None,
    origen: Optional[str] = None,
    skip: int = 0,
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
        q += " AND m.tipo=?"; params.append(tipo)
    if categoria:
        q += " AND m.categoria=?"; params.append(categoria)
    if fecha_desde:
        q += " AND m.fecha >= ?"; params.append(fecha_desde)
    if fecha_hasta:
        q += " AND m.fecha <= ?"; params.append(fecha_hasta)
    if moneda:
        q += " AND m.moneda=?"; params.append(moneda)
    if origen:
        q += " AND m.origen=?"; params.append(origen)
    q += " ORDER BY m.fecha DESC, m.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, skip])
    rows = rows_to_list(con.execute(q, params).fetchall())
    con.close()
    return rows


@router.post('/')
def crear_operacion(body: MaestroOperacionCreate, user=Depends(get_current_user)):
    fecha = body.fecha or date.today().isoformat()
    monto = body.monto
    moneda = body.moneda
    tipo = body.tipo

    tasa_bcv = body.tasa_bcv or tasa_bcv_hoy()
    tasa_real = body.tasa_real or tasa_custom_hoy()

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
        body.nro_documento,
        float(monto),
        moneda,
        body.metodo,
        tipo,
        body.categoria,
        body.subcategoria,
        body.descripcion,
        tasa_bcv,
        monto_usd_bcv,
        tasa_real,
        monto_real_usd,
        body.origen,
        body.pago_id,
        body.odoo_ref,
        body.estado,
        body.journal_nombre,
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
    sets, vals = [], []
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


# ── REPORTES INTERNOS ─────────────────────────────────────────────────────────

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
    f_desde = fecha_desde or hoy[:8] + '01'
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
