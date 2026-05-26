"""
Módulo de Fraccionamiento — divide unidades padre en sub-unidades rastreables
para ventas, regalos y ajustes de stock.
Deshabilitado por defecto; se activa desde Configuración.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone, date

from database import get_con
from routers.auth import get_current_user, require_roles
from models.schemas import row_to_dict, rows_to_list
from models.schemas_input import (
    FraccionamientoLoteCreate, FraccionamientoVentaCreate,
    FraccionamientoLineaCreate, FraccionamientoPagoCreate,
    FraccionamientoRegaloCreate, FraccionamientoAjusteCreate,
)

router = APIRouter(prefix='/fraccionamiento', tags=['fraccionamiento'])


def _require_modulo(con=None):
    _con = con or get_con()
    row = _con.execute(
        "SELECT valor FROM config_app WHERE clave='modulo_fraccionamiento_activo'"
    ).fetchone()
    if con is None:
        _con.close()
    if not row or str(row[0]) not in ('1', 'true'):
        raise HTTPException(
            status_code=403,
            detail='El módulo de Fraccionamiento está desactivado. Actívalo desde Configuración.'
        )


def _generar_codigo_lote(con) -> str:
    row = con.execute(
        "SELECT MAX(id) FROM fraccionamiento_lotes"
    ).fetchone()
    next_id = (row[0] or 0) + 1
    return f'FL-{str(next_id).zfill(4)}'


def _generar_codigo_venta(con) -> str:
    row = con.execute(
        "SELECT MAX(id) FROM fraccionamiento_ventas"
    ).fetchone()
    next_id = (row[0] or 0) + 1
    return f'FV-{str(next_id).zfill(4)}'


# ── LOTES ─────────────────────────────────────────────────────────────────────

@router.get('/lotes')
def listar_lotes(user=Depends(get_current_user)):
    con = get_con()
    _require_modulo(con)
    rows = rows_to_list(con.execute(
        "SELECT * FROM fraccionamiento_lotes ORDER BY creado_en DESC"
    ).fetchall())
    con.close()
    return rows


@router.post('/lotes')
def crear_lote(body: FraccionamientoLoteCreate, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)

    cantidad_padre = body.cantidad_padre
    factor = body.factor_conversion
    total_sub = cantidad_padre * factor
    costo_padre = body.costo_padre_usd
    costo_sub = round(costo_padre / factor, 6) if costo_padre else None

    codigo = _generar_codigo_lote(con)
    ahora = datetime.now(timezone.utc).isoformat()

    cur = con.execute("""
        INSERT INTO fraccionamiento_lotes
            (codigo, producto_ref, producto_nombre, unidad_padre, cantidad_padre,
             subunidad_nombre, factor_conversion, total_subunidades, stock_disponible,
             costo_padre_usd, costo_subunidad_usd, precio_venta_usd,
             estado, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'activo',?,?,?)
    """, (
        codigo, body.producto_ref, body.producto_nombre,
        body.unidad_padre, cantidad_padre, body.subunidad_nombre,
        factor, total_sub, total_sub,
        costo_padre, costo_sub, body.precio_venta_usd,
        body.notas, user['id'], ahora
    ))
    lote_id = cur.lastrowid

    # Decrementar inventario_interno si hay producto_ref
    if body.producto_ref:
        con.execute("""
            UPDATE inventario_interno
            SET stock_actual = GREATEST(0, stock_actual - ?),
                ultima_actualizacion = ?
            WHERE producto_codigo = ?
        """, (cantidad_padre, ahora, body.producto_ref))

    # Crear movimiento inicial de entrada
    con.execute("""
        INSERT INTO fraccionamiento_movimientos
            (lote_id, tipo, cantidad, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?)
    """, (lote_id, 'entrada', total_sub, 'Creación de lote', user['id'], ahora))

    con.commit()
    lote = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_lotes WHERE id=?", (lote_id,)
    ).fetchone())
    con.close()
    return lote


@router.get('/lotes/{lote_id}')
def detalle_lote(lote_id: int, user=Depends(get_current_user)):
    con = get_con()
    _require_modulo(con)
    lote = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_lotes WHERE id=?", (lote_id,)
    ).fetchone())
    if not lote:
        con.close(); raise HTTPException(404, 'Lote no encontrado')
    movimientos = rows_to_list(con.execute(
        "SELECT * FROM fraccionamiento_movimientos WHERE lote_id=? ORDER BY creado_en DESC",
        (lote_id,)
    ).fetchall())
    lote['movimientos'] = movimientos
    con.close()
    return lote


@router.put('/lotes/{lote_id}/cerrar')
def cerrar_lote(lote_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    lote = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_lotes WHERE id=?", (lote_id,)
    ).fetchone())
    if not lote:
        con.close(); raise HTTPException(404, 'Lote no encontrado')
    con.execute("UPDATE fraccionamiento_lotes SET estado='cerrado' WHERE id=?", (lote_id,))
    con.commit(); con.close()
    return {'mensaje': 'Lote cerrado'}


@router.post('/lotes/{lote_id}/regalo')
def registrar_regalo(lote_id: int, body: FraccionamientoRegaloCreate,
                     user=Depends(require_roles('admin', 'gerente'))):
    cantidad = body.cantidad
    con = get_con()
    _require_modulo(con)
    lote = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_lotes WHERE id=?", (lote_id,)
    ).fetchone())
    if not lote:
        con.close(); raise HTTPException(404, 'Lote no encontrado')
    if lote['stock_disponible'] < cantidad:
        con.close(); raise HTTPException(400, 'Stock insuficiente')

    ahora = datetime.now(timezone.utc).isoformat()
    con.execute(
        "UPDATE fraccionamiento_lotes SET stock_disponible = stock_disponible - ? WHERE id=?",
        (cantidad, lote_id)
    )
    con.execute("""
        INSERT INTO fraccionamiento_movimientos
            (lote_id, tipo, cantidad, destinatario, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?)
    """, (lote_id, 'regalo', -cantidad, body.destinatario, body.notas, user['id'], ahora))
    con.commit(); con.close()
    return {'mensaje': 'Regalo registrado', 'cantidad': cantidad}


@router.post('/lotes/{lote_id}/ajuste')
def ajustar_lote(lote_id: int, body: FraccionamientoAjusteCreate,
                 user=Depends(require_roles('admin', 'gerente'))):
    delta = body.cantidad_delta
    con = get_con()
    _require_modulo(con)
    lote = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_lotes WHERE id=?", (lote_id,)
    ).fetchone())
    if not lote:
        con.close(); raise HTTPException(404, 'Lote no encontrado')
    nuevo_stock = lote['stock_disponible'] + delta
    if nuevo_stock < 0:
        con.close(); raise HTTPException(400, 'El ajuste resultaría en stock negativo')

    ahora = datetime.now(timezone.utc).isoformat()
    con.execute(
        "UPDATE fraccionamiento_lotes SET stock_disponible = ? WHERE id=?",
        (nuevo_stock, lote_id)
    )
    con.execute("""
        INSERT INTO fraccionamiento_movimientos
            (lote_id, tipo, cantidad, referencia, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?)
    """, (lote_id, 'ajuste', delta, body.motivo, body.motivo, user['id'], ahora))
    con.commit(); con.close()
    return {'mensaje': 'Ajuste registrado', 'stock_nuevo': nuevo_stock}


# ── VENTAS ────────────────────────────────────────────────────────────────────

@router.get('/ventas')
def listar_ventas(user=Depends(get_current_user)):
    con = get_con()
    _require_modulo(con)
    rows = rows_to_list(con.execute(
        "SELECT * FROM fraccionamiento_ventas ORDER BY creado_en DESC"
    ).fetchall())
    con.close()
    return rows


@router.post('/ventas/crear')
def crear_venta(body: FraccionamientoVentaCreate, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)

    codigo = _generar_codigo_venta(con)
    ahora = datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO fraccionamiento_ventas
            (codigo, cliente_nombre, vendedor_id, estado, notas, creado_en)
        VALUES (?,?,?,'borrador',?,?)
    """, (codigo, body.cliente_nombre, user['id'], body.notas, ahora))
    venta_id = cur.lastrowid
    con.commit()
    venta = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_ventas WHERE id=?", (venta_id,)
    ).fetchone())
    con.close()
    return venta


