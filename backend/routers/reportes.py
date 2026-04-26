import io
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from routers.precios import precio_con_lista
from models.schemas import rows_to_list
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/reportes', tags=['reportes'])


@router.get('/cxc')
def reporte_cxc(lista_id: int = None,
                user=Depends(require_roles('gerente', 'admin'))):
    """Reporte de Cuentas por Cobrar: ventas Odoo + ventas internas."""
    odoo = get_odoo()
    tasa_bcv = tasa_bcv_hoy() or 0
    tasa_custom = tasa_custom_hoy() or 0

    # Ventas Odoo
    try:
        ventas_odoo = odoo.get_ventas()
    except Exception as e:
        ventas_odoo = []

    con = get_con()

    # Pagos confirmados/recibidos agrupados por orden
    pagos_rows = rows_to_list(con.execute("""
        SELECT odoo_order_name, SUM(equivalente_usd) as total_usd
        FROM pagos
        WHERE estado IN ('recibido','enviado_sheets','enviado_odoo','confirmado_odoo')
          AND odoo_order_name IS NOT NULL
        GROUP BY odoo_order_name
    """).fetchall())
    pagos_por_orden = {p['odoo_order_name']: p['total_usd'] for p in pagos_rows}

    resultado_odoo = []
    for v in ventas_odoo:
        total = v.get('amount_total', 0)
        pagado = pagos_por_orden.get(v['name'], 0) or 0
        saldo = total - pagado

        estado_cxc = 'pendiente'
        if pagado >= total:
            estado_cxc = 'pagado'
        elif pagado > 0:
            estado_cxc = 'parcialmente_pagado'

        resultado_odoo.append({
            'fuente': 'odoo',
            'codigo': v['name'],
            'cliente': v['partner_id'][1] if v.get('partner_id') else '',
            'total_usd': total,
            'pagado_usd': pagado,
            'saldo_usd': saldo,
            'saldo_ves_bcv': saldo * tasa_bcv,
            'saldo_ves_custom': saldo * tasa_custom if tasa_custom else None,
            'estado': v.get('state'),
            'estado_cxc': estado_cxc,
            'fecha': v.get('date_order', ''),
            'invoice_status': v.get('invoice_status'),
        })

    # Ventas internas
    ventas_int = rows_to_list(con.execute("""
        SELECT * FROM ventas_internas
        WHERE estado IN ('confirmada','pagada')
        ORDER BY creado_en DESC
    """).fetchall())

    pagos_int = rows_to_list(con.execute("""
        SELECT venta_interna_id, SUM(equivalente_usd) as total_usd
        FROM pagos
        WHERE estado IN ('recibido','enviado_sheets','enviado_odoo','confirmado_odoo')
          AND venta_interna_id IS NOT NULL
        GROUP BY venta_interna_id
    """).fetchall())
    pagos_por_vi = {p['venta_interna_id']: p['total_usd'] for p in pagos_int}

    resultado_int = []
    for v in ventas_int:
        total = v['total_usd'] or 0
        pagado = pagos_por_vi.get(v['id'], 0) or 0
        saldo = total - pagado

        estado_cxc = 'pendiente'
        if pagado >= total:
            estado_cxc = 'pagado'
        elif pagado > 0:
            estado_cxc = 'parcialmente_pagado'

        resultado_int.append({
            'fuente': 'interna',
            'codigo': v['codigo'],
            'cliente': v['cliente_nombre'],
            'total_usd': total,
            'pagado_usd': pagado,
            'saldo_usd': saldo,
            'saldo_ves_bcv': saldo * tasa_bcv,
            'saldo_ves_custom': saldo * tasa_custom if tasa_custom else None,
            'estado': v['estado'],
            'estado_cxc': estado_cxc,
            'fecha': v['creado_en'],
        })

    con.close()

    total_saldo = sum(r['saldo_usd'] for r in resultado_odoo + resultado_int)
    return {
        'tasa_bcv': tasa_bcv,
        'tasa_custom': tasa_custom,
        'total_saldo_usd': total_saldo,
        'total_saldo_ves_bcv': total_saldo * tasa_bcv,
        'ventas': resultado_odoo + resultado_int,
    }


