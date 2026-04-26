from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.operaciones import (CrearNotaRequest, ProponeDescuentosRequest,
                                 RechazarNotaRequest, LimiteDescuentoCreate)
from models.schemas import row_to_dict, rows_to_list
from services.validaciones import get_limite_descuento, validar_condiciones_nota

router = APIRouter(prefix='/notas-credito', tags=['descuentos'])


@router.post('/crear')
def crear_nota(body: CrearNotaRequest, user=Depends(get_current_user)):
    odoo = get_odoo()

    # Verificar que la orden existe en Odoo
    ordenes = odoo.get_venta_por_nombre(body.odoo_order_name)
    if not ordenes:
        raise HTTPException(status_code=404,
                            detail=f'Orden {body.odoo_order_name} no encontrada en Odoo')
    orden = ordenes[0]

    con = get_con()

    # Una sola nota activa por orden
    existente = con.execute("""
        SELECT id FROM notas_credito
        WHERE odoo_order_name=? AND estado NOT IN ('rechazada')
    """, (body.odoo_order_name,)).fetchone()
    if existente:
        con.close()
        raise HTTPException(status_code=409,
                            detail=f'Ya existe una nota de crédito activa para {body.odoo_order_name} (ID: {existente["id"]})')

    # Leer condiciones desde configuración global
    def cfg(clave):
        r = con.execute("SELECT valor FROM config_nota_credito WHERE clave=?", (clave,)).fetchone()
        return r['valor'] if r else None

    cur = con.execute("""
        INSERT INTO notas_credito
            (odoo_order_name, odoo_order_id, vendedor_id,
             condicion_pago_requerido, condicion_moneda, condicion_dias_pago)
        VALUES (?,?,?,?,?,?)
    """, (
        body.odoo_order_name,
        orden['id'],
        user['id'],
        int(cfg('requiere_pago') or 0),
        cfg('moneda_pago') or None,
        int(cfg('dias_max_entrega') or 0) or None,
    ))
    nota_id = cur.lastrowid

    # Cargar líneas desde Odoo
    lineas = odoo.get_lineas_venta(orden['id'])
    for l in lineas:
        ref = l.get('default_code') or ''
        limite = get_limite_descuento(ref)
        con.execute("""
            INSERT INTO notas_credito_lineas
                (nota_id, odoo_line_id, producto_id, producto_nombre,
                 producto_ref, precio_original, descuento_maximo)
            VALUES (?,?,?,?,?,?,?)
        """, (nota_id, l['id'],
              l['product_id'][0] if l.get('product_id') else None,
              l['product_id'][1] if l.get('product_id') else '',
              ref, l.get('price_unit', 0), limite))

    con.commit()
    con.close()
    return {'id': nota_id, 'mensaje': 'Nota de crédito creada',
            'lineas_cargadas': len(lineas)}


@router.get('/{nota_id}/lineas')
def lineas_nota(nota_id: int, user=Depends(get_current_user)):
    con = get_con()
    lineas = rows_to_list(con.execute(
        "SELECT * FROM notas_credito_lineas WHERE nota_id=?", (nota_id,)
    ).fetchall())
    con.close()
    return lineas


