"""
Pagos Fiscales — Alcaldía, INCES, Aseo Urbano, IVA, ISLR, etc.

Flujo:
  1. Registrar el pago con cuenta de gasto y banco/caja que paga
  2. Validar → crea egreso en maestro_operaciones
  3. Enviar a Odoo → crea asiento contable (account.move):
       Débito:  cuenta de gasto (ej. 6.3.1 Impuestos municipales)
       Crédito: cuenta de banco/caja (ej. 1.1.1.1 Banesco VES)
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.schemas import rows_to_list, row_to_dict
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/pagos-fiscales', tags=['pagos-fiscales'])

TIPOS_FISCAL = ('alcaldia', 'inces', 'aseo', 'iva', 'islr', 'seniat', 'sso', 'otro')


def _get_or_404(con, pf_id: int) -> dict:
    row = row_to_dict(con.execute(
        "SELECT * FROM pagos_fiscales WHERE id=?", (pf_id,)
    ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail='Pago fiscal no encontrado')
    return row


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get('')
def listar(
    estado: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    con = get_con()
    clauses, params = [], []
    if estado:   clauses.append("estado=?");       params.append(estado)
    if tipo:     clauses.append("tipo=?");          params.append(tipo)
    if fecha_desde: clauses.append("fecha>=?");    params.append(fecha_desde)
    if fecha_hasta: clauses.append("fecha<=?");    params.append(fecha_hasta)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM pagos_fiscales {where} ORDER BY fecha DESC, id DESC", params
    ).fetchall())
    con.close()
    return rows


@router.get('/tipos')
def tipos_disponibles(user=Depends(get_current_user)):
    return list(TIPOS_FISCAL)


@router.get('/{pf_id}')
def obtener(pf_id: int, user=Depends(get_current_user)):
    con = get_con()
    p = _get_or_404(con, pf_id)
    con.close()
    return p


@router.post('')
def crear(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    """
    Body:
    {
      "tipo": "alcaldia",           # requerido
      "descripcion": "Alcaldía enero 2025",  # requerido
      "monto": 150000.0,            # requerido
      "moneda": "VES",              # default VES
      "equivalente_usd": 3.90,      # opcional
      "fecha": "2025-01-31",        # requerido
      "referencia": "PLANILLA-001", # opcional
      "periodo": "2025-01",         # opcional
      "odoo_cuenta_gasto_id": 350,  # cuenta de gasto en Odoo
      "odoo_cuenta_gasto_codigo": "6.3.1",
      "odoo_cuenta_haber_id": 42,   # banco/caja que paga
      "odoo_cuenta_haber_codigo": "1.1.1.1",
      "odoo_journal_id": 7,         # diario contable
      "notas": ""
    }
    """
    reqs = ['tipo', 'descripcion', 'monto', 'fecha']
    for f in reqs:
        if not body.get(f):
            raise HTTPException(status_code=400, detail=f'Campo requerido: {f}')
    tipo = body['tipo'].lower()
    if tipo not in TIPOS_FISCAL:
        raise HTTPException(status_code=400,
                            detail=f'Tipo inválido. Válidos: {", ".join(TIPOS_FISCAL)}')

    ahora = datetime.utcnow().isoformat()
    con = get_con()
    cur = con.execute("""
        INSERT INTO pagos_fiscales
            (tipo, descripcion, periodo, monto, moneda, equivalente_usd,
             fecha, referencia,
             odoo_cuenta_gasto_id, odoo_cuenta_gasto_codigo,
             odoo_cuenta_haber_id, odoo_cuenta_haber_codigo,
             odoo_journal_id, estado, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'borrador',?,?,?)
    """, (
        tipo,
        body['descripcion'],
        body.get('periodo') or '',
        float(body['monto']),
        body.get('moneda', 'VES'),
        float(body['equivalente_usd']) if body.get('equivalente_usd') else None,
        body['fecha'],
        body.get('referencia') or '',
        int(body['odoo_cuenta_gasto_id']) if body.get('odoo_cuenta_gasto_id') else None,
        body.get('odoo_cuenta_gasto_codigo') or '',
        int(body['odoo_cuenta_haber_id']) if body.get('odoo_cuenta_haber_id') else None,
        body.get('odoo_cuenta_haber_codigo') or '',
        int(body['odoo_journal_id']) if body.get('odoo_journal_id') else None,
        body.get('notas') or '',
        user['id'], ahora,
    ))
    pf_id = cur.lastrowid
    con.commit()
    p = _get_or_404(con, pf_id)
    con.close()
    return p


@router.put('/{pf_id}')
def actualizar(pf_id: int, body: dict,
               user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    p = _get_or_404(con, pf_id)
    if p['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400, detail='Solo se pueden editar pagos en borrador')

    campos = ['tipo', 'descripcion', 'periodo', 'monto', 'moneda', 'equivalente_usd',
              'fecha', 'referencia',
              'odoo_cuenta_gasto_id', 'odoo_cuenta_gasto_codigo',
              'odoo_cuenta_haber_id', 'odoo_cuenta_haber_codigo',
              'odoo_journal_id', 'notas']
    sets, params = [], []
    for f in campos:
        if f in body:
            sets.append(f"{f}=?"); params.append(body[f])
    if not sets:
        con.close()
        raise HTTPException(status_code=400, detail='Sin campos para actualizar')
    params.append(pf_id)
    con.execute(f"UPDATE pagos_fiscales SET {', '.join(sets)} WHERE id=?", params)
    con.commit()
    p = _get_or_404(con, pf_id)
    con.close()
    return p


@router.delete('/{pf_id}')
def eliminar(pf_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    p = _get_or_404(con, pf_id)
    if p['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400, detail='Solo se pueden eliminar pagos en borrador')
    con.execute("DELETE FROM pagos_fiscales WHERE id=?", (pf_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Eliminado'}


# ── VALIDAR → Maestro ─────────────────────────────────────────────────────────

@router.post('/{pf_id}/validar')
def validar(pf_id: int, user=Depends(require_roles('gerente', 'admin'))):
    """Valida el pago fiscal: crea egreso en maestro_operaciones."""
    con = get_con()
    p = _get_or_404(con, pf_id)
    if p['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'No se puede validar un pago en estado "{p["estado"]}"')

    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    monto = float(p['monto'])
    moneda = p['moneda']
    if moneda == 'USD':
        usd = monto
    else:
        usd = p.get('equivalente_usd') or round(monto / bcv, 4)

    tipo_label = {
        'alcaldia': 'Alcaldía', 'inces': 'INCES', 'aseo': 'Aseo Urbano',
        'iva': 'IVA', 'islr': 'ISLR', 'seniat': 'SENIAT',
        'sso': 'Seguro Social', 'otro': 'Fiscal',
    }.get(p['tipo'], p['tipo'].upper())

    cur = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, odoo_journal_id, journal_nombre, estado)
        VALUES (?,?,?,'egreso','Pagos Fiscales',?,?,?,?,?,?,'pago_fiscal',?,?,'registrado')
    """, (p['fecha'], monto, moneda,
          tipo_label,
          p['descripcion'],
          bcv, usd, custom, usd,
          p.get('odoo_journal_id'),
          p.get('odoo_cuenta_haber_codigo') or ''))
    maestro_id = cur.lastrowid

    con.execute("""
        UPDATE pagos_fiscales SET estado='validado', maestro_op_id=? WHERE id=?
    """, (maestro_id, pf_id))
    con.commit()
    p = _get_or_404(con, pf_id)
    con.close()
    return {'mensaje': 'Pago fiscal validado y registrado en Maestro',
            'pago': p, 'maestro_op_id': maestro_id}


