"""
Nómina — registro de pagos de nómina, bonificaciones y nóminas de terceros.

Flujo principal:
  1. Importar lotes de Odoo HR Payroll (auto/manual).
  2. O crear manualmente: bonificación, bono, hora extra, etc.
  3. Pagar → registra en maestro_operaciones como egreso.
  4. Enviar a Odoo → asiento contable.

Nóminas de terceros (pago a través de proveedor):
  - El empleado recibe su pago a través de un proveedor (banco/nómina tercerizada).
  - Se descuenta de la cuenta por pagar al proveedor (reducción de AP).
  - Misma lógica que Zelle de Terceros pero para nómina.

Todo elemento tiene cuenta contable configurable en nomina_config_cuentas.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime, date, timezone
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.schemas import rows_to_list, row_to_dict
from models.schemas_input import NominaCreate, NominaUpdate, NominaTerceroCreate, NominaImportarOdoo
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/nomina', tags=['nomina'])

TIPOS = ('nomina', 'bonificacion', 'hora_extra', 'bono', 'otro')


def _get_or_404(con, nom_id: int) -> dict:
    row = row_to_dict(con.execute(
        "SELECT * FROM nomina_registros WHERE id=?", (nom_id,)
    ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail='Registro de nómina no encontrado')
    return row


# ── CONFIGURACIÓN CONTABLE ────────────────────────────────────────────────────

@router.get('/config-cuentas')
def listar_config(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM nomina_config_cuentas ORDER BY tipo"
    ).fetchall())
    con.close()
    return rows


@router.put('/config-cuentas/{tipo}')
def actualizar_config(tipo: str, body: dict,
                      user=Depends(require_roles('gerente', 'admin'))):
    if tipo not in TIPOS:
        raise HTTPException(status_code=400, detail=f'Tipo inválido: {TIPOS}')
    con = get_con()
    campos = ['odoo_cuenta_gasto_id', 'odoo_cuenta_gasto_codigo',
              'odoo_cuenta_gasto_nombre', 'odoo_journal_id', 'odoo_journal_nombre']
    sets, vals = [], []
    for c in campos:
        if c in body:
            sets.append(f"{c}=?"); vals.append(body[c])
    if not sets:
        con.close()
        return {'mensaje': 'Sin cambios'}
    vals.append(tipo)
    con.execute(
        f"UPDATE nomina_config_cuentas SET {', '.join(sets)} WHERE tipo=?", vals
    )
    con.commit()
    con.close()
    return {'mensaje': 'Configuración actualizada'}


# ── CRUD REGISTROS ─────────────────────────────────────────────────────────────

@router.get('')
def listar(
    tipo: Optional[str] = Query(None),
    periodo: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    origen: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    con = get_con()
    clauses, params = [], []
    if tipo:    clauses.append("tipo=?");    params.append(tipo)
    if periodo: clauses.append("periodo=?"); params.append(periodo)
    if estado:  clauses.append("estado=?");  params.append(estado)
    if origen:  clauses.append("origen=?");  params.append(origen)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM nomina_registros {where} ORDER BY periodo DESC, id DESC", params
    ).fetchall())
    con.close()
    return rows


@router.get('/{nom_id}')
def obtener(nom_id: int, user=Depends(get_current_user)):
    con = get_con()
    n = _get_or_404(con, nom_id)
    con.close()
    return n


@router.post('')
def crear(body: NominaCreate, user=Depends(require_roles('gerente', 'admin'))):
    # Auto-lookup config contable si no se proveyó cuenta
    con = get_con()
    odoo_cuenta_gasto_id = body.odoo_cuenta_gasto_id
    odoo_cuenta_gasto_codigo = body.odoo_cuenta_gasto_codigo
    odoo_journal_id = body.odoo_journal_id
    if not odoo_cuenta_gasto_id:
        cfg = row_to_dict(con.execute(
            "SELECT * FROM nomina_config_cuentas WHERE tipo=?", (body.tipo,)
        ).fetchone())
        if cfg:
            odoo_cuenta_gasto_id = odoo_cuenta_gasto_id or cfg.get('odoo_cuenta_gasto_id')
            odoo_cuenta_gasto_codigo = odoo_cuenta_gasto_codigo or cfg.get('odoo_cuenta_gasto_codigo')
            odoo_journal_id = odoo_journal_id or cfg.get('odoo_journal_id')

    ahora = datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO nomina_registros
            (periodo, tipo, descripcion, origen,
             empleado_nombre, empleado_odoo_id, es_grupal,
             monto, moneda, equivalente_usd,
             odoo_cuenta_gasto_id, odoo_cuenta_gasto_codigo,
             odoo_cuenta_banco_id, odoo_journal_id,
             estado, notas, creado_por, creado_en)
        VALUES (?,?,?,'manual',?,?,?,?,?,?,?,?,?,?,'borrador',?,?,?)
    """, (
        body.periodo, body.tipo, body.descripcion,
        body.empleado_nombre, body.empleado_odoo_id,
        1 if body.es_grupal else 0,
        body.monto, body.moneda,
        body.equivalente_usd,
        odoo_cuenta_gasto_id, odoo_cuenta_gasto_codigo,
        body.odoo_cuenta_banco_id, odoo_journal_id,
        body.notas, user['id'], ahora,
    ))
    nom_id = cur.lastrowid
    con.commit()
    n = _get_or_404(con, nom_id)
    con.close()
    return n