@router.get('/ventas')
def reporte_ventas(vendedor_id: int = None, cliente: str = None,
                   fecha_desde: str = None, fecha_hasta: str = None,
                   marca: str = None, lista_id: int = None,
                   user=Depends(require_roles('gerente', 'admin'))):
    """Reporte de ventas extendido con campos extra (marca, categoría)."""
    odoo = get_odoo()
    try:
        ventas_odoo = odoo.get_ventas()
    except Exception:
        ventas_odoo = []

    con = get_con()
    extras = {r['producto_ref']: dict(r) for r in rows_to_list(
        con.execute("SELECT * FROM productos_extra").fetchall()
    )}

    lista_info = None
    if lista_id:
        lista_info = con.execute(
            "SELECT * FROM listas_precios WHERE id=?", (lista_id,)
        ).fetchone()

    resultado = []
    for v in ventas_odoo:
        # Filtros básicos
        if fecha_desde and v.get('date_order', '') < fecha_desde:
            continue
        if fecha_hasta and v.get('date_order', '') > fecha_hasta:
            continue
        if cliente and cliente.lower() not in (
            v['partner_id'][1].lower() if v.get('partner_id') else ''
        ):
            continue

        lineas = odoo.get_lineas_venta(v['id'])

        for l in lineas:
            ref = l.get('default_code') or ''
            extra = extras.get(ref, {})

            if marca and extra.get('marca', '').lower() != marca.lower():
                continue

            precio_lista = None
            if lista_info:
                precio_lista = precio_con_lista(
                    l, lista_id,
                    lista_info['umbral_descuento_excluir'] if lista_info else None
                )

            resultado.append({
                'orden': v['name'],
                'cliente': v['partner_id'][1] if v.get('partner_id') else '',
                'fecha': v.get('date_order', ''),
                'producto_ref': ref,
                'producto': l['product_id'][1] if l.get('product_id') else '',
                'cantidad': l.get('product_uom_qty'),
                'precio_odoo': l.get('price_unit'),
                'descuento_pct': l.get('discount', 0),
                'subtotal': l.get('price_subtotal'),
                'marca': extra.get('marca'),
                'categoria_local': extra.get('categoria_local'),
                'precio_lista': precio_lista,
            })

    con.close()
    return resultado


@router.get('/ventas/exportar-excel')
def exportar_excel(user=Depends(require_roles('gerente', 'admin'))):
    """Exporta el reporte de ventas a Excel."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        raise HTTPException(status_code=500,
                            detail='openpyxl no instalado')

    odoo = get_odoo()
    try:
        ventas = odoo.get_ventas()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Ventas CxC'

    headers = ['Orden', 'Cliente', 'Fecha', 'Total USD', 'Estado', 'Facturación']
    header_fill = PatternFill('solid', fgColor='2E75B6')
    header_font = Font(bold=True, color='FFFFFF')

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')

    for row_idx, v in enumerate(ventas, 2):
        ws.cell(row=row_idx, column=1, value=v.get('name'))
        ws.cell(row=row_idx, column=2,
                value=v['partner_id'][1] if v.get('partner_id') else '')
        ws.cell(row=row_idx, column=3, value=v.get('date_order', ''))
        ws.cell(row=row_idx, column=4, value=v.get('amount_total', 0))
        ws.cell(row=row_idx, column=5, value=v.get('state'))
        ws.cell(row=row_idx, column=6, value=v.get('invoice_status'))

    # Ajustar anchos
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max(12, max_len + 2)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=ventas_cxc.xlsx'}
    )


@router.get('/resumen-dashboard')
def resumen_dashboard(user=Depends(require_roles('gerente', 'admin'))):
    """Datos para el dashboard principal."""
    con = get_con()
    total_propuestos = con.execute(
        "SELECT COUNT(*) FROM notas_credito WHERE estado='enviada'"
    ).fetchone()[0]
    total_pagos_pendientes = con.execute(
        "SELECT COUNT(*) FROM pagos WHERE estado='propuesto'"
    ).fetchone()[0]
    total_pagos_recibidos = con.execute(
        "SELECT COUNT(*) FROM pagos WHERE estado='recibido'"
    ).fetchone()[0]
    total_vi = con.execute(
        "SELECT COUNT(*) FROM ventas_internas WHERE estado='confirmada'"
    ).fetchone()[0]
    con.close()

    return {
        'aprobaciones_pendientes': total_propuestos,
        'pagos_pendientes_recepcion': total_pagos_pendientes,
        'pagos_listos_para_odoo': total_pagos_recibidos,
        'ventas_internas_activas': total_vi,
    }
