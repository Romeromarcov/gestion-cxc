from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.schemas import rows_to_list, row_to_dict

router = APIRouter(prefix='/replicas', tags=['replicas'])


def _get_config(con) -> dict:
    rows = con.execute("SELECT clave, valor FROM config_app").fetchall()
    return {r['clave']: r['valor'] for r in rows}


def _sync_orden(odoo, con, order_name: str, cfg: dict) -> dict:
    """Crea o actualiza la réplica de una orden. Retorna dict con estado y detalles.

    Lógica:
    - Si la orden usa Lista USD directamente → no se crea réplica (estado='skip_usd')
    - Si usa Lista VES → crear réplica con precios de Lista USD y calcular sub/tax/total
    - Si falta precio en Lista USD → estado='error'
    """
    pl_ves_nombre = cfg.get('pricelist_ves_nombre', 'Precio USD Pago VES')
    pl_usd_nombre = cfg.get('pricelist_usd_nombre', 'Lista USD')

    # Obtener orden de Odoo con pricelist y moneda
    ordenes = odoo.get_venta_por_nombre(order_name)
    if not ordenes:
        return {'estado': 'error', 'error': f'Orden {order_name} no encontrada en Odoo'}

    orden = ordenes[0]
    pricelist_name = ''
    if orden.get('pricelist_id'):
        pricelist_name = orden['pricelist_id'][1] if isinstance(orden['pricelist_id'], list) else ''
    currency = ''
    if orden.get('currency_id'):
        currency = orden['currency_id'][1] if isinstance(orden['currency_id'], list) else ''

    # Validación: si la orden ya usa la Lista USD no se necesita réplica
    if pricelist_name == pl_usd_nombre:
        return {'estado': 'skip_usd', 'error': None}

    # Solo se replica si viene de la Lista VES
    if pricelist_name != pl_ves_nombre:
        return {'estado': 'skip', 'error': None}

    # Obtener pricelist VES e USD en Odoo
    pl_ves = odoo.get_pricelist_by_name(pl_ves_nombre)
    pl_usd = odoo.get_pricelist_by_name(pl_usd_nombre)
    if not pl_usd:
        return {'estado': 'error',
                'error': f'Lista de precios "{pl_usd_nombre}" no encontrada en Odoo'}

    # Líneas de la orden (incluye price_subtotal, price_tax, price_total)
    lineas_odoo = odoo.get_lineas_venta(orden['id'])
    if not lineas_odoo:
        return {'estado': 'error', 'error': 'La orden no tiene líneas'}

    # product.product IDs para consultar precios
    product_ids = [l['product_id'][0] for l in lineas_odoo if l.get('product_id')]

    precios_ves = {}
    if pl_ves:
        precios_ves = odoo.get_precios_lista(pl_ves['id'], product_ids)

    precios_usd = odoo.get_precios_lista(pl_usd['id'], product_ids)

    # Verificar que todos los productos tengan precio USD
    faltantes = [pid for pid in product_ids if not precios_usd.get(pid)]
    if faltantes:
        prods_sin_precio = [
            l['product_id'][1] for l in lineas_odoo
            if l.get('product_id') and l['product_id'][0] in faltantes
        ]
        return {'estado': 'error',
                'error': f'Sin precio en "{pl_usd_nombre}": {", ".join(prods_sin_precio)}'}

    # Calcular subtotales, impuestos y totales por línea
    lineas_replica = []
    sum_sub_ves = 0.0
    sum_tax_ves = 0.0
    sum_sub_usd = 0.0
    sum_tax_usd = 0.0

    for l in lineas_odoo:
        if not l.get('product_id'):
            continue
        pid = l['product_id'][0]
        qty = float(l.get('product_uom_qty', 1) or 1)

        # Tasa impositiva efectiva desde la línea original de Odoo
        price_subtotal_odoo = float(l.get('price_subtotal', 0) or 0)
        price_total_odoo    = float(l.get('price_total', 0) or 0)
        if price_subtotal_odoo > 0:
            tax_rate = (price_total_odoo - price_subtotal_odoo) / price_subtotal_odoo
        else:
            tax_rate = 0.0

        p_ves = float(precios_ves.get(pid) or 0)
        p_usd = float(precios_usd.get(pid) or 0)

        sub_ves  = p_ves * qty
        tax_ves  = sub_ves * tax_rate
        tot_ves  = sub_ves + tax_ves

        sub_usd  = p_usd * qty
        tax_usd  = sub_usd * tax_rate
        tot_usd  = sub_usd + tax_usd

        sum_sub_ves += sub_ves
        sum_tax_ves += tax_ves
        sum_sub_usd += sub_usd
        sum_tax_usd += tax_usd

        lineas_replica.append({
            'odoo_line_id':    l['id'],
            'producto_ref':    l.get('default_code', ''),
            'producto_nombre': l['product_id'][1] if l.get('product_id') else '',
            'cantidad':        qty,
            'tax_rate':        tax_rate,
            'precio_lista_ves': p_ves,
            'subtotal_ves':    sub_ves,
            'tax_ves':         tax_ves,
            'total_ves':       tot_ves,
            'precio_lista_usd': p_usd,
            'subtotal_usd':    sub_usd,
            'tax_usd':         tax_usd,
            'total_usd':       tot_usd,
        })

    total_lista_ves = sum_sub_ves + sum_tax_ves
    total_lista_usd = sum_sub_usd + sum_tax_usd

    # Upsert réplica
    ahora = datetime.utcnow().isoformat()
    existing = con.execute(
        "SELECT id FROM ordenes_replica WHERE odoo_order_name=?", (order_name,)
    ).fetchone()

    if existing:
        replica_id = existing['id']
        con.execute("""
            UPDATE ordenes_replica
            SET moneda_orden=?,
                subtotal_lista_ves=?, tax_lista_ves=?, total_lista_ves=?,
                subtotal_lista_usd=?, tax_lista_usd=?, total_lista_usd=?,
                estado='activa', error_detalle=NULL, actualizado_en=?
            WHERE id=?
        """, (currency,
              sum_sub_ves, sum_tax_ves, total_lista_ves,
              sum_sub_usd, sum_tax_usd, total_lista_usd,
              ahora, replica_id))
        con.execute("DELETE FROM ordenes_replica_lineas WHERE replica_id=?", (replica_id,))
    else:
        cur = con.execute("""
            INSERT INTO ordenes_replica(odoo_order_name, moneda_orden,
                subtotal_lista_ves, tax_lista_ves, total_lista_ves,
                subtotal_lista_usd, tax_lista_usd, total_lista_usd,
                estado, actualizado_en)
            VALUES(?,?,?,?,?,?,?,?,'activa',?)
        """, (order_name, currency,
              sum_sub_ves, sum_tax_ves, total_lista_ves,
              sum_sub_usd, sum_tax_usd, total_lista_usd,
              ahora))
        replica_id = cur.lastrowid

    for lr in lineas_replica:
        con.execute("""
            INSERT INTO ordenes_replica_lineas
                (replica_id, odoo_line_id, producto_ref, producto_nombre, cantidad,
                 tax_rate,
                 precio_lista_ves, subtotal_ves, tax_ves, total_ves,
                 precio_lista_usd, subtotal_usd, tax_usd, total_usd)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (replica_id, lr['odoo_line_id'], lr['producto_ref'], lr['producto_nombre'],
              lr['cantidad'], lr['tax_rate'],
              lr['precio_lista_ves'], lr['subtotal_ves'], lr['tax_ves'], lr['total_ves'],
              lr['precio_lista_usd'], lr['subtotal_usd'], lr['tax_usd'], lr['total_usd']))

    return {
        'estado': 'activa', 'error': None,
        'subtotal_lista_ves': sum_sub_ves, 'tax_lista_ves': sum_tax_ves, 'total_lista_ves': total_lista_ves,
        'subtotal_lista_usd': sum_sub_usd, 'tax_lista_usd': sum_tax_usd, 'total_lista_usd': total_lista_usd,
    }


@router.post('/sync')
def sync_replicas(user=Depends(require_roles('gerente', 'admin'))):
    """Sincroniza réplicas para todas las órdenes con la lista VES configurada.
    - Órdenes con Lista USD directamente: no necesitan réplica (skip_usd).
    - Órdenes con Lista VES: crea/actualiza réplica con precios Lista USD.
    - Órdenes con factura publicada: desactiva réplica.
    """
    odoo = get_odoo()
    con = get_con()
    cfg = _get_config(con)
    pl_ves_nombre = cfg.get('pricelist_ves_nombre', 'Precio USD Pago VES')
    pl_usd_nombre = cfg.get('pricelist_usd_nombre', 'Lista USD')

    try:
        ventas = odoo.get_ventas()
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error consultando Odoo: {e}')

    creadas = 0
    errores = 0
    desactivadas = 0
    skip_usd = 0

    for v in ventas:
        order_name = v['name']
        pricelist_name = ''
        if v.get('pricelist_id'):
            pricelist_name = v['pricelist_id'][1] if isinstance(v['pricelist_id'], list) else ''

        # Órdenes que no son ni VES ni USD list → ignorar
        if pricelist_name not in (pl_ves_nombre, pl_usd_nombre):
            continue

        # Órdenes con Lista USD directamente → no necesitan réplica
        if pricelist_name == pl_usd_nombre:
            skip_usd += 1
            continue

        # Orden con Lista VES: si factura publicada → desactivar réplica
        if v.get('invoice_status') == 'invoiced':
            updated = con.execute("""
                UPDATE ordenes_replica SET estado='inactiva', actualizado_en=?
                WHERE odoo_order_name=? AND estado='activa'
            """, (datetime.utcnow().isoformat(), order_name)).rowcount
            if updated:
                desactivadas += updated
            continue

        result = _sync_orden(odoo, con, order_name, cfg)
        if result['estado'] == 'error':
            ahora = datetime.utcnow().isoformat()
            existing = con.execute(
                "SELECT id FROM ordenes_replica WHERE odoo_order_name=?", (order_name,)
            ).fetchone()
            if existing:
                con.execute("""
                    UPDATE ordenes_replica SET estado='error', error_detalle=?, actualizado_en=?
                    WHERE odoo_order_name=?
                """, (result['error'], ahora, order_name))
            else:
                con.execute("""
                    INSERT INTO ordenes_replica(odoo_order_name, estado, error_detalle, actualizado_en)
                    VALUES(?,?,?,?)
                """, (order_name, 'error', result['error'], ahora))
            errores += 1
        elif result['estado'] == 'activa':
            creadas += 1

    con.commit()
    con.close()
    return {
        'sincronizadas': creadas,
        'errores': errores,
        'desactivadas': desactivadas,
        'sin_replica_lista_usd': skip_usd,
    }


@router.get('')
def listar_replicas(estado: str = None, user=Depends(get_current_user)):
    con = get_con()
    if estado:
        rows = rows_to_list(con.execute(
            "SELECT * FROM ordenes_replica WHERE estado=? ORDER BY actualizado_en DESC",
            (estado,)
        ).fetchall())
    else:
        rows = rows_to_list(con.execute(
            "SELECT * FROM ordenes_replica ORDER BY actualizado_en DESC"
        ).fetchall())
    con.close()
    return rows


@router.get('/errores')
def listar_errores(user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM ordenes_replica WHERE estado='error' ORDER BY actualizado_en DESC"
    ).fetchall())
    con.close()
    return rows


@router.get('/{order_name}/lineas')
def lineas_replica(order_name: str, user=Depends(get_current_user)):
    con = get_con()
    replica = row_to_dict(con.execute(
        "SELECT * FROM ordenes_replica WHERE odoo_order_name=?", (order_name,)
    ).fetchone())
    if not replica:
        con.close()
        raise HTTPException(status_code=404, detail='Réplica no encontrada')
    lineas = rows_to_list(con.execute(
        "SELECT * FROM ordenes_replica_lineas WHERE replica_id=?", (replica['id'],)
    ).fetchall())
    con.close()
    return {'replica': replica, 'lineas': lineas}


@router.post('/{order_name}/reintentar')
def reintentar_replica(order_name: str, user=Depends(require_roles('gerente', 'admin'))):
    """Reintenta crear la réplica de una orden en estado 'error'."""
    odoo = get_odoo()
    con = get_con()
    cfg = _get_config(con)

    existing = con.execute(
        "SELECT id, estado FROM ordenes_replica WHERE odoo_order_name=?", (order_name,)
    ).fetchone()
    if not existing or existing['estado'] != 'error':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo se puede reintentar réplicas en estado error')

    result = _sync_orden(odoo, con, order_name, cfg)
    ahora = datetime.utcnow().isoformat()
    if result['estado'] == 'error':
        con.execute("""
            UPDATE ordenes_replica SET estado='error', error_detalle=?, actualizado_en=?
            WHERE odoo_order_name=?
        """, (result['error'], ahora, order_name))
        con.commit()
        con.close()
        raise HTTPException(status_code=422,
                            detail=f'Error al crear réplica: {result["error"]}')

    con.commit()
    con.close()
    return {
        'mensaje': 'Réplica creada exitosamente',
        'subtotal_lista_ves': result.get('subtotal_lista_ves'),
        'tax_lista_ves':      result.get('tax_lista_ves'),
        'total_lista_ves':    result.get('total_lista_ves'),
        'subtotal_lista_usd': result.get('subtotal_lista_usd'),
        'tax_lista_usd':      result.get('tax_lista_usd'),
        'total_lista_usd':    result.get('total_lista_usd'),
    }