@router.put('/{nota_id}/proponer-descuentos')
def proponer_descuentos(nota_id: int, body: ProponeDescuentosRequest,
                        user=Depends(get_current_user)):
    con = get_con()
    nota = row_to_dict(con.execute(
        "SELECT * FROM notas_credito WHERE id=? AND vendedor_id=?",
        (nota_id, user['id'])
    ).fetchone())

    # Gerentes y admins pueden proponer también
    if not nota and user['rol'] in ('gerente', 'admin'):
        nota = row_to_dict(con.execute(
            "SELECT * FROM notas_credito WHERE id=?", (nota_id,)
        ).fetchone())

    if not nota:
        con.close()
        raise HTTPException(status_code=404, detail='Nota no encontrada o sin acceso')

    if nota['estado'] not in ('borrador', 'enviada'):
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'No se puede modificar una nota en estado {nota["estado"]}')

    errores = []
    for l in body.lineas:
        linea = row_to_dict(con.execute(
            "SELECT * FROM notas_credito_lineas WHERE id=? AND nota_id=?",
            (l.line_id, nota_id)
        ).fetchone())
        if not linea:
            errores.append(f'Línea {l.line_id} no pertenece a esta nota')
            continue
        if l.descuento_pct > (linea['descuento_maximo'] or 100):
            errores.append(
                f'Descuento {l.descuento_pct}% supera el límite '
                f'{linea["descuento_maximo"]}% para {linea["producto_nombre"]}'
            )

    if errores:
        con.close()
        raise HTTPException(status_code=422, detail={'errores': errores})

    for l in body.lineas:
        con.execute(
            "UPDATE notas_credito_lineas SET descuento_propuesto=? WHERE id=? AND nota_id=?",
            (l.descuento_pct, l.line_id, nota_id)
        )

    con.execute("UPDATE notas_credito SET estado='enviada' WHERE id=?", (nota_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Descuentos propuestos, nota enviada a aprobación'}


@router.get('/pendientes-aprobacion')
def pendientes(user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    notas = rows_to_list(con.execute("""
        SELECT nc.*, u.nombre as vendedor_nombre
        FROM notas_credito nc
        LEFT JOIN usuarios u ON u.id = nc.vendedor_id
        WHERE nc.estado = 'enviada'
        ORDER BY nc.creado_en DESC
    """).fetchall())
    con.close()
    return notas


@router.get('')
def listar_notas(user=Depends(get_current_user)):
    con = get_con()
    if user['rol'] == 'vendedor':
        notas = rows_to_list(con.execute(
            "SELECT * FROM notas_credito WHERE vendedor_id=? ORDER BY creado_en DESC",
            (user['id'],)
        ).fetchall())
    else:
        notas = rows_to_list(con.execute(
            "SELECT * FROM notas_credito ORDER BY creado_en DESC"
        ).fetchall())
    con.close()
    return notas


@router.post('/{nota_id}/aprobar')
def aprobar_nota(nota_id: int, user=Depends(require_roles('gerente', 'admin'))):
    odoo = get_odoo()
    con = get_con()
    nota = row_to_dict(con.execute(
        "SELECT * FROM notas_credito WHERE id=?", (nota_id,)
    ).fetchone())
    con.close()

    if not nota:
        raise HTTPException(status_code=404, detail='Nota no encontrada')
    if nota['estado'] != 'enviada':
        raise HTTPException(status_code=400,
                            detail=f'Estado actual: {nota["estado"]}')

    # Validar condiciones
    resultado = validar_condiciones_nota(nota_id, odoo)
    if not resultado['ok']:
        raise HTTPException(status_code=422, detail=resultado)

    # Obtener líneas aprobadas
    con = get_con()
    lineas = rows_to_list(con.execute(
        "SELECT * FROM notas_credito_lineas WHERE nota_id=?", (nota_id,)
    ).fetchall())
    con.close()

    lineas_con_descuento = [l for l in lineas if l.get('descuento_propuesto') is not None]

    # Aplicar en Odoo — sale.order.line
    if lineas_con_descuento:
        try:
            odoo.aplicar_descuento_lineas(nota['odoo_order_id'], [
                {'line_id': l['odoo_line_id'], 'discount': l['descuento_propuesto']}
                for l in lineas_con_descuento if l.get('odoo_line_id')
            ])
        except Exception as e:
            raise HTTPException(status_code=502,
                                detail=f'Error aplicando en Odoo: {e}')

    # Intentar aplicar también en factura borrador (solo si no tiene número asignado)
    aplicado_factura = 0
    try:
        factura, inv_lineas = odoo.get_factura_borrador_con_lineas(nota['odoo_order_name'])
        if factura and inv_lineas:
            # Construir mapa: sale_order_line_id → move_line_id
            sol_to_mol = {}
            for ml in inv_lineas:
                for sol_id in (ml.get('sale_line_ids') or []):
                    sol_to_mol[sol_id] = ml['id']

            descuentos_factura = []
            for l in lineas_con_descuento:
                if l.get('odoo_line_id') and l['odoo_line_id'] in sol_to_mol:
                    descuentos_factura.append({
                        'line_id': sol_to_mol[l['odoo_line_id']],
                        'discount': l['descuento_propuesto']
                    })

            if descuentos_factura:
                odoo.aplicar_descuento_factura(factura['id'], descuentos_factura)
                aplicado_factura = 1
    except Exception:
        pass  # No bloquear si la factura no existe o ya está confirmada

    con = get_con()
    con.execute("""
        UPDATE notas_credito
        SET estado='aprobada', aprobado_por=?, aprobado_en=?,
            aplicado_odoo=1, aplicado_factura=?
        WHERE id=?
    """, (user['id'], datetime.utcnow().isoformat(), aplicado_factura, nota_id))
    con.execute("""
        UPDATE notas_credito_lineas
        SET descuento_aprobado = descuento_propuesto
        WHERE nota_id=?
    """, (nota_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Nota aprobada y aplicada en Odoo',
            'aplicado_factura': bool(aplicado_factura)}


@router.post('/{nota_id}/rechazar')
def rechazar_nota(nota_id: int, body: RechazarNotaRequest,
                  user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    nota = row_to_dict(con.execute(
        "SELECT * FROM notas_credito WHERE id=?", (nota_id,)
    ).fetchone())
    if not nota or nota['estado'] != 'enviada':
        con.close()
        raise HTTPException(status_code=400, detail='Nota no encontrada o no está en revisión')
    con.execute("""
        UPDATE notas_credito SET estado='rechazada', rechazado_motivo=? WHERE id=?
    """, (body.motivo, nota_id))
    con.commit()
    con.close()
    return {'mensaje': 'Nota rechazada'}


@router.get('/limites-descuento')
def listar_limites(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute("SELECT * FROM limites_descuento").fetchall())
    con.close()
    return rows


@router.post('/limites-descuento')
def crear_limite(body: LimiteDescuentoCreate,
                 user=Depends(require_roles('admin'))):
    con = get_con()
    cur = con.execute("""
        INSERT INTO limites_descuento(tipo,referencia,limite_pct,creado_por)
        VALUES(?,?,?,?)
    """, (body.tipo, body.referencia, body.limite_pct, user['id']))
    con.commit()
    con.close()
    return {'id': cur.lastrowid, 'mensaje': 'Límite creado'}


@router.delete('/limites-descuento/{lid}')
def eliminar_limite(lid: int, user=Depends(require_roles('admin'))):
    con = get_con()
    con.execute("DELETE FROM limites_descuento WHERE id=?", (lid,))
    con.commit()
    con.close()
    return {'mensaje': 'Límite eliminado'}
