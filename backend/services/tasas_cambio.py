"""
Servicio de tasas de cambio.

Fuentes BCV (en orden de prioridad):
  1. dolarapi.com  — API venezolana con tasa BCV publicada; sin SSL issues.
  2. exchangedynamic.com — API alternativa.
  3. Scrape directo bcv.org.ve — fallback; usa bundle SSL del SO.
"""
import httpx
import ssl
import re
import logging
from datetime import date
from database import get_con

logger = logging.getLogger(__name__)


# ── SSL context para bcv.org.ve (cadena incompleta) ───────────────────────────

def _bcv_ssl_context() -> ssl.SSLContext:
    """SSL context con bundle del SO (más completo que certifi solo)."""
    ctx = ssl.create_default_context()
    for bundle in (
        '/etc/ssl/certs/ca-certificates.crt',   # Debian/Ubuntu/Railway
        '/etc/pki/tls/certs/ca-bundle.crt',     # RHEL/CentOS
        '/etc/ssl/cert.pem',                     # Alpine / macOS
    ):
        try:
            ctx.load_verify_locations(cafile=bundle)
            return ctx
        except (FileNotFoundError, ssl.SSLError):
            continue
    import certifi
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx


# ── Fuentes de tasa BCV ───────────────────────────────────────────────────────

async def _bcv_via_dolarapi() -> dict | None:
    """
    Fuente 1: ve.dolarapi.com — agrega y publica la tasa oficial BCV.
    Respuesta: lista de objetos, filtrar por fuente=="BCV".
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get('https://ve.dolarapi.com/v1/dolares')
        items = r.json() if isinstance(r.json(), list) else []
        tasas = {}
        for item in items:
            fuente = (item.get('fuente') or '').upper()
            nombre = (item.get('nombre') or '').lower()
            if fuente == 'BCV' or 'bcv' in nombre or 'oficial' in nombre:
                val = item.get('promedio') or item.get('venta') or item.get('precio')
                if val:
                    tasas['usd_ves'] = float(val)
                    break
        if tasas:
            logger.info('_bcv_via_dolarapi: %s', tasas)
            return tasas
    except Exception as e:
        logger.warning('_bcv_via_dolarapi: %s', e)
    return None


async def _bcv_via_exchangedynamic() -> dict | None:
    """Fuente 2: API alternativa con datos BCV."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get('https://api.exchangedynamic.com/v1/rate/USD/VES')
        data = r.json()
        rate = data.get('rate') or data.get('value') or data.get('price')
        if rate:
            tasas = {'usd_ves': float(rate)}
            logger.info('_bcv_via_exchangedynamic: %s', tasas)
            return tasas
    except Exception as e:
        logger.warning('_bcv_via_exchangedynamic: %s', e)
    return None


async def _bcv_via_scrape() -> dict | None:
    """Fuente 3: scrape directo de bcv.org.ve (fallback; SSL incompleto)."""
    url = 'https://www.bcv.org.ve/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'es-VE,es;q=0.9',
    }
    try:
        ssl_ctx = _bcv_ssl_context()
        async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                     verify=ssl_ctx) as client:
            r = await client.get(url, headers=headers)
        html = r.text

        tasa_usd = tasa_eur = None

        # BeautifulSoup
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            for div_id, key in [('dolar', 'usd'), ('euro', 'eur')]:
                div = soup.find(id=div_id)
                if div:
                    strong = div.find('strong')
                    if strong:
                        val = float(strong.get_text(strip=True).replace(',', '.'))
                        if key == 'usd':
                            tasa_usd = val
                        else:
                            tasa_eur = val
        except Exception as e:
            logger.warning('_bcv_via_scrape BS4: %s', e)

        # Regex fallback
        if not tasa_usd:
            for pat in [
                r'id=["\']dolar["\'][^>]*>.*?<strong[^>]*>([\d,\.]+)</strong>',
                r'D[oó]lar.*?<strong[^>]*>([\d,\.]+)</strong>',
            ]:
                m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
                if m:
                    tasa_usd = float(m.group(1).replace(',', '.'))
                    break

        if not tasa_eur:
            m = re.search(
                r'id=["\']euro["\'][^>]*>.*?<strong[^>]*>([\d,\.]+)</strong>',
                html, re.DOTALL | re.IGNORECASE)
            if m:
                tasa_eur = float(m.group(1).replace(',', '.'))

        if tasa_usd or tasa_eur:
            result = {}
            if tasa_usd:
                result['usd_ves'] = tasa_usd
            if tasa_eur:
                result['eur_ves'] = tasa_eur
            logger.info('_bcv_via_scrape: %s', result)
            return result

    except Exception as e:
        logger.error('_bcv_via_scrape: %s', e)
    return None


