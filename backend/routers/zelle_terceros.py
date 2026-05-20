"""
Zelle de Terceros — control de pagos Zelle recibidos en cuentas de proveedores.

Flujo:
  1. Registrar el pago Zelle (proveedor cuya cuenta lo recibió, monto, cliente, orden).
  2. Decidir acción:
     a) ABONAR → crea pago outbound en Odoo (reduce AP como si se pagara en USD),
                  registra egreso en maestro.
     b) SOLICITAR REINTEGRO → crea asiento contable: Déb Cxc-Proveedor / Créd Caja-USD
                               y opcionalmente asiento de comisión.
  3. Estado: pendiente → abonado | reintegro_pendiente | anulado
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime, timezone
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.schemas import rows_to_list, row_to_dict
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/zelle-terceros', tags=['zelle-terceros'])


def _get_or_404(con, zt_id: int) -> dict:
    row = row_to_dict(con.execute(
        "SELECT * FROM zelle_terceros WHERE id=?", (zt_id,)
    ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail='Registro Zelle no encontrado')
    return row


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get('')
def listar(
    estado: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    sin_proveedor: Optional[bool] = Query(None),
    user=Depends(get_current_user),
):
    """
    sin_proveedor=true → solo registros originados desde Pagos (metodo=zelle_tercero)
                         que aún no tienen proveedor asignado.
    sin_proveedor=false → solo los que ya tienen proveedor (flujo completo).
    """
    con = get_con()
    clauses, params = [], []
    if estado:        clauses.append("estado=?");          params.append(estado)
    if fecha_desde:   clauses.append("fecha>=?");           params.append(fecha_desde)
    if fecha_hasta:   clauses.append("fecha<=?");           params.append(fecha_hasta)
    if sin_proveedor is True:
        clauses.append("proveedor_id IS NULL")
    elif sin_proveedor is False:
        clauses.append("proveedor_id IS NOT NULL")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM zelle_terceros {where} ORDER BY fecha DESC, id DESC", params
    ).fetchall())
    con.close()
    return rows


@router.get('/{zt_id}')
def obtener(zt_id: int, user=Depends(get_current_user)):
    con = get_con()
    z = _get_or_404(con, zt_id)
    con.close()
    return z


@router.post('')
def crear(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    """
    Body:
    {
      "fecha": "2025-02-01",
      "descripcion": "Pago cliente vía Zelle Proveedor X",
      "proveedor_id": 45,            # partner_id Odoo del proveedor
      "proveedor_nombre": "Proveedor X, C.A.",
      "cliente_nombre": "Cliente ABC",
      "orden_cobrada": "S00123",     # orden de venta que se cobraba
      "monto_usd": 250.00,
      "referencia_zelle": "CONF-789012",
      "notas": ""
    }
    """
    reqs = ['fecha', 'proveedor_nombre', 'monto_usd']
    for f in reqs:
        if not body.get(f):
            raise HTTPException(status_code=400, detail=f'Campo requerido: {f}')

    ahora = datetime.now(timezone.utc).isoformat()
    con = get_con()
    cur = con.execute("""
        INSERT INTO zelle_terceros
            (fecha, descripcion, proveedor_id, proveedor_nombre,
             cliente_nombre, orden_cobrada, monto_usd, referencia_zelle,
             estado, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,'pendiente',?,?,?,?)
    """, (
        body['fecha'],
        body.get('descripcion') or '',
        int(body['proveedor_id']) if body.get('proveedor_id') else None,
        body['proveedor_nombre'],
        body.get('cliente_nombre') or '',
        body.get('orden_cobrada') or '',
        float(body['monto_usd']),
        body.get('referencia_zelle') or '',
        'pendiente',
        body.get('notas') or '',
        user['id'], ahora,
    ))
    zt_id = cur.lastrowid
    con.commit()
    z = _get_or_404(con, zt_id)
    con.close()
    return z


@router.put('/{zt_id}')
def actualizar(zt_id: int, body: dict,
               user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    z = _get_or_404(con, zt_id)
    if z['estado'] != 'pendiente':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo se pueden editar registros pendientes')
    campos = ['fecha', 'descripcion', 'proveedor_id', 'proveedor_nombre',
              'cliente_nombre', 'orden_cobrada', 'monto_usd',
              'referencia_zelle', 'notas']
    sets, params = [], []
    for f in campos:
        if f in body:
            sets.append(f"{f}=?"); params.append(body[f])
    if not sets:
        con.close()
        raise HTTPException(status_code=400, detail='Sin campos para actualizar')
    params.append(zt_id)
    con.execute(f"UPDATE zelle_terceros SET {', '.join(sets)} WHERE id=?", params)
    con.commit()
    z = _get_or_404(con, zt_id)
    con.close()
    return z


@router.delete('/{zt_id}')
def eliminar(zt_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    z = _get_or_404(con, zt_id)
    if z['estado'] != 'pendiente':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo se pueden eliminar registros pendientes')
    con.execute("DELETE FROM zelle_terceros WHERE id=?", (zt_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Eliminado'}


# ── ABONAR → reduce AP del proveedor en Odoo ──────────────────────────────────

@router.post('/{zt_id}/abonar')
def abonar(zt_id: int, body: dict,
           user=Depends(require_roles('gerente', 'admin'))):
    """
    Registra el Zelle como pago al proveedor (reduce AP en Odoo).
    Crea account.payment outbound hacia el proveedor.

    Body:
    {
      "journal_id": 12,             # diario de banco/caja USD
      "currency_id": 2,             # (opcional) ID moneda USD en Odoo
      "notas": ""
    }
    """
    con = get_con()
    z = _get_or_404(con, zt_id)
    if z['estado'] != 'pendiente':
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'No se puede abonar un registro en estado "{z["estado"]}"')

    journal_id = body.get('journal_id')
    if not journal_id:
        con.close()
        raise HTTPException(status_code=400, detail='journal_id es requerido')

    if not z.get('proveedor_id'):
        con.close()
        raise HTTPException(status_code=400,
                            detail='Se requiere proveedor_id (partner Odoo) para crear el pago')

    odoo = get_odoo()
    ref = (z.get('descripcion') or f"Zelle {z['proveedor_nombre']}") + \
          (f" | Ref: {z['referencia_zelle']}" if z.get('referencia_zelle') else '')

    try:
        pago_id = odoo.crear_pago_proveedor_usd(
            partner_id=int(z['proveedor_id']),
            monto=float(z['monto_usd']),
            fecha=z['fecha'],
            journal_id=int(journal_id),
            ref=ref,
            currency_id=body.get('currency_id'),
        )
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    # Registrar en maestro como egreso
    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    monto = float(z['monto_usd'])
    cur = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, odoo_payment_id, odoo_journal_id, odoo_partner_id,
             journal_nombre, estado)
        VALUES (?,?,?,'egreso','Zelle Terceros','Abono Proveedor',?,?,?,?,?,'zelle_terceros',?,?,?,?,'confirmado')
    """, (z['fecha'], monto, 'USD',
          ref, bcv, monto, custom, monto,
          pago_id, int(journal_id),
          z.get('proveedor_id'),
          z['proveedor_nombre']))
    maestro_id = cur.lastrowid

    con.execute("""
        UPDATE zelle_terceros
        SET estado='abonado', tipo_accion='abonar',
            odoo_payment_id=?, odoo_journal_id=?, maestro_op_id=?
        WHERE id=?
    """, (pago_id, int(journal_id), maestro_id, zt_id))
    con.commit()
    z = _get_or_404(con, zt_id)
    con.close()
    return {
        'mensaje': 'Pago al proveedor creado en Odoo y registrado en Maestro',
        'odoo_payment_id': pago_id,
        'maestro_op_id': maestro_id,
        'zelle': z
    }


