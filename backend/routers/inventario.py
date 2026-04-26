from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
from database import get_con
from routers.auth import get_current_user, require_roles
from models.operaciones import AjusteInventario, ProductoInventario, CompraInternaCreate
from models.schemas import row_to_dict, rows_to_list

router = APIRouter(prefix='/inventario-interno', tags=['inventario'])


# ── PRODUCTOS EXTRA (campos adicionales locales) ───────────────────────────────

@router.get('/productos-extra')
def listar_productos_extra(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute("SELECT * FROM productos_extra ORDER BY producto_ref").fetchall())
    con.close()
    return rows


@router.put('/productos-extra')
def upsert_producto_extra(body: dict, user=Depends(require_roles('admin', 'gerente'))):
    """Guarda o actualiza campos extra de un producto: {producto_ref, marca, categoria_local, datos_extra}"""
    ref = body.get('producto_ref')
    if not ref:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail='producto_ref requerido')
    con = get_con()
    con.execute("""
        INSERT INTO productos_extra(producto_ref, marca, categoria_local, datos_extra)
        VALUES(?,?,?,?)
        ON CONFLICT(producto_ref) DO UPDATE SET
            marca = excluded.marca,
            categoria_local = excluded.categoria_local,
            datos_extra = excluded.datos_extra
    """, (ref, body.get('marca'), body.get('categoria_local'), body.get('datos_extra')))
    con.commit()
    con.close()
    return {'mensaje': 'Campos extra guardados'}


@router.get('')
def listar_inventario(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM inventario_interno ORDER BY producto_nombre"
    ).fetchall())
    con.close()
    return rows


@router.post('')
def crear_producto(body: ProductoInventario,
                   user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    try:
        con.execute("""
            INSERT INTO inventario_interno
                (producto_codigo, producto_nombre, stock_actual, costo_usd,
                 ultima_actualizacion)
            VALUES (?,?,?,?,?)
        """, (body.producto_codigo, body.producto_nombre, body.stock_actual,
              body.costo_usd, datetime.utcnow().isoformat()))
        con.commit()
        con.close()
        return {'mensaje': 'Producto creado'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/{codigo}/ajustar')
def ajustar_stock(codigo: str, body: AjusteInventario,
                  user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    prod = row_to_dict(con.execute(
        "SELECT * FROM inventario_interno WHERE producto_codigo=?", (codigo,)
    ).fetchone())
    if not prod:
        con.close()
        raise HTTPException(status_code=404, detail='Producto no encontrado')
    nuevo = max(0, prod['stock_actual'] + body.cantidad_delta)
    con.execute("""
        UPDATE inventario_interno
        SET stock_actual=?, ultima_actualizacion=?
        WHERE producto_codigo=?
    """, (nuevo, datetime.utcnow().isoformat(), codigo))
    con.commit()
    con.close()
    return {'mensaje': 'Stock ajustado', 'stock_nuevo': nuevo}


# ── COMPRAS INTERNAS ─────────────────────────────────────────────────────────

@router.get('/compras')
def listar_compras(user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM compras_internas ORDER BY creado_en DESC"
    ).fetchall())
    con.close()
    return rows


@router.post('/compras/crear')
def crear_compra(body: CompraInternaCreate,
                 user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    cur = con.execute("""
        INSERT INTO compras_internas(proveedor, fecha, total_usd)
        VALUES (?,?,?)
    """, (body.proveedor, body.fecha, body.total_usd))
    compra_id = cur.lastrowid

    for l in body.lineas:
        con.execute("""
            INSERT INTO compras_internas_lineas
                (compra_id, producto_codigo, producto_nombre, cantidad, costo_unitario)
            VALUES (?,?,?,?,?)
        """, (compra_id, l.get('producto_codigo'), l.get('producto_nombre'),
              l.get('cantidad', 0), l.get('costo_unitario', 0)))

        # Actualizar inventario
        con.execute("""
            INSERT INTO inventario_interno
                (producto_codigo, producto_nombre, stock_actual, costo_usd, ultima_actualizacion)
            VALUES (?,?,?,?,?)
            ON CONFLICT(producto_codigo) DO UPDATE SET
                stock_actual = stock_actual + excluded.stock_actual,
                costo_usd = excluded.costo_usd,
                ultima_actualizacion = excluded.ultima_actualizacion
        """, (l.get('producto_codigo'), l.get('producto_nombre'),
              l.get('cantidad', 0), l.get('costo_unitario', 0),
              datetime.utcnow().isoformat()))

    con.execute(
        "UPDATE compras_internas SET estado='confirmada' WHERE id=?", (compra_id,)
    )
    con.commit()
    con.close()
    return {'id': compra_id, 'mensaje': 'Compra registrada e inventario actualizado'}
