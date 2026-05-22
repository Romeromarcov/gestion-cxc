"""
Módulo Interno — Inventario Interno
Stock local de productos no gestionados en Odoo.
Deshabilitado por defecto; se activa desde Configuración → Módulo Interno.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone

from database import get_con
from routers.auth import get_current_user, require_roles
from models.operaciones import ProductoInventario, AjusteInventario
from models.schemas import row_to_dict, rows_to_list

router = APIRouter(prefix='/inventario-interno', tags=['inventario-interno'])


def _require_modulo(con=None):
    _con = con or get_con()
    row = _con.execute(
        "SELECT valor FROM config_app WHERE clave='modulo_interno_activo'"
    ).fetchone()
    if con is None:
        _con.close()
    if not row or str(row[0]) not in ('1', 'true'):
        raise HTTPException(
            status_code=403,
            detail='El módulo interno está desactivado. Actívalo desde Configuración.'
        )


@router.get('')
def listar_inventario(user=Depends(get_current_user)):
    con = get_con()
    _require_modulo(con)
    rows = rows_to_list(con.execute(
        "SELECT * FROM inventario_interno ORDER BY producto_nombre"
    ).fetchall())
    con.close()
    return rows


@router.post('')
def crear_producto(body: ProductoInventario,
                   user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    try:
        con.execute("""
            INSERT INTO inventario_interno
                (producto_codigo, producto_nombre, stock_actual, costo_usd,
                 ultima_actualizacion)
            VALUES (?,?,?,?,?)
        """, (body.producto_codigo, body.producto_nombre, body.stock_actual,
              body.costo_usd, datetime.now(timezone.utc).isoformat()))
        con.commit()
        con.close()
        return {'mensaje': 'Producto creado'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/{codigo}')
def actualizar_producto(codigo: str, body: ProductoInventario,
                        user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    prod = row_to_dict(con.execute(
        "SELECT id FROM inventario_interno WHERE producto_codigo=?", (codigo,)
    ).fetchone())
    if not prod:
        con.close()
        raise HTTPException(status_code=404, detail='Producto no encontrado')
    con.execute("""
        UPDATE inventario_interno
        SET producto_nombre=?, costo_usd=?, ultima_actualizacion=?
        WHERE producto_codigo=?
    """, (body.producto_nombre, body.costo_usd,
          datetime.now(timezone.utc).isoformat(), codigo))
    con.commit()
    con.close()
    return {'mensaje': 'Producto actualizado'}


@router.put('/{codigo}/ajustar')
def ajustar_stock(codigo: str, body: AjusteInventario,
                  user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    prod = row_to_dict(con.execute(
        "SELECT * FROM inventario_interno WHERE producto_codigo=?", (codigo,)
    ).fetchone())
    if not prod:
        con.close()
        raise HTTPException(status_code=404, detail='Producto no encontrado')
    nuevo = max(0.0, float(prod['stock_actual'] or 0) + body.cantidad_delta)
    con.execute("""
        UPDATE inventario_interno
        SET stock_actual=?, ultima_actualizacion=?
        WHERE producto_codigo=?
    """, (nuevo, datetime.now(timezone.utc).isoformat(), codigo))
    con.commit()
    con.close()
    return {'mensaje': 'Stock ajustado', 'stock_nuevo': nuevo}


@router.delete('/{codigo}')
def eliminar_producto(codigo: str,
                      user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    con.execute(
        "DELETE FROM inventario_interno WHERE producto_codigo=?", (codigo,)
    )
    con.commit()
    con.close()
    return {'mensaje': 'Producto eliminado'}
