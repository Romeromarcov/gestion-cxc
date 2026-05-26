"""
Adaptador de compatibilidad PostgreSQL ↔ sqlite3.

Traduce automáticamente el SQL de este proyecto (escrito para sqlite3) a
PostgreSQL sin modificar los 22 routers. Las traducciones que aplica:

  SQL runtime (INSERT / SELECT / UPDATE / DELETE)
  ─────────────────────────────────────────────────
  • ?  →  %s                            (placeholders)
  • INSERT OR IGNORE INTO t  →  INSERT INTO t … ON CONFLICT DO NOTHING
  • strftime('%Y-%m', col)  →  LEFT(col, 7)   (usado en reportes.py)
  • Inyecta RETURNING id en INSERTs normales → .lastrowid funciona

  DDL (CREATE TABLE / ALTER TABLE)
  ────────────────────────────────
  • INTEGER PRIMARY KEY AUTOINCREMENT  →  SERIAL PRIMARY KEY
  • PRAGMA …  →  ignorado (FK siempre activos en PostgreSQL)

  Filas resultantes
  ──────────────────
  • CompatRow hereda de dict  →  row['col']  y  row[0]  funcionan
    (hay código que usa fetchone()[0] y {r[0] for r in …})

  Gestión de transacciones
  ─────────────────────────
  • Si un execute() falla → rollback automático para mantener la
    conexión usable (patrón try/except en las migraciones).
"""
from __future__ import annotations

import re
import logging
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ── Expresiones regulares de traducción SQL ───────────────────────────────────

_RE_INSERT_IGNORE = re.compile(
    r'\bINSERT\s+OR\s+IGNORE\s+INTO\b', re.IGNORECASE
)
_RE_AUTOINCREMENT = re.compile(
    r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b', re.IGNORECASE
)
_RE_STRFTIME_YM = re.compile(
    r"strftime\s*\(\s*'%Y-%m'\s*,\s*(\w+)\s*\)", re.IGNORECASE
)
_RE_STRFTIME_YMD = re.compile(
    r"strftime\s*\(\s*'%Y-%m-%d'\s*,\s*(\w+)\s*\)", re.IGNORECASE
)


def _translate(sql: str) -> tuple[str, bool]:
    """
    Devuelve (sql_postgresql, tiene_on_conflict).
    tiene_on_conflict indica que no hay que inyectar RETURNING id.
    """
    s = sql

    # 1. Placeholders
    s = s.replace('?', '%s')

    # 2. INSERT OR IGNORE → INSERT … ON CONFLICT DO NOTHING
    #    También detectamos ON CONFLICT explícito (p.ej. DO UPDATE SET) para
    #    evitar añadir RETURNING id en tablas sin columna id (como config_app).
    has_on_conflict = bool(_RE_INSERT_IGNORE.search(s)) or ('ON CONFLICT' in s.upper())
    if _RE_INSERT_IGNORE.search(s):
        s = _RE_INSERT_IGNORE.sub('INSERT INTO', s)
        s = s.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'

    # 3. DDL: AUTOINCREMENT → SERIAL
    s = _RE_AUTOINCREMENT.sub('SERIAL PRIMARY KEY', s)

    # 4. strftime SQLite → equivalente PostgreSQL sobre columnas TEXT 'YYYY-MM-DD'
    s = _RE_STRFTIME_YM.sub(r'LEFT(\1, 7)', s)
    s = _RE_STRFTIME_YMD.sub(r'LEFT(\1, 10)', s)

    return s, has_on_conflict


# ── CompatRow ─────────────────────────────────────────────────────────────────

