from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime, timezone
from database import get_con
from routers.auth import get_current_user, require_roles
from models.schemas import rows_to_list, row_to_dict

router = APIRouter(prefix='/creditos', tags=['creditos'])


# ── Helpers ─────────────────────────────────────────────────────────────────

def _get_credito_or_404(con, credito_id: int) -> dict:
    row = row_to_dict(con.execute(
        "SELECT * FROM creditos_cliente WHERE id=?", (credito_id,)
    ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail='Crédito no encontrado')
    return row


# ── Listar ───────────────────────────────────────────────────────────────────

@router.get('')
def listar_creditos(
    estado: Optional[str] = Query(None, description='disponible | aplicado | devuelto | anulado'),
    partner_id: Optional[int] = Query(None),
    motivo: Optional[str] = Query(None, description='sobrepago | ajuste | devolucion'),
    user=Depends(get_current_user),
):
    """Lista créditos de clientes con filtros opcionales."""
    con = get_con()
    clauses = []
    params = []

    if estado:
        clauses.append("estado=?")
        params.append(estado)
    if partner_id:
        clauses.append("partner_id=?")
        params.append(partner_id)
    if motivo:
        clauses.append("motivo=?")
        params.append(motivo)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = rows_to_list(con.execute(
        f"SELECT * FROM creditos_cliente {where} ORDER BY creado_en DESC",
        params
    ).fetchall())
    con.close()
    return rows


@router.get('/disponibles')
def creditos_disponibles(
    partner_id: Optional[int] = Query(None),
    user=Depends(get_current_user),
):
    """Lista créditos en estado 'disponible', opcionalmente filtrados por cliente."""
    con = get_con()
    if partner_id:
        rows = rows_to_list(con.execute(
            "SELECT * FROM creditos_cliente WHERE estado='disponible' AND partner_id=? ORDER BY creado_en DESC",
            (partner_id,)
        ).fetchall())
    else:
        rows = rows_to_list(con.execute(
            "SELECT * FROM creditos_cliente WHERE estado='disponible' ORDER BY creado_en DESC"
        ).fetchall())
    con.close()
    return rows


@router.get('/resumen')
def resumen_creditos(user=Depends(require_roles('gerente', 'admin'))):
    """Totales agrupados por estado y moneda."""
    con = get_con()
    rows = rows_to_list(con.execute("""
        SELECT estado, moneda,
               COUNT(*) as cantidad,
               ROUND(SUM(monto), 4) as total_monto,
               ROUND(SUM(COALESCE(equivalente_usd, monto)), 4) as total_usd
        FROM creditos_cliente
        GROUP BY estado, moneda
        ORDER BY estado, moneda
    """).fetchall())
    con.close()
    return rows


@router.get('/{credito_id}')
def obtener_credito(credito_id: int, user=Depends(get_current_user)):
    con = get_con()
    credito = _get_credito_or_404(con, credito_id)
    con.close()
    return credito


# ── Crear ────────────────────────────────────────────────────────────────────

@router.post('')
def crear_credito(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    """
    Crea un crédito de cliente manualmente (ajuste, devolución, etc.).
    Para sobrepagos detectados automáticamente, el sistema los crea internamente
    desde el endpoint de pagos.

    Body esperado:
    {
        "partner_id": int,            # obligatorio
        "partner_nombre": str,        # obligatorio
        "monto": float,               # obligatorio, > 0
        "moneda": "USD" | "VES",      # obligatorio
        "equivalente_usd": float,     # opcional
        "motivo": "sobrepago" | "ajuste" | "devolucion",  # default: ajuste
        "notas": str,                 # opcional
        "odoo_order_name": str,       # opcional
        "pago_id": int                # opcional
    }
    """
    # Validaciones
    partner_id = body.get('partner_id')
    partner_nombre = body.get('partner_nombre', '').strip()
    monto = body.get('monto')
    moneda = body.get('moneda', 'USD').upper()

    if not partner_id:
        raise HTTPException(status_code=400, detail='partner_id es requerido')
    if not partner_nombre:
        raise HTTPException(status_code=400, detail='partner_nombre es requerido')
    if not monto or float(monto) <= 0:
        raise HTTPException(status_code=400, detail='monto debe ser mayor a 0')
    if moneda not in ('USD', 'VES'):
        raise HTTPException(status_code=400, detail='moneda debe ser USD o VES')

    motivo = body.get('motivo', 'ajuste')
    if motivo not in ('sobrepago', 'ajuste', 'devolucion'):
        raise HTTPException(status_code=400, detail='motivo inválido')

    con = get_con()
    ahora = datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO creditos_cliente
            (partner_id, partner_nombre, odoo_order_name, monto, moneda,
             equivalente_usd, motivo, notas, estado, pago_id, creado_por, creado_en)
        VALUES (?,?,?,?,?,?,?,?,'disponible',?,?,?)
    """, (
        int(partner_id),
        partner_nombre,
        body.get('odoo_order_name') or None,
        float(monto),
        moneda,
        float(body['equivalente_usd']) if body.get('equivalente_usd') else None,
        motivo,
        body.get('notas') or None,
        int(body['pago_id']) if body.get('pago_id') else None,
        user['id'],
        ahora,
    ))
    credito_id = cur.lastrowid
    con.commit()
    credito = _get_credito_or_404(con, credito_id)
    con.close()
    return credito


# ── Actualizar estado ─────────────────────────────────────────────────────────

@router.put('/{credito_id}/aplicar')
def aplicar_credito(credito_id: int, body: dict,
                    user=Depends(require_roles('gerente', 'admin'))):
    """
    Marca un crédito como 'aplicado' a una orden.
    Body: { "aplicado_a_orden": "S00123", "notas": "..." }
    """
    orden = (body.get('aplicado_a_orden') or '').strip()
    if not orden:
        raise HTTPException(status_code=400, detail='aplicado_a_orden es requerido')

    con = get_con()
    credito = _get_credito_or_404(con, credito_id)
    if credito['estado'] != 'disponible':
        con.close()
        raise HTTPException(
            status_code=400,
            detail=f'No se puede aplicar un crédito en estado "{credito["estado"]}"'
        )

    ahora = datetime.now(timezone.utc).isoformat()
    notas_extra = body.get('notas') or ''
    notas_nueva = f"{credito.get('notas') or ''}\n[Aplicado {ahora}] {notas_extra}".strip()

    con.execute("""
        UPDATE creditos_cliente
        SET estado='aplicado', aplicado_a_orden=?, notas=?
        WHERE id=?
    """, (orden, notas_nueva, credito_id))
    con.commit()
    credito = _get_credito_or_404(con, credito_id)
    con.close()
    return credito


@router.put('/{credito_id}/devolver')
def devolver_credito(credito_id: int, body: dict = None,
                     user=Depends(require_roles('gerente', 'admin'))):
    """Marca un crédito como 'devuelto' (reembolsado al cliente)."""
    con = get_con()
    credito = _get_credito_or_404(con, credito_id)
    if credito['estado'] not in ('disponible',):
        con.close()
        raise HTTPException(
            status_code=400,
            detail=f'No se puede devolver un crédito en estado "{credito["estado"]}"'
        )

    ahora = datetime.now(timezone.utc).isoformat()
    notas_extra = (body or {}).get('notas') or ''
    notas_nueva = f"{credito.get('notas') or ''}\n[Devuelto {ahora}] {notas_extra}".strip()

    con.execute("""
        UPDATE creditos_cliente
        SET estado='devuelto', notas=?
        WHERE id=?
    """, (notas_nueva, credito_id))
    con.commit()
    credito = _get_credito_or_404(con, credito_id)
    con.close()
    return credito


@router.put('/{credito_id}/anular')
def anular_credito(credito_id: int, body: dict = None,
                   user=Depends(require_roles('gerente', 'admin'))):
    """Anula un crédito (solo si está disponible)."""
    con = get_con()
    credito = _get_credito_or_404(con, credito_id)
    if credito['estado'] != 'disponible':
        con.close()
        raise HTTPException(
            status_code=400,
            detail=f'Solo se pueden anular créditos disponibles'
        )

    ahora = datetime.now(timezone.utc).isoformat()
    notas_extra = (body or {}).get('notas') or ''
    notas_nueva = f"{credito.get('notas') or ''}\n[Anulado {ahora}] {notas_extra}".strip()

    con.execute("""
        UPDATE creditos_cliente
        SET estado='anulado', notas=?
        WHERE id=?
    """, (notas_nueva, credito_id))
    con.commit()
    credito = _get_credito_or_404(con, credito_id)
    con.close()
    return credito


@router.put('/{credito_id}')
def actualizar_credito(credito_id: int, body: dict,
                       user=Depends(require_roles('gerente', 'admin'))):
    """
    Actualización general de campos editables de un crédito disponible.
    Campos permitidos: notas, equivalente_usd.
    """
    con = get_con()
    credito = _get_credito_or_404(con, credito_id)
    if credito['estado'] != 'disponible':
        con.close()
        raise HTTPException(
            status_code=400,
            detail='Solo se pueden editar créditos en estado disponible'
        )

    sets = []
    params = []
    if 'notas' in body:
        sets.append("notas=?")
        params.append(body['notas'])
    if 'equivalente_usd' in body:
        sets.append("equivalente_usd=?")
        params.append(float(body['equivalente_usd']) if body['equivalente_usd'] is not None else None)

    if not sets:
        con.close()
        raise HTTPException(status_code=400, detail='No hay campos para actualizar')

    params.append(credito_id)
    con.execute(f"UPDATE creditos_cliente SET {', '.join(sets)} WHERE id=?", params)
    con.commit()
    credito = _get_credito_or_404(con, credito_id)
    con.close()
    return credito
