"""
Tests de integración para /auth — login, JWT, rate limiting, cambio de password.

Todos estos tests usan el `client` de conftest (TestClient con SQLite in-memory).

Estructura de respuesta del login:
  {
    "access_token": "...",
    "token_type": "bearer",
    "debe_cambiar_password": false,
    "usuario": { "id": 1, "nombre": "...", "email": "...", "rol": "admin" }
  }
"""
import pytest
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _limpiar_intentos(sqlite_db):
    """Borra todos los intentos de login de la BD SQLite de tests."""
    sqlite_db.execute("DELETE FROM login_attempts")
    sqlite_db.commit()


# ── Tests de login ────────────────────────────────────────────────────────────

class TestLogin:

    def test_login_exitoso_admin(self, client, get_con_patch):
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 200
        data = resp.json()
        assert 'access_token' in data
        assert data['token_type'] == 'bearer'
        assert data['usuario']['rol'] == 'admin'
        assert data['usuario']['email'] == 'admin@test.local'

    def test_login_devuelve_nombre_en_usuario(self, client, get_con_patch):
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 200
        assert 'nombre' in resp.json()['usuario']

    def test_login_password_incorrecto(self, client, get_con_patch, sqlite_db):
        _limpiar_intentos(sqlite_db)
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'password_equivocado',
            })
        assert resp.status_code == 401

    def test_login_usuario_inexistente(self, client, get_con_patch, sqlite_db):
        _limpiar_intentos(sqlite_db)
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'noexiste@test.local',
                'password': 'cualquiera',
            })
        assert resp.status_code == 401

    def test_login_email_vacio(self, client, get_con_patch, sqlite_db):
        _limpiar_intentos(sqlite_db)
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': '',
                'password': 'admin1234',
            })
        # Email vacío: Pydantic (422) o credenciales inválidas (401)
        assert resp.status_code in (401, 422)


# ── Tests de JWT ──────────────────────────────────────────────────────────────

class TestJWT:
    """Valida que el token emitido sea decodificable y contenga los campos correctos."""

    def test_token_contiene_id_y_rol(self, client, get_con_patch, sqlite_db):
        import jwt as pyjwt
        from config import SECRET_KEY

        _limpiar_intentos(sqlite_db)
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 200
        token = resp.json()['access_token']
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        assert 'sub' in payload           # ID del usuario
        assert payload['rol'] == 'admin'
        assert 'exp' in payload

    def test_token_invalido_rechazado(self, client):
        resp = client.get('/auth/me', headers={'Authorization': 'Bearer token_falso'})
        assert resp.status_code == 401

    def test_sin_token_rechazado(self, client):
        resp = client.get('/auth/me')
        assert resp.status_code in (401, 403)

    def test_me_con_token_valido(self, client, get_con_patch, sqlite_db):
        _limpiar_intentos(sqlite_db)
        with patch('routers.auth.get_con', get_con_patch):
            resp_login = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp_login.status_code == 200
        token = resp_login.json()['access_token']

        with patch('routers.auth.get_con', get_con_patch):
            resp = client.get('/auth/me', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 200
        assert resp.json()['email'] == 'admin@test.local'


# ── Tests de rate limiting (PostgreSQL-based) ─────────────────────────────────

class TestRateLimiting:
    """
    El rate limiter guarda intentos en login_attempts (SQLite en tests).
    5 intentos fallidos → el 6to intento recibe 429 (aunque la password sea correcta).
    """

    def test_rate_limit_tras_5_fallos(self, client, get_con_patch, sqlite_db):
        # Empezar con tabla limpia para esta IP de test
        _limpiar_intentos(sqlite_db)

        with patch('routers.auth.get_con', get_con_patch):
            for _ in range(5):
                client.post('/auth/login', json={
                    'email': 'admin@test.local',
                    'password': 'password_mal',
                })
            # El 6to intento debe ser bloqueado incluso con la clave correcta
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 429
        assert 'Demasiados intentos' in resp.json()['detail']

    def test_rate_limit_se_limpia_tras_login_exitoso(self, client, get_con_patch, sqlite_db):
        """Después de un login exitoso los intentos se borran y el acceso vuelve a la normalidad."""
        _limpiar_intentos(sqlite_db)

        with patch('routers.auth.get_con', get_con_patch):
            # 3 intentos fallidos
            for _ in range(3):
                client.post('/auth/login', json={
                    'email': 'admin@test.local',
                    'password': 'mal',
                })
            # Login exitoso → borra intentos
            resp_ok = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
            assert resp_ok.status_code == 200

            # Ahora puede volver a intentar sin ser bloqueado
            resp_post = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp_post.status_code == 200

    def test_rate_limit_limpieza_manual(self, client, get_con_patch, sqlite_db):
        """Borrar la tabla manualmente resetea el contador."""
        _limpiar_intentos(sqlite_db)

        with patch('routers.auth.get_con', get_con_patch):
            for _ in range(5):
                client.post('/auth/login', json={
                    'email': 'admin@test.local',
                    'password': 'mal',
                })

        # Reset manual (simula que pasaron 60 segundos / limpieza)
        _limpiar_intentos(sqlite_db)

        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 200


# ── Tests de roles ────────────────────────────────────────────────────────────

class TestRoles:
    """require_roles rechaza usuarios sin el rol necesario."""

    def test_vendedor_no_puede_crear_usuario(self, client, get_con_patch, sqlite_db):
        _limpiar_intentos(sqlite_db)
        with patch('routers.auth.get_con', get_con_patch):
            resp_login = client.post('/auth/login', json={
                'email': 'vendedor@test.local',
                'password': 'vendedor123',
            })
        assert resp_login.status_code == 200
        token = resp_login.json()['access_token']

        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/usuarios', json={
                'nombre': 'Nuevo',
                'email': 'nuevo@test.local',
                'password': 'pass123',
                'rol': 'vendedor',
            }, headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 403
