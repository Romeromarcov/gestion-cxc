"""
Gastos y Compromisos — registro de gastos únicos, recurrentes y servicios públicos.

Flujo:
  1. Crear gasto (borrador). Si es recurrente, queda como plantilla.
  2. Para recurrentes: registrar el pago mensual (gastos_pagos_mensuales).
  3. Validar → registra en maestro_operaciones.
  4. Enviar a Odoo → asiento contable (cuenta gasto / cuenta banco).

Configuración contable por categoría → tabla gastos_config_cuentas.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime, date
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.schemas import rows_to_list, row_to_dict
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/gastos', tags=['gastos'])

TIPOS = ('unico', 'recurrente', 'servicio_publico')


def _get_or_404(con, gasto_id: int) -> dict:
    row = row_to_dict(con.execute(
        "SELECT * FROM gastos WHERE id=?", (gasto_id,)
    ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail='Gasto no encontrado')
    return row


# ── CONFIGURACIÓN CONTABLE ────────────────────────────────────────────────────

@router.get('/config-cuentas')
def listar_config(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM gastos_config_cuentas ORDER BY categoria, subcategoria"
    ).fetchall())
    con.close()
    return rows


@router.put('/config-cuentas/{cfg_id}')
def actualizar_config(cfg_id: int, body: dict,
                      user=Depends(require_roles('gerente', 'admin'))):
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
    vals.append(cfg_id)
    con.execute(f"UPDATE gastos_config_cuentas SET {', '.join(sets)} WHERE id=?", vals)
    con.commit()
    con.close()
    return {'mensaje': 'Configuración actualizada'}


@router.post('/config-cuentas')
def crear_config(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    cat = body.get('categoria')
    if not cat:
        raise HTTPException(status_code=400, detail='categoria es requerido')
    con = get_con()
    cur = con.execute("""
        INSERT OR REPLACE INTO gastos_config_cuentas
            (categoria, subcategoria, odoo_cuenta_gasto_id, odoo_cuenta_gasto_codigo,
             odoo_cuenta_gasto_nombre, odoo_journal_id, odoo_journal_nombre)
        VALUES (?,?,?,?,?,?,?)
    """, (cat, body.get('subcategoria'),
          body.get('odoo_cuenta_gasto_id'), body.get('odoo_cuenta_gasto_codigo'),
          body.get('odoo_cuenta_gasto_nombre'), body.get('odoo_journal_id'),
          body.get('odoo_journal_nombre')))
    con.commit()
    cfg_id = cur.lastrowid
    con.close()
    return {'id': cfg_id, 'mensaje': 'Configuración creada'}


# ── GASTOS CRUD ────────────────────────────────────────────────────────────────

@router.get('')
def listar(
    tipo: Optional[str] = Query(None),
    categoria: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    es_recurrente: Optional[int] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    con = get_con()
    clauses, params = [], []
    if tipo:          clauses.append("tipo=?");           params.append(tipo)
    if categoria:     clauses.append("categoria=?");      params.append(categoria)
    if estado:        clauses.append("estado=?");         params.append(estado)
    if es_recurrente is not None:
        clauses.append("es_recurrente=?");               params.append(es_recurrente)
    if fecha_desde:   clauses.append("fecha>=?");         params.append(fecha_desde)
    if fecha_hasta:   clauses.append("fecha<=?");         params.append(fecha_hasta)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM gastos {where} ORDER BY fecha DESC, id DESC", params
    ).fetchall())
    con.close()
    return rows


@router.get('/recurrentes-pendientes')
def recurrentes_pendientes(user=Depends(get_current_user)):
    """Gastos recurrentes activos con su estado del mes actual."""
    hoy = date.today()
    periodo = hoy.strftime('%Y-%m')
    con = get_con()
    gastos = rows_to_list(con.execute(
        "SELECT * FROM gastos WHERE es_recurrente=1 AND activo=1 ORDER BY categoria, descripcion"
    ).fetchall())
    for g in gastos:
        pago = row_to_dict(con.execute(
            "SELECT * FROM gastos_pagos_mensuales WHERE gasto_id=? AND periodo=?",
            (g['id'], periodo)
        ).fetchone())
        g['pago_mes_actual'] = pago
        g['pagado_este_mes'] = pago is not None and pago.get('estado') == 'pagado'
        g['vencido'] = hoy.day > (g.get('dia_pago') or 1) and not g['pagado_este_mes']
    con.close()
    return gastos


@router.get('/checklist/{anio}')
def checklist_anual(anio: int, user=Depends(get_current_user)):
    """Checklist de pagos de gastos recurrentes para todo el año."""
    con = get_con()
    gastos = rows_to_list(con.execute(
        "SELECT id, categoria, subcategoria, descripcion, monto, moneda, dia_pago "
        "FROM gastos WHERE es_recurrente=1 AND activo=1"
    ).fetchall())
    meses = [f"{anio}-{str(m).zfill(2)}" for m in range(1, 13)]
    pagos = rows_to_list(con.execute(
        "SELECT * FROM gastos_pagos_mensuales WHERE periodo LIKE ?",
        (f"{anio}-%",)
    ).fetchall())
    pago_map = {}
    for p in pagos:
        pago_map[(p['gasto_id'], p['periodo'])] = p
    resultado = []
    for g in gastos:
        g['meses'] = {m: pago_map.get((g['id'], m)) for m in meses}
        resultado.append(g)
    con.close()
    return resultado


@router.get('/{gasto_id}')
def obtener(gasto_id: int, user=Depends(get_current_user)):
    con = get_con()
    g = _get_or_404(con, gasto_id)
    # Pagos mensuales si es recurrente
    if g.get('es_recurrente'):
        g['pagos_mensuales'] = rows_to_list(con.execute(
            "SELECT * FROM gastos_pagos_mensuales WHERE gasto_id=? ORDER BY periodo DESC",
            (gasto_id,)
        ).fetchall())
    con.close()
    return g


@router.post('')
def crear(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    """
    Body:
    {
      "tipo": "recurrente",          # unico | recurrente | servicio_publico
      "categoria": "Servicios Públicos",
      "subcategoria": "Electricidad / Corpoelec",
      "descripcion": "Corpoelec oficina",
      "monto": 85000.0,
      "moneda": "VES",
      "fecha": "2025-03-01",
      "es_recurrente": true,
      "dia_pago": 15,
      "odoo_cuenta_gasto_id": 410,
      "odoo_journal_id": 7,
      "proveedor_nombre": "Corpoelec"
    }
    """
    reqs = ['tipo', 'categoria', 'descripcion', 'monto', 'fecha']
    for f in reqs:
        if not body.get(f):
            raise HTTPException(status_code=400, detail=f'Campo requerido: {f}')
    if body['tipo'] not in TIPOS:
        raise HTTPException(status_code=400, detail=f'Tipo inválido: {TIPOS}')

    # Auto-lookup config contable si no viene en el body
    con = get_con()
    if not body.get('odoo_cuenta_gasto_id'):
        cfg = row_to_dict(con.execute(
            "SELECT * FROM gastos_config_cuentas WHERE categoria=? AND (subcategoria=? OR subcategoria IS NULL) LIMIT 1",
            (body['categoria'], body.get('subcategoria'))
        ).fetchone())
        if cfg:
            body.setdefault('odoo_cuenta_gasto_id', cfg.get('odoo_cuenta_gasto_id'))
            body.setdefault('odoo_cuenta_gasto_codigo', cfg.get('odoo_cuenta_gasto_codigo'))
            body.setdefault('odoo_journal_id', cfg.get('odoo_journal_id'))

    ahora = datetime.utcnow().isoformat()
    cur = con.execute("""
        INSERT INTO gastos
            (tipo, categoria, subcategoria, descripcion, proveedor_nombre, proveedor_odoo_id,
             monto, moneda, equivalente_usd, fecha, referencia, periodo,
             es_recurrente, dia_pago, activo,
             odoo_cuenta_gasto_id, odoo_cuenta_gasto_codigo,
             odoo_cuenta_banco_id, odoo_journal_id,
             estado, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,'borrador',?,?,?)
    """, (
        body['tipo'], body['categoria'], body.get('subcategoria'),
        body['descripcion'], body.get('proveedor_nombre'), body.get('proveedor_odoo_id'),
        float(body['monto']), body.get('moneda', 'VES'),
        float(body['equivalente_usd']) if body.get('equivalente_usd') else None,
        body['fecha'], body.get('referencia'), body.get('periodo'),
        1 if body.get('es_recurrente') else 0,
        int(body['dia_pago']) if body.get('dia_pago') else 1,
        body.get('odoo_cuenta_gasto_id'), body.get('odoo_cuenta_gasto_codigo'),
        body.get('odoo_cuenta_banco_id'), body.get('odoo_journal_id'),
        body.get('notas'), user['id'], ahora,
    ))
    gasto_id = cur.lastrowid
    con.commit()
    g = _get_or_404(con, gasto_id)
    con.close()
    return g


@router.put('/{gasto_id}')
def actualizar(gasto_id: int, body: dict,
               user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    g = _get_or_404(con, gasto_id)
    campos = ['tipo', 'categoria', 'subcategoria', 'descripcion', 'proveedor_nombre',
              'monto', 'moneda', 'equivalente_usd', 'fecha', 'referencia', 'periodo',
              'es_recurrente', 'dia_pago', 'activo',
              'odoo_cuenta_gasto_id', 'odoo_cuenta_gasto_codigo',
              'odoo_cuenta_banco_id', 'odoo_journal_id', 'notas']
    sets, vals = [], []
    for f in campos:
        if f in body:
            sets.append(f"{f}=?"); vals.append(body[f])
    if not sets:
        con.close()
        return {'mensaje': 'Sin cambios'}
    vals.append(gasto_id)
    con.execute(f"UPDATE gastos SET {', '.join(sets)} WHERE id=?", vals)
    con.commit()
    g = _get_or_404(con, gasto_id)
    con.close()
    return g


@router.delete('/{gasto_id}')
def eliminar(gasto_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    g = _get_or_404(con, gasto_id)
    if g['estado'] not in ('borrador',):
        # Para recurrentes activos, solo desactivar
        if g.get('es_recurrente'):
            con.execute("UPDATE gastos SET activo=0 WHERE id=?", (gasto_id,))
            con.commit()
            con.close()
            return {'mensaje': 'Gasto recurrente desactivado'}
        con.close()
        raise HTTPException(status_code=400, detail='Solo se pueden eliminar gastos en borrador')
    con.execute("DELETE FROM gastos WHERE id=?", (gasto_id,))
    con.commit()
    con.close()
    return {'mensaje': 'Eliminado'}


# ── VALIDAR / PAGAR ────────────────────────────────────────────────────────────

@router.post('/{gasto_id}/pagar')
def pagar(gasto_id: int, body: dict = None,
          user=Depends(require_roles('gerente', 'admin'))):
    """
    Marca el gasto como pagado y registra en maestro_operaciones.
    Para gastos recurrentes, crea entrada en gastos_pagos_mensuales.
    body: { "fecha_pago": "YYYY-MM-DD", "monto_real": float, "referencia": str, "periodo": "YYYY-MM" }
    """
    body = body or {}
    con = get_con()
    g = _get_or_404(con, gasto_id)
    if g['estado'] == 'pagado' and not g.get('es_recurrente'):
        con.close()
        raise HTTPException(status_code=400, detail='Gasto ya pagado')

    fecha_pago = body.get('fecha_pago') or g['fecha']
    monto = float(body.get('monto_real') or g['monto'])
    moneda = g['moneda']
    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    usd = monto if moneda in ('USD', 'USDT') else round(monto / bcv, 4)

    desc = g['descripcion']
    if g.get('categoria'):
        desc = f"[{g['categoria']}] {desc}"

    cur = con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, monto, moneda, tipo, categoria, subcategoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, journal_nombre, odoo_journal_id, estado)
        VALUES (?,?,?,'egreso',?,?,?,?,?,?,?,'gasto',?,?,'confirmado')
    """, (fecha_pago, monto, moneda,
          g.get('categoria') or 'Gasto',
          g.get('subcategoria'),
          desc, bcv, usd, custom, usd,
          g.get('odoo_journal_id'),
          ''))
    maestro_id = cur.lastrowid

    if g.get('es_recurrente'):
        periodo = body.get('periodo') or datetime.utcnow().strftime('%Y-%m')
        con.execute("""
            INSERT OR REPLACE INTO gastos_pagos_mensuales
                (gasto_id, periodo, monto_pagado, fecha_pago, referencia, maestro_op_id, estado)
            VALUES (?,?,?,?,?,?,'pagado')
        """, (gasto_id, periodo, monto, fecha_pago,
              body.get('referencia'), maestro_id))
    else:
        con.execute(
            "UPDATE gastos SET estado='pagado', maestro_op_id=? WHERE id=?",
            (maestro_id, gasto_id)
        )
    con.commit()
    g = _get_or_404(con, gasto_id)
    con.close()
    return {'mensaje': 'Gasto registrado en Maestro', 'maestro_op_id': maestro_id, 'gasto': g}