@router.get('/ventas/{venta_id}')
def detalle_venta(venta_id: int, user=Depends(get_current_user)):
    con = get_con()
    _require_modulo(con)
    venta = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_ventas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta:
        con.close(); raise HTTPException(404, 'Venta no encontrada')
    lineas = rows_to_list(con.execute(
        "SELECT l.*, lt.producto_nombre, lt.subunidad_nombre FROM fraccionamiento_ventas_lineas l "
        "JOIN fraccionamiento_lotes lt ON lt.id = l.lote_id WHERE l.venta_id=?",
        (venta_id,)
    ).fetchall())
    pagos = rows_to_list(con.execute(
        "SELECT * FROM fraccionamiento_pagos WHERE venta_id=? ORDER BY creado_en DESC",
        (venta_id,)
    ).fetchall())
    venta['lineas'] = lineas
    venta['pagos'] = pagos
    con.close()
    return venta


@router.post('/ventas/{venta_id}/agregar-linea')
def agregar_linea(venta_id: int, body: FraccionamientoLineaCreate,
                  user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    venta = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_ventas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta:
        con.close(); raise HTTPException(404, 'Venta no encontrada')
    if venta['estado'] != 'borrador':
        con.close(); raise HTTPException(400, 'Solo se pueden agregar líneas a ventas en borrador')

    lote_id = body.lote_id
    cantidad = body.cantidad
    precio = body.precio_unitario_usd
    descuento = body.descuento_pct
    subtotal = round(cantidad * precio * (1 - descuento / 100), 4)

    lote = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_lotes WHERE id=?", (lote_id,)
    ).fetchone())
    if not lote:
        con.close(); raise HTTPException(404, 'Lote no encontrado')
    if lote['stock_disponible'] < cantidad:
        con.close(); raise HTTPException(400, f'Stock insuficiente: disponible {lote["stock_disponible"]}')

    con.execute("""
        INSERT INTO fraccionamiento_ventas_lineas
            (venta_id, lote_id, cantidad, precio_unitario_usd, descuento_pct, subtotal_usd)
        VALUES (?,?,?,?,?,?)
    """, (venta_id, lote_id, cantidad, precio, descuento, subtotal))

    # Recalcular total venta
    con.execute("""
        UPDATE fraccionamiento_ventas
        SET total_usd = (SELECT COALESCE(SUM(subtotal_usd),0) FROM fraccionamiento_ventas_lineas WHERE venta_id=?)
        WHERE id=?
    """, (venta_id, venta_id))
    con.commit(); con.close()
    return {'mensaje': 'Línea agregada', 'subtotal_usd': subtotal}


