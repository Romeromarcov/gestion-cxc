import time
import logging
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
import jwt as pyjwt
from datetime import datetime, timedelta, timezone
from database import get_con
from models.operaciones import LoginRequest, UsuarioCreate, UsuarioUpdate
from models.schemas import row_to_dict, rows_to_list
from config import SECRET_KEY, ACCESS_TOKEN_EXPIRE_HOURS

router = APIRouter(prefix='/auth', tags=['auth'])
security = HTTPBearer()
pwd_ctx = CryptContext(schemes=['bcrypt'], deprecated='auto')
logger = logging.getLogger(__name__)

ALGORITHM = 'HS256'

# Rate limiting: máx 5 intentos de login por IP en ventana de 60 segundos
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60.0


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    attempts = _login_attempts[ip]
    _login_attempts[ip] = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    if len(_login_attempts[ip]) >= _RATE_LIMIT_MAX:
        logger.warning('rate limit de login alcanzado para IP %s', ip)
        raise HTTPException(status_code=429, detail='Demasiados intentos. Intenta en 60 segundos.')
    _login_attempts[ip].append(now)


def create_token(data: dict) -> str:
    payload = data.copy()
    payload['exp'] = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail='Token inválido o expirado')


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    payload = decode_token(creds.credentials)
    con = get_con()
    usuario = row_to_dict(con.execute(
        "SELECT id,nombre,email,rol,activo FROM usuarios WHERE id=?",
        (payload['sub'],)
    ).fetchone())
    con.close()
    if not usuario or not usuario['activo']:
        raise HTTPException(status_code=401, detail='Usuario inactivo o no encontrado')
    return usuario


def require_roles(*roles):
    def checker(user=Depends(get_current_user)):
        if user['rol'] not in roles:
            raise HTTPException(status_code=403, detail='Sin permiso para esta operación')
        return user
    return checker


# ── ENDPOINTS ────────────────────────────────────────────────────────────────

@router.post('/login')
def login(body: LoginRequest, request: Request):
    ip = request.client.host if request.client else 'unknown'
    _check_rate_limit(ip)
    con = get_con()
    user = row_to_dict(con.execute(
        "SELECT * FROM usuarios WHERE email=? AND activo=1", (body.email,)
    ).fetchone())
    con.close()
    if not user or not pwd_ctx.verify(body.password, user['password_hash']):
        logger.warning('intento de login fallido para email=%s desde IP=%s', body.email, ip)
        raise HTTPException(status_code=401, detail='Credenciales incorrectas')
    # login exitoso: limpiar contador de intentos
    _login_attempts.pop(ip, None)
    token = create_token({'sub': user['id'], 'rol': user['rol']})
    return {'access_token': token, 'token_type': 'bearer',
            'usuario': {'id': user['id'], 'nombre': user['nombre'],
                        'email': user['email'], 'rol': user['rol']}}


@router.get('/me')
def me(user=Depends(get_current_user)):
    return user


@router.get('/usuarios')
def listar_usuarios(user=Depends(require_roles('admin', 'gerente'))):
    con = get_con()
    rows = rows_to_list(con.execute(
        "SELECT id,nombre,email,rol,activo,creado_en FROM usuarios"
    ).fetchall())
    con.close()
    return rows


@router.post('/usuarios')
def crear_usuario(body: UsuarioCreate, user=Depends(require_roles('admin'))):
    pw_hash = pwd_ctx.hash(body.password)
    con = get_con()
    try:
        cur = con.execute(
            "INSERT INTO usuarios(nombre,email,password_hash,rol) VALUES(?,?,?,?)",
            (body.nombre, body.email, pw_hash, body.rol)
        )
        con.commit()
        return {'id': cur.lastrowid, 'mensaje': 'Usuario creado'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@router.put('/usuarios/{uid}')
def actualizar_usuario(uid: int, body: UsuarioUpdate,
                       user=Depends(require_roles('admin'))):
    con = get_con()
    if body.nombre:
        con.execute("UPDATE usuarios SET nombre=? WHERE id=?", (body.nombre, uid))
    if body.rol:
        con.execute("UPDATE usuarios SET rol=? WHERE id=?", (body.rol, uid))
    if body.activo is not None:
        con.execute("UPDATE usuarios SET activo=? WHERE id=?", (body.activo, uid))
    con.commit()
    con.close()
    return {'mensaje': 'Usuario actualizado'}
