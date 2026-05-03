from fastapi import APIRouter, HTTPException, Depends
from database import get_con
from routers.auth import get_current_user, require_roles
from models.operaciones import ListaPrecioCreate, ItemListaPrecio, ProductoExtraUpdate
from models.schemas import row_to_dict, rows_to_list

router = APIRouter(prefix='/precios', tags=['precios'])


def precio_con_lista(odoo_line: dict, lista_id: int,
                     umbral_excluir: float = None) -> float | None:
    """Retorna el precio de lista para una línea de venta Odoo.
    Retorna None si el producto tiene descuento alto o no está en la lista."""
    if umbral_excluir is not None:
        if (odoo_line.get('discount') or 0) >= umbral_excluir:
            return None
    con = get_con()
    ref = odoo_line.get('default_code') or ''
    item = row_to_dict(con.execute("""
        SELECT lpi.precio FROM listas_precios_items lpi
        WHERE lpi.lista_id=? AND lpi.producto_ref=?
    """, (lista_id, ref)).fetchone())
    con.close()
    return item['precio'] if item else None


# ── LISTAS DE PRECIOS ─────────────────────────────────────────────────────────

@router.get('')
def listar_listas(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM listas_precios ORDER BY nombre"
    ).fetchall())
    con.close()
    return rows


@router.post('')
def crear_lista(body: ListaPrecioCreate,
                user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    cur = con.execute("""
        INSERT INTO listas_precios(nombre, moneda, activa, umbral_descuento_excluir)
        VALUES (?,?,?,?)
    """, (body.nombre, body.moneda, body.activa, body.umbral_descuento_excluir))
    con.commit()
    con.close()
    return {'id': cur.lastrowid, 'mensaje': 'Lista de precios creada'}


@router.get('/{lista_id}/items')
def listar_items(lista_id: int, user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM listas_precios_items WHERE lista_id=?", (lista_id,)
    ).fetchall())
    con.close()
    return rows


@router.post('/{lista_id}/items')
def agregar_item(lista_id: int, body: ItemListaPrecio,
                 user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    # Upsert: si ya existe el producto en esta lista, actualiza
    existing = con.execute("""
        SELECT id FROM listas_precios_items
        WHERE lista_id=? AND producto_ref=?
    """, (lista_id, body.producto_ref)).fetchone()
    if existing:
        con.execute("""
            UPDATE listas_precios_items SET precio=?
            WHERE lista_id=? AND producto_ref=?
        """, (body.precio, lista_id, body.producto_ref))
    else:
        con.execute("""
            INSERT INTO listas_precios_items(lista_id, producto_ref, precio)
            VALUES (?,?,?)
        """, (lista_id, body.producto_ref, body.precio))
    con.commit()
    con.close()
    return {'mensaje': 'Precio guardado'}


@router.delete('/{lista_id}/items/{item_id}')
def eliminar_item(lista_id: int, item_id: int,
                  user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    con.execute(
        "DELETE FROM listas_precios_items WHERE id=? AND lista_id=?",
        (item_id, lista_id)
    )
    con.commit()
    con.close()
    return {'mensaje': 'Item eliminado'}


# ── PRODUCTOS EXTRA ───────────────────────────────────────────────────────────

@router.get('/productos-extra')
def listar_productos_extra(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM productos_extra ORDER BY producto_ref"
    ).fetchall())
    con.close()
    return rows


@router.post('/productos-extra')
def upsert_producto_extra(body: ProductoExtraUpdate,
                          user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    con.execute("""
        INSERT INTO productos_extra(producto_ref, marca, categoria_local, datos_extra)
        VALUES (?,?,?,?)
        ON CONFLICT(producto_ref) DO UPDATE SET
            marca = excluded.marca,
            categoria_local = excluded.categoria_local,
            datos_extra = excluded.datos_extra
    """, (body.producto_ref, body.marca, body.categoria_local, body.datos_extra))
    con.commit()
    con.close()
    return {'mensaje': 'Producto extra guardado'}
