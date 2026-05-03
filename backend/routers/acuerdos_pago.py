"""Acuerdos de pago especiales (condiciones distintas de Odoo)."""
import json
from fastapi import APIRouter, HTTPException, Depends
from routers.auth import get_current_user, require_roles
from models.schemas import rows_to_list
from database import get_con
from datetime import date as _date, timedelta

router = APIRouter(prefix='/acuerdos-pago', tags=['acuerdos_pago'])


def _generar_cuotas(acuerdo_id, fecha_inicio, plazo_total_dias, periodicidad,
                    monto_total, monto_cuota, porcentaje_abono, con):
    """Genera las cuotas automáticamente según la periodicidad."""
    con.execute("DELETE FROM acuerdos_pago_cuotas WHERE acuerdo_id=?", (acuerdo_id,))
    paso = {'semanal': 7, 'quincenal': 15, 'mensual': 30, 'unico': plazo_total_dias}
    dias_paso = paso.get(periodicidad, 30)
    fecha = _date.fromisoformat(fecha_inicio)
    fecha_fin = fecha + timedelta(days=plazo_total_dias)
    cuota_num = 1
    acumulado = 0
    while fecha <= fecha_fin:
        if porcentaje_abono:
            monto = round(monto_total * porcentaje_abono / 100, 2)
        else:
            monto = round(monto_cuota, 2)
        restante = round(monto_total - acumulado, 2)
        if monto > restante:
            monto = restante
        if monto <= 0:
            break
        con.execute("""INSERT INTO acuerdos_pago_cuotas
                       (acuerdo_id, numero_cuota, fecha_vencimiento, monto_esperado)
                       VALUES (?,?,?,?)""",
                    (acuerdo_id, cuota_num, fecha.isoformat(), monto))
        acumulado += monto
        cuota_num += 1
        if periodicidad == 'unico':
            break
        fecha += timedelta(days=dias_paso)


@router.get('')
def listar_acuerdos(cliente_id: int = None, estado: str = None,
                    user=Depends(get_current_user)):
    con = get_con()
    q = "SELECT * FROM acuerdos_pago WHERE 1=1"
    params = []
    if cliente_id:
        q += " AND cliente_id=?"; params.append(cliente_id)
    if estado:
        q += " AND estado=?"; params.append(estado)
    q += " ORDER BY creado_en DESC"
    rows = rows_to_list(con.execute(q, params).fetchall())
    con.close()
    return rows


@router.get('/{acuerdo_id}')
def get_acuerdo(acuerdo_id: int, user=Depends(get_current_user)):
    con = get_con()
    acuerdo = con.execute("SELECT * FROM acuerdos_pago WHERE id=?", (acuerdo_id,)).fetchone()
    if not acuerdo:
        con.close()
        raise HTTPException(status_code=404, detail='Acuerdo no encontrado')
    acuerdo = dict(acuerdo)
    cuotas = rows_to_list(con.execute(
        "SELECT * FROM acuerdos_pago_cuotas WHERE acuerdo_id=? ORDER BY numero_cuota",
        (acuerdo_id,)
    ).fetchall())
    con.close()
    # Calcular estado real de cuotas
    hoy = _date.today().isoformat()
    for c in cuotas:
        if c['estado'] == 'pendiente' and c['fecha_vencimiento'] < hoy:
            c['estado'] = 'vencido'
    acuerdo['cuotas'] = cuotas
    return acuerdo