# ── ENVIAR A ODOO ──────────────────────────────────────────────────────────────

@router.post('/{gasto_id}/enviar-odoo')
def enviar_odoo(gasto_id: int, body: dict = None,
                user=Depends(require_roles('gerente', 'admin'))):
    """
    Crea asiento contable en Odoo: Déb cuenta-gasto / Créd cuenta-banco.
    Para gastos recurrentes, enviar el pago mensual: body.periodo = "YYYY-MM"
    body: { "odoo_cuenta_gasto_id", "odoo_cuenta_banco_id", "odoo_journal_id", "periodo" }
    """
    body = body or {}
    con = get_con()
    g = _get_or_404(con, gasto_id)

    pago_mensual = None
    if g.get('es_recurrente'):
        periodo = body.get('periodo') or datetime.utcnow().strftime('%Y-%m')
        pago_mensual = row_to_dict(con.execute(
            "SELECT * FROM gastos_pagos_mensuales WHERE gasto_id=? AND periodo=?",
            (gasto_id, periodo)
        ).fetchone())
        if not pago_mensual:
            con.close()
            raise HTTPException(status_code=400,
                                detail=f'No hay pago registrado para el período {periodo}')
        if pago_mensual.get('odoo_move_id'):
            con.close()
            raise HTTPException(status_code=400, detail='Ya enviado a Odoo para ese período')
    else:
        if g.get('odoo_move_id'):
            con.close()
            raise HTTPException(status_code=400, detail='Ya enviado a Odoo')

    cta_gasto = body.get('odoo_cuenta_gasto_id') or g.get('odoo_cuenta_gasto_id')
    cta_banco  = body.get('odoo_cuenta_banco_id') or g.get('odoo_cuenta_banco_id')
    journal_id = body.get('odoo_journal_id') or g.get('odoo_journal_id')

    if not cta_gasto or not cta_banco:
        con.close()
        raise HTTPException(status_code=400,
                            detail='Se requieren odoo_cuenta_gasto_id y odoo_cuenta_banco_id')

    monto = float(pago_mensual['monto_pagado'] if pago_mensual else g['monto'])
    fecha = pago_mensual['fecha_pago'] if pago_mensual else g['fecha']
    ref = g['descripcion']

    odoo = get_odoo()
    lineas = [
        {'account_id': int(cta_gasto), 'name': ref, 'debit': monto, 'credit': 0},
        {'account_id': int(cta_banco),  'name': ref, 'debit': 0, 'credit': monto},
    ]
    try:
        move_id = odoo.crear_asiento_contable(fecha=fecha, lineas=lineas,
                                               ref=ref, diario_id=journal_id)
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=f'Error Odoo: {e}')

    if pago_mensual:
        con.execute(
            "UPDATE gastos_pagos_mensuales SET odoo_move_id=?, estado='enviado_odoo' WHERE id=?",
            (move_id, pago_mensual['id'])
        )
        # Actualizar maestro si tiene id
        if pago_mensual.get('maestro_op_id'):
            con.execute(
                "UPDATE maestro_operaciones SET odoo_ref=? WHERE id=?",
                (f"move/{move_id}", pago_mensual['maestro_op_id'])
            )
    else:
        con.execute(
            "UPDATE gastos SET odoo_move_id=?, estado='enviado_odoo' WHERE id=?",
            (move_id, gasto_id)
        )
        if g.get('maestro_op_id'):
            con.execute(
                "UPDATE maestro_operaciones SET odoo_ref=? WHERE id=?",
                (f"move/{move_id}", g['maestro_op_id'])
            )
    con.commit()
    g = _get_or_404(con, gasto_id)
    con.close()
    return {'mensaje': 'Asiento creado en Odoo', 'odoo_move_id': move_id, 'gasto': g}


# ── Helpers ────────────────────────────────────────────────────────────────────

@router.get('/helpers/categorias')
def categorias(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT DISTINCT categoria, subcategoria FROM gastos_config_cuentas ORDER BY categoria, subcategoria"
    ).fetchall())
    con.close()
    return rows


@router.get('/helpers/cuentas-odoo')
def cuentas_odoo(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_cuentas_gasto()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/journals')
def journals(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_journals()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/cuentas-banco')
def cuentas_banco(user=Depends(get_current_user)):
    try:
        odoo = get_odoo()
        return odoo.get_cuentas('liquidez')
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
