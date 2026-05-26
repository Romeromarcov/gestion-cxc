"""
Tests de integración para /auth — login, JWT, rate limiting, cambio de password.

Todos estos tests usan el `client` de conftest (TestClient con SQLite in-memory).
"""
import pytest
from unittest.mock import patch


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
        assert data['rol'] == 'admin'

    def test_login_password_incorrecto(self, client, get_con_patch):
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'password_equivocado',
            })
        assert resp.status_code == 401

    def test_login_usuario_inexistente(self, client, get_con_patch):
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'noexiste@test.local',
                'password': 'cualquiera',
            })
        assert resp.status_code == 401

    def test_login_email_vacio(self, client):
        resp = client.post('/auth/login', json={
            'email': '',
            'password': 'admin1234',
        })
        # Pydantic valida email vacío o el router devuelve 422/401
        assert resp.status_code in (401, 422)

    def test_login_devuelve_nombre_usuario(self, client, get_con_patch):
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 200
        assert 'nombre' in resp.json()


class TestJWT:
    """Valida que el token emitido sea decodificable y contenga los campos correctos."""

    def test_token_contiene_id_y_rol(self, client, get_con_patch):
        import jwt as pyjwt
        from config import SECRET_KEY

        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        token = resp.json()['access_token']
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        assert 'id' in payload
        assert payload['rol'] == 'admin'
        assert 'exp' in payload

    def test_token_invalido_rechazado(self, client):
        resp = client.get('/auth/me', headers={'Authorization': 'Bearer token_falso'})
        assert resp.status_code == 401

    def test_sin_token_rechazado(self, client):
        resp = client.get('/auth/me')
        assert resp.status_code in (401, 403)

    def test_me_con_token_valido(self, client, get_con_patch):
        with patch('routers.auth.get_con', get_con_patch):
            resp_login = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        token = resp_login.json()['access_token']

        with patch('routers.auth.get_con', get_con_patch):
            resp = client.get('/auth/me',
                              headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 200
        assert resp.json()['email'] == 'admin@test.local'


class TestRateLimiting:
    """El rate limiter en memoria bloquea tras 5 intentos fallidos."""

    def test_rate_limit_tras_5_fallos(self, client, get_con_patch):
        # Limpiar el estado interno del rate limiter antes del test
        from routers.auth import _login_attempts
        _login_attempts.clear()

        with patch('routers.auth.get_con', get_con_patch):
            for _ in range(5):
                client.post('/auth/login', json={
                    'email': 'admin@test.local',
                    'password': 'mal_password',
                })
            # El 6to intento debe ser bloqueado
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 429

    def test_rate_limit_se_limpia(self, client, get_con_patch):
        """Limpiando el estado del rate limiter, el login vuelve a funcionar."""
        from routers.auth import _login_attempts
        _login_attempts.clear()

        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/login', json={
                'email': 'admin@test.local',
                'password': 'admin1234',
            })
        assert resp.status_code == 200


class TestRoles:
    """require_roles rechaza usuarios sin el rol necesario."""

    def test_vendedor_no_puede_crear_usuario(self, client, get_con_patch):
        # Login como vendedor
        with patch('routers.auth.get_con', get_con_patch):
            resp_login = client.post('/auth/login', json={
                'email': 'vendedor@test.local',
                'password': 'vendedor123',
            })
        assert resp_login.status_code == 200
        token = resp_login.json()['access_token']

        # Intentar crear usuario (requiere admin)
        with patch('routers.auth.get_con', get_con_patch):
            resp = client.post('/auth/usuarios', json={
                'nombre': 'Nuevo',
                'email': 'nuevo@test.local',
                'password': 'pass123',
                'rol': 'vendedor',
            }, headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 403
