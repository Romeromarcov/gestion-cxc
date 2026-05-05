"""
Compras Odoo — vista de órdenes de compra y reporte CxP (Cuentas por Pagar).

Lee directamente de Odoo sin tabla local propia (solo referencia en maestro).
Permite:
  - Ver todas las órdenes de compra confirmadas.
  - Ver estado de facturación y pago de cada orden.
  - Reporte CxP: órdenes con saldo pendiente de pago.
  - Importar pago al Maestro cuando se paga una factura de proveedor.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import date
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo

router = APIRouter(prefix='/compras-odoo', tags=['compras-odoo'])


@router.get('')
def listar_compras(
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    pago_estado: Optional[str] = Query(None),  # pendiente | facturado | parcial | pagado
    proveedor: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    """Lista órdenes de compra de Odoo con estado de pago."""
    try:
        odoo = get_odoo()
        orders = odoo.get_ordenes_compra(fecha_desde, fecha_hasta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    # Filtros en memoria
    if pago_estado:
        orders = [o for o in orders if o.get('pago_estado') == pago_estado]
    if proveedor:
        orders = [o for o in orders
                  if proveedor.lower() in (o.get('proveedor') or '').lower()]
    return orders


@router.get('/cxp')
def reporte_cxp(
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    """
    Reporte de Cuentas por Pagar: órdenes de compra con saldo pendiente.
    Agrupa por proveedor con totales.
    """
    try:
        odoo = get_odoo()
        orders = odoo.get_ordenes_compra(fecha_desde, fecha_hasta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    # Solo con saldo pendiente
    pendientes = [o for o in orders if o.get('pago_estado') not in ('pagado',)
                  and o.get('monto_pendiente', 0) > 0]

    # Agrupar por proveedor
    por_proveedor = {}
    for o in pendientes:
        prov = o.get('proveedor') or 'Sin proveedor'
        prov_id = (o.get('partner_id') or [None])[0]
        if prov not in por_proveedor:
            por_proveedor[prov] = {
                'proveedor': prov,
                'proveedor_id': prov_id,
                'total_pendiente': 0,
                'ordenes': [],
            }
        por_proveedor[prov]['total_pendiente'] += o.get('monto_pendiente', 0)
        por_proveedor[prov]['ordenes'].append({
            'id': o['id'],
            'name': o['name'],
            'fecha': (o.get('date_order') or '')[:10],
            'total': o.get('amount_total', 0),
            'pendiente': o.get('monto_pendiente', 0),
            'moneda': o.get('moneda', 'USD'),
            'pago_estado': o.get('pago_estado'),
            'facturas': o.get('facturas', []),
        })

    resultado = sorted(por_proveedor.values(),
                       key=lambda x: x['total_pendiente'], reverse=True)
    total_cxp = sum(r['total_pendiente'] for r in resultado)

    return {
        'total_cxp': round(total_cxp, 2),
        'proveedores_count': len(resultado),
        'ordenes_count': len(pendientes),
        'detalle': resultado,
    }


@router.get('/resumen')
def resumen_compras(user=Depends(get_current_user)):
    """KPIs de compras del mes actual."""
    hoy = date.today()
    desde = hoy.strftime('%Y-%m-01')
    hasta = hoy.isoformat()
    try:
        odoo = get_odoo()
        orders = odoo.get_ordenes_compra(desde, hasta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    total = sum(o.get('amount_total', 0) for o in orders)
    pendiente = sum(o.get('monto_pendiente', 0) for o in orders)
    pagado = total - pendiente
    return {
        'total_compras': round(total, 2),
        'total_pagado': round(pagado, 2),
        'total_pendiente': round(pendiente, 2),
        'ordenes_count': len(orders),
        'ordenes_pendiente': sum(1 for o in orders if o.get('pago_estado') != 'pagado'),
    }
