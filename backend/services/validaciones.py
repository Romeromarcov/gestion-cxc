"""Reglas de negocio para validar condiciones de notas de crédito y promociones."""
from database import get_con
from models.schemas import row_to_dict, rows_to_list


def get_limite_descuento(producto_ref: str, categoria: str = None) -> float:
    """Retorna el % máximo de descuento permitido para un producto/categoría."""
    con = get_con()
    # Prioridad: producto > categoría > default 100%
    row = con.execute("""SELECT limite_pct FROM limites_descuento
                         WHERE tipo='producto' AND referencia=?
                         LIMIT 1""", (producto_ref,)).fetchone()
    if row:
        con.close()
        return row['limite_pct']

    if categoria:
        row = con.execute("""SELECT limite_pct FROM limites_descuento
                             WHERE tipo='categoria' AND referencia=?
                             LIMIT 1""", (categoria,)).fetchone()
        if row:
            con.close()
            return row['limite_pct']

    con.close()
    return 100.0


def _get_config(clave: str, default='0'):
    con = get_con()
    r = con.execute("SELECT valor FROM config_nota_credito WHERE clave=?", (clave,)).fetchone()
    con.close()
    return r['valor'] if r else default


def validar_condiciones_nota(nota_id: int, odoo_client=None) -> dict:
    """Valida condiciones de una nota de crédito usando la configuración global."""
    con = get_con()
    nota = row_to_dict(con.execute(
        "SELECT * FROM notas_credito WHERE id=?", (nota_id,)
    ).fetchone())
    con.close()

    if not nota:
        return {'ok': False, 'error': 'Nota no encontrada'}

    errors = []

    # Leer configuración global
    requiere_pago = int(_get_config('requiere_pago', '0'))
    moneda_requerida = _get_config('moneda_pago', '') or None
    dias_max = int(_get_config('dias_max_entrega', '0'))

    # Condición 1: requiere pago registrado
    if requiere_pago or nota.get('condicion_pago_requerido'):
        con = get_con()
        pagos = rows_to_list(con.execute("""
            SELECT * FROM pagos
            WHERE odoo_order_name=? AND estado IN ('recibido','enviado_odoo','confirmado_odoo')
        """, (nota['odoo_order_name'],)).fetchall())
        con.close()

        if not pagos:
            errors.append('Se requiere un pago registrado para esta orden')
            return {'ok': False, 'errors': errors}

        # Condición 2: moneda del pago
        cond_moneda = moneda_requerida or nota.get('condicion_moneda')
        if cond_moneda:
            pagos_moneda = [p for p in pagos if p['moneda'] == cond_moneda]
            if not pagos_moneda:
                errors.append(f"El pago debe ser en {cond_moneda}")

        # Condición 3: días desde entrega
        cond_dias = dias_max or nota.get('condicion_dias_pago')
        if cond_dias and odoo_client:
            try:
                from datetime import datetime
                entregas = odoo_client.get_entrega_por_origen(nota['odoo_order_name'])
                if entregas and entregas[0].get('date_done'):
                    fecha_entrega = datetime.fromisoformat(
                        entregas[0]['date_done'].split(' ')[0]
                    )
                    for pago in pagos:
                        if pago.get('fecha_pago'):
                            fecha_pago = datetime.fromisoformat(pago['fecha_pago'])
                            dias = (fecha_pago - fecha_entrega).days
                            if dias > cond_dias:
                                errors.append(
                                    f"El pago fue registrado {dias} días después "
                                    f"de la entrega (máximo {cond_dias})"
                                )
            except Exception:
                pass  # No bloquear si Odoo no responde

    if errors:
        return {'ok': False, 'errors': errors}
    return {'ok': True}