@router.put('/{nom_id}')
def actualizar(nom_id: int, body: NominaUpdate,
               user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    n = _get_or_404(con, nom_id)
    if n['estado'] not in ('borrador',):
        con.close()
        raise HTTPException(status_code=400, detail='Solo se pueden editar registros en borrador')
    data = body.model_dump(exclude_none=True)
    if not data:
        con.close()
        return {'mensaje': 'Sin cambios'}
    sets = [f"{f}=?" for f in data]
    vals = list(data.values()) + [nom_id]
    con.execute(f"UPDATE nomina_registros SET {', '.join(sets)} WHERE id=?", vals)
    con.commit()
    n = _get_or_404(con, nom_id)
    con.close()
    return n


@router.delete('/{nom_id}')
def eliminar(nom_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    n = _get_or_404(con, nom_id)
    if n['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400, detail='Solo se pueden eliminar registros en borrador')
    con.execute("DELETE FROM nomina_registros WHERE id=?", (nom_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Eliminado'}


# ── PAGAR → Maestro ────────────────────────────────────────────────────────────

@router.post('/{nom_id}/pagar')
def pagar(nom_id: int, body: dict = None,
          user=Depends(require_roles('gerente', 'admin'))):
    """Marca como pagado y registra egreso en maestro_operaciones."""
    body = body or {}
    con = get_con()
    n = _get_or_404(con, nom_id)
    if n['estado'] != 'borrador':
        con.close()
        raise HTTPException(status_code=400, detail=f'Estado actual: {n["estado"]}')

    monto = float(body.get('monto_real') or n['monto'])
    moneda = n['moneda']
    fecha = body.get('fecha_pago') or date.today().isoformat()
    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    usd = monto if moneda in ('USD', 'USDT') else round(monto / bcv, 4)

    tipo_label = {'nomina': 'Nómina', 'bonificacion': 'Bonificación',
                  'hora_extra': 'Hora Extra', 'bono': 'Bono', 'otro': 'Nómina'}.get(n['tipo'], 'Nómina')
    desc = n['descripcion']
    if n.get('empleado_nombre') and not n.get('es_grupal'):
        desc = f"{n['descripcion']} — {n['empleado_nombre']}"

    cur = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, odoo_journal_id, journal_nombre, estado)
        VALUES (?,?,?,'egreso','Nómina',?,?,?,?,?,?,'nomina',?,?,'confirmado')
    """, (fecha, monto, moneda, tipo_label, desc,
          bcv, usd, custom, usd,
          n.get('odoo_journal_id'), ''))
    maestro_id = cur.lastrowid

    con.execute(
        "UPDATE nomina_registros SET estado='pagado', maestro_op_id=? WHERE id=?",
        (maestro_id, nom_id)
    )
    con.commit()
    n = _get_or_404(con, nom_id)
    con.close()
    return {'mensaje': 'Nómina registrada en Maestro', 'maestro_op_id': maestro_id, 'nomina': n}


# ── ENVIAR A ODOO ──────────────────────────────────────────────────────────────

@router.post('/{nom_id}/enviar-odoo')
def enviar_odoo(nom_id: int, body: dict = None,
                user=Depends(require_roles('gerente', 'admin'))):
    """
    Crea asiento contable en Odoo: Déb cuenta-gasto / Créd cuenta-banco.
    body: { odoo_cuenta_gasto_id, odoo_cuenta_banco_id, odoo_journal_id }
    """
    body = body or {}
    con = get_con()
    n = _get_or_404(con, nom_id)
    if n['estado'] not in ('borrador', 'pagado'):
        con.close()
        raise HTTPException(status_code=400, detail=f'Estado actual: {n["estado"]}')
    if n.get('odoo_move_id'):
        con.close()
        raise HTTPException(status_code=400, detail='Ya enviado a Odoo')

    cta_gasto = body.get('odoo_cuenta_gasto_id') or n.get('odoo_cuenta_gasto_id')
    cta_banco  = body.get('odoo_cuenta_banco_id') or n.get('odoo_cuenta_banco_id')
    journal_id = body.get('odoo_journal_id') or n.get('odoo_journal_id')
    if not cta_gasto or not cta_banco:
        con.close()
        raise HTTPException(status_code=400,
                            detail='Se requieren odoo_cuenta_gasto_id y odoo_cuenta_banco_id')

    monto = float(n['monto'])
    ref = n['descripcion']
    lineas = [
        {'account_id': int(cta_gasto), 'name': ref, 'debit': monto, 'credit': 0},
        {'account_id': int(cta_banco),  'name': ref, 'debit': 0, 'credit': monto},
    ]
    if n.get('empleado_odoo_id') and not n.get('es_grupal'):
        lineas[0]['partner_id'] = int(n['empleado_odoo_id'])

    odoo = get_odoo()
    try:
        move_id = odoo.crear_asiento_contable(
            fecha=date.today().isoformat(), lineas=lineas, ref=ref, diario_id=journal_id
        )
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    con.execute(
        "UPDATE nomina_registros SET estado='enviado_odoo', odoo_move_id=? WHERE id=?",
        (move_id, nom_id)
    )
    if n.get('maestro_op_id'):
        con.execute(
            "UPDATE maestro_operaciones SET odoo_ref=? WHERE id=?",
            (f"move/{move_id}", n['maestro_op_id'])
        )
    con.commit()
    n = _get_or_404(con, nom_id)
    con.close()
    return {'mensaje': 'Asiento creado en Odoo', 'odoo_move_id': move_id, 'nomina': n}


# ── IMPORTAR DESDE ODOO ────────────────────────────────────────────────────────

@router.post('/importar-odoo')
def importar_odoo(body: NominaImportarOdoo, user=Depends(require_roles('gerente', 'admin'))):
    """Importa lotes de nómina de Odoo HR Payroll."""
    odoo = get_odoo()
    try:
        batches = odoo.get_payslip_batches(body.fecha_desde, body.fecha_hasta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    if not batches:
        return {'importados': 0, 'mensaje': 'Sin lotes de nómina en ese período'}

    con = get_con()
    ya_importados = {r[0] for r in con.execute(
        "SELECT odoo_payslip_batch_id FROM nomina_registros WHERE odoo_payslip_batch_id IS NOT NULL"
    ).fetchall()}

    ahora = datetime.now(timezone.utc).isoformat()
    importados = 0
    for b in batches:
        if b['id'] in ya_importados:
            continue
        periodo = (b.get('date_start') or '')[:7]  # YYYY-MM
        cur = con.execute("""
            INSERT INTO nomina_registros
                (periodo, tipo, descripcion, origen, es_grupal,
                 monto, moneda, odoo_payslip_batch_id, estado, creado_por, creado_en)
            VALUES (?,?,?,?,?,?,?,?,'pagado',?,?)
        """, (
            periodo, 'nomina',
            b.get('name') or f'Nómina {periodo}',
            'odoo_payslip', 1,
            0.0,  # monto se actualiza después si es necesario
            'VES',
            b['id'], user['id'], ahora,
        ))
        importados += 1

    con.commit()
    con.close()
    return {'importados': importados, 'mensaje': f'{importados} lotes importados'}


# ── NÓMINAS DE TERCEROS ────────────────────────────────────────────────────────

@router.get('/terceros')
def listar_terceros(
    estado: Optional[str] = Query(None),
    periodo: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    con = get_con()
    clauses, params = [], []
    if estado:  clauses.append("estado=?");  params.append(estado)
    if periodo: clauses.append("periodo=?"); params.append(periodo)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM nomina_terceros {where} ORDER BY periodo DESC, id DESC", params
    ).fetchall())
    con.close()
    return rows


@router.post('/terceros')
def crear_tercero(body: NominaTerceroCreate, user=Depends(require_roles('gerente', 'admin'))):
    """Registra un pago de nómina a través de proveedor (descuento AP)."""
    con = get_con()
    ahora = datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO nomina_terceros
            (nomina_id, periodo, descripcion, empleado_nombre, monto, moneda,
             proveedor_id, proveedor_nombre, referencia,
             odoo_cuenta_gasto_id, odoo_cuenta_ap_id, odoo_journal_id,
             estado, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pendiente',?,?,?)
    """, (
        body.nomina_id, body.periodo,
        body.descripcion or f"Nómina {body.empleado_nombre}",
        body.empleado_nombre, body.monto,
        body.moneda,
        body.proveedor_id, body.proveedor_nombre,
        body.referencia,
        body.odoo_cuenta_gasto_id, body.odoo_cuenta_ap_id,
        body.odoo_journal_id,
        body.notas, user['id'], ahora,
    ))
    tid = cur.lastrowid
    con.commit()
    row = row_to_dict(con.execute(
        "SELECT * FROM nomina_terceros WHERE id=?", (tid,)
    ).fetchone())
    con.close()
    return row


@router.post('/terceros/{tid}/descontar-ap')
def descontar_ap(tid: int, body: dict = None,
                 user=Depends(require_roles('gerente', 'admin'))):
    """
    Crea pago outbound al proveedor en Odoo (reduce AP) y registra egreso en maestro.
    body: { "odoo_journal_id": int }
    """
    body = body or {}
    con = get_con()
    t = row_to_dict(con.execute(
        "SELECT * FROM nomina_terceros WHERE id=?", (tid,)
    ).fetchone())
    if not t:
        con.close()
        raise HTTPException(status_code=404, detail='Registro no encontrado')
    if t['estado'] != 'pendiente':
        con.close()
        raise HTTPException(status_code=400, detail=f'Estado: {t["estado"]}')

    if not t.get('proveedor_id'):
        con.close()
        raise HTTPException(status_code=400, detail='Se requiere proveedor_id de Odoo')

    journal_id = body.get('odoo_journal_id') or t.get('odoo_journal_id')
    if not journal_id:
        con.close()
        raise HTTPException(status_code=400, detail='journal_id es requerido')

    odoo = get_odoo()
    ref = t['descripcion'] or f"Nómina tercero {t['empleado_nombre']}"
    try:
        pago_id = odoo.crear_pago_proveedor_usd(
            partner_id=int(t['proveedor_id']),
            monto=float(t['monto']),
            fecha=date.today().isoformat(),
            journal_id=int(journal_id),
            ref=ref,
        )
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    monto = float(t['monto'])
    usd = monto if t['moneda'] in ('USD', 'USDT') else round(monto / bcv, 4)

    cur = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, odoo_payment_id, odoo_journal_id, odoo_partner_id, estado)
        VALUES (?,?,?,'egreso','Nómina','Terceros',?,?,?,?,?,'nomina_tercero',?,?,?,'confirmado')
    """, (date.today().isoformat(), monto, t['moneda'],
          ref, bcv, usd, custom, usd,
          pago_id, int(journal_id), t.get('proveedor_id')))
    maestro_id = cur.lastrowid

    con.execute("""
        UPDATE nomina_terceros
        SET estado='descontado', odoo_payment_id=?, odoo_journal_id=?, maestro_op_id=?
        WHERE id=?
    """, (pago_id, int(journal_id), maestro_id, tid))
    con.commit()
    t = row_to_dict(con.execute("SELECT * FROM nomina_terceros WHERE id=?", (tid,)).fetchone())
    con.close()
    return {'mensaje': 'Pago al proveedor creado en Odoo', 'odoo_payment_id': pago_id, 'tercero': t}


# ── Helpers ────────────────────────────────────────────────────────────────────

@router.get('/helpers/empleados-odoo')
def empleados_odoo(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_empleados_odoo()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/cuentas')
def cuentas(tipo: Optional[str] = Query(None), user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_cuentas(tipo)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/journals')
def journals(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_journals()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/buscar-cuenta')
def buscar_cuenta_nomina(q: str = '', tipo: str = None, user=Depends(get_current_user)):
    if not q or len(q) < 2:
        return []
    try:
        odoo = get_odoo()
        return odoo.buscar_cuentas(q, tipo)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
