"""
Módulo Interno — Compras Internas
Registro de compras locales (no registradas en Odoo) con actualización automática
del inventario interno y generación de Cuenta por Pagar.
Deshabilitado por defecto; se activa desde Configuración → Módulo Interno.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone

from database import get_con
from routers.auth import get_current_user, require_roles
from models.operaciones import CompraInternaCreate
from models.schemas import row_to_dict, rows_to_list
from models.schemas_input import RechazoBody

router = APIRouter(prefix='/compras-internas', tags=['compras-internas'])


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
def listar_compras(user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    rows = rows_to_list(con.execute(
        "SELECT * FROM compras_internas ORDER BY creado_en DESC"
    ).fetchall())
    con.close()
    return rows


@router.get('/{compra_id}')
def detalle_compra(compra_id: int, user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)
    compra = row_to_dict(con.execute(
        "SELECT * FROM compras_internas WHERE id=?", (compra_id,)
    ).fetchone())
    if not compra:
        con.close()
        raise HTTPException(status_code=404, detail='Compra no encontrada')
    lineas = rows_to_list(con.execute(
        "SELECT * FROM compras_internas_lineas WHERE compra_id=?", (compra_id,)
    ).fetchall())
    con.close()
    return {**compra, 'lineas': lineas}


@router.post('/crear')
def crear_compra(body: CompraInternaCreate,
                 user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    _require_modulo(con)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Calcular total desde líneas si no se especificó
    total_calculado = sum(
        float(l.cantidad) * float(l.costo_unitario) for l in body.lineas
    ) if body.lineas else body.total_usd

    cur = con.execute("""
        INSERT INTO compras_internas(proveedor, fecha, total_usd, estado)
        VALUES (?,?,?,'confirmada')
    """, (body.proveedor, body.fecha, total_calculado))
    compra_id = cur.lastrowid

    for linea in body.lineas:
        con.execute("""
            INSERT INTO compras_internas_lineas
                (compra_id, producto_codigo, producto_nombre, cantidad, costo_unitario)
            VALUES (?,?,?,?,?)
        """, (compra_id, linea.producto_codigo, linea.producto_nombre,
              linea.cantidad, linea.costo_unitario))

        # Actualizar inventario (UPSERT: suma si ya existe)
        con.execute("""
            INSERT INTO inventario_interno
                (producto_codigo, producto_nombre, stock_actual, costo_usd,
                 ultima_actualizacion)
            VALUES (?,?,?,?,?)
            ON CONFLICT(producto_codigo) DO UPDATE SET
                stock_actual = inventario_interno.stock_actual + excluded.stock_actual,
                costo_usd = excluded.costo_usd,
                ultima_actualizacion = excluded.ultima_actualizacion
        """, (linea.producto_codigo, linea.producto_nombre,
              linea.cantidad, linea.costo_unitario, now_iso))

    # Crear automáticamente una Cuenta por Pagar
    cur_cxp = con.execute("""
        INSERT INTO cuentas_por_pagar
            (compra_id, proveedor, fecha_emision, monto_total, moneda,
             saldo_pendiente, estado)
        VALUES (?,?,?,?,'USD',?,'pendiente')
    """, (compra_id, body.proveedor, body.fecha,
          total_calculado, total_calculado))
    cxp_id = cur_cxp.lastrowid

    con.commit()
    con.close()
    return {
        'id': compra_id,
        'cxp_id': cxp_id,
        'total_usd': total_calculado,
        'mensaje': 'Compra registrada, inventario actualizado y CxP generada',
    }


@router.delete('/{compra_id}')
def anular_compra(compra_id: int,
                  user=Depends(require_roles('admin', 'gerente'))):
    """Marca la compra como anulada (no elimina; la CxP queda cancelada también)."""
    con = get_con()
    _require_modulo(con)
    compra = row_to_dict(con.execute(
        "SELECT id FROM compras_internas WHERE id=?", (compra_id,)
    ).fetchone())
    if not compra:
        con.close()
        raise HTTPException(status_code=404, detail='Compra no encontrada')
    con.execute(
        "UPDATE compras_internas SET estado='anulada' WHERE id=?", (compra_id,)
    )
    con.execute(
        "UPDATE cuentas_por_pagar SET estado='cancelada' WHERE compra_id=?",
        (compra_id,)
    )
    con.commit()
    con.close()
    return {'mensaje': 'Compra anulada y CxP cancelada'}


@router.post('/{compra_id}/solicitar-aprobacion')
def solicitar_aprobacion_compra(compra_id: int, user=Depends(get_current_user)):
    con = get_con()
    compra = row_to_dict(con.execute(
        "SELECT * FROM compras_internas WHERE id=?", (compra_id,)
    ).fetchone())
    if not compra:
        con.close(); raise HTTPException(404, 'Compra no encontrada')
    if compra.get('aprobacion_estado') == 'aprobado':
        con.close(); raise HTTPException(400, 'Ya aprobado')
    con.execute("""UPDATE compras_internas SET aprobacion_estado='pendiente',
                   aprobacion_solicitada_en=? WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), compra_id))
    con.commit(); con.close()
    return {'mensaje': 'Aprobación solicitada'}


@router.post('/{compra_id}/aprobar')
def aprobar_compra(compra_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    compra = row_to_dict(con.execute(
        "SELECT id FROM compras_internas WHERE id=?", (compra_id,)
    ).fetchone())
    if not compra:
        con.close(); raise HTTPException(404, 'Compra no encontrada')
    con.execute("""UPDATE compras_internas SET aprobacion_estado='aprobado',
                   aprobado_por=?, aprobado_en=? WHERE id=?""",
                (user['id'], datetime.now(timezone.utc).isoformat(), compra_id))
    con.commit(); con.close()
    return {'mensaje': 'Compra aprobada'}


@router.post('/{compra_id}/rechazar')
def rechazar_compra(compra_id: int, body: RechazoBody, user=Depends(require_roles('gerente', 'admin'))):
    motivo = body.motivo
    con = get_con()
    compra = row_to_dict(con.execute(
        "SELECT id FROM compras_internas WHERE id=?", (compra_id,)
    ).fetchone())
    if not compra:
        con.close(); raise HTTPException(404, 'Compra no encontrada')
    con.execute("""UPDATE compras_internas SET aprobacion_estado='rechazado',
                   rechazo_motivo=?, aprobado_por=?, aprobado_en=? WHERE id=?""",
                (motivo, user['id'], datetime.now(timezone.utc).isoformat(), compra_id))
    con.commit(); con.close()
    return {'mensaje': 'Compra rechazada'}
