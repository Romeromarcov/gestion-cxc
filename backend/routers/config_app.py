from fastapi import APIRouter, HTTPException, Depends
from database import get_con
from routers.auth import get_current_user, require_roles
from models.operaciones import MonedaCreate, MetodoPagoCreate
from models.schemas import rows_to_list, row_to_dict

router = APIRouter(prefix='/config', tags=['configuracion'])


@router.get('/monedas')
def listar_monedas(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute("SELECT * FROM monedas ORDER BY codigo").fetchall())
    con.close()
    return rows


@router.post('/monedas')
def crear_moneda(body: MonedaCreate,
                 user=Depends(require_roles('admin'))):
    con = get_con()
    try:
        con.execute("""
            INSERT INTO monedas(codigo,nombre,simbolo,activa) VALUES(?,?,?,?)
        """, (body.codigo, body.nombre, body.simbolo, body.activa))
        con.commit()
        con.close()
        return {'mensaje': 'Moneda creada'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/monedas/{codigo}/toggle')
def toggle_moneda(codigo: str, user=Depends(require_roles('admin'))):
    con = get_con()
    con.execute(
        "UPDATE monedas SET activa = 1 - activa WHERE codigo=?", (codigo,)
    )
    con.commit()
    con.close()
    return {'mensaje': 'Estado de moneda actualizado'}


@router.get('/metodos-pago')
def listar_metodos(user=Depends(get_current_user)):
    con = get_con()
    rows = rows_to_list(con.execute("SELECT * FROM metodos_pago ORDER BY nombre").fetchall())
    con.close()
    return rows


@router.post('/metodos-pago')
def crear_metodo(body: MetodoPagoCreate,
                 user=Depends(require_roles('admin'))):
    con = get_con()
    try:
        con.execute("""
            INSERT INTO metodos_pago(nombre,monedas_permitidas,odoo_journal_id)
            VALUES(?,?,?)
        """, (body.nombre, body.monedas_permitidas, body.odoo_journal_id))
        con.commit()
        con.close()
        return {'mensaje': 'Método de pago creado'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/metodos-pago/{mid}/toggle')
def toggle_metodo(mid: int, user=Depends(require_roles('admin'))):
    con = get_con()
    con.execute("UPDATE metodos_pago SET activo = 1 - activo WHERE id=?", (mid,))
    con.commit()
    con.close()
    return {'mensaje': 'Estado actualizado'}


# ── CONFIGURACIÓN NOTAS DE CRÉDITO ───────────────────────────────────────────

@router.get('/notas-credito')
def get_config_notas(user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    rows = rows_to_list(con.execute("SELECT * FROM config_nota_credito").fetchall())
    con.close()
    return {r['clave']: r['valor'] for r in rows}


@router.put('/notas-credito')
def update_config_notas(body: dict, user=Depends(require_roles('admin'))):
    """Actualiza una o más claves de configuración: {clave: valor}"""
    allowed = {'requiere_pago', 'moneda_pago', 'dias_max_entrega', 'descuento_maximo'}
    con = get_con()
    for clave, valor in body.items():
        if clave in allowed:
            con.execute("UPDATE config_nota_credito SET valor=? WHERE clave=?",
                        (str(valor), clave))
    con.commit()
    con.close()
    return {'mensaje': 'Configuración guardada'}


# ── CONDICIONES NC (multi-condición, multi-moneda) ───────────────────────────

@router.get('/nc-condiciones')
def listar_nc_condiciones(user=Depends(require_roles('gerente', 'admin'))):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT * FROM nc_condiciones ORDER BY id"
    ).fetchall())
    con.close()
    return rows


@router.post('/nc-condiciones')
def crear_nc_condicion(body: dict, user=Depends(require_roles('admin'))):
    import json
    for f in ['nombre', 'monedas', 'descuento_max_pct']:
        if body.get(f) is None:
            raise HTTPException(status_code=422, detail=f'Campo requerido: {f}')
    monedas = body['monedas'] if isinstance(body['monedas'], str) else json.dumps(body['monedas'])
    con = get_con()
    cur = con.execute("""
        INSERT INTO nc_condiciones(nombre, monedas, descuento_max_pct, requiere_pago, dias_max_entrega, activa)
        VALUES (?,?,?,?,?,1)
    """, (body['nombre'], monedas,
          float(body['descuento_max_pct']),
          int(body.get('requiere_pago', 0)),
          int(body.get('dias_max_entrega', 0))))
    con.commit()
    con.close()
    return {'id': cur.lastrowid, 'mensaje': 'Condición creada'}


@router.put('/nc-condiciones/{cid}')
def actualizar_nc_condicion(cid: int, body: dict, user=Depends(require_roles('admin'))):
    import json
    con = get_con()
    if not con.execute("SELECT id FROM nc_condiciones WHERE id=?", (cid,)).fetchone():
        con.close()
        raise HTTPException(status_code=404, detail='Condición no encontrada')
    campos = ['nombre', 'descuento_max_pct', 'requiere_pago', 'dias_max_entrega', 'activa']
    sets = [f"{c}=?" for c in campos if c in body]
    vals = [body[c] for c in campos if c in body]
    if 'monedas' in body:
        monedas = body['monedas'] if isinstance(body['monedas'], str) else json.dumps(body['monedas'])
        sets.append("monedas=?"); vals.append(monedas)
    if sets:
        con.execute(f"UPDATE nc_condiciones SET {','.join(sets)} WHERE id=?", vals + [cid])
        con.commit()
    con.close()
    return {'ok': True}


@router.delete('/nc-condiciones/{cid}')
def eliminar_nc_condicion(cid: int, user=Depends(require_roles('admin'))):
    con = get_con()
    con.execute("DELETE FROM nc_condiciones WHERE id=?", (cid,))
    con.commit()
    con.close()
    return {'ok': True}
