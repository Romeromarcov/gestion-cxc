"""
Requisiciones de Mercancía — solicitudes internas de productos del inventario.

Motivos:
  - muestra:        muestra comercial para cliente
  - obsequio:       regalo/cortesía
  - daño:           producto dañado, retirar de inventario
  - uso_empleado:   uso personal del empleado (descuento/beneficio)
  - uso_empresa:    materiales para uso interno de la empresa

Flujo:
  1. Empleado/vendedor crea solicitud (estado: 'solicitada')
  2. Gerente aprueba o rechaza
  3. Al aprobar con ejecución en Odoo → ajuste de inventario (stock.scrap) +
     asiento contable (account.move) → estado: 'ejecutada'
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.schemas import rows_to_list, row_to_dict
from services.tasas_cambio import tasa_bcv_hoy, tasa_custom_hoy

router = APIRouter(prefix='/requisiciones', tags=['requisiciones'])

MOTIVOS = ('muestra', 'obsequio', 'daño', 'uso_empleado', 'uso_empresa')


def _get_req_or_404(con, req_id: int) -> dict:
    row = row_to_dict(con.execute(
        "SELECT * FROM requisiciones WHERE id=?", (req_id,)
    ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail='Requisición no encontrada')
    return row


def _get_lineas(con, req_id: int) -> list:
    return rows_to_list(con.execute(
        "SELECT * FROM requisiciones_lineas WHERE requisicion_id=? ORDER BY id",
        (req_id,)
    ).fetchall())


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get('')
def listar(
    estado: Optional[str] = Query(None),
    motivo: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    con = get_con()
    clauses, params = [], []
    if estado:      clauses.append("estado=?");     params.append(estado)
    if motivo:      clauses.append("motivo=?");     params.append(motivo)
    if fecha_desde: clauses.append("creado_en>=?"); params.append(fecha_desde)
    if fecha_hasta: clauses.append("creado_en<=?"); params.append(fecha_hasta + 'T23:59')
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM requisiciones {where} ORDER BY creado_en DESC", params
    ).fetchall())
    con.close()
    return rows


@router.get('/{req_id}')
def obtener(req_id: int, user=Depends(get_current_user)):
    con = get_con()
    req = _get_req_or_404(con, req_id)
    lineas = _get_lineas(con, req_id)
    con.close()
    return {'requisicion': req, 'lineas': lineas}


@router.post('')
def crear(body: dict, user=Depends(get_current_user)):
    """
    Body:
    {
      "empleado_nombre": "Juan Pérez",
      "departamento": "Ventas",       # opcional
      "motivo": "muestra",
      "descripcion": "Muestras para cliente XYZ",
      "notas": "",
      "odoo_cuenta_id": 350,          # cuenta contable del gasto/ajuste (opcional)
      "odoo_cuenta_codigo": "7.1.1",
      "odoo_journal_id": 5,
      "odoo_location_id": 8,          # ubicación de almacén (opcional)
      "lineas": [
        {"producto_ref": "PROD-001",
         "producto_nombre": "Aceite Motor 1L",
         "producto_odoo_id": 42,
         "cantidad": 2,
         "unidad": "unidades",
         "costo_unitario": 5.50}
      ]
    }
    """
    reqs = ['empleado_nombre', 'motivo', 'lineas']
    for f in reqs:
        if not body.get(f):
            raise HTTPException(status_code=400, detail=f'Campo requerido: {f}')
    if body['motivo'] not in MOTIVOS:
        raise HTTPException(status_code=400,
                            detail=f'Motivo inválido. Válidos: {", ".join(MOTIVOS)}')
    if not isinstance(body['lineas'], list) or not body['lineas']:
        raise HTTPException(status_code=400, detail='Debe incluir al menos una línea')

    ahora = datetime.utcnow().isoformat()
    con = get_con()
    cur = con.execute("""
        INSERT INTO requisiciones
            (empleado_nombre, departamento, motivo, descripcion,
             odoo_cuenta_id, odoo_cuenta_codigo, odoo_journal_id, odoo_location_id,
             estado, notas, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,'solicitada',?,?,?)
    """, (
        body['empleado_nombre'],
        body.get('departamento') or '',
        body['motivo'],
        body.get('descripcion') or '',
        int(body['odoo_cuenta_id']) if body.get('odoo_cuenta_id') else None,
        body.get('odoo_cuenta_codigo') or '',
        int(body['odoo_journal_id']) if body.get('odoo_journal_id') else None,
        int(body['odoo_location_id']) if body.get('odoo_location_id') else None,
        body.get('notas') or '',
        user['id'], ahora,
    ))
    req_id = cur.lastrowid

    for l in body['lineas']:
        if not l.get('producto_nombre') or not l.get('cantidad'):
            continue
        con.execute("""
            INSERT INTO requisiciones_lineas
                (requisicion_id, producto_ref, producto_nombre, producto_odoo_id,
                 cantidad, unidad, costo_unitario)
            VALUES (?,?,?,?,?,?,?)
        """, (
            req_id,
            l.get('producto_ref') or '',
            l['producto_nombre'],
            int(l['producto_odoo_id']) if l.get('producto_odoo_id') else None,
            float(l['cantidad']),
            l.get('unidad', 'unidades'),
            float(l['costo_unitario']) if l.get('costo_unitario') else None,
        ))

    con.commit()
    req = _get_req_or_404(con, req_id)
    lineas = _get_lineas(con, req_id)
    con.close()
    return {'requisicion': req, 'lineas': lineas}


@router.put('/{req_id}')
def actualizar(req_id: int, body: dict,
               user=Depends(require_roles('gerente', 'admin'))):
    """Actualiza campos de la requisición (solo en estado solicitada)."""
    con = get_con()
    req = _get_req_or_404(con, req_id)
    if req['estado'] != 'solicitada':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo se pueden editar requisiciones en estado solicitada')
    campos = ['empleado_nombre', 'departamento', 'motivo', 'descripcion',
              'odoo_cuenta_id', 'odoo_cuenta_codigo', 'odoo_journal_id',
              'odoo_location_id', 'notas']
    sets, params = [], []
    for f in campos:
        if f in body:
            sets.append(f"{f}=?"); params.append(body[f])
    if sets:
        params.append(req_id)
        con.execute(f"UPDATE requisiciones SET {', '.join(sets)} WHERE id=?", params)

    # Actualizar líneas si se incluyen
    if 'lineas' in body and isinstance(body['lineas'], list):
        con.execute("DELETE FROM requisiciones_lineas WHERE requisicion_id=?", (req_id,))
        for l in body['lineas']:
            if not l.get('producto_nombre') or not l.get('cantidad'):
                continue
            con.execute("""
                INSERT INTO requisiciones_lineas
                    (requisicion_id, producto_ref, producto_nombre, producto_odoo_id,
                     cantidad, unidad, costo_unitario)
                VALUES (?,?,?,?,?,?,?)
            """, (req_id, l.get('producto_ref') or '', l['producto_nombre'],
                  int(l['producto_odoo_id']) if l.get('producto_odoo_id') else None,
                  float(l['cantidad']), l.get('unidad', 'unidades'),
                  float(l['costo_unitario']) if l.get('costo_unitario') else None))

    con.commit()
    req = _get_req_or_404(con, req_id)
    lineas = _get_lineas(con, req_id)
    con.close()
    return {'requisicion': req, 'lineas': lineas}


# ── FLUJO DE APROBACIÓN ───────────────────────────────────────────────────────

@router.post('/{req_id}/aprobar')
def aprobar(req_id: int, body: dict = None,
            user=Depends(require_roles('gerente', 'admin'))):
    """Aprueba la requisición (estado: aprobada)."""
    body = body or {}
    con = get_con()
    req = _get_req_or_404(con, req_id)
    if req['estado'] != 'solicitada':
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'No se puede aprobar una requisición en estado "{req["estado"]}"')

    ahora = datetime.utcnow().isoformat()
    notas = body.get('notas_aprobacion') or ''
    con.execute("""
        UPDATE requisiciones
        SET estado='aprobada', aprobado_por=?, fecha_aprobacion=?, notas_aprobacion=?
        WHERE id=?
    """, (user['id'], ahora, notas, req_id))
    con.commit()
    req = _get_req_or_404(con, req_id)
    con.close()
    return req


@router.post('/{req_id}/rechazar')
def rechazar(req_id: int, body: dict = None,
             user=Depends(require_roles('gerente', 'admin'))):
    """Rechaza la requisición."""
    body = body or {}
    con = get_con()
    req = _get_req_or_404(con, req_id)
    if req['estado'] not in ('solicitada', 'aprobada'):
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'No se puede rechazar una requisición en estado "{req["estado"]}"')

    ahora = datetime.utcnow().isoformat()
    con.execute("""
        UPDATE requisiciones
        SET estado='rechazada', aprobado_por=?, fecha_aprobacion=?, notas_aprobacion=?
        WHERE id=?
    """, (user['id'], ahora, body.get('motivo') or '', req_id))
    con.commit()
    req = _get_req_or_404(con, req_id)
    con.close()
    return req


# ── EJECUTAR (ajuste inventario + asiento Odoo) ───────────────────────────────

@router.post('/{req_id}/ejecutar')
def ejecutar(req_id: int, body: dict = None,
             user=Depends(require_roles('gerente', 'admin'))):
    """
    Ejecuta la requisición aprobada:
    1. Para cada línea con producto_odoo_id: crea stock.scrap en Odoo
    2. Crea asiento contable agrupado con el total de costos
    3. Registra egreso en Maestro
    4. Marca estado = 'ejecutada'

    Body opcional:
    {
      "odoo_cuenta_id": int,    # sobreescribe la cuenta de la requisición
      "odoo_journal_id": int,
      "odoo_location_id": int,
      "cuenta_credito_id": int  # banco/caja que absorbe el costo (default: cta stock)
    }
    """
    body = body or {}
    con = get_con()
    req = _get_req_or_404(con, req_id)
    if req['estado'] != 'aprobada':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Solo se pueden ejecutar requisiciones aprobadas')

    lineas = _get_lineas(con, req_id)
    if not lineas:
        con.close()
        raise HTTPException(status_code=400, detail='La requisición no tiene líneas')

    odoo = get_odoo()
    cuenta_id  = body.get('odoo_cuenta_id')   or req.get('odoo_cuenta_id')
    journal_id = body.get('odoo_journal_id')   or req.get('odoo_journal_id')
    location_id = body.get('odoo_location_id') or req.get('odoo_location_id')

    # ── 1. Ajuste de inventario en Odoo por producto ──────────────────────────
    scrap_ids = []
    for l in lineas:
        if not l.get('producto_odoo_id'):
            continue
        try:
            scrap_id = odoo.crear_ajuste_inventario(
                product_id=int(l['producto_odoo_id']),
                qty=float(l['cantidad']),
                location_id=int(location_id) if location_id else 8,  # 8=virtual location typical
                reason=req.get('descripcion') or req['motivo'],
            )
            scrap_ids.append(scrap_id)
        except Exception:
            pass  # Si falla el scrap, continuar con el asiento

    # ── 2. Asiento contable en Odoo ───────────────────────────────────────────
    move_id = None
    total_costo = sum(
        float(l['cantidad']) * float(l['costo_unitario'] or 0)
        for l in lineas if l.get('costo_unitario')
    )
    if total_costo > 0 and cuenta_id:
        cuenta_credito = body.get('cuenta_credito_id') or cuenta_id
        motivo_label = {
            'muestra': 'Muestras comerciales', 'obsequio': 'Obsequios/Cortesías',
            'daño': 'Pérdidas por daño', 'uso_empleado': 'Uso empleado',
            'uso_empresa': 'Uso empresa',
        }.get(req['motivo'], req['motivo'])
        desc_asiento = f"{motivo_label} — {req.get('descripcion') or req['empleado_nombre']}"
        lineas_asiento = [
            {'account_id': int(cuenta_id),     'name': desc_asiento,
             'debit': total_costo, 'credit': 0},
            {'account_id': int(cuenta_credito), 'name': desc_asiento,
             'debit': 0, 'credit': total_costo},
        ]
        try:
            move_id = odoo.crear_asiento_contable(
                fecha=datetime.utcnow().strftime('%Y-%m-%d'),
                lineas=lineas_asiento,
                ref=desc_asiento,
                diario_id=int(journal_id) if journal_id else None,
            )
        except Exception:
            move_id = None

    # ── 3. Registro en Maestro ────────────────────────────────────────────────
    bcv = tasa_bcv_hoy() or 1.0
    custom = tasa_custom_hoy() or bcv
    usd = round(total_costo / bcv, 4) if total_costo else 0
    maestro_id = None
    if total_costo > 0:
        cur = con.execute("""
            INSERT INTO maestro_operaciones
                (fecha, monto, moneda, tipo, categoria, subcategoria,
                 descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
                 origen, estado)
            VALUES (?,?,?,'egreso','Requisición',?,?,?,?,?,?,'requisicion','registrado')
        """, (datetime.utcnow().strftime('%Y-%m-%d'),
              total_costo, 'USD',
              req['motivo'],
              req.get('descripcion') or f"Req #{req_id}",
              bcv, usd, custom, usd))
        maestro_id = cur.lastrowid

    # ── 4. Actualizar estado ──────────────────────────────────────────────────
    con.execute("""
        UPDATE requisiciones
        SET estado='ejecutada', odoo_move_id=?,
            odoo_cuenta_id=?, odoo_journal_id=?, odoo_location_id=?
        WHERE id=?
    """, (move_id,
          int(cuenta_id) if cuenta_id else None,
          int(journal_id) if journal_id else None,
          int(location_id) if location_id else None,
          req_id))
    con.commit()
    req = _get_req_or_404(con, req_id)
    lineas_final = _get_lineas(con, req_id)
    con.close()

    return {
        'mensaje': 'Requisición ejecutada',
        'requisicion': req,
        'lineas': lineas_final,
        'odoo_move_id': move_id,
        'scraps_creados': len(scrap_ids),
        'maestro_op_id': maestro_id,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

@router.get('/helpers/cuentas-gasto')
def cuentas_gasto(user=Depends(get_current_user)):
    try:
        return get_odoo().get_cuentas_gasto()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/ubicaciones')
def ubicaciones(user=Depends(get_current_user)):
    try:
        return get_odoo().get_ubicaciones_stock('internal')
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/journals')
def journals(user=Depends(get_current_user)):
    try:
        return get_odoo().get_journals()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/helpers/productos')
def buscar_productos(q: str = '', user=Depends(get_current_user)):
    try:
        return get_odoo().get_productos_odoo(limite=100)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
