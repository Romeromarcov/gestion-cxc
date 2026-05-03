"""CRM de seguimiento de cobranza."""
from fastapi import APIRouter, HTTPException, Depends
from routers.auth import get_current_user, require_roles
from models.schemas import rows_to_list
from database import get_con
from datetime import date as _date

router = APIRouter(prefix='/cobranza', tags=['cobranza'])


# ── Gestiones ─────────────────────────────────────────────────────────────────

@router.get('/gestiones')
def listar_gestiones(cliente_id: int = None, orden_name: str = None,
                     ejecutivo_id: int = None, desde: str = None, hasta: str = None,
                     user=Depends(get_current_user)):
    con = get_con()
    q = "SELECT g.*, u.nombre as ejecutivo_nombre FROM cobranza_gestiones g LEFT JOIN usuarios u ON u.id=g.ejecutivo_id WHERE 1=1"
    params = []
    if cliente_id:
        q += " AND g.cliente_id=?"; params.append(cliente_id)
    if orden_name:
        q += " AND g.orden_name=?"; params.append(orden_name)
    if ejecutivo_id:
        q += " AND g.ejecutivo_id=?"; params.append(ejecutivo_id)
    if desde:
        q += " AND g.fecha_gestion>=?"; params.append(desde)
    if hasta:
        q += " AND g.fecha_gestion<=?"; params.append(hasta)
    q += " ORDER BY g.fecha_gestion DESC, g.id DESC LIMIT 500"
    rows = rows_to_list(con.execute(q, params).fetchall())
    con.close()
    return rows


