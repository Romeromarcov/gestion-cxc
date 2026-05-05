"""
Cambios de Divisas — registro de operaciones de conversión de moneda.

Flujo:
  1. Se crea el cambio en estado 'borrador'
  2. Al validar → se crean dos registros en maestro_operaciones (egreso + ingreso)
  3. Al enviar a Odoo → se crea el asiento contable correspondiente
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.schemas import rows_to_list, row_to_dict
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/cambios-divisa', tags=['cambios-divisa'])


def _get_or_404(con, cambio_id: int) -> dict:
    row = row_to_dict(con.execute(
        "SELECT * FROM cambios_divisa WHERE id=?", (cambio_id,)
    ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail='Cambio de divisa no encontrado')
    return row


def _usd_equiv(monto: float, moneda: str) -> float:
    """Calcula equivalente USD usando tasa BCV."""
    if moneda == 'USD':
        return monto
    bcv = tasa_bcv_hoy() or 1.0
    return round(monto / bcv, 4)


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get('')
def listar_cambios(
    estado: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    con = get_con()
    clauses = []
    params = []
    if estado:
        clauses.append("estado=?"); params.append(estado)
    if fecha_desde:
        clauses.append("fecha>=?"); params.append(fecha_desde)
    if fecha_hasta:
        clauses.append("fecha<=?"); params.append(fecha_hasta)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM cambios_divisa {where} ORDER BY fecha DESC, id DESC",
        params
    ).fetchall())
    con.close()
    return rows


@router.get('/{cambio_id}')
def obtener_cambio(cambio_id: int, user=Depends(get_current_user)):
    con = get_con()
    c = _get_or_404(con, cambio_id)
    con.close()
    return c


@router.post('')
def crear_cambio(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    """
    Crea un cambio de divisa en estado borrador.

    Body:
    {
      "fecha": "2025-01-15",
      "descripcion": "Cambio USD → VES",
      "monto_egreso": 1000.0,
      "moneda_egreso": "USD",
      "banco_egreso": "Banesco USD",
      "odoo_journal_egreso": 12,
      "monto_ingreso": 3850000.0,
      "moneda_ingreso": "VES",
      "banco_ingreso": "Banesco VES",
      "odoo_journal_ingreso": 7,
      "tasa_cambio": 38.50,
      "notas": ""
    }
    """
    reqs = ['fecha', 'monto_egreso', 'moneda_egreso', 'monto_ingreso', 'moneda_ingreso', 'tasa_cambio']
    for f in reqs:
        if not body.get(f):
            raise HTTPException(status_code=400, detail=f'Campo requerido: {f}')
    if body['moneda_egreso'] == body['moneda_ingreso']:
        raise HTTPException(status_code=400, detail='Las monedas de egreso e ingreso deben ser diferentes')

    ahora = datetime.utcnow().isoformat()
    con = get_con()
    cur = con.execute("""
        INSERT INTO cambios_divisa
            (fecha, descripcion, monto_egreso, moneda_egreso, banco_egreso, odoo_journal_egreso,
             monto_ingreso, moneda_ingreso, banco_ingreso, odoo_journal_ingreso,
             tasa_cambio, estado, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'borrador',?,?,?)
    """, (
        body['fecha'],
        body.get('descripcion') or f"{body['moneda_egreso']} → {body['moneda_ingreso']}",
        float(body['monto_egreso']),
        body['moneda_egreso'],
        body.get('banco_egreso') or '',
        int(body['odoo_journal_egreso']) if body.get('odoo_journal_egreso') else None,
        float(body['monto_ingreso']),
        body['moneda_ingreso'],
        body.get('banco_ingreso') or '',
        int(body['odoo_journal_ingreso']) if body.get('odoo_journal_ingreso') else None,
        float(body['tasa_cambio']),
        body.get('notas') or '',
        user['id'], ahora,
    ))
    cambio_id = cur.lastrowid
    con.commit()
    c = _get_or_404(con, cambio_id)
    con.close()
    return c


@router.put('/{cambio_id}')
def actualizar_cambio(cambio_id: int, body: dict,
                      user=Depends(require_roles('gerente', 'admin'))):
    """Actualiza un cambio en estado borrador."""
    con = get_con()
    c = _get_or_404(con, cambio_id)
    if c['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400, detail='Solo se pueden editar cambios en borrador')

    campos = ['fecha', 'descripcion', 'monto_egreso', 'moneda_egreso', 'banco_egreso',
              'odoo_journal_egreso', 'monto_ingreso', 'moneda_ingreso', 'banco_ingreso',
              'odoo_journal_ingreso', 'tasa_cambio', 'notas']
    sets, params = [], []
    for f in campos:
        if f in body:
            sets.append(f"{f}=?")
            params.append(body[f])
    if not sets:
        con.close()
        raise HTTPException(status_code=400, detail='Sin campos para actualizar')
    params.append(cambio_id)
    con.execute(f"UPDATE cambios_divisa SET {', '.join(sets)} WHERE id=?", params)
    con.commit()
    c = _get_or_404(con, cambio_id)
    con.close()
    return c


@router.delete('/{cambio_id}')
def eliminar_cambio(cambio_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    c = _get_or_404(con, cambio_id)
    if c['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400, detail='Solo se pueden eliminar cambios en borrador')
    con.execute("DELETE FROM cambios_divisa WHERE id=?", (cambio_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Eliminado'}


# ── VALIDAR → Maestro ──────────────────────────────────────────────────────────

@router.post('/{cambio_id}/validar')
def validar_cambio(cambio_id: int, user=Depends(require_roles('gerente', 'admin'))):
    """
    Valida el cambio de divisa: crea dos registros en maestro_operaciones
    (un egreso y un ingreso) y actualiza el estado a 'validado'.
    """
    con = get_con()
    c = _get_or_404(con, cambio_id)
    if c['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400, detail=f'No se puede validar un cambio en estado "{c["estado"]}"')

    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    desc = c.get('descripcion') or f"Cambio {c['moneda_egreso']}→{c['moneda_ingreso']}"

    def equiv_usd(monto, moneda):
        if moneda == 'USD':
            return float(monto)
        return round(float(monto) / bcv, 4)

    # Egreso
    monto_egr = float(c['monto_egreso'])
    moneda_egr = c['moneda_egreso']
    usd_egr = equiv_usd(monto_egr, moneda_egr)
    cur1 = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, odoo_journal_id, journal_nombre, estado)
        VALUES (?,?,?,'egreso','Cambio Divisa','Egreso',?,?,?,?,?,'cambio_divisa',?,?,'registrado')
    """, (c['fecha'], monto_egr, moneda_egr,
          f"{desc} — sale {moneda_egr}",
          bcv, usd_egr, custom, usd_egr,
          c.get('odoo_journal_egreso'),
          c.get('banco_egreso') or ''))
    egr_id = cur1.lastrowid

    # Ingreso
    monto_ing = float(c['monto_ingreso'])
    moneda_ing = c['moneda_ingreso']
    usd_ing = equiv_usd(monto_ing, moneda_ing)
    cur2 = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, odoo_journal_id, journal_nombre, estado)
        VALUES (?,?,?,'ingreso','Cambio Divisa','Ingreso',?,?,?,?,?,'cambio_divisa',?,?,'registrado')
    """, (c['fecha'], monto_ing, moneda_ing,
          f"{desc} — entra {moneda_ing}",
          bcv, usd_ing, custom, usd_ing,
          c.get('odoo_journal_ingreso'),
          c.get('banco_ingreso') or ''))
    ing_id = cur2.lastrowid

    con.execute("""
        UPDATE cambios_divisa
        SET estado='validado', maestro_egreso_id=?, maestro_ingreso_id=?
        WHERE id=?
    """, (egr_id, ing_id, cambio_id))
    con.commit()
    c = _get_or_404(con, cambio_id)
    con.close()
    return {'mensaje': 'Cambio validado y registrado en Maestro', 'cambio': c,
            'maestro_egreso_id': egr_id, 'maestro_ingreso_id': ing_id}


# ── ENVIAR A ODOO ──────────────────────────────────────────────────────────────

@router.post('/{cambio_id}/enviar-odoo')
def enviar_cambio_odoo(cambio_id: int, body: dict = None,
                       user=Depends(require_roles('gerente', 'admin'))):
    """
    Crea el asiento contable en Odoo para el cambio de divisa.

    Body opcional:
    {
      "diario_id": int,           # diario para el asiento (opcional)
      "cuenta_egreso_id": int,    # cuenta activo del banco/caja origen
      "cuenta_ingreso_id": int,   # cuenta activo del banco/caja destino
    }
    """
    body = body or {}
    con = get_con()
    c = _get_or_404(con, cambio_id)
    if c['estado'] not in ('validado',):
        con.close()
        raise HTTPException(status_code=400,
                            detail='Debe validar el cambio antes de enviarlo a Odoo')
    if c.get('enviado_odoo'):
        con.close()
        raise HTTPException(status_code=400, detail='Ya fue enviado a Odoo')

    cuenta_egreso = body.get('cuenta_egreso_id') or c.get('odoo_journal_egreso')
    cuenta_ingreso = body.get('cuenta_ingreso_id') or c.get('odoo_journal_ingreso')
    if not cuenta_egreso or not cuenta_ingreso:
        con.close()
        raise HTTPException(status_code=400,
                            detail='Se requieren cuenta_egreso_id y cuenta_ingreso_id para el asiento')

    odoo = get_odoo()
    desc = c.get('descripcion') or f"Cambio {c['moneda_egreso']}→{c['moneda_ingreso']}"
    lineas = [
        # Crédito en cuenta origen (sale)
        {'account_id': int(cuenta_egreso), 'name': f'{desc} — egreso',
         'debit': 0, 'credit': float(c['monto_egreso'])},
        # Débito en cuenta destino (entra)
        {'account_id': int(cuenta_ingreso), 'name': f'{desc} — ingreso',
         'debit': float(c['monto_ingreso']), 'credit': 0},
    ]

    try:
        move_id = odoo.crear_asiento_contable(
            fecha=c['fecha'],
            lineas=lineas,
            ref=desc,
            diario_id=body.get('diario_id'),
        )
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    con.execute("""
        UPDATE cambios_divisa
        SET estado='enviado_odoo', enviado_odoo=1, odoo_move_id=?
        WHERE id=?
    """, (move_id, cambio_id))
    con.commit()
    c = _get_or_404(con, cambio_id)
    con.close()
    return {'mensaje': 'Asiento contable creado en Odoo', 'odoo_move_id': move_id, 'cambio': c}


# ── Helpers para el frontend ──────────────────────────────────────────────────

@router.get('/helpers/journals')
def listar_journals(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_journals()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/cuentas')
def listar_cuentas(tipo: Optional[str] = Query(None), user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_cuentas(tipo)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Config de journals y cuentas por defecto ─────────────────────────────────

@router.get('/config')
def get_config(user=Depends(get_current_user)):
    con = get_con()
    row = row_to_dict(con.execute("SELECT * FROM cambios_divisa_config WHERE id=1").fetchone())
    con.close()
    return row or {}


@router.put('/config')
def update_config(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    allowed = {
        'journal_egreso_id', 'journal_egreso_nombre',
        'cuenta_egreso_id',  'cuenta_egreso_nombre',
        'journal_ingreso_id', 'journal_ingreso_nombre',
        'cuenta_ingreso_id', 'cuenta_ingreso_nombre',
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail='Sin campos válidos')
    con = get_con()
    sets = ', '.join(f"{k}=?" for k in updates)
    con.execute(f"UPDATE cambios_divisa_config SET {sets} WHERE id=1", list(updates.values()))
    con.commit()
    row = row_to_dict(con.execute("SELECT * FROM cambios_divisa_config WHERE id=1").fetchone())
    con.close()
    return row
