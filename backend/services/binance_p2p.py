"""
Scraper Binance P2P — tasa USDT/VES.

Algoritmo:
  - Obtiene las primeras 5 ofertas de COMPRA (BUY)  → lo que pagan los compradores
  - Obtiene las primeras 5 ofertas de VENTA  (SELL) → lo que piden los vendedores
  - Promedia los 10 precios juntos → tasa de referencia de mercado P2P
  - Persiste como tasa_custom en tasas_cambio (par=USD_VES, fuente='binance_p2p')
  - Actualiza cada 30 minutos desde el scheduler.

Motivo para usar solo 5+5:
  Las primeras 5 ofertas de cada lado reflejan el precio de mercado real
  (las más competitivas). Promediar 50+ diluye la señal con órdenes marginales.
"""
import httpx
import logging
from datetime import date

logger = logging.getLogger(__name__)

_URL = 'https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search'
_HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}


async def _fetch_prices(trade_type: str, rows: int = 5) -> list[float]:
    """
    Pide las primeras `rows` ofertas de Binance P2P para USDT/VES.
    trade_type: 'BUY' (compradores) | 'SELL' (vendedores).
    Devuelve lista de precios (float).
    """
    body = {
        'fiat': 'VES',
        'page': 1,
        'rows': rows,
        'tradeType': trade_type,
        'asset': 'USDT',
        'countries': [],
        'proMerchantAds': False,
        'shieldMerchantAds': False,
        'filterType': 'all',
        'periods': [],
        'additionalKycVerifyFilter': 0,
        'publisherType': None,
        'payTypes': [],
        'classifies': ['mass', 'profession', 'fiat_trade'],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(_URL, json=body, headers=_HEADERS)
        ads = r.json().get('data', [])
        prices = [
            float(ad['adv']['price'])
            for ad in ads
            if ad.get('adv', {}).get('price')
        ]
        return prices
    except Exception as e:
        logger.warning('binance_p2p._fetch_prices(%s): %s', trade_type, e)
        return []


def _filtrar_outliers(precios: list[float]) -> list[float]:
    """Elimina precios que estén más de 2 desviaciones estándar del promedio.

    En el P2P de Binance aparecen ofertas extremas (~774 VES/USD cuando el
    mercado está en ~737) que son outliers obvios y distorsionan el promedio.
    Con pocos datos (5-6 precios) usamos rango intercuartílico o el criterio
    de 1.5×IQR para no perder datos válidos.
    """
    if len(precios) < 3:
        return precios
    precios_sorted = sorted(precios)
    n = len(precios_sorted)
    q1 = precios_sorted[n // 4]
    q3 = precios_sorted[(3 * n) // 4]
    iqr = q3 - q1
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    filtrados = [p for p in precios if lo <= p <= hi]
    return filtrados if filtrados else precios   # nunca devolver lista vacía


async def actualizar_tasa_binance_p2p() -> dict:
    """
    Promedia las primeras 5 ofertas BUY + las primeras 5 ofertas SELL (10 precios)
    y persiste el resultado como tasa_custom en tasas_cambio.

    Usar 5+5 en lugar de 50+50 porque:
      - Las 5 primeras son las más competitivas y representan el precio real
      - Menos requests = menos chance de rate-limiting por Binance
      - El promedio de 10 precios top es más preciso que el de 100 diluidos
    También se filtran outliers (IQR 1.5×) antes de promediar.
    """
    buy_prices  = await _fetch_prices('BUY',  rows=5)
    sell_prices = await _fetch_prices('SELL', rows=5)
    # Filtrar outliers en cada lado antes de combinar
    buy_prices  = _filtrar_outliers(buy_prices)
    sell_prices = _filtrar_outliers(sell_prices)
    all_prices  = buy_prices + sell_prices

    if not all_prices:
        logger.warning('binance_p2p: sin precios disponibles')
        return {'error': 'Sin precios disponibles en Binance P2P'}

    promedio = round(sum(all_prices) / len(all_prices), 4)

    from database import get_con
    hoy = date.today().isoformat()
    con = get_con()
    existing = con.execute(
        "SELECT id FROM tasas_cambio WHERE par='USD_VES' AND fecha=? AND fuente='binance_p2p'",
        (hoy,)
    ).fetchone()
    if existing:
        con.execute(
            "UPDATE tasas_cambio SET tasa_custom=? WHERE id=?",
            (promedio, existing[0])
        )
    else:
        con.execute(
            "INSERT INTO tasas_cambio(fecha, par, tasa_custom, fuente) VALUES(?,?,?,?)",
            (hoy, 'USD_VES', promedio, 'binance_p2p')
        )
    con.commit()
    con.close()

    logger.info(
        'binance_p2p: %d BUY %s + %d SELL %s → PROMEDIO=%.4f (filtrado outliers)',
        len(buy_prices), buy_prices,
        len(sell_prices), sell_prices,
        promedio,
    )
    return {
        'fecha': hoy,
        'buy_count': len(buy_prices),
        'sell_count': len(sell_prices),
        'total_precios': len(all_prices),
        'promedio': promedio,
        'buy_precios': buy_prices,
        'sell_precios': sell_prices,
        'buy_min': min(buy_prices)  if buy_prices  else None,
        'buy_max': max(buy_prices)  if buy_prices  else None,
        'sell_min': min(sell_prices) if sell_prices else None,
        'sell_max': max(sell_prices) if sell_prices else None,
    }