@router.post('')
def crear_acuerdo(body: dict, user=Depends(get_current_user)):
    for f in ['cliente_id', 'descripcion', 'monto_total', 'fecha_inicio']:
        if not body.get(f) and body.get(f) != 0:
            raise HTTPException(status_code=422, detail=f'Campo requerido: {f}')
    monto = float(body['monto_total'])
    fecha_inicio = body['fecha_inicio']
    plazo = int(body.get('plazo_total_dias') or 90)
    periodicidad = body.get('periodicidad', 'semanal')
    porc = float(body.get('porcentaje_abono') or 0)
    monto_cuota = float(body.get('monto_cuota') or 0)
    fecha_venc = (_date.fromisoformat(fecha_inicio) + timedelta(days=plazo)).isoformat()

    con = get_con()
    cur = con.execute("""
        INSERT INTO acuerdos_pago
            (cliente_id, cliente_nombre, descripcion, monto_total, moneda,
             plazo_total_dias, periodicidad, porcentaje_abono, monto_cuota,
             fecha_inicio, fecha_vencimiento, ordenes_odoo, estado, notas, creado_por)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        body['cliente_id'], body.get('cliente_nombre', ''),
        body['descripcion'], monto,
        body.get('moneda', 'USD'),
        plazo, periodicidad, porc, monto_cuota,
        fecha_inicio, fecha_venc,
        json.dumps(body.get('ordenes_odoo', [])),
        'activo',
        body.get('notas', ''),
        user['id'],
    ))
    acuerdo_id = cur.lastrowid
    _generar_cuotas(acuerdo_id, fecha_inicio, plazo, periodicidad,
                    monto, monto_cuota, porc, con)
    con.commit()
    con.close()
    return {'id': acuerdo_id, 'mensaje': 'Acuerdo creado'}


@router.put('/{acuerdo_id}')
def actualizar_acuerdo(acuerdo_id: int, body: dict, user=Depends(get_current_user)):
    con = get_con()
    acuerdo = con.execute("SELECT * FROM acuerdos_pago WHERE id=?", (acuerdo_id,)).fetchone()
    if not acuerdo:
        con.close()
        raise HTTPException(status_code=404, detail='Acuerdo no encontrado')
    campos = ['descripcion', 'estado', 'notas', 'ordenes_odoo']
    sets = [f"{c}=?" for c in campos if c in body]
    vals = [body[c] for c in campos if c in body]
    if sets:
        con.execute(f"UPDATE acuerdos_pago SET {','.join(sets)} WHERE id=?", vals + [acuerdo_id])
        con.commit()
    con.close()
    return {'ok': True}


@router.put('/{acuerdo_id}/cuotas/{cuota_id}/pagar')
def marcar_cuota_pagada(acuerdo_id: int, cuota_id: int, body: dict,
                        user=Depends(get_current_user)):
    monto_pagado = float(body.get('monto_pagado') or 0)
    con = get_con()
    cuota = con.execute(
        "SELECT * FROM acuerdos_pago_cuotas WHERE id=? AND acuerdo_id=?",
        (cuota_id, acuerdo_id)
    ).fetchone()
    if not cuota:
        con.close()
        raise HTTPException(status_code=404, detail='Cuota no encontrada')
    esperado = cuota['monto_esperado']
    estado = 'pagado' if monto_pagado >= esperado else 'parcial'
    con.execute(
        "UPDATE acuerdos_pago_cuotas SET monto_pagado=?, estado=?, notas=? WHERE id=?",
        (monto_pagado, estado, body.get('notas', ''), cuota_id)
    )
    # Verificar si el acuerdo está cumplido
    pendientes = con.execute(
        "SELECT COUNT(*) FROM acuerdos_pago_cuotas WHERE acuerdo_id=? AND estado NOT IN ('pagado')",
        (acuerdo_id,)
    ).fetchone()[0]
    if pendientes == 0:
        con.execute("UPDATE acuerdos_pago SET estado='cumplido' WHERE id=?", (acuerdo_id,))
    con.commit()
    con.close()
    return {'ok': True, 'estado_cuota': estado}


@router.delete('/{acuerdo_id}')
def eliminar_acuerdo(acuerdo_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    con.execute("DELETE FROM acuerdos_pago_cuotas WHERE acuerdo_id=?", (acuerdo_id,))
    con.execute("DELETE FROM acuerdos_pago WHERE id=?", (acuerdo_id,))
    con.commit()
    con.close()
    return {'ok': True}