# ── Función pública ───────────────────────────────────────────────────────────

async def obtener_tasa_bcv() -> dict:
    """
    Obtiene la tasa BCV intentando las fuentes en orden:
      1. dolarapi.com
      2. exchangedynamic.com
      3. Scrape directo bcv.org.ve
    Persiste en BD como tasa_bcv. Retorna dict con los valores guardados.
    """
    tasas: dict | None = None
    for fetcher in (_bcv_via_dolarapi, _bcv_via_exchangedynamic, _bcv_via_scrape):
        tasas = await fetcher()
        if tasas:
            break

    if not tasas:
        logger.error('obtener_tasa_bcv: todas las fuentes fallaron')
        return {'error': 'No se pudo obtener la tasa BCV de ninguna fuente'}

    hoy = date.today().isoformat()
    con = get_con()
    par_map = {'usd_ves': 'USD_VES', 'eur_ves': 'EUR_VES'}
    for campo, par in par_map.items():
        val = tasas.get(campo)
        if val:
            con.execute("""
                INSERT INTO tasas_cambio(fecha, par, tasa_bcv, fuente)
                VALUES (?, ?, ?, 'bcv')
                ON CONFLICT DO NOTHING
            """, (hoy, par, val))
    con.commit()
    con.close()

    logger.info('obtener_tasa_bcv: guardado %s', tasas)
    return {'fecha': hoy, **tasas}


# ── Helpers síncronos (usados desde routers) ──────────────────────────────────

def tasa_bcv_hoy(par: str = 'USD_VES') -> float | None:
    con = get_con()
    row = con.execute("""SELECT tasa_bcv FROM tasas_cambio
                         WHERE par=? AND tasa_bcv IS NOT NULL
                         ORDER BY fecha DESC, id DESC LIMIT 1""", (par,)).fetchone()
    con.close()
    return row['tasa_bcv'] if row else None


def tasa_custom_hoy(par: str = 'USD_VES') -> float | None:
    con = get_con()
    row = con.execute("""SELECT tasa_custom FROM tasas_cambio
                         WHERE par=? AND tasa_custom IS NOT NULL
                         ORDER BY fecha DESC, id DESC LIMIT 1""", (par,)).fetchone()
    con.close()
    return row['tasa_custom'] if row else None


def tasa_bcv_en_fecha(par: str = 'USD_VES', fecha: str = None) -> float | None:
    """Tasa BCV más cercana (igual o anterior) a la fecha dada."""
    con = get_con()
    if fecha:
        row = con.execute("""SELECT tasa_bcv FROM tasas_cambio
                             WHERE par=? AND tasa_bcv IS NOT NULL AND fecha <= ?
                             ORDER BY fecha DESC, id DESC LIMIT 1""",
                          (par, fecha)).fetchone()
    else:
        row = con.execute("""SELECT tasa_bcv FROM tasas_cambio
                             WHERE par=? AND tasa_bcv IS NOT NULL
                             ORDER BY fecha DESC, id DESC LIMIT 1""", (par,)).fetchone()
    con.close()
    return row['tasa_bcv'] if row else None


def convertir(monto: float, moneda_origen: str, moneda_destino: str,
              fecha: str = None) -> float | None:
    if moneda_origen == moneda_destino:
        return monto
    par_fwd = f'{moneda_origen}_{moneda_destino}'
    par_rev = f'{moneda_destino}_{moneda_origen}'
    con = get_con()
    q = """SELECT tasa_bcv FROM tasas_cambio
           WHERE par=? AND tasa_bcv IS NOT NULL
           ORDER BY fecha DESC LIMIT 1"""
    row = con.execute(q, (par_fwd,)).fetchone()
    if row:
        con.close()
        return monto * row['tasa_bcv']
    row = con.execute(q, (par_rev,)).fetchone()
    con.close()
    if row and row['tasa_bcv']:
        return monto / row['tasa_bcv']
    return None
