import time
import logging
import bcrypt as _bcrypt
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt as pyjwt
from datetime import datetime, timedelta, timezone
from database import get_con
from models.operaciones import LoginRequest, UsuarioCreate, UsuarioUpdate
from models.schemas_input import CambiarPasswordRequest
from models.schemas import row_to_dict, rows_to_list
from config import SECRET_KEY, ACCESS_TOKEN_EXPIRE_HOURS

router = APIRouter(prefix='/auth', tags=['auth'])
security = HTTPBearer()
logger = logging.getLogger(__name__)


# ── Helpers de password (bcrypt 4.x directo, sin passlib) ────────────────────

def _hash_password(password: str) -> str:
    """Hashea una contraseña con bcrypt. Retorna el hash como string."""
    return _bcrypt.hashpw(password.encode('utf-8'), _bcrypt.gensalt()).decode('utf-8')


def _verify_password(plain: str, hashed: str) -> bool:
    """Verifica una contraseña contra su hash bcrypt."""
    try:
        return _bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

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
        "SELECT id,nombre,email,rol,activo,debe_cambiar_password FROM usuarios WHERE id=?",
        (payload['sub'],)
    ).fetchone())
    con.close()
    if not usuario or not usuario['activo']:
        raise HTTPException(status_code=401, detail='Usuario inactivo o no encontrado')
    return usuario


def get_current_user_strict(creds: HTTPAuthorizationCredentials = Depends(security)):
    """
    Como get_current_user pero bloquea el acceso si el usuario tiene
    debe_cambiar_password=1. Usar en endpoints que no sean el propio cambio de password.
    """
    user = get_current_user(creds)
    if user.get('debe_cambiar_password'):
        raise HTTPException(
            status_code=403,
            detail={
                'code': 'PASSWORD_CHANGE_REQUIRED',
                'mensaje': 'Debes cambiar tu contraseña antes de continuar.',
                'endpoint': 'PUT /auth/cambiar-password',
            }
        )
    return user


def require_roles(*roles):
    def checker(user=Depends(get_current_user_strict)):
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
    if not user or not _verify_password(body.password, user['password_hash']):
        logger.warning('intento de login fallido para email=%s desde IP=%s', body.email, ip)
        raise HTTPException(status_code=401, detail='Credenciales incorrectas')
    # login exitoso: limpiar contador de intentos
    _login_attempts.pop(ip, None)
    token = create_token({'sub': user['id'], 'rol': user['rol']})
    debe_cambiar = bool(user.get('debe_cambiar_password'))
    logger.info('login exitoso email=%s debe_cambiar_password=%s', body.email, debe_cambiar)
    return {
        'access_token': token,
        'token_type': 'bearer',
        'debe_cambiar_password': debe_cambiar,
        'usuario': {
            'id': user['id'],
            'nombre': user['nombre'],
            'email': user['email'],
            'rol': user['rol'],
        },
    }


@router.put('/cambiar-password')
def cambiar_password(body: CambiarPasswordRequest, user=Depends(get_current_user)):
    """
    Permite al usuario autenticado cambiar su propia contraseña.
    Requerido cuando debe_cambiar_password=1 (contraseña por defecto).
    """
    password_actual = body.password_actual
    password_nueva  = body.password_nueva

    con = get_con()
    try:
        row = row_to_dict(con.execute(
            "SELECT password_hash FROM usuarios WHERE id=?", (user['id'],)
        ).fetchone())
        if not row or not _verify_password(password_actual, row['password_hash']):
            raise HTTPException(status_code=401, detail='Contraseña actual incorrecta')

        nuevo_hash = _hash_password(password_nueva)
        con.execute(
            "UPDATE usuarios SET password_hash=?, debe_cambiar_password=0 WHERE id=?",
            (nuevo_hash, user['id'])
        )
        con.commit()
        logger.info('contraseña cambiada para usuario id=%s', user['id'])
        return {'mensaje': 'Contraseña actualizada correctamente'}
    finally:
        con.close()


@router.get('/me')
def me(user=Depends(get_current_user)):
    """Devuelve datos del usuario autenticado, incluyendo si debe cambiar contraseña."""
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
    pw_hash = _hash_password(body.password)
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
    try:
        if body.nombre:
            con.execute("UPDATE usuarios SET nombre=? WHERE id=?", (body.nombre, uid))
        if body.rol:
            con.execute("UPDATE usuarios SET rol=? WHERE id=?", (body.rol, uid))
        if body.activo is not None:
            con.execute("UPDATE usuarios SET activo=? WHERE id=?", (body.activo, uid))
        con.commit()
        return {'mensaje': 'Usuario actualizado'}
    finally:
        con.close()
