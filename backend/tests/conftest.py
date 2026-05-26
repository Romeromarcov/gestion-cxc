"""
Fixtures compartidas para el test suite de GestionCxC.

Estrategia de aislamiento:
  • La BD real usa PostgreSQL vía psycopg2 + CompatConnection.
  • En tests usamos SQLite in-memory con la misma interfaz de sqlite3
    (row_factory = sqlite3.Row) para que row_to_dict / rows_to_list funcionen.
  • Monkeypatch de `database.get_con` devuelve la conexión SQLite temporal,
    sin tocar la BD real ni requerir credenciales.
  • `main.py` llama `init_db()` en el startup lifespan; TestClient sin
    `with` evita ejecutar ese lifespan (FastAPI ≥ 0.93 permite esto).
"""
import sqlite3
import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────────────

class _SqliteCompatRow(dict):
    """
    Imita CompatRow: soporta acceso por clave (row['col']) y por índice (row[0]).
    Usado para que row_to_dict / rows_to_list funcionen en los tests.
    """
    def __init__(self, sqlite_row: sqlite3.Row):
        super().__init__(dict(sqlite_row))
        self._keys = list(dict(sqlite_row).keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)

    def get(self, key, default=None):
        return super().get(key, default)


class _SqliteCursor:
    """Wrapper fino sobre sqlite3.Cursor que expone .lastrowid."""
    def __init__(self, cur: sqlite3.Cursor):
        self._cur = cur

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        return _SqliteCompatRow(row) if row else None

    def fetchall(self):
        return [_SqliteCompatRow(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for row in self._cur:
            yield _SqliteCompatRow(row)


class _SqliteCompatConn:
    """
    Wrapper sobre sqlite3.Connection que imita la API de CompatConnection
    (execute, executescript, commit, close).
    """
    def __init__(self, con: sqlite3.Connection):
        self._con = con
        self._con.row_factory = sqlite3.Row

    def execute(self, sql: str, params=()):
        # sqlite3 ya habla el mismo dialecto — sin traducción necesaria
        return _SqliteCursor(self._con.execute(sql, params))

    def executescript(self, script: str):
        # executescript en sqlite3 no acepta parámetros; se usa para DDL
        self._con.executescript(script)
        return self

    def commit(self):
        self._con.commit()

    def close(self):
        # No cerramos la conexión compartida dentro del fixture;
        # el fixture se encarga de cerrarla al finalizar el scope.
        pass


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope='session')
def sqlite_db():
    """
    Conexión SQLite in-memory con el esquema mínimo necesario.
    Scope=session → una sola BD para toda la sesión de tests (más rápido).
    """
    con = sqlite3.connect(':memory:', check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')

    # Esquema mínimo: tablas usadas por auth + los routers bajo test
    con.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            rol TEXT DEFAULT 'vendedor',
            activo INTEGER DEFAULT 1,
            debe_cambiar_password INTEGER DEFAULT 0,
            creado_en TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tasas_cambio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            par TEXT NOT NULL,
            tasa_bcv REAL,
            tasa_custom REAL,
            fuente TEXT DEFAULT 'bcv'
        );

        CREATE TABLE IF NOT EXISTS categorias_operacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            categoria TEXT NOT NULL,
            subcategoria TEXT,
            cuenta_odoo TEXT,
            odoo_journal_id INTEGER,
            odoo_account_id INTEGER,
            odoo_account_code TEXT,
            activa INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS maestro_operaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            nro_documento TEXT,
            monto REAL NOT NULL,
            moneda TEXT NOT NULL,
            metodo TEXT,
            tipo TEXT NOT NULL,
            categoria TEXT,
            subcategoria TEXT,
            descripcion TEXT,
            tasa_bcv REAL,
            monto_usd_bcv REAL,
            tasa_real REAL,
            monto_real_usd REAL,
            origen TEXT DEFAULT 'manual',
            pago_id INTEGER,
            odoo_ref TEXT,
            estado TEXT DEFAULT 'confirmado',
            journal_nombre TEXT,
            odoo_payment_id INTEGER,
            odoo_conciliado INTEGER DEFAULT 0,
            odoo_journal_id INTEGER,
            odoo_partner_id INTEGER,
            creado_por INTEGER,
            creado_en TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS config_app (
            clave TEXT PRIMARY KEY,
            valor TEXT,
            descripcion TEXT
        );
    """)
    con.commit()

    # Seed: usuario admin para tests de auth
    import bcrypt as _bcrypt
    pw_hash = _bcrypt.hashpw(b'admin1234', _bcrypt.gensalt()).decode('utf-8')
    con.execute(
        "INSERT OR IGNORE INTO usuarios(nombre,email,password_hash,rol,activo) VALUES(?,?,?,?,?)",
        ('Admin Test', 'admin@test.local', pw_hash, 'admin', 1)
    )
    # Seed: vendedor
    pw_hash2 = _bcrypt.hashpw(b'vendedor123', _bcrypt.gensalt()).decode('utf-8')
    con.execute(
        "INSERT OR IGNORE INTO usuarios(nombre,email,password_hash,rol,activo) VALUES(?,?,?,?,?)",
        ('Vendedor Test', 'vendedor@test.local', pw_hash2, 'vendedor', 1)
    )
    # Seed: tasa BCV de hoy
    from datetime import date
    con.execute(
        "INSERT INTO tasas_cambio(fecha,par,tasa_bcv,tasa_custom) VALUES(?,?,?,?)",
        (date.today().isoformat(), 'USD_VES', 36.50, 37.00)
    )
    con.commit()

    yield con
    con.close()


@pytest.fixture(scope='session')
def get_con_patch(sqlite_db):
    """
    Retorna un callable que devuelve siempre la misma _SqliteCompatConn.
    Listo para usar en monkeypatch.
    """
    compat = _SqliteCompatConn(sqlite_db)
    return lambda: compat


@pytest.fixture(scope='session')
def app(get_con_patch):
    """
    Instancia de la FastAPI app con get_con completamente parchado.
    No ejecuta lifespan (evita init_db + conexión PostgreSQL).
    """
    with patch('database.get_con', get_con_patch):
        from main import app as _app
        yield _app


@pytest.fixture(scope='session')
def client(app):
    """TestClient reutilizable en toda la sesión."""
    # lifespan=False → no ejecuta startup/shutdown (evita init_db)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(scope='session')
def admin_token(client):
    """JWT del usuario admin, obtenido una sola vez por sesión."""
    resp = client.post('/auth/login', json={
        'email': 'admin@test.local',
        'password': 'admin1234',
    })
    assert resp.status_code == 200, resp.text
    return resp.json()['access_token']


@pytest.fixture(scope='session')
def vendedor_token(client):
    """JWT del usuario vendedor."""
    resp = client.post('/auth/login', json={
        'email': 'vendedor@test.local',
        'password': 'vendedor123',
    })
    assert resp.status_code == 200, resp.text
    return resp.json()['access_token']


@pytest.fixture(scope='session')
def admin_headers(admin_token):
    return {'Authorization': f'Bearer {admin_token}'}


@pytest.fixture(scope='session')
def vendedor_headers(vendedor_token):
    return {'Authorization': f'Bearer {vendedor_token}'}
