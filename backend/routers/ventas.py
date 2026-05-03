from fastapi import APIRouter, HTTPException, Depends
from routers.auth import get_current_user, require_roles
from models.schemas import rows_to_list
from database import get_con
from odoo_client import OdooClient

router = APIRouter(prefix='/odoo', tags=['odoo'])

_odoo_instance = None


def get_odoo() -> OdooClient:
    global _odoo_instance
    # Intentar reutilizar instancia existente; si falla, reconectar
    if _odoo_instance is not None:
        try:
            _odoo_instance.call('res.lang', 'search_count', [[]])  # ping liviano
        except Exception:
            _odoo_instance = None  # forzar reconexión
    if _odoo_instance is None:
        try:
            _odoo_instance = OdooClient()
        except Exception as e:
            raise HTTPException(status_code=503,
                                detail=f'No se puede conectar a Odoo: {e}')
    return _odoo_instance


@router.get('/ventas')
def listar_ventas(solo_confirmadas: bool = True,
                  extendidas: bool = True,
                  user=Depends(get_current_user)):
    """Ventas de Odoo. Con extendidas=True incluye estado de entrega y factura."""
    odoo = get_odoo()
    try:
        if extendidas:
            ventas = odoo.get_ventas_extendidas(solo_confirmadas)
        else:
            ventas = odoo.get_ventas(solo_confirmadas)
        if user['rol'] == 'vendedor':
            nombre = user['nombre'].lower()
            ventas = [v for v in ventas
                      if nombre in (v.get('user_id') or ['', ''])[1].lower()]
        return ventas
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/ventas/{order_name}/lineas')
def lineas_venta(order_name: str, user=Depends(get_current_user)):
    odoo = get_odoo()
    try:
        ordenes = odoo.get_venta_por_nombre(order_name)
        if not ordenes:
            raise HTTPException(status_code=404, detail='Orden no encontrada')
        return odoo.get_lineas_venta(ordenes[0]['id'])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/clientes/buscar')
