import json
from fastapi import APIRouter, HTTPException, Depends
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.operaciones import PromocionCreate, ValidarPromocionRequest
from models.schemas import row_to_dict, rows_to_list
from services.validaciones import get_limite_descuento

router = APIRouter(prefix='/promociones', tags=['promociones'])


@router.get('')
def listar_promociones(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM promociones ORDER BY creado_en DESC"
    ).fetchall())
    con.close()
    return rows


@router.post('')
def crear_promocion(body: PromocionCreate,
                    user=Depends(require_roles('admin'))):
    con = get_con()
    cur = con.execute("""
        INSERT INTO promociones
            (nombre, descripcion, activa, descuento_pct, producto_obsequio_ref,
             condicion_cliente_nuevo, condicion_min_productos, condicion_json)
        VALUES (?,?,?,?,?,?,?,?)
    """, (body.nombre, body.descripcion, body.activa, body.descuento_pct,
          body.producto_obsequio_ref, body.condicion_cliente_nuevo,
          body.condicion_min_productos, body.condicion_json))
    con.commit()
    con.close()
    return {'id': cur.lastrowid, 'mensaje': 'Promoción creada'}


@router.put('/{promo_id}')
def actualizar_promocion(promo_id: int, body: PromocionCreate,
                         user=Depends(require_roles('admin'))):
    con = get_con()
    con.execute("""
        UPDATE promociones
        SET nombre=?, descripcion=?, activa=?, descuento_pct=?,
            producto_obsequio_ref=?, condicion_cliente_nuevo=?,
            condicion_min_productos=?, condicion_json=?
        WHERE id=?
    """, (body.nombre, body.descripcion, body.activa, body.descuento_pct,
          body.producto_obsequio_ref, body.condicion_cliente_nuevo,
          body.condicion_min_productos, body.condicion_json, promo_id))
    con.commit()
    con.close()
    return {'mensaje': 'Promoción actualizada'}


@router.post('/validar')
def validar_promocion(body: ValidarPromocionRequest,
                      user=Depends(get_current_user)):
    odoo = get_odoo()
    con = get_con()
    promo = row_to_dict(con.execute(
        "SELECT * FROM promociones WHERE id=? AND activa=1",
        (body.promocion_id,)
    ).fetchone())
    con.close()

    if not promo:
        raise HTTPException(status_code=404,
                            detail='Promoción no encontrada o inactiva')

    # Cargar la orden de Odoo
    ordenes = odoo.get_venta_por_nombre(body.odoo_order_name)
    if not ordenes:
        raise HTTPException(status_code=404,
                            detail=f'Orden {body.odoo_order_name} no encontrada')
    orden = ordenes[0]
    lineas = odoo.get_lineas_venta(orden['id'])

    errores_condicion = []

    # Condición: cliente nuevo
    if promo['condicion_cliente_nuevo']:
        partner_id = orden['partner_id'][0]
        historial = odoo.historial_compras_cliente(partner_id)
        # Si tiene más de 1 orden (la actual podría ser la primera)
        if len(historial) > 1:
            errores_condicion.append('El cliente ya tiene compras anteriores (no es cliente nuevo)')

    # Condición: mínimo de productos
    if promo['condicion_min_productos'] and len(lineas) < promo['condicion_min_productos']:
        errores_condicion.append(
            f'La orden tiene {len(lineas)} productos; '
            f'se requieren al menos {promo["condicion_min_productos"]}'
        )

    # Condiciones adicionales en JSON
    if promo.get('condicion_json'):
        try:
            conds = json.loads(promo['condicion_json'])
            # Futuras condiciones extensibles aquí
        except Exception:
            pass

    if errores_condicion:
        raise HTTPException(status_code=422, detail={
            'mensaje': 'Condiciones no cumplidas',
            'errores': errores_condicion
        })

    # Crear nota de crédito automática con el producto de obsequio al 99%
    producto_ref = promo['producto_obsequio_ref']
    linea_obsequio = next(
        (l for l in lineas if l.get('default_code') == producto_ref), None
    )

    if not linea_obsequio and producto_ref:
        raise HTTPException(status_code=404,
                            detail=f'Producto {producto_ref} no encontrado en la orden')

    con = get_con()
    cur = con.execute("""
        INSERT INTO notas_credito
            (odoo_order_name, odoo_order_id, vendedor_id, estado)
        VALUES (?,?,?,?)
    """, (body.odoo_order_name, orden['id'], user['id'], 'enviada'))
    nota_id = cur.lastrowid

    if linea_obsequio:
        limite = get_limite_descuento(producto_ref or '')
        con.execute("""
            INSERT INTO notas_credito_lineas
                (nota_id, odoo_line_id, producto_id, producto_nombre,
                 producto_ref, precio_original, descuento_maximo, descuento_propuesto)
            VALUES (?,?,?,?,?,?,?,?)
        """, (nota_id, linea_obsequio['id'],
              linea_obsequio['product_id'][0] if linea_obsequio.get('product_id') else None,
              linea_obsequio['product_id'][1] if linea_obsequio.get('product_id') else '',
              producto_ref, linea_obsequio.get('price_unit', 0),
              limite, promo['descuento_pct']))

    con.commit()
    con.close()
    return {
        'mensaje': 'Condiciones cumplidas. Nota de crédito creada pendiente de aprobación.',
        'nota_id': nota_id,
        'promocion': promo['nombre'],
        'descuento_aplicado': promo['descuento_pct'],
        'producto': producto_ref,
    }
