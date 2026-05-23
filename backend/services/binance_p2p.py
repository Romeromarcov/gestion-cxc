"""
Scrapes Binance P2P to get USDT/VES rate.
Calculates average of the top buy price and top sell price (highest reported).
Updates tasas_cambio table as tasa_custom for par=USD_VES.
"""
import httpx
import logging
from datetime import date

logger = logging.getLogger(__name__)

BINANCE_P2P_URL = 'https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search'
HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

async def _fetch_best_price(trade_type: str) -> float | None:
    """Fetches best price for given trade_type: BUY or SELL. Returns highest price seen."""
    body = {
        "fiat": "VES", "page": 1, "rows": 20,
        "tradeType": trade_type, "asset": "USDT",
        "countries": [], "proMerchantAds": False, "shieldMerchantAds": False,
        "filterType": "all", "periods": [], "additionalKycVerifyFilter": 0,
        "publisherType": None, "payTypes": [], "classifies": ["mass","profession","fiat_trade"]
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(BINANCE_P2P_URL, json=body, headers=HEADERS)
        data = r.json()
        ads = data.get('data', [])
        prices = [float(ad['adv']['price']) for ad in ads if ad.get('adv', {}).get('price')]
        return max(prices) if prices else None
    except Exception as e:
        logger.warning('binance_p2p._fetch_best_price(%s): %s', trade_type, e)
        return None


async def actualizar_tasa_binance_p2p() -> dict:
    """
    Fetches buy+sell prices, averages them, persists as tasa_custom in tasas_cambio.
    BUY = what buyers pay (highest buy = seller asks most)
    SELL = what sellers offer (highest sell = buyer wants most)
    """
    buy_max  = await _fetch_best_price('BUY')
    sell_max = await _fetch_best_price('SELL')

    if not buy_max and not sell_max:
        logger.warning('binance_p2p: no prices found')
        return {'error': 'No prices found'}

    prices = [p for p in [buy_max, sell_max] if p]
    promedio = round(sum(prices) / len(prices), 4)

    from database import get_con
    hoy = date.today().isoformat()
    con = get_con()
    # Upsert: if today's record exists update tasa_custom, otherwise insert
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

    logger.info('binance_p2p: BUY_MAX=%s SELL_MAX=%s PROMEDIO=%s', buy_max, sell_max, promedio)
    return {'fecha': hoy, 'buy_max': buy_max, 'sell_max': sell_max, 'promedio': promedio}