# ── SOLICITAR REINTEGRO → crea CxC proveedor ──────────────────────────────────

@router.post('/{zt_id}/solicitar-reintegro')
def solicitar_reintegro(zt_id: int, body: dict,
                         user=Depends(require_roles('gerente', 'admin'))):
    """
    Registra una cuenta por cobrar al proveedor por el Zelle recibido.
    Opcionalmente resta comisión.

    Asiento:
      Débito:  Cuenta por cobrar proveedores / proveedor  (monto total)
      Crédito: Cuenta de caja/banco USD                    (monto total)
    Si hay comisión:
      Débito:  Cuenta de gasto comisión                    (monto_comision)
      Crédito: Cuenta por cobrar proveedores               (monto_comision)

    Body:
    {
      "odoo_cuenta_cobrar_id": 301,   # cuenta activo por cobrar (ej. 1.2.1 CxC Otros)
      "odoo_cuenta_caja_id": 42,      # cuenta de caja/banco donde "entró" el Zelle
      "odoo_journal_id": 7,           # diario contable para el asiento
      "comision_pct": 2.5,            # (opcional) % de comisión del proveedor
      "odoo_cuenta_comision_id": 520  # (opcional) cuenta de gasto de comisión
    }
    """
    con = get_con()
    z = _get_or_404(con, zt_id)
    if z['estado'] != 'pendiente':
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'No se puede procesar un registro en estado "{z["estado"]}"')

    cta_cobrar = body.get('odoo_cuenta_cobrar_id')
    cta_caja   = body.get('odoo_cuenta_caja_id')
    journal_id = body.get('odoo_journal_id')
    if not cta_cobrar or not cta_caja:
        con.close()
        raise HTTPException(status_code=400,
                            detail='Se requieren odoo_cuenta_cobrar_id y odoo_cuenta_caja_id')

    monto_total = float(z['monto_usd'])
    comision_pct = float(body.get('comision_pct') or 0)
    monto_comision = round(monto_total * comision_pct / 100, 4) if comision_pct else 0
    monto_reintegro = round(monto_total - monto_comision, 4)

    ref = (z.get('descripcion') or f"Reintegro Zelle {z['proveedor_nombre']}") + \
          (f" | Ref: {z['referencia_zelle']}" if z.get('referencia_zelle') else '')

    # Construir líneas del asiento
    partner_id = z.get('proveedor_id')
    lineas = [
        # Débito: CxC proveedor por el total
        {'account_id': int(cta_cobrar), 'name': ref,
         'debit': monto_total, 'credit': 0,
         **(({'partner_id': int(partner_id)} if partner_id else {}))},
        # Crédito: cuenta caja/banco
        {'account_id': int(cta_caja), 'name': ref,
         'debit': 0, 'credit': monto_total},
    ]
    cta_comision = body.get('odoo_cuenta_comision_id')
    if monto_comision and cta_comision:
        lineas += [
            # Débito: gasto comisión
            {'account_id': int(cta_comision),
             'name': f'Comisión Zelle ({comision_pct}%)',
             'debit': monto_comision, 'credit': 0},
            # Crédito: reduce CxC proveedor
            {'account_id': int(cta_cobrar),
             'name': f'Comisión Zelle ({comision_pct}%)',
             'debit': 0, 'credit': monto_comision,
             **(({'partner_id': int(partner_id)} if partner_id else {}))},
        ]

    odoo = get_odoo()
    try:
        move_id = odoo.crear_asiento_contable(
            fecha=z['fecha'],
            lineas=lineas,
            ref=ref,
            diario_id=journal_id,
        )
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    # Maestro: ingreso "pendiente de reintegro"
    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    cur = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, odoo_ref, journal_nombre, estado)
        VALUES (?,?,?,'ingreso','Zelle Terceros','Reintegro Pendiente',?,?,?,?,?,'zelle_terceros',?,?,'registrado')
    """, (z['fecha'], monto_total, 'USD',
          ref, bcv, monto_total, custom, monto_total,
          f"move/{move_id}",
          z['proveedor_nombre']))
    maestro_id = cur.lastrowid

    con.execute("""
        UPDATE zelle_terceros
        SET estado='reintegro_pendiente', tipo_accion='reintegro',
            odoo_move_id=?, comision_pct=?, monto_comision=?, monto_reintegro=?,
            odoo_cuenta_cobrar_id=?, odoo_cuenta_comision_id=?,
            odoo_journal_id=?, maestro_op_id=?
        WHERE id=?
    """, (move_id, comision_pct, monto_comision, monto_reintegro,
          int(cta_cobrar),
          int(cta_comision) if cta_comision else None,
          int(journal_id) if journal_id else None,
          maestro_id, zt_id))
    con.commit()
    z = _get_or_404(con, zt_id)
    con.close()
    return {
        'mensaje': 'Asiento de reintegro creado en Odoo',
        'odoo_move_id': move_id,
        'monto_reintegro': monto_reintegro,
        'monto_comision': monto_comision,
        'maestro_op_id': maestro_id,
        'zelle': z
    }


@router.post('/{zt_id}/marcar-reintegrado')
def marcar_reintegrado(zt_id: int, body: dict = None,
                        user=Depends(require_roles('gerente', 'admin'))):
    """Marca el reintegro como recibido."""
    body = body or {}
    con = get_con()
    z = _get_or_404(con, zt_id)
    if z['estado'] != 'reintegro_pendiente':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo registros en reintegro_pendiente pueden marcarse como reintegrados')
    con.execute("UPDATE zelle_terceros SET estado='reintegrado' WHERE id=?", (zt_id,))
    con.commit()
    z = _get_or_404(con, zt_id)
    con.close()
    return {'mensaje': 'Marcado como reintegrado', 'zelle': z}


# ── ASOCIAR PROVEEDOR (Zelle originado desde Pagos) ───────────────────────────

@router.post('/{zt_id}/asociar-proveedor')
def asociar_proveedor(zt_id: int, body: dict,
                       user=Depends(require_roles('gerente', 'admin'))):
    """
    Asocia un Zelle Tercero (originado desde Pagos, sin proveedor) al proveedor
    Odoo correspondiente. Opcionalmente registra el ingreso en efectivo en Maestro.

    Body:
    {
      "proveedor_id": 45,             # partner_id Odoo del proveedor
      "proveedor_nombre": "Inversiones XYZ",
      "tipo_accion": "abonar",        # o "reintegro" — solo orienta, no ejecuta aún
      "journal_efectivo_id": 7,       # diario de efectivo/caja para el ingreso
      "cliente_nombre": "Cliente ABC",
      "descripcion": "Cobro vía Zelle Tercero"
    }
    """
    con = get_con()
    z = _get_or_404(con, zt_id)
    if z['estado'] != 'pendiente':
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'No se puede modificar un registro en estado "{z["estado"]}"')
    if z.get('proveedor_id'):
        con.close()
        raise HTTPException(status_code=400,
                            detail='Este registro ya tiene proveedor asignado')

    proveedor_id   = body.get('proveedor_id')
    proveedor_nom  = body.get('proveedor_nombre', '')
    tipo_accion    = body.get('tipo_accion')          # orienta la siguiente acción, no ejecuta
    journal_ef_id  = body.get('journal_efectivo_id')
    cliente_nom    = body.get('cliente_nombre', z.get('cliente_nombre', ''))
    descripcion    = body.get('descripcion') or z.get('descripcion') or ''

    if not proveedor_id or not proveedor_nom:
        con.close()
        raise HTTPException(status_code=400,
                            detail='proveedor_id y proveedor_nombre son requeridos')

    con.execute("""
        UPDATE zelle_terceros
        SET proveedor_id=?, proveedor_nombre=?, tipo_accion=?,
            journal_efectivo_id=?, cliente_nombre=?, descripcion=?
        WHERE id=?
    """, (int(proveedor_id), proveedor_nom, tipo_accion,
          int(journal_ef_id) if journal_ef_id else None,
          cliente_nom, descripcion, zt_id))

    # Registrar el ingreso en Maestro (efectivo recibido de Zelle)
    # solo si se indica journal_efectivo_id
    maestro_id = None
    if journal_ef_id:
        bcv = tasa_bcv_hoy() or 1.0
        custom = tasa_custom_hoy() or bcv
        monto = float(z['monto_usd'])
        ref = descripcion or f"Ingreso Zelle Tercero — {proveedor_nom}"
        cur = con.execute("""
            INSERT INTO maestro_operaciones
                (fecha, monto, moneda, tipo, categoria, subcategoria,
                 descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
                 origen, odoo_journal_id, odoo_partner_id, journal_nombre, estado)
            VALUES (?,?,?,'ingreso','Zelle Terceros','Ingreso Efectivo',?,?,?,?,?,'zelle_terceros',?,?,?,'registrado')
        """, (z['fecha'], monto, 'USD',
              ref, bcv, monto, custom, monto,
              int(journal_ef_id), int(proveedor_id) if proveedor_id else None,
              proveedor_nom))
        maestro_id = cur.lastrowid
        if maestro_id:
            con.execute("UPDATE zelle_terceros SET maestro_op_id=? WHERE id=?",
                        (maestro_id, zt_id))

    con.commit()
    z = _get_or_404(con, zt_id)
    con.close()
    return {
        'mensaje': 'Proveedor asociado correctamente',
        'maestro_op_id': maestro_id,
        'zelle': z
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

@router.get('/helpers/journals')
def journals(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_journals()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/cuentas')
def cuentas(tipo: Optional[str] = Query(None), user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_cuentas(tipo)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/proveedores')
def buscar_proveedores(q: str = '', user=Depends(get_current_user)):
    if len(q) < 2:
        return []
    try:
        odoo = get_odoo()
        return odoo.buscar_proveedores(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
