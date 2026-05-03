from fastapi import APIRouter, HTTPException, Depends
from database import get_con
from routers.auth import get_current_user, require_roles
from models.schemas import rows_to_list

router = APIRouter(prefix='/inventario-interno', tags=['inventario'])


@router.get('/productos-extra')
def listar_productos_extra(user=Depends(get_current_user)):
    """Campos extra locales (marca, categoría) enlazados a productos de Odoo."""
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM productos_extra ORDER BY producto_ref"
    ).fetchall())
    con.close()
    return rows


@router.put('/productos-extra')
def upsert_producto_extra(body: dict, user=Depends(require_roles('admin', 'gerente'))):
    """Guarda o actualiza campos extra de un producto de Odoo:
    {producto_ref, marca, categoria_local, datos_extra}"""
    ref = body.get('producto_ref')
    if not ref:
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