@router.post('/gestiones')
def registrar_gestion(body: dict, user=Depends(get_current_user)):
    required = ['cliente_id', 'fecha_gestion', 'tipo_contacto', 'resultado']
    for f in required:
        if not body.get(f):
            raise HTTPException(status_code=422, detail=f'Campo requerido: {f}')
    con = get_con()
    cur = con.execute("""
        INSERT INTO cobranza_gestiones
            (cliente_id, cliente_nombre, orden_name, ejecutivo_id,
             fecha_gestion, tipo_contacto, resultado,
             monto_prometido, fecha_promesa, comentarios,
             proxima_accion, fecha_proxima)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        body['cliente_id'],
        body.get('cliente_nombre', ''),
        body.get('orden_name'),
        body.get('ejecutivo_id') or user['id'],
        body['fecha_gestion'],
        body['tipo_contacto'],
        body['resultado'],
        body.get('monto_prometido'),
        body.get('fecha_promesa'),
        body.get('comentarios', ''),
        body.get('proxima_accion', ''),
        body.get('fecha_proxima'),
    ))
    con.commit()
    gestion = rows_to_list(con.execute(
        "SELECT * FROM cobranza_gestiones WHERE id=?", (cur.lastrowid,)
    ).fetchall())
    con.close()
    return gestion[0] if gestion else {'id': cur.lastrowid}


@router.put('/gestiones/{gestion_id}')
def actualizar_gestion(gestion_id: int, body: dict, user=Depends(get_current_user)):
    con = get_con()
    existe = con.execute("SELECT id FROM cobranza_gestiones WHERE id=?", (gestion_id,)).fetchone()
    if not existe:
        con.close()
        raise HTTPException(status_code=404, detail='Gestión no encontrada')
    campos = ['tipo_contacto', 'resultado', 'monto_prometido', 'fecha_promesa',
              'comentarios', 'proxima_accion', 'fecha_proxima']
    sets = [f"{c}=?" for c in campos if c in body]
    vals = [body[c] for c in campos if c in body]
    if sets:
        con.execute(f"UPDATE cobranza_gestiones SET {','.join(sets)} WHERE id=?",
                    vals + [gestion_id])
        con.commit()
    con.close()
    return {'ok': True}


@router.delete('/gestiones/{gestion_id}')
def eliminar_gestion(gestion_id: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    con.execute("DELETE FROM cobranza_gestiones WHERE id=?", (gestion_id,))
    con.commit()
    con.close()
    return {'ok': True}


# ── Agenda del día / próximas acciones ─────────────────────────────────────────

@router.get('/agenda')
def agenda_hoy(user=Depends(get_current_user)):
    """Gestiones con fecha_proxima <= hoy (pendientes de acción)."""
    hoy = _date.today().isoformat()
    con = get_con()
    rows = rows_to_list(con.execute("""
        SELECT g.*, u.nombre as ejecutivo_nombre
        FROM cobranza_gestiones g
        LEFT JOIN usuarios u ON u.id=g.ejecutivo_id
        WHERE g.fecha_proxima <= ? AND g.resultado != 'pago_realizado'
        ORDER BY g.fecha_proxima ASC
        LIMIT 100
    """, (hoy,)).fetchall())
    con.close()
    return rows


# ── Prioridades de cobranza ────────────────────────────────────────────────────

@router.get('/prioridades')
def prioridades_cobranza(user=Depends(get_current_user)):
    """
    Lista de clientes con deuda vencida, ordenada por score de prioridad.
    Score = (dias_vencida * 3) + (monto_pendiente / 100) + (intentos_sin_respuesta * 5)
    """
    con = get_con()
    # Traer ventas con deudas pendientes desde Odoo es responsabilidad del frontend
    # Aquí retornamos stats de gestiones para cruzar
    intentos = rows_to_list(con.execute("""
        SELECT cliente_id, cliente_nombre,
               COUNT(*) as total_gestiones,
               SUM(CASE WHEN resultado IN ('no_contesto','buzon') THEN 1 ELSE 0 END) as sin_respuesta,
               MAX(fecha_gestion) as ultima_gestion,
               SUM(CASE WHEN resultado='promesa_pago' AND fecha_promesa < date('now') THEN 1 ELSE 0 END) as promesas_incumplidas
        FROM cobranza_gestiones
        GROUP BY cliente_id
    """).fetchall())
    con.close()
    return intentos


# ── Plantillas de mensajes ─────────────────────────────────────────────────────

@router.get('/plantillas')
def listar_plantillas(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM cobranza_plantillas ORDER BY tipo, dias_relativos"
    ).fetchall())
    con.close()
    return rows


@router.post('/plantillas')
def crear_plantilla(body: dict, user=Depends(require_roles('gerente', 'admin'))):
    for f in ['nombre', 'tipo', 'mensaje']:
        if not body.get(f):
            raise HTTPException(status_code=422, detail=f'Campo requerido: {f}')
    con = get_con()
    cur = con.execute("""
        INSERT INTO cobranza_plantillas(nombre, tipo, dias_relativos, canal, mensaje, activa)
        VALUES (?,?,?,?,?,1)
    """, (body['nombre'], body['tipo'], body.get('dias_relativos', 0),
          body.get('canal', 'whatsapp'), body['mensaje']))
    con.commit()
    con.close()
    return {'id': cur.lastrowid, 'mensaje': 'Plantilla creada'}


@router.put('/plantillas/{pid}')
def actualizar_plantilla(pid: int, body: dict,
                         user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    campos = ['nombre', 'tipo', 'dias_relativos', 'canal', 'mensaje', 'activa']
    sets = [f"{c}=?" for c in campos if c in body]
    vals = [body[c] for c in campos if c in body]
    if sets:
        con.execute(f"UPDATE cobranza_plantillas SET {','.join(sets)} WHERE id=?",
                    vals + [pid])
        con.commit()
    con.close()
    return {'ok': True}


@router.delete('/plantillas/{pid}')
def eliminar_plantilla(pid: int, user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    con.execute("DELETE FROM cobranza_plantillas WHERE id=?", (pid,))
    con.commit()
    con.close()
    return {'ok': True}


@router.post('/plantillas/{pid}/preview')
def preview_plantilla(pid: int, body: dict, user=Depends(get_current_user)):
    """Genera preview del mensaje sustituyendo variables."""
    con = get_con()
    plantilla = con.execute("SELECT * FROM cobranza_plantillas WHERE id=?", (pid,)).fetchone()
    con.close()
    if not plantilla:
        raise HTTPException(status_code=404, detail='Plantilla no encontrada')
    msg = plantilla['mensaje']
    for k, v in body.items():
        msg = msg.replace(f'{{{k}}}', str(v))
    return {'mensaje': msg}
