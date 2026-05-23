"""
Scraper Binance P2P — tasa USDT/VES.

Algoritmo:
  - Obtiene las primeras 20 ofertas de COMPRA (BUY)  → lo que pagan los compradores
  - Obtiene las primeras 20 ofertas de VENTA  (SELL) → lo que piden los vendedores
  - Promedia los 40 precios juntos → tasa de referencia de mercado P2P
  - Persiste como tasa_custom en tasas_cambio (par=USD_VES, fuente='binance_p2p')
  - Actualiza cada 30 minutos desde el scheduler.
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


async def _fetch_prices(trade_type: str, rows: int = 50) -> list[float]:
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


async def actualizar_tasa_binance_p2p() -> dict:
    """
    Promedia las primeras 50 ofertas BUY + las primeras 50 ofertas SELL (hasta 100 precios)
    y persiste el resultado como tasa_custom en tasas_cambio.
    """
    buy_prices, sell_prices = await _fetch_prices('BUY'), await _fetch_prices('SELL')
    all_prices = buy_prices + sell_prices

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
        'binance_p2p: %d ofertas BUY + %d ofertas SELL = %d precios → PROMEDIO=%.4f',
        len(buy_prices), len(sell_prices), len(all_prices), promedio
    )
    return {
        'fecha': hoy,
        'buy_count': len(buy_prices),
        'sell_count': len(sell_prices),
        'total_precios': len(all_prices),
        'promedio': promedio,
        'buy_min': min(buy_prices) if buy_prices else None,
        'buy_max': max(buy_prices) if buy_prices else None,
        'sell_min': min(sell_prices) if sell_prices else None,
        'sell_max': max(sell_prices) if sell_prices else None,
    }
