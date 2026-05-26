"""
Tests unitarios para db_adapter._translate() y _SqliteCompatRow.

Estos tests son 100% puros — sin BD, sin HTTP, sin fixtures pesadas.
Se ejecutan en milisegundos y validan la lógica de traducción SQL.
"""
import pytest
from db_adapter import _translate


class TestTranslate:
    """Casos de traducción SQL SQLite → PostgreSQL."""

    def test_placeholder_simple(self):
        sql, _ = _translate("SELECT * FROM t WHERE id=?")
        assert '%s' in sql
        assert '?' not in sql

    def test_placeholder_multiple(self):
        sql, _ = _translate("INSERT INTO t(a,b,c) VALUES(?,?,?)")
        assert sql.count('%s') == 3
        assert '?' not in sql

    def test_insert_or_ignore(self):
        sql, has_conflict = _translate(
            "INSERT OR IGNORE INTO usuarios(email) VALUES(?)"
        )
        assert 'INSERT INTO usuarios' in sql
        assert 'OR IGNORE' not in sql
        assert 'ON CONFLICT DO NOTHING' in sql
        assert has_conflict is True

    def test_insert_normal_no_conflict(self):
        sql, has_conflict = _translate(
            "INSERT INTO pagos(monto) VALUES(?)"
        )
        assert has_conflict is False

    def test_on_conflict_do_update(self):
        """INSERT … ON CONFLICT DO UPDATE no debe duplicar ON CONFLICT."""
        raw = """INSERT INTO inventario_interno(codigo, stock) VALUES(?,?)
                 ON CONFLICT(codigo) DO UPDATE SET stock = excluded.stock"""
        sql, has_conflict = _translate(raw)
        assert has_conflict is True
        assert sql.count('ON CONFLICT') == 1

    def test_autoincrement_to_serial(self):
        sql, _ = _translate(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT)"
        )
        assert 'SERIAL PRIMARY KEY' in sql
        assert 'AUTOINCREMENT' not in sql

    def test_strftime_ym(self):
        sql, _ = _translate(
            "SELECT strftime('%Y-%m', fecha) FROM maestro_operaciones"
        )
        assert 'LEFT(fecha, 7)' in sql
        assert 'strftime' not in sql

    def test_strftime_ymd(self):
        sql, _ = _translate(
            "SELECT strftime('%Y-%m-%d', creado_en) FROM pagos"
        )
        assert 'LEFT(creado_en, 10)' in sql
        assert 'strftime' not in sql

    def test_no_mutation_on_plain_select(self):
        """Un SELECT sin placeholders ni funciones especiales no debe cambiar."""
        raw = "SELECT id, nombre FROM usuarios WHERE activo=1"
        sql, has_conflict = _translate(raw)
        assert sql == raw
        assert has_conflict is False

    def test_case_insensitive_insert_or_ignore(self):
        sql, has_conflict = _translate(
            "insert or ignore into t(x) values(?)"
        )
        assert 'OR IGNORE' not in sql.upper() or 'INSERT OR IGNORE' not in sql.upper()
        assert has_conflict is True

    def test_pragma_stays_untouched(self):
        """PRAGMA no tiene traducción en _translate (se ignora en executescript)."""
        sql, _ = _translate("PRAGMA foreign_keys = ON")
        # _translate no toca PRAGMAs — deben pasar sin error
        assert 'PRAGMA' in sql


class TestTranslateReturnType:
    """_translate siempre devuelve (str, bool)."""

    def test_returns_tuple(self):
        result = _translate("SELECT 1")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_second_element_bool(self):
        _, has_conflict = _translate("SELECT 1")
        assert isinstance(has_conflict, bool)
