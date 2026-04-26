from fastapi import APIRouter, HTTPException, Depends
from database import get_con
from routers.auth import get_current_user, require_roles
from models.operaciones import VentaInternaCreate, LineaVentaInterna
from models.schemas import row_to_dict, rows_to_list

router = APIRouter(prefix='/ventas-internas', tags=['ventas-internas'])


def _generar_codigo(con) -> str:
    row = con.execute(
        "SELECT codigo FROM ventas_internas ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row and row['codigo']:
        num = int(row['codigo'].split('-')[1]) + 1
    else:
        num = 1
    return f'VI-{num:04d}'


@router.post('/crear')
def crear_venta(body: VentaInternaCreate, user=Depends(get_current_user)):
    con = get_con()
    codigo = _generar_codigo(con)
    cur = con.execute("""
        INSERT INTO ventas_internas(codigo, cliente_nombre, cliente_id_odoo,
                                    vendedor_id, notas)
        VALUES (?,?,?,?,?)
    """, (codigo, body.cliente_nombre, body.cliente_id_odoo,
          user['id'], body.notas))
    con.commit()
    venta_id = cur.lastrowid
    con.close()
    return {'id': venta_id, 'codigo': codigo, 'mensaje': 'Venta interna creada'}


@router.get('')
def listar_ventas(user=Depends(get_current_user)):
    con = get_con()
    if user['rol'] == 'vendedor':
        rows = rows_to_list(con.execute(
            "SELECT * FROM ventas_internas WHERE vendedor_id=? ORDER BY creado_en DESC",
            (user['id'],)
        ).fetchall())
    else:
        rows = rows_to_list(con.execute(
            "SELECT * FROM ventas_internas ORDER BY creado_en DESC"
        ).fetchall())
    con.close()
    return rows


@router.get('/{venta_id}')
def detalle_venta(venta_id: int, user=Depends(get_current_user)):
    con = get_con()
    venta = row_to_dict(con.execute(
        "SELECT * FROM ventas_internas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta:
        con.close()
        raise HTTPException(status_code=404, detail='Venta no encontrada')
    lineas = rows_to_list(con.execute(
        "SELECT * FROM ventas_internas_lineas WHERE venta_id=?", (venta_id,)
    ).fetchall())
    con.close()
    return {**venta, 'lineas': lineas}


@router.post('/{venta_id}/agregar-linea')
def agregar_linea(venta_id: int, body: LineaVentaInterna,
                  user=Depends(get_current_user)):
    con = get_con()
    venta = row_to_dict(con.execute(
        "SELECT * FROM ventas_internas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta or venta['estado'] not in ('borrador',):
        con.close()
        raise HTTPException(status_code=400,
                            detail='Venta no encontrada o ya confirmada')

    precio_neto = body.precio_unitario * (1 - body.descuento_pct / 100)
    subtotal = precio_neto * body.cantidad

    con.execute("""
        INSERT INTO ventas_internas_lineas
            (venta_id, producto_codigo, producto_nombre, cantidad,
             precio_unitario, descuento_pct)
        VALUES (?,?,?,?,?,?)
    """, (venta_id, body.producto_codigo, body.producto_nombre,
          body.cantidad, body.precio_unitario, body.descuento_pct))

    # Recalcular total
    total = con.execute("""
        SELECT SUM(cantidad * precio_unitario * (1 - descuento_pct/100))
        FROM ventas_internas_lineas WHERE venta_id=?
    """, (venta_id,)).fetchone()[0] or 0

    con.execute("UPDATE ventas_internas SET total_usd=? WHERE id=?",
                (total, venta_id))
    con.commit()
    con.close()
    return {'mensaje': 'Línea agregada', 'total_usd': total}


@router.put('/{venta_id}/confirmar')
def confirmar_venta(venta_id: int, user=Depends(get_current_user)):
    con = get_con()
    venta = row_to_dict(con.execute(
        "SELECT * FROM ventas_internas WHERE id=?", (venta_id,)
    ).fetchone())
    if not venta or venta['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Venta no encontrada o no está en borrador')

    lineas = rows_to_list(con.execute(
        "SELECT * FROM ventas_internas_lineas WHERE venta_id=?", (venta_id,)
    ).fetchall())
    if not lineas:
        con.close()
        raise HTTPException(status_code=400,
                            detail='La venta no tiene líneas')

    # Descontar inventario automáticamente
    for l in lineas:
        con.execute("""
            UPDATE inventario_interno
            SET stock_actual = MAX(0, stock_actual - ?)
            WHERE producto_codigo=?
        """, (l['cantidad'], l['producto_codigo']))

    con.execute(
        "UPDATE ventas_internas SET estado='confirmada' WHERE id=?", (venta_id,)
    )
    con.commit()
    con.close()
    return {'mensaje': 'Venta confirmada e inventario descontado'}


@router.put('/{venta_id}/anular')
def anular_venta(venta_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    con.execute(
        "UPDATE ventas_internas SET estado='anulada' WHERE id=?", (venta_id,)
    )
    con.commit()
    con.close()
    return {'mensaje': 'Venta anulada'}
