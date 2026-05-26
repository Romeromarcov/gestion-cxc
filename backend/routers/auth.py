import random
import logging
import bcrypt as _bcrypt
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

# ── Rate limiting persistente en PostgreSQL ───────────────────────────────────
# Sin Redis: usamos la misma BD ya disponible.
# Ventaja: funciona con múltiples workers/réplicas y sobrevive reinicios.

_RATE_LIMIT_MAX    = 5      # intentos fallidos antes de bloquear
_RATE_LIMIT_WINDOW = 60     # segundos de ventana deslizante


def _check_rate_limit_db(ip: str, con) -> None:
    """
    Cuenta intentos fallidos de esta IP en la ventana reciente.
    Si supera el límite → HTTPException 429.
    Siempre registra el intento actual (para contarlo en futuras llamadas).
    Hace limpieza lazy del 5 % de las veces (evita crecer indefinidamente).
    """
    ventana = (
        datetime.now(timezone.utc) - timedelta(seconds=_RATE_LIMIT_WINDOW)
    ).isoformat()

    row = con.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip=? AND intentado_en > ?",
        (ip, ventana),
    ).fetchone()
    count = row[0] if row else 0

    if count >= _RATE_LIMIT_MAX:
        logger.warning('rate limit alcanzado IP=%s (%d intentos en %ds)', ip, count, _RATE_LIMIT_WINDOW)
        raise HTTPException(
            status_code=429,
            detail='Demasiados intentos de inicio de sesión. Intenta de nuevo en 60 segundos.',
        )

    con.execute(
        "INSERT INTO login_attempts(ip, intentado_en) VALUES(?,?)",
        (ip, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()

    # Limpieza lazy: borrar registros de hace más de 2 horas (≈5 % de las veces)
    if random.random() < 0.05:
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        con.execute("DELETE FROM login_attempts WHERE intentado_en < ?", (old,))
        con.commit()


def _clear_attempts(ip: str, con) -> None:
    """Elimina los intentos de una IP tras un login exitoso."""
    con.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
    con.commit()


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
    con = get_con()
    try:
        # 1. Rate limit (registra el intento en BD; lanza 429 si excede el límite)
        _check_rate_limit_db(ip, con)

        # 2. Verificar credenciales
        user = row_to_dict(con.execute(
            "SELECT * FROM usuarios WHERE email=? AND activo=1", (body.email,)
        ).fetchone())

        if not user or not _verify_password(body.password, user['password_hash']):
            logger.warning('login fallido email=%s IP=%s', body.email, ip)
            raise HTTPException(status_code=401, detail='Credenciales incorrectas')

        # 3. Login exitoso: borrar intentos acumulados de esta IP
        _clear_attempts(ip, con)

        token = create_token({'sub': user['id'], 'rol': user['rol']})
        debe_cambiar = bool(user.get('debe_cambiar_password'))
        logger.info('login exitoso email=%s debe_cambiar=%s', body.email, debe_cambiar)
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
    finally:
        con.close()


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