@router.delete('/ventas/{venta_id}/linea/{linea_id}')
def eliminar_linea(venta_id: int, linea_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    venta = row_to_dict(con.execute(
        "SELECT estado FROM fraccionamiento_ventas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta:
        con.close(); raise HTTPException(404, 'Venta no encontrada')
    if venta['estado'] != 'borrador':
        con.close(); raise HTTPException(400, 'Solo se pueden eliminar líneas de ventas en borrador')
    con.execute(
        "DELETE FROM fraccionamiento_ventas_lineas WHERE id=? AND venta_id=?",
        (linea_id, venta_id)
    )
    con.execute("""
        UPDATE fraccionamiento_ventas
        SET total_usd = (SELECT COALESCE(SUM(subtotal_usd),0) FROM fraccionamiento_ventas_lineas WHERE venta_id=?)
        WHERE id=?
    """, (venta_id, venta_id))
    con.commit(); con.close()
    return {'mensaje': 'Línea eliminada'}


@router.put('/ventas/{venta_id}/confirmar')
def confirmar_venta(venta_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    venta = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_ventas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta:
        con.close(); raise HTTPException(404, 'Venta no encontrada')
    if venta['estado'] != 'borrador':
        con.close(); raise HTTPException(400, 'Solo se pueden confirmar ventas en borrador')

    lineas = rows_to_list(con.execute(
        "SELECT * FROM fraccionamiento_ventas_lineas WHERE venta_id=?", (venta_id,)
    ).fetchall())
    if not lineas:
        con.close(); raise HTTPException(400, 'La venta no tiene líneas')

    ahora = datetime.now(timezone.utc).isoformat()

    # Decrementar stock y crear movimientos
    for linea in lineas:
        lote = row_to_dict(con.execute(
            "SELECT stock_disponible FROM fraccionamiento_lotes WHERE id=?", (linea['lote_id'],)
        ).fetchone())
        if not lote or lote['stock_disponible'] < linea['cantidad']:
            con.close(); raise HTTPException(400, f'Stock insuficiente en lote {linea["lote_id"]}')
        con.execute(
            "UPDATE fraccionamiento_lotes SET stock_disponible = stock_disponible - ? WHERE id=?",
            (linea['cantidad'], linea['lote_id'])
        )
        # Actualizar estado del lote si se agota
        con.execute("""
            UPDATE fraccionamiento_lotes SET estado='agotado'
            WHERE id=? AND stock_disponible <= 0
        """, (linea['lote_id'],))
        con.execute("""
            INSERT INTO fraccionamiento_movimientos
                (lote_id, tipo, cantidad, precio_unitario_usd, venta_id, notas, creado_por, creado_en)
            VALUES (?,?,?,?,?,?,?,?)
        """, (linea['lote_id'], 'venta', -linea['cantidad'], linea['precio_unitario_usd'],
              venta_id, f'Venta {venta["codigo"]}', user['id'], ahora))

    total = float(venta['total_usd'] or 0)
    con.execute("""
        UPDATE fraccionamiento_ventas
        SET estado='confirmada', saldo_pendiente=? WHERE id=?
    """, (total, venta_id))
    con.commit(); con.close()
    return {'mensaje': 'Venta confirmada', 'total_usd': total}


@router.put('/ventas/{venta_id}/anular')
def anular_venta(venta_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    venta = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_ventas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta:
        con.close(); raise HTTPException(404, 'Venta no encontrada')
    con.execute(
        "UPDATE fraccionamiento_ventas SET estado='anulada' WHERE id=?", (venta_id,)
    )
    con.commit(); con.close()
    return {'mensaje': 'Venta anulada'}


@router.post('/ventas/{venta_id}/registrar-pago')
def registrar_pago(venta_id: int, body: FraccionamientoPagoCreate,
                   user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    venta = row_to_dict(con.execute(
        "SELECT * FROM fraccionamiento_ventas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta:
        con.close(); raise HTTPException(404, 'Venta no encontrada')
    if venta['estado'] != 'confirmada':
        con.close(); raise HTTPException(400, 'Solo se pueden registrar pagos en ventas confirmadas')

    monto = body.monto
    ahora = datetime.now(timezone.utc).isoformat()
    con.execute("""
        INSERT INTO fraccionamiento_pagos
            (venta_id, monto, moneda, metodo, referencia, fecha_pago, notas, registrado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (venta_id, monto, body.moneda, body.metodo,
          body.referencia, body.fecha_pago, body.notas, user['id'], ahora))

    # Reducir saldo pendiente
    con.execute("""
        UPDATE fraccionamiento_ventas
        SET saldo_pendiente = GREATEST(0, saldo_pendiente - ?)
        WHERE id=?
    """, (monto, venta_id))
    con.commit(); con.close()
    return {'mensaje': 'Pago registrado', 'monto': monto}


# ── RESUMEN / KPIs ────────────────────────────────────────────────────────────

@router.get('/resumen')
def resumen(user=Depends(get_current_user)):
    con = get_con()
    _require_modulo(con)
    mes_actual = date.today().strftime('%Y-%m')

    lotes_activos = con.execute(
        "SELECT COUNT(*) FROM fraccionamiento_lotes WHERE estado='activo'"
    ).fetchone()[0]
    total_sub_disponible = con.execute(
        "SELECT COALESCE(SUM(stock_disponible),0) FROM fraccionamiento_lotes WHERE estado='activo'"
    ).fetchone()[0]
    ventas_mes = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(total_usd),0) FROM fraccionamiento_ventas "
        "WHERE estado='confirmada' AND creado_en LIKE ?",
        (f'{mes_actual}%',)
    ).fetchone()
    pendiente_cobro = con.execute(
        "SELECT COALESCE(SUM(saldo_pendiente),0) FROM fraccionamiento_ventas WHERE estado='confirmada'"
    ).fetchone()[0]

    con.close()
    return {
        'lotes_activos': lotes_activos,
        'total_subunidades_disponible': float(total_sub_disponible or 0),
        'ventas_mes_count': ventas_mes[0],
        'ventas_mes_usd': float(ventas_mes[1] or 0),
        'pendiente_cobro': float(pendiente_cobro or 0),
    }
