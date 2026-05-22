"""
Módulo Interno — Cuentas por Pagar (CxP)
Cuentas a pagar generadas automáticamente desde Compras Internas.
Permite registrar pagos parciales o totales y llevar el saldo pendiente.
Deshabilitado por defecto; se activa desde Configuración → Módulo Interno.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone

from database import get_con
from routers.auth import get_current_user, require_roles
from models.operaciones import PagoCxPCreate
from models.schemas import row_to_dict, rows_to_list

router = APIRouter(prefix='/cuentas-por-pagar-internas', tags=['cuentas-por-pagar'])


def _require_modulo(con=None):
    _con = con or get_con()
    row = _con.execute(
        "SELECT valor FROM config_app WHERE clave='modulo_interno_activo'"
    ).fetchone()
    if con is None:
        _con.close()
    if not row or str(row[0]) not in ('1', 'true'):
        raise HTTPException(
            status_code=403,
            detail='El módulo interno está desactivado. Actívalo desde Configuración.'
        )


@router.get('')
def listar_cxp(
    estado: str | None = None,
    proveedor: str | None = None,
    user=Depends(require_roles('admin', 'gerente'))
):
    con = get_con()
    _require_modulo(con)
    query = "SELECT * FROM cuentas_por_pagar WHERE 1=1"
    params: list = []
    if estado:
        query += " AND estado=?"
        params.append(estado)
    if proveedor:
        query += " AND proveedor ILIKE ?"
        params.append(f'%{proveedor}%')
    query += " ORDER BY fecha_emision DESC"
    rows = rows_to_list(con.execute(query, params).fetchall())
    con.close()
    return rows


@router.get('/resumen')
def resumen_cxp(user=Depends(require_roles('admin', 'gerente'))):
    """KPIs rápidos: total pendiente, vencido, pagado."""
    con = get_con()
    _require_modulo(con)
    hoy = datetime.now(timezone.utc).date().isoformat()
    total_pendiente = con.execute(
        "SELECT COALESCE(SUM(saldo_pendiente),0) FROM cuentas_por_pagar WHERE estado='pendiente'"
    ).fetchone()[0]
    total_vencido = con.execute(
        "SELECT COALESCE(SUM(saldo_pendiente),0) FROM cuentas_por_pagar "
        "WHERE estado='pendiente' AND fecha_vencimiento IS NOT NULL AND fecha_vencimiento < ?",
        (hoy,)
    ).fetchone()[0]
    total_pagado = con.execute(
        "SELECT COALESCE(SUM(monto_total),0) FROM cuentas_por_pagar WHERE estado='pagada'"
    ).fetchone()[0]
    count_pendiente = con.execute(
        "SELECT COUNT(*) FROM cuentas_por_pagar WHERE estado='pendiente'"
    ).fetchone()[0]
    con.close()
    return {
        'total_pendiente_usd': round(float(total_pendiente), 2),
        'total_vencido_usd':   round(float(total_vencido), 2),
        'total_pagado_usd':    round(float(total_pagado), 2),
        'count_pendiente':     int(count_pendiente),
    }


@router.get('/{cxp_id}')
def detalle_cxp(cxp_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    cxp = row_to_dict(con.execute(
        "SELECT * FROM cuentas_por_pagar WHERE id=?", (cxp_id,)
    ).fetchone())
    if not cxp:
        con.close()
        raise HTTPException(status_code=404, detail='CxP no encontrada')
    pagos = rows_to_list(con.execute(
        "SELECT * FROM cuentas_por_pagar_pagos WHERE cxp_id=? ORDER BY fecha_pago",
        (cxp_id,)
    ).fetchall())
    con.close()
    return {**cxp, 'pagos': pagos}


@router.post('/{cxp_id}/registrar-pago')
def registrar_pago(cxp_id: int, body: PagoCxPCreate,
                   user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    cxp = row_to_dict(con.execute(
        "SELECT * FROM cuentas_por_pagar WHERE id=?", (cxp_id,)
    ).fetchone())
    if not cxp:
        con.close()
        raise HTTPException(status_code=404, detail='CxP no encontrada')
    if cxp['estado'] in ('pagada', 'cancelada'):
        con.close()
        raise HTTPException(status_code=400,
                            detail=f"CxP ya está {cxp['estado']}")

    saldo_actual = float(cxp['saldo_pendiente'] or 0)
    if body.monto <= 0:
        con.close()
        raise HTTPException(status_code=400, detail='El monto debe ser mayor a 0')
    if body.monto > saldo_actual + 0.01:
        con.close()
        raise HTTPException(status_code=400,
                            detail=f'Monto ({body.monto}) supera el saldo pendiente ({saldo_actual:.2f})')

    fecha_pago = body.fecha_pago or datetime.now(timezone.utc).date().isoformat()

    con.execute("""
        INSERT INTO cuentas_por_pagar_pagos
            (cxp_id, monto, moneda, metodo, referencia, fecha_pago, notas,
             registrado_por)
        VALUES (?,?,?,?,?,?,?,?)
    """, (cxp_id, body.monto, body.moneda, body.metodo,
          body.referencia, fecha_pago, body.notas, user['id']))

    nuevo_saldo = max(0.0, round(saldo_actual - body.monto, 4))
    nuevo_estado = 'pagada' if nuevo_saldo <= 0.001 else 'parcial'

    con.execute("""
        UPDATE cuentas_por_pagar
        SET saldo_pendiente=?, estado=?
        WHERE id=?
    """, (nuevo_saldo, nuevo_estado, cxp_id))
    con.commit()
    con.close()
    return {
        'mensaje': 'Pago registrado',
        'saldo_pendiente': nuevo_saldo,
        'estado': nuevo_estado,
    }


@router.put('/{cxp_id}/cancelar')
def cancelar_cxp(cxp_id: int,
                 user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    con.execute(
        "UPDATE cuentas_por_pagar SET estado='cancelada' WHERE id=?", (cxp_id,)
    )
    con.commit()
    con.close()
    return {'mensaje': 'CxP cancelada'}


@router.put('/{cxp_id}/fecha-vencimiento')
def set_fecha_vencimiento(cxp_id: int, body: dict,
                          user=Depends(require_roles('admin', 'gerente'))):
    """Permite actualizar la fecha de vencimiento y notas de una CxP."""
    con = get_con()
    _require_modulo(con)
    fecha = body.get('fecha_vencimiento')
    notas = body.get('notas')
    con.execute("""
        UPDATE cuentas_por_pagar
        SET fecha_vencimiento=?, notas=COALESCE(?, notas)
        WHERE id=?
    """, (fecha, notas, cxp_id))
    con.commit()
    con.close()
    return {'mensaje': 'CxP actualizada'}
