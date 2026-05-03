import re
import xmlrpc.client
from config import ODOO_HOST, ODOO_DB, ODOO_USER, ODOO_API_KEY


class OdooClient:
    def __init__(self):
        base = f'https://{ODOO_HOST}'
        common = xmlrpc.client.ServerProxy(f'{base}/xmlrpc/2/common', allow_none=True)
        self.uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        self.models = xmlrpc.client.ServerProxy(f'{base}/xmlrpc/2/object', allow_none=True)
        if not self.uid:
            raise Exception('Autenticación Odoo fallida — verificar credenciales en .env')

    def call(self, model, method, domain=None, kwargs=None):
        return self.models.execute_kw(
            ODOO_DB, self.uid, ODOO_API_KEY,
            model, method, domain or [[]], kwargs or {}
        )

    # ── LECTURA ──────────────────────────────────────────────────────────────

    def get_ventas(self, solo_confirmadas=True):
        dom = [('state', 'in', ['sale', 'done'])] if solo_confirmadas else []
        return self.call('sale.order', 'search_read', [dom], {
            'fields': ['name', 'partner_id', 'amount_total', 'date_order',
                       'state', 'invoice_status', 'user_id'],
            'limit': 200
        })

    def get_venta_por_nombre(self, nombre):
        return self.call('sale.order', 'search_read',
                         [[['name', '=', nombre]]],
                         {'fields': ['id', 'name', 'partner_id', 'amount_total',
                                     'state', 'invoice_status', 'user_id'], 'limit': 1})

    def get_lineas_venta(self, orden_id):
        lineas = self.call('sale.order.line', 'search_read',
                           [[['order_id', '=', orden_id]]],
                           {'fields': ['id', 'product_id', 'product_uom_qty',
                                       'price_unit', 'discount', 'price_subtotal']})
        # Obtener default_code (ref. interna) desde product.product
        if lineas:
            product_ids = [l['product_id'][0] for l in lineas if l.get('product_id')]
            if product_ids:
                prods = self.call('product.product', 'read', [product_ids],
                                  {'fields': ['id', 'default_code']})
                prod_map = {p['id']: (p.get('default_code') or '') for p in prods}
                for l in lineas:
                    pid = l['product_id'][0] if l.get('product_id') else None
                    l['default_code'] = prod_map.get(pid, '') if pid else ''
        return lineas

    def get_entregas(self):
        return self.call('stock.picking', 'search_read',
                         [[['state', 'in', ['confirmed', 'assigned', 'done']]]],
                         {'fields': ['name', 'partner_id', 'scheduled_date', 'state',
                                     'date_done', 'origin', 'move_ids'], 'limit': 200})

    def get_entrega_por_origen(self, sale_order_name):
        return self.call('stock.picking', 'search_read',
                         [[['origin', '=', sale_order_name]]],
                         {'fields': ['id', 'name', 'state', 'date_done', 'partner_id']})

    def get_pagos(self, limite=100):
        return self.call('account.payment', 'search_read',
                         [[['state', '!=', 'cancelled']]],
                         {'fields': ['name', 'partner_id', 'amount', 'date', 'state',
                                     'payment_type', 'journal_id', 'memo', 'currency_id'],
                          'limit': limite})

    def get_journals(self):
        return self.call('account.journal', 'search_read',
                         [[['type', 'in', ['bank', 'cash']]]],
                         {'fields': ['id', 'name', 'type', 'currency_id']})

    def get_factura_borrador(self, sale_order_name):
        return self.call('account.move', 'search_read',
                         [[['invoice_origin', '=', sale_order_name], ['state', '=', 'draft']]],
                         {'fields': ['id', 'name', 'state', 'invoice_line_ids'], 'limit': 1})

    def get_lineas_factura(self, move_id):
        return self.call('account.move.line', 'search_read',
                         [[['move_id', '=', move_id], ['display_type', '=', 'product']]],
                         {'fields': ['id', 'product_id', 'quantity', 'price_unit',
                                     'discount', 'price_subtotal', 'sale_line_ids']})

    def get_cliente(self, partner_id):
        return self.call('res.partner', 'read', [[partner_id]],
                         {'fields': ['id', 'name', 'email', 'phone', 'customer_rank']})

    def historial_compras_cliente(self, partner_id):
        return self.call('sale.order', 'search_read',
                         [[['partner_id', '=', partner_id],
                           ['state', 'in', ['sale', 'done']]]],
                         {'fields': ['id', 'name', 'date_order'], 'limit': 5})

    def get_pago(self, pago_id):
        return self.call('account.payment', 'read', [[pago_id]],
                         {'fields': ['id', 'name', 'state', 'amount', 'date']})

    def buscar_clientes(self, query: str, limite: int = 15):
        """Busca clientes por nombre para el autocompletado."""
        return self.call('res.partner', 'search_read',
                         [[['name', 'ilike', query], ['customer_rank', '>', 0]]],
                         {'fields': ['id', 'name', 'email', 'phone'], 'limit': limite})

    def _term_days_map(self, term_ids):
        """Retorna dict {term_id: días} leyendo nb_days de las líneas del término.
        Si nb_days es 0 (término mal configurado en Odoo), extrae el número del nombre
        con regex (ej. "7 días" → 7, "30 Days" → 30, "Immediate Payment" → 0).
        """
        result = {}
        if not term_ids:
            return result
        try:
            terms = self.call('account.payment.term', 'read',
                              [list(term_ids)], {'fields': ['id', 'name', 'line_ids']}) or []
            all_line_ids = [lid for t in terms for lid in (t.get('line_ids') or [])]
            lines_by_id = {}
            if all_line_ids:
                raw_lines = self.call('account.payment.term.line', 'read',
                                      [all_line_ids],
                                      {'fields': ['id', 'payment_id', 'nb_days',
                                                  'delay_type', 'value']}) or []
                for l in raw_lines:
                    lines_by_id[l['id']] = l

            for t in terms:
                tid = t['id']
                term_line_ids = t.get('line_ids') or []
                # Tomar el máximo nb_days entre todas las líneas
                days = 0
                for lid in term_line_ids:
                    l = lines_by_id.get(lid, {})
                    try:
                        d = int(l.get('nb_days') or 0)
                    except Exception:
                        d = 0
                    days = max(days, d)

                # Fallback: si nb_days es 0, extraer número del nombre del término
                # Ej: "7 días" → 7,  "30 Days" → 30,  "Immediate Payment" → 0
                if days == 0:
                    m = re.search(r'\b(\d+)\b', t.get('name', ''))
                    if m:
                        days = int(m.group(1))

                result[tid] = days
        except Exception:
            pass
        return result

    def get_ventas_extendidas(self, solo_confirmadas=True):
        """Ventas con info de entrega, factura, moneda y términos de pago.
        Vencimiento = fecha de última entrega completada + días del término de pago.
        """
        dom = [('state', 'in', ['sale', 'done'])] if solo_confirmadas else []
        ventas = self.call('sale.order', 'search_read', [dom], {
            'fields': ['name', 'partner_id', 'amount_total', 'amount_untaxed',
                       'amount_tax', 'date_order',
                       'state', 'invoice_status', 'user_id',
                       'currency_id', 'payment_term_id'],
            'limit': 200
        })
        if not ventas:
            return []

        nombres = [v['name'] for v in ventas]

        # Días por término de pago (batch)
        term_ids = {v['payment_term_id'][0]
                    for v in ventas if v.get('payment_term_id')}
        term_days = self._term_days_map(term_ids)

        # Entregas — guardar la más reciente completada (done); si no hay done, la más reciente
        try:
            entregas = self.call('stock.picking', 'search_read',
                                 [[['origin', 'in', nombres],
                                   ['picking_type_code', '=', 'outgoing']]],
                                 {'fields': ['origin', 'state', 'date_done',
                                             'scheduled_date'], 'limit': 500})
            ent_map = {}
            for e in entregas:
                o = e['origin']
                if o not in ent_map:
                    ent_map[o] = e
                else:
                    prev = ent_map[o]
                    # Prioridad: done > cualquier otro estado
                    if e['state'] == 'done' and prev['state'] != 'done':
                        ent_map[o] = e
                    elif e['state'] == 'done' and prev['state'] == 'done':
                        # Ambas done → la más reciente
                        curr_d = (e.get('date_done') or '')[:10]
                        prev_d = (prev.get('date_done') or '')[:10]
                        if curr_d > prev_d:
                            ent_map[o] = e
        except Exception:
            ent_map = {}

        # Facturas — solo para mostrar número, estado y estado de pago (NO para calcular vencimiento)
        try:
            facturas = self.call('account.move', 'search_read',
                                 [[['invoice_origin', 'in', nombres],
                                   ['move_type', '=', 'out_invoice']]],
                                 {'fields': ['invoice_origin', 'name', 'state',
                                             'payment_state'], 'limit': 500})
            fac_map = {}
            for f in facturas:
                o = f['invoice_origin']
                # Preferir posted sobre draft para mostrar
                if o not in fac_map or f['state'] == 'posted':
                    fac_map[o] = f
        except Exception:
            fac_map = {}

        from datetime import date as _date, timedelta
        hoy = _date.today()

        for v in ventas:
            ent = ent_map.get(v['name'], {})
            v['entrega_estado'] = ent.get('state')
            v['entregado'] = ent.get('state') == 'done'

            # Fecha de la última entrega completada
            fecha_entrega = None
            if ent.get('state') == 'done' and ent.get('date_done'):
                try:
                    fecha_entrega = _date.fromisoformat(ent['date_done'][:10])
                except Exception:
                    pass
            v['entrega_fecha'] = fecha_entrega.isoformat() if fecha_entrega else None

            # Datos de factura (solo display)
            fac = fac_map.get(v['name'])
            v['factura_numero']      = fac.get('name') if fac else None
            v['factura_estado']      = fac.get('state') if fac else None
            v['factura_pago_estado'] = fac.get('payment_state') if fac else None

            # Término de pago: días numéricos
            term_entry = v.get('payment_term_id')
            tid = term_entry[0] if isinstance(term_entry, (list, tuple)) else None
            dias_plazo = term_days.get(tid, 0) if tid else 0

            # Vencimiento = última entrega completada + días del término
            if fecha_entrega is not None:
                v['factura_vencimiento'] = (fecha_entrega + timedelta(days=dias_plazo)).isoformat()
            else:
                v['factura_vencimiento'] = None

            # ¿Vencida? (independiente del estado de factura)
            venc = v['factura_vencimiento']
            ya_pagada = (v['factura_pago_estado'] or '') in ('paid', 'in_payment', 'reversed')

            if venc and not ya_pagada:
                try:
                    dias = (hoy - _date.fromisoformat(venc)).days
                    v['dias_vencida'] = max(0, dias)
                    v['vencida'] = dias > 0
                except Exception:
                    v['dias_vencida'] = 0
                    v['vencida'] = False
            else:
                v['dias_vencida'] = 0
                v['vencida'] = False

            # Moneda y términos de pago (para display)
            v['moneda'] = v.get('currency_id', [None, 'USD'])[1] if v.get('currency_id') else 'USD'
            v['termino_pago'] = v.get('payment_term_id', [None, ''])[1] if v.get('payment_term_id') else ''

        return ventas

    def get_pagos_odoo_clientes(self, limite: int = 200):
        """Pagos de clientes validados en Odoo 18 (in_process o paid = confirmados)."""
        campos_base = ['id', 'name', 'partner_id', 'amount', 'date',
                       'state', 'journal_id', 'memo', 'currency_id']

        # Odoo 18: draft=Borrador, in_process=En proceso, paid=Pagado/Conciliado
        pagos = self.call('account.payment', 'search_read',
                          [[['payment_type', '=', 'inbound'],
                            ['partner_type', '=', 'customer'],
                            ['state', 'in', ['in_process', 'paid']]]],
                          {'fields': campos_base, 'limit': limite})

        if not pagos:
            return pagos

        # Para cada pago buscar facturas asociadas mediante reconciliación
        for p in pagos:
            p['conciliado'] = p['state'] == 'paid'
            p['facturas_asociadas'] = []
            try:
                # Buscar apuntes contables del pago y sus conciliaciones
                move_lines = self.call('account.move.line', 'search_read',
                                       [[['payment_id', '=', p['id']],
                                         ['account_type', '=', 'asset_receivable']]],
                                       {'fields': ['id', 'matched_debit_ids',
                                                   'matched_credit_ids'], 'limit': 10})
                # Extraer IDs de facturas desde las conciliaciones parciales
                reconcile_ids = []
                for ml in (move_lines or []):
                    reconcile_ids += ml.get('matched_debit_ids', [])
                    reconcile_ids += ml.get('matched_credit_ids', [])

                if reconcile_ids:
                    partials = self.call('account.partial.reconcile', 'read',
                                        [reconcile_ids],
                                        {'fields': ['debit_move_id', 'credit_move_id']})
                    aml_ids = set()
                    for pr in (partials or []):
                        if pr.get('debit_move_id'):
                            aml_ids.add(pr['debit_move_id'][0])
                        if pr.get('credit_move_id'):
                            aml_ids.add(pr['credit_move_id'][0])

                    if aml_ids:
                        inv_lines = self.call('account.move.line', 'search_read',
                                              [[['id', 'in', list(aml_ids)],
                                                ['move_id.move_type', '=', 'out_invoice']]],
                                              {'fields': ['move_id'], 'limit': 10})
                        move_ids = list({l['move_id'][0] for l in inv_lines
                                         if l.get('move_id')})
                        if move_ids:
                            facturas = self.call('account.move', 'read',
                                                 [move_ids],
                                                 {'fields': ['id', 'name',
                                                             'invoice_origin',
                                                             'payment_state']})
                            p['facturas_asociadas'] = facturas or []
                            if facturas:
                                p['conciliado'] = True
            except Exception:
                pass

        return pagos

    def get_ordenes_pendientes_cliente(self, partner_id: int):
        """Órdenes confirmadas de un cliente con saldo pendiente.
        Incluye facturadas cuya factura no está totalmente pagada."""
        ordenes = self.call('sale.order', 'search_read',
                            [[['partner_id', '=', partner_id],
                              ['state', 'in', ['sale', 'done']]]],
                            {'fields': ['id', 'name', 'amount_total', 'date_order',
                                        'invoice_status', 'currency_id'], 'limit': 100})
        if not ordenes:
            return []
        nombres = [o['name'] for o in ordenes]
        try:
            facturas = self.call('account.move', 'search_read',
                                 [[['invoice_origin', 'in', nombres],
                                   ['move_type', '=', 'out_invoice'],
                                   ['state', '=', 'posted']]],
                                 {'fields': ['invoice_origin', 'payment_state'], 'limit': 200})
            fac_map = {f['invoice_origin']: f for f in facturas}
        except Exception:
            fac_map = {}
        resultado = []
        for o in ordenes:
            fac = fac_map.get(o['name'])
            pago_estado = fac['payment_state'] if fac else None
            if pago_estado in ('paid', 'in_payment', 'reversed'):
                continue
            o['pago_estado_factura'] = pago_estado
            o['moneda'] = o.get('currency_id', [None, 'USD'])[1] if o.get('currency_id') else 'USD'
            resultado.append(o)
        return resultado

    def get_productos_odoo(self, limite: int = 500):
        """Productos activos con precio, costo, referencia, categoría y volumen."""
        return self.call('product.template', 'search_read',
                         [[['sale_ok', '=', True], ['active', '=', True]]],
                         {'fields': ['id', 'name', 'default_code', 'list_price',
                                     'standard_price', 'categ_id', 'uom_id',
                                     'type', 'volume', 'weight'], 'limit': limite})

    def get_clientes_odoo(self, limite: int = 500):
        """Clientes con al menos una venta (confirmada, factura o cotización)."""
        socios = self.call('res.partner', 'search_read',
                           [[['customer_rank', '>', 0]]],
                           {'fields': ['id', 'name', 'email', 'phone',
                                       'create_date', 'sale_order_count'],
                            'limit': limite})
        return socios

    def get_factura_borrador_con_lineas(self, sale_order_name: str):
        """Retorna la factura borrador con sus líneas y la relación a sale.order.line."""
        facturas = self.call('account.move', 'search_read',
                             [[['invoice_origin', '=', sale_order_name],
                               ['state', '=', 'draft'],
                               ['move_type', '=', 'out_invoice']]],
                             {'fields': ['id', 'name', 'state', 'invoice_line_ids'], 'limit': 1})
        if not facturas:
            return None, []
        factura = facturas[0]
        lineas = self.call('account.move.line', 'search_read',
                           [[['move_id', '=', factura['id']], ['display_type', '=', 'product']]],
                           {'fields': ['id', 'product_id', 'quantity', 'price_unit',
                                       'discount', 'sale_line_ids']})
        return factura, lineas

    def get_pagos_proveedor(self, fecha_desde=None, fecha_hasta=None, limite=300):
        """Pagos de proveedor (outbound) validados en Odoo."""
        domain = [
            ['payment_type', '=', 'outbound'],
            ['partner_type', '=', 'supplier'],
            ['state', 'in', ['in_process', 'paid', 'posted']],
        ]
        if fecha_desde:
            domain.append(['date', '>=', fecha_desde])
        if fecha_hasta:
            domain.append(['date', '<=', fecha_hasta])
        return self.call('account.payment', 'search_read', domain, {
            'fields': ['id', 'name', 'partner_id', 'amount', 'date',
                       'state', 'journal_id', 'memo', 'currency_id'],
            'limit': limite,
        })

    def get_cuentas_gasto(self):
        """Cuentas contables de tipo gasto para mapeo de categorías."""
        return self.call('account.account', 'search_read',
                         [[['account_type', 'in',
                            ['expense', 'expense_direct_cost', 'expense_depreciation']]]],
                         {'fields': ['id', 'code', 'name', 'account_type'], 'limit': 500})

    def verificar_conciliacion_lote(self, payment_ids_inbound, payment_ids_outbound):
        """
        Retorna dict {payment_id: {'conciliado': bool, 'refs': [str]}}
        para lotes de pagos de cliente (inbound) y proveedor (outbound).
        """
        result = {}
        pares = [
            (payment_ids_inbound, 'inbound', 'reconciled_invoice_ids', 'out_invoice'),
            (payment_ids_outbound, 'outbound', 'reconciled_bill_ids',  'in_invoice'),
        ]
        for ids, ptype, reconcile_field, move_type in pares:
            if not ids:
                continue
            try:
                pagos = self.call('account.payment', 'read', [list(ids)],
                                  {'fields': ['id', 'state', reconcile_field]}) or []
                for p in pagos:
                    linked = p.get(reconcile_field) or []
                    # linked puede ser lista de IDs o lista de [id, name]
                    ids_facturas = [x[0] if isinstance(x, (list, tuple)) else x
                                    for x in linked]
                    refs = []
                    if ids_facturas:
                        try:
                            moves = self.call('account.move', 'read',
                                              [ids_facturas], {'fields': ['name']}) or []
                            refs = [m['name'] for m in moves]
                        except Exception:
                            refs = [str(i) for i in ids_facturas]
                    result[p['id']] = {
                        'conciliado': bool(ids_facturas),
                        'refs': refs,
                        'state': p.get('state'),
                    }
            except Exception:
                pass
        return result

    def buscar_proveedores(self, query: str, limite: int = 15):
        """Busca proveedores por nombre."""
        return self.call('res.partner', 'search_read',
                         [[['name', 'ilike', query], ['supplier_rank', '>', 0]]],
                         {'fields': ['id', 'name', 'email'], 'limit': limite})

    # ── ESCRITURA ─────────────────────────────────────────────────────────────

    def aplicar_descuento_lineas(self, orden_id, lineas_descuento):
        """lineas_descuento = [{'line_id': X, 'discount': 10.0}, ...]"""
        for l in lineas_descuento:
            # write(ids, vals) — ambos van en args posicionales
            self.call('sale.order.line', 'write',
                      [[l['line_id']], {'discount': l['discount']}])

    def aplicar_descuento_factura(self, move_id, lineas_descuento):
        for l in lineas_descuento:
            self.call('account.move.line', 'write',
                      [[l['line_id']], {'discount': l['discount']}])

    def crear_pago_borrador(self, partner_id, monto, fecha, journal_id, ref=''):
        vals = {
            'partner_id': partner_id,
            'amount': float(monto or 0),
            'date': fecha or '',
            'journal_id': journal_id,
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'memo': ref or '',   # Odoo 18: 'ref' → 'memo'
        }
        # XML-RPC no acepta None — eliminar claves vacías por seguridad
        vals = {k: v for k, v in vals.items() if v is not None}
        pago_id = self.call('account.payment', 'create', [vals])
        return pago_id

    def confirmar_pago(self, pago_id):
        self.call('account.payment', 'action_post', [[pago_id]])

    def crear_pago_proveedor(self, monto, fecha, journal_id,
                              ref='', partner_id=None, currency_id=None):
        """Crea y confirma un pago de proveedor (outbound) en Odoo."""
        vals = {
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'amount':       float(monto or 0),
            'date':         fecha or '',
            'journal_id':   journal_id,
            'memo':         ref or '',
        }
        if partner_id:
            vals['partner_id'] = partner_id
        if currency_id:
            vals['currency_id'] = currency_id
        # XML-RPC no acepta None
        vals = {k: v for k, v in vals.items() if v is not None and v != ''}
        pago_id = self.call('account.payment', 'create', [vals])
        if isinstance(pago_id, list):
            pago_id = pago_id[0]
        self.call('account.payment', 'action_post', [[pago_id]])
        return pago_id
