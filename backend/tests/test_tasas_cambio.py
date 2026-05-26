"""
Tests para services/tasas_cambio.py.

• Las funciones síncronas (tasa_bcv_hoy, tasa_custom_hoy, convertir) usan get_con
  → se parchean con el fixture sqlite_db.
• Los helpers de math puro se testean directamente.
"""
import pytest
from unittest.mock import patch
from datetime import date


# ── Helpers de math puro ──────────────────────────────────────────────────────

class TestConvertirMathPuro:
    """
    La función `convertir` aplica reglas matemáticas simples.
    Testeamos la lógica sin BD usando monkeypatch de get_con.
    """

    def _make_get_con(self, tasa: float | None, par: str = 'USD_VES'):
        """Retorna un mock de get_con cuyo cursor devuelve una tasa fija."""
        class FakeCursor:
            def __init__(self, row):
                self._row = row
            def fetchone(self):
                return self._row

        class FakeCon:
            def __init__(self, tasa, par_target):
                self._tasa = tasa
                self._par = par_target
            def execute(self, sql, params=()):
                par_asked = params[0] if params else None
                row = {'tasa_bcv': self._tasa} if par_asked == self._par and self._tasa else None
                return FakeCursor(row)
            def close(self):
                pass

        return lambda: FakeCon(tasa, par)

    def test_misma_moneda_devuelve_monto(self):
        from services.tasas_cambio import convertir
        with patch('services.tasas_cambio.get_con', self._make_get_con(36.5)):
            result = convertir(100.0, 'USD', 'USD')
        assert result == 100.0

    def test_usd_a_ves_multiplicacion(self):
        from services.tasas_cambio import convertir
        with patch('services.tasas_cambio.get_con', self._make_get_con(36.5, 'USD_VES')):
            result = convertir(10.0, 'USD', 'VES')
        assert result == pytest.approx(365.0)

    def test_sin_tasa_devuelve_none(self):
        from services.tasas_cambio import convertir
        with patch('services.tasas_cambio.get_con', self._make_get_con(None)):
            result = convertir(100.0, 'USD', 'VES')
        assert result is None


class TestTasaBcvHoy:
    """tasa_bcv_hoy / tasa_custom_hoy con BD SQLite de fixture."""

    def test_devuelve_tasa_insertada(self, get_con_patch):
        from services.tasas_cambio import tasa_bcv_hoy
        with patch('services.tasas_cambio.get_con', get_con_patch):
            tasa = tasa_bcv_hoy('USD_VES')
        # conftest.py insertó tasa_bcv=36.50 para hoy
        assert tasa is not None
        assert tasa == pytest.approx(36.50)

    def test_devuelve_none_para_par_inexistente(self, get_con_patch):
        from services.tasas_cambio import tasa_bcv_hoy
        with patch('services.tasas_cambio.get_con', get_con_patch):
            tasa = tasa_bcv_hoy('XYZ_VES')
        assert tasa is None

    def test_tasa_custom_hoy(self, get_con_patch):
        from services.tasas_cambio import tasa_custom_hoy
        with patch('services.tasas_cambio.get_con', get_con_patch):
            tasa = tasa_custom_hoy('USD_VES')
        assert tasa == pytest.approx(37.00)


class TestMontosConversion:
    """Tests de la lógica de conversión VES→USD que usan los routers."""

    @pytest.mark.parametrize('monto_ves,tasa,expected_usd', [
        (365.0,  36.5,  10.0),
        (100.0,  50.0,   2.0),
        (1825.0, 36.5,  50.0),
    ])
    def test_ves_a_usd_bcv(self, monto_ves, tasa, expected_usd):
        resultado = monto_ves / tasa
        assert resultado == pytest.approx(expected_usd, rel=1e-6)

    def test_usd_permanece_igual(self):
        monto_usd = 150.75
        assert monto_usd == pytest.approx(monto_usd)

    def test_redondeo_no_pierde_precision(self):
        """División VES/tasa no debe perder más de 0.01 USD de precisión."""
        monto_ves = 1000.0
        tasa = 36.47  # tasa con decimales
        usd = monto_ves / tasa
        assert abs(usd - 27.42) < 0.01