class CompatRow(dict):
    """
    Fila que se comporta como dict Y como tupla indexada.

    Soporta tanto:
        row['nombre_columna']   (acceso por nombre — en todos los routers)
        row[0]                  (acceso por índice — en reportes y main.py)
    """
    __slots__ = ('_values',)

    def __init__(self, description, values):
        keys = [col.name for col in description]
        super().__init__(zip(keys, values))
        object.__setattr__(self, '_values', list(values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return object.__getattribute__(self, '_values')[key]
        return super().__getitem__(key)


# ── CompatCursor ──────────────────────────────────────────────────────────────

class CompatCursor:
    """Cursor psycopg2 con interfaz sqlite3."""

    __slots__ = ('_conn', '_cur', 'lastrowid', '_rowcount')

    def __init__(self, pg_conn):
        self._conn   = pg_conn
        self._cur    = pg_conn.cursor()
        self.lastrowid: int | None = None
        self._rowcount: int        = 0

    # ── helpers ───────────────────────────────────────────────────────────────

    def _wrap(self, row):
        if row is None:
            return None
        return CompatRow(self._cur.description, row)

    def _auto_rollback(self, exc: Exception) -> None:
        """Rollback silencioso para dejar la conexión usable tras un error."""
        try:
            self._conn.rollback()
        except Exception:
            pass
        logger.debug('db_adapter: rollback tras error — %s', exc)

    # ── interfaz pública ──────────────────────────────────────────────────────

    def execute(self, sql: str, params=None) -> 'CompatCursor':
        pg_sql, has_on_conflict = _translate(sql)
        upper = pg_sql.strip().upper()

        is_insert      = upper.startswith('INSERT')
        needs_returning = (
            is_insert
            and not has_on_conflict
            and 'RETURNING' not in upper
        )
        if needs_returning:
            pg_sql = pg_sql.rstrip().rstrip(';') + ' RETURNING id'

        try:
            self._cur.execute(pg_sql, params or None)
            self._rowcount = self._cur.rowcount

            if needs_returning and self._rowcount > 0:
                row = self._cur.fetchone()
                self.lastrowid = row[0] if row else None
            else:
                self.lastrowid = None

        except psycopg2.Error as exc:
            self._auto_rollback(exc)
            raise

        return self

    def executemany(self, sql: str, seq_of_params) -> 'CompatCursor':
        pg_sql, _ = _translate(sql)
        try:
            psycopg2.extras.execute_batch(self._cur, pg_sql, seq_of_params)
            self._rowcount = self._cur.rowcount
        except psycopg2.Error as exc:
            self._auto_rollback(exc)
            raise
        return self

    def fetchone(self):
        try:
            return self._wrap(self._cur.fetchone())
        except psycopg2.ProgrammingError:
            return None

    def fetchall(self) -> list:
        try:
            return [self._wrap(r) for r in (self._cur.fetchall() or [])]
        except psycopg2.ProgrammingError:
            return []

    @property
    def rowcount(self) -> int:
        return self._rowcount


# ── CompatConnection ──────────────────────────────────────────────────────────

class CompatConnection:
    """
    Conexión psycopg2 con interfaz sqlite3.

    Cuando se construye con `pool=` (lo hace database.get_con()), `close()`
    devuelve la conexión al pool en lugar de cerrarla físicamente.
    Sin pool (p. ej. init_db con conexión directa), `close()` cierra la conexión.
    """

    __slots__ = ('_conn', '_pool')

    def __init__(self, pg_conn, pool=None):
        self._conn = pg_conn
        self._pool = pool  # ThreadedConnectionPool o None

    def execute(self, sql: str, params=None) -> CompatCursor:
        cur = CompatCursor(self._conn)
        return cur.execute(sql, params)

    def executemany(self, sql: str, seq_of_params) -> CompatCursor:
        cur = CompatCursor(self._conn)
        return cur.executemany(sql, seq_of_params)

    def executescript(self, script: str) -> None:
        """
        Ejecuta múltiples sentencias separadas por ';' (usado solo en init_db).
        Omite automáticamente las líneas PRAGMA de SQLite.
        """
        for stmt in script.split(';'):
            clean = stmt.strip()
            if not clean:
                continue
            if clean.upper().startswith('PRAGMA'):
                continue
            try:
                self.execute(clean)
            except Exception as exc:
                # Las sentencias DDL con IF NOT EXISTS raramente fallan;
                # si ocurre, logueamos y continuamos.
                logger.warning('executescript: sentencia omitida — %s | %s',
                               clean[:80], exc)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        """
        Devuelve la conexión al pool (si existe) o la cierra directamente.
        Siempre hace rollback antes de devolver al pool para limpiar
        cualquier transacción abierta accidentalmente.
        """
        if self._pool is not None:
            try:
                self._conn.rollback()   # limpia transacciones abiertas
            except Exception:
                pass
            self._pool.putconn(self._conn)
        else:
            self._conn.close()
