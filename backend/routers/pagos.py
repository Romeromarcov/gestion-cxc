from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, date
from database import get_con
from routers.auth import get_current_user, require_roles
from routers.ventas import get_odoo
from models.operaciones import PagoCreate, TasaCustomRequest
from models.schemas import row_to_dict, rows_to_list
from services.tasas_cambio import (tasa_bcv_hoy, tasa_custom_hoy, convertir,
                                    obtener_tasa_bcv)
from services.google_sheets import exportar_pagos

router = APIRouter(prefix='/pagos', tags=['pagos'])


def _calcular_equivalencias(monto: float, moneda: str,
                             tasa_bcv: float, tasa_custom: float) -> dict:
    equiv_usd = None
    equiv_ves = None
    if moneda == 'USD':
        equiv_usd = monto
        if tasa_bcv:
            equiv_ves = monto * tasa_bcv
    elif moneda == 'VES':
        equiv_ves = monto
        if tasa_bcv:
            equiv_usd = monto / tasa_bcv
    elif moneda == 'USDT':
        equiv_usd = monto
        if tasa_bcv:
            equiv_ves = monto * tasa_bcv
    elif moneda == 'EUR':
        tasa_eur = tasa_bcv_hoy('EUR_VES')
        if tasa_eur:
            equiv_ves = monto * tasa_eur
            if tasa_bcv:
                equiv_usd = equiv_ves / tasa_bcv
    return {'equivalente_usd': equiv_usd, 'equivalente_ves': equiv_ves}


@router.post('/registrar')
def registrar_pago(body: PagoCreate, user=Depends(get_current_user)):
    tasa_bcv = body.tasa_bcv or tasa_bcv_hoy()
    tasa_custom = body.tasa_custom or tasa_custom_hoy()
    equivs = _calcular_equivalencias(body.monto, body.moneda, tasa_bcv, tasa_custom)

    con = get_con()
    cur = con.execute("""
        INSERT INTO pagos
            (odoo_order_name, venta_interna_id, vendedor_id, monto, moneda,
             metodo, banco, tasa_bcv, tasa_custom, equivalente_usd, equivalente_ves,
             referencia, fecha_pago)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        body.odoo_order_name, body.venta_interna_id, user['id'],
        body.monto, body.moneda, body.metodo, body.banco,
        tasa_bcv, tasa_custom,
        equivs['equivalente_usd'], equivs['equivalente_ves'],
        body.referencia,
        body.fecha_pago or date.today().isoformat()
    ))
    con.commit()
    pago_id = cur.lastrowid
    con.close()
    return {'id': pago_id, 'mensaje': 'Pago registrado', **equivs}


@router.get('')
def listar_pagos(estado: str = None, user=Depends(get_current_user)):
    con = get_con()
    if user['rol'] == 'vendedor':
        if estado:
            rows = rows_to_list(con.execute(
                "SELECT * FROM pagos WHERE vendedor_id=? AND estado=? ORDER BY creado_en DESC",
                (user['id'], estado)
            ).fetchall())
        else:
            rows = rows_to_list(con.execute(
                "SELECT * FROM pagos WHERE vendedor_id=? ORDER BY creado_en DESC",
                (user['id'],)
            ).fetchall())
    else:
        if estado:
            rows = rows_to_list(con.execute(
                "SELECT * FROM pagos WHERE estado=? ORDER BY creado_en DESC",
                (estado,)
            ).fetchall())
        else:
            rows = rows_to_list(con.execute(
                "SELECT * FROM pagos ORDER BY creado_en DESC"
            ).fetchall())
    con.close()
    return rows


@router.post('/{pago_id}/recibir')
def marcar_recibido(pago_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    pago = row_to_dict(con.execute(
        "SELECT * FROM pagos WHERE id=?", (pago_id,)
    ).fetchone())
    if not pago or pago['estado'] != 'propuesto':
        con.close()
        raise HTTPException(status_code=400,
                            detail='Pago no encontrado o ya procesado')
    con.execute("""
        UPDATE pagos SET estado='recibido', recibido_por=?, recibido_en=?
        WHERE id=?
    """, (user['id'], datetime.utcnow().isoformat(), pago_id))

    # Auto-registro en maestro de operaciones como ingreso de cobranza
    _registrar_en_maestro(con, pago, user['id'])

    con.commit()
    con.close()
    return {'mensaje': 'Pago marcado como recibido'}


def _registrar_en_maestro(con, pago: dict, usuario_id: int):
    """Crea una entrada en maestro_operaciones cuando un pago es confirmado."""
    # Evitar duplicados
    existe = con.execute(
        "SELECT id FROM maestro_operaciones WHERE pago_id=? AND origen='pago_sistema'",
        (pago['id'],)
    ).fetchone()
    if existe:
        return

    monto = pago.get('monto', 0)
    moneda = pago.get('moneda', 'USD')
    tasa_bcv = pago.get('tasa_bcv')
    tasa_real = pago.get('tasa_custom')
    fecha = pago.get('fecha_pago') or date.today().isoformat()

    # Calcular equivalente USD
    monto_usd_bcv = None
    monto_real_usd = None
    if moneda in ('USD', 'USDT'):
        monto_usd_bcv = float(monto)
        monto_real_usd = float(monto)
    elif moneda == 'VES':
        if tasa_bcv:
            monto_usd_bcv = float(monto) / tasa_bcv
        if tasa_real:
            monto_real_usd = float(monto) / tasa_real
    elif moneda == 'EUR':
        tasa_eur = tasa_bcv_hoy('EUR_VES')
        if tasa_eur and tasa_bcv:
            monto_usd_bcv = (float(monto) * tasa_eur) / tasa_bcv
        if tasa_eur and tasa_real:
            monto_real_usd = (float(monto) * tasa_eur) / tasa_real

    referencia = pago.get('odoo_order_name') or pago.get('referencia') or ''
    descripcion = f"Cobranza {referencia}".strip() if referencia else 'Cobranza'

    con.execute("""
        INSERT INTO maestro_operaciones
            (fecha, nro_documento, monto, moneda, metodo, tipo, categoria,
             descripcion, tasa_bcv, monto_usd_bcv, tasa_real, monto_real_usd,
             origen, pago_id, estado, creado_por)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        fecha,
        pago.get('referencia'),
        float(monto),
        moneda,
        pago.get('metodo'),
        'ingreso',
        'Cobranza',
        descripcion,
        tasa_bcv,
        monto_usd_bcv,
        tasa_real,
        monto_real_usd,
        'pago_sistema',
        pago['id'],
        'confirmado',
        usuario_id
    ))