def buscar_clientes(q: str = '', user=Depends(get_current_user)):
    """Autocompletado de clientes por nombre."""
    if len(q) < 2:
        return []
    odoo = get_odoo()
    try:
        return odoo.buscar_clientes(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/clientes/{partner_id}/ordenes-pendientes')
def ordenes_pendientes_cliente(partner_id: int, user=Depends(get_current_user)):
    """Órdenes de un cliente que no están completamente pagadas."""
    odoo = get_odoo()
    try:
        ordenes = odoo.get_ordenes_pendientes_cliente(partner_id)
        # Agregar cuánto ya está pagado en nuestro sistema
        con = get_con()
        for o in ordenes:
            row = con.execute("""
                SELECT COALESCE(SUM(equivalente_usd),0) as pagado
                FROM pagos
                WHERE odoo_order_name=?
                  AND estado IN ('recibido','enviado_sheets','enviado_odoo','confirmado_odoo')
            """, (o['name'],)).fetchone()
            o['pagado_sistema'] = row['pagado'] if row else 0
            o['saldo'] = (o.get('amount_total') or 0) - o['pagado_sistema']
        con.close()
        return ordenes
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/clientes/{partner_id}')
def get_cliente(partner_id: int, user=Depends(get_current_user)):
    odoo = get_odoo()
    try:
        return odoo.get_cliente(partner_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/pagos-clientes')
def pagos_clientes_odoo(limite: int = 200,
                        user=Depends(require_roles('gerente', 'admin'))):
    """Pagos de clientes en Odoo, excluyendo los ya importados al sistema."""
    odoo = get_odoo()
    try:
        pagos_odoo = odoo.get_pagos_odoo_clientes(limite)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Excluir los que ya están en nuestro sistema (importados O enviados desde aquí)
    con = get_con()
    importados = {r['odoo_payment_id'] for r in rows_to_list(
        con.execute("SELECT odoo_payment_id FROM pagos_odoo_importados").fetchall()
    )}
    enviados = {r['odoo_payment_id'] for r in rows_to_list(
        con.execute(
            "SELECT odoo_payment_id FROM pagos WHERE odoo_payment_id IS NOT NULL"
        ).fetchall()
    )}
    ya_registrados = importados | enviados
    con.close()

    nuevos = [p for p in pagos_odoo if p['id'] not in ya_registrados]
    return {'total_odoo': len(pagos_odoo), 'nuevos': len(nuevos), 'pagos': nuevos}


@router.post('/pagos-clientes/{odoo_payment_id}/importar')
def importar_pago_odoo(odoo_payment_id: int,
                       odoo_order_name: str = None,
                       user=Depends(require_roles('gerente', 'admin'))):
    """Importa un pago de Odoo al sistema y opcionalmente lo asocia a una orden."""
    odoo = get_odoo()
    try:
        pago_data = odoo.get_pago(odoo_payment_id)
        if not pago_data:
            raise HTTPException(status_code=404, detail='Pago no encontrado en Odoo')
        p = pago_data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    con = get_con()
    # Verificar que no esté ya importado
    exists = con.execute("SELECT 1 FROM pagos_odoo_importados WHERE odoo_payment_id=?",
                         (odoo_payment_id,)).fetchone()
    if exists:
        con.close()
        raise HTTPException(status_code=400, detail='Este pago ya fue importado')

    from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy
    from datetime import date
    tasa_bcv = tasa_bcv_hoy()
    tasa_custom = tasa_custom_hoy()

    cur = con.execute("""
        INSERT INTO pagos
            (odoo_order_name, vendedor_id, monto, moneda, metodo,
             tasa_bcv, tasa_custom, equivalente_usd, equivalente_ves,
             referencia, estado, odoo_payment_id, fecha_pago)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        odoo_order_name,
        user['id'],
        p.get('amount', 0),
        p.get('currency_id', [None, 'USD'])[1] if p.get('currency_id') else 'USD',
        'odoo_importado',
        tasa_bcv, tasa_custom,
        p.get('amount', 0),  # asumimos USD si viene de Odoo
        (p.get('amount', 0) * tasa_bcv) if tasa_bcv else None,
        p.get('memo', ''),
        'confirmado_odoo',  # ya está posted en Odoo → confirmado directamente
        odoo_payment_id,
        p.get('date', date.today().isoformat()),
    ))
    pago_id = cur.lastrowid
    con.execute("INSERT INTO pagos_odoo_importados(odoo_payment_id) VALUES(?)",
                (odoo_payment_id,))
    con.commit()
    con.close()
    return {'id': pago_id, 'mensaje': 'Pago importado correctamente'}


@router.get('/pagos-proveedores')
def pagos_proveedores_odoo(fecha_desde: str = None, fecha_hasta: str = None,
                           limite: int = 300,
                           user=Depends(require_roles('gerente', 'admin'))):
    """Pagos a proveedores en Odoo (outbound), excluyendo los ya importados al sistema."""
    odoo = get_odoo()
    try:
        pagos_odoo = odoo.get_pagos_proveedor(fecha_desde, fecha_hasta, limite)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    con = get_con()
    # Excluir los ya vinculados al maestro de operaciones
    importados = {r['odoo_payment_id'] for r in rows_to_list(
        con.execute(
            "SELECT odoo_payment_id FROM maestro_operaciones "
            "WHERE odoo_payment_id IS NOT NULL AND tipo='egreso'"
        ).fetchall()
    )}
    con.close()

    nuevos = [p for p in pagos_odoo if p['id'] not in importados]
    return {
        'total_odoo': len(pagos_odoo),
        'nuevos': len(nuevos),
        'pagos': nuevos,
    }


@router.post('/pagos-proveedores/{odoo_payment_id}/importar')
def importar_pago_proveedor(odoo_payment_id: int,
                             user=Depends(require_roles('gerente', 'admin'))):
    """Importa un pago a proveedor de Odoo como egreso en el maestro de operaciones."""
    odoo = get_odoo()
    try:
        data = odoo.get_pagos_proveedor()
        p = next((x for x in data if x['id'] == odoo_payment_id), None)
        if not p:
            raise HTTPException(status_code=404, detail='Pago no encontrado en Odoo')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    con = get_con()
    existe = con.execute(
        "SELECT id FROM maestro_operaciones WHERE odoo_payment_id=? AND tipo='egreso'",
        (odoo_payment_id,)
    ).fetchone()
    if existe:
        con.close()
        raise HTTPException(status_code=400, detail='Este pago ya fue importado')

    from services.tasas_cambio import tasa_bcv_hoy
    tasa = tasa_bcv_hoy() or 1
    monto = float(p.get('amount') or 0)
    moneda = (p.get('currency_id') or [None, 'USD'])[1] if p.get('currency_id') else 'USD'
    monto_usd = monto if moneda == 'USD' else round(monto / tasa, 4)
    journal = p.get('journal_id') or [None, '']
    partner = p.get('partner_id') or [None, '']

    con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, nro_documento, monto, moneda, tipo, categoria, descripcion,
             tasa_bcv, monto_usd_bcv, monto_real_usd, origen,
             odoo_payment_id, odoo_journal_id, odoo_partner_id, odoo_ref,
             journal_nombre, creado_por)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        (p.get('date') or '')[:10],
        p.get('name', ''),
        monto, moneda, 'egreso', 'Compra', partner[1] if partner[1] else '',
        tasa, monto_usd, monto_usd, 'odoo_gasto',
        odoo_payment_id,
        journal[0] if journal[0] else None,
        partner[0] if partner[0] else None,
        p.get('memo', ''),
        journal[1] if len(journal) > 1 else '',
        user['id'],
    ))
    con.commit()
    con.close()
    return {'mensaje': 'Pago importado correctamente al maestro de operaciones'}


@router.get('/journals')
def listar_journals(user=Depends(get_current_user)):
    odoo = get_odoo()
    try:
        return odoo.get_journals()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/productos')
def listar_productos_odoo(limite: int = 500, user=Depends(get_current_user)):
    """Productos activos de Odoo para usar en listas de precios y promociones."""
    odoo = get_odoo()
    try:
        return odoo.get_productos_odoo(limite)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/clientes')
def listar_clientes_odoo(limite: int = 500, user=Depends(get_current_user)):
    """Clientes de Odoo con conteo de órdenes."""
    odoo = get_odoo()
    try:
        return odoo.get_clientes_odoo(limite)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/entregas')
def get_entregas(user=Depends(get_current_user)):
    odoo = get_odoo()
    return odoo.get_entregas() or []


@router.get('/status')
def odoo_status():
    try:
        odoo = OdooClient()
        return {'status': 'ok', 'uid': odoo.uid}
    except Exception as e:
        return {'status': 'error', 'detalle': str(e)}