# ── ENVIAR A ODOO ─────────────────────────────────────────────────────────────

@router.post('/{pf_id}/enviar-odoo')
def enviar_odoo(pf_id: int, body: dict = None,
                user=Depends(require_roles('gerente', 'admin'))):
    """
    Crea el asiento contable en Odoo:
      Débito:  cuenta de gasto
      Crédito: cuenta de banco/caja

    Si las cuentas ya están configuradas en el registro se usan automáticamente.
    Se pueden sobreescribir en body: {cuenta_gasto_id, cuenta_haber_id, journal_id}
    """
    body = body or {}
    con = get_con()
    p = _get_or_404(con, pf_id)
    if p['estado'] not in ('validado',):
        con.close()
        raise HTTPException(status_code=400,
                            detail='Debe validar el pago antes de enviarlo a Odoo')
    if p.get('odoo_move_id'):
        con.close()
        raise HTTPException(status_code=400, detail='Ya fue enviado a Odoo')

    cta_gasto = body.get('cuenta_gasto_id') or p.get('odoo_cuenta_gasto_id')
    cta_haber  = body.get('cuenta_haber_id')  or p.get('odoo_cuenta_haber_id')
    journal_id = body.get('journal_id') or p.get('odoo_journal_id')

    if not cta_gasto or not cta_haber:
        con.close()
        raise HTTPException(status_code=400,
                            detail='Se requieren cuenta_gasto_id y cuenta_haber_id')

    monto = float(p['monto'])
    odoo = get_odoo()
    ref = f"{p['descripcion']}" + (f" | {p['referencia']}" if p.get('referencia') else '')
    lineas = [
        {'account_id': int(cta_gasto), 'name': ref, 'debit': monto, 'credit': 0},
        {'account_id': int(cta_haber),  'name': ref, 'debit': 0, 'credit': monto},
    ]

    try:
        move_id = odoo.crear_asiento_contable(
            fecha=p['fecha'],
            lineas=lineas,
            ref=ref,
            diario_id=journal_id,
        )
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    con.execute("""
        UPDATE pagos_fiscales
        SET estado='enviado_odoo', odoo_move_id=?,
            odoo_cuenta_gasto_id=?, odoo_cuenta_haber_id=?, odoo_journal_id=?
        WHERE id=?
    """, (move_id,
          int(cta_gasto), int(cta_haber),
          int(journal_id) if journal_id else None,
          pf_id))
    con.commit()
    p = _get_or_404(con, pf_id)
    con.close()
    return {'mensaje': 'Asiento contable creado en Odoo', 'odoo_move_id': move_id, 'pago': p}


# ── Helpers ───────────────────────────────────────────────────────────────────

@router.get('/helpers/cuentas-gasto')
def cuentas_gasto(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_cuentas_gasto()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/cuentas-liquidez')
def cuentas_liquidez(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_cuentas('liquidez')
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/journals')
def journals(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_journals()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