@router.post('/{pago_id}/enviar-odoo')
def enviar_a_odoo(pago_id: int, journal_id: int,
                  user=Depends(require_roles('gerente', 'admin'))):
    odoo = get_odoo()
    con = get_con()
    pago = row_to_dict(con.execute(
        "SELECT * FROM pagos WHERE id=?", (pago_id,)
    ).fetchone())
    if not pago or pago['estado'] != 'recibido':
        con.close()
        raise HTTPException(status_code=400,
                            detail='El pago debe estar en estado "recibido"')

    # Obtener partner_id desde Odoo
    partner_id = None
    if pago.get('odoo_order_name'):
        ordenes = odoo.get_venta_por_nombre(pago['odoo_order_name'])
        if ordenes:
            partner_id = ordenes[0]['partner_id'][0]

    if not partner_id:
        con.close()
        raise HTTPException(status_code=422,
                            detail='No se pudo obtener el cliente de Odoo')

    try:
        odoo_payment_id = odoo.crear_pago_borrador(
            partner_id=partner_id,
            monto=pago['equivalente_usd'] or pago['monto'],
            fecha=pago['fecha_pago'] or date.today().isoformat(),
            journal_id=journal_id,
            ref=pago.get('referencia', '')
        )
        con.execute("""
            UPDATE pagos SET estado='enviado_odoo', odoo_payment_id=? WHERE id=?
        """, (odoo_payment_id, pago_id))
        # Propagar odoo_payment_id al maestro de operaciones si existe
        con.execute("""
            UPDATE maestro_operaciones
            SET odoo_payment_id=?, odoo_journal_id=?
            WHERE pago_id=? AND origen='pago_sistema'
        """, (odoo_payment_id, journal_id, pago_id))
        con.commit()
        con.close()
        return {'mensaje': 'Pago enviado a Odoo como borrador',
                'odoo_payment_id': odoo_payment_id}
    except Exception as e:
        con.close()
        raise HTTPException(status_code=502, detail=str(e))


@router.post('/exportar-sheets')
def exportar_a_sheets(user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    pagos = rows_to_list(con.execute("""
        SELECT p.*, u.nombre as vendedor
        FROM pagos p
        LEFT JOIN usuarios u ON u.id = p.vendedor_id
        WHERE p.estado = 'recibido'
    """).fetchall())
    con.close()

    if not pagos:
        return {'mensaje': 'No hay pagos recibidos para exportar'}

    resultado = exportar_pagos(pagos)
    if 'error' in resultado:
        raise HTTPException(status_code=500, detail=resultado['error'])

    # Marcar como enviados a sheets
    con = get_con()
    for p in pagos:
        con.execute(
            "UPDATE pagos SET estado='enviado_sheets' WHERE id=?", (p['id'],)
        )
    con.commit()
    con.close()
    return {'mensaje': f'{len(pagos)} pagos exportados a Google Sheets'}


# ── TASAS DE CAMBIO ───────────────────────────────────────────────────────────

@router.get('/tasas/hoy')
def tasas_hoy(user=Depends(get_current_user)):
    return {
        'usd_ves_bcv':   tasa_bcv_hoy('USD_VES'),
        'eur_ves_bcv':   tasa_bcv_hoy('EUR_VES'),
        'usd_ves_custom': tasa_custom_hoy('USD_VES'),
        'eur_ves_custom': tasa_custom_hoy('EUR_VES'),
    }


@router.post('/tasas/actualizar-bcv')
async def actualizar_bcv(user=Depends(require_roles('gerente', 'admin'))):
    resultado = await obtener_tasa_bcv()
    return resultado


@router.post('/tasas/custom')
def guardar_tasa_custom(body: TasaCustomRequest,
                        user=Depends(require_roles('gerente', 'admin'))):
    from services.tasas_cambio import tasa_bcv_hoy
    con = get_con()
    hoy = body.fecha or date.today().isoformat()
    tasa_bcv = tasa_bcv_hoy(body.par)
    con.execute("""
        INSERT INTO tasas_cambio(fecha, par, tasa_bcv, tasa_custom, fuente)
        VALUES(?,?,?,?,'manual')
    """, (hoy, body.par, tasa_bcv, body.tasa_custom))
    con.commit()
    con.close()
    return {'mensaje': 'Tasa custom guardada', 'par': body.par,
            'tasa_custom': body.tasa_custom}


@router.get('/tasas/historial')
def historial_tasas(par: str = 'USD_VES', limite: int = 30,
                    user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute("""
        SELECT * FROM tasas_cambio WHERE par=?
        ORDER BY fecha DESC, id DESC LIMIT ?
    """, (par, limite)).fetchall())
    con.close()
    return rows
