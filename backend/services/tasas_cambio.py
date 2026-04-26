import httpx
import re
from datetime import date
from database import get_con


async def obtener_tasa_bcv():
    """Scrapea el BCV para USD/VES y EUR/VES y persiste en BD."""
    url = 'https://www.bcv.org.ve/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'es-VE,es;q=0.9',
    }
    try:
        async with httpx.AsyncClient(timeout=20, verify=False,
                                     follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
        html = r.text

        tasa_usd = None
        tasa_eur = None

        # Método 1: BeautifulSoup con id="dolar"
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            div_usd = soup.find(id='dolar')
            if div_usd:
                strong = div_usd.find('strong')
                if strong:
                    tasa_usd = float(strong.get_text(strip=True).replace(',', '.'))

            div_eur = soup.find(id='euro')
            if div_eur:
                strong = div_eur.find('strong')
                if strong:
                    tasa_eur = float(strong.get_text(strip=True).replace(',', '.'))
        except Exception:
            pass

        # Método 2: regex como fallback
        if not tasa_usd:
            patterns_usd = [
                r'id=["\']dolar["\'][^>]*>.*?<strong[^>]*>([\d,\.]+)</strong>',
                r'"dolar"[^>]*>.*?<strong>([\d,\.]+)</strong>',
                r'D[oó]lar.*?<strong[^>]*>([\d,\.]+)</strong>',
            ]
            for pat in patterns_usd:
                m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
                if m:
                    tasa_usd = float(m.group(1).replace(',', '.'))
                    break

        if not tasa_eur:
            patterns_eur = [
                r'id=["\']euro["\'][^>]*>.*?<strong[^>]*>([\d,\.]+)</strong>',
                r'"euro"[^>]*>.*?<strong>([\d,\.]+)</strong>',
            ]
            for pat in patterns_eur:
                m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
                if m:
                    tasa_eur = float(m.group(1).replace(',', '.'))
                    break

        hoy = date.today().isoformat()
        con = get_con()

        if tasa_usd:
            con.execute("""INSERT INTO tasas_cambio(fecha,par,tasa_bcv,fuente)
                           VALUES(?,?,?,?)""", (hoy, 'USD_VES', tasa_usd, 'bcv'))
        if tasa_eur:
            con.execute("""INSERT INTO tasas_cambio(fecha,par,tasa_bcv,fuente)
                           VALUES(?,?,?,?)""", (hoy, 'EUR_VES', tasa_eur, 'bcv'))

        con.commit()
        con.close()

        resultado = {'fecha': hoy, 'usd_ves': tasa_usd, 'eur_ves': tasa_eur}
        if not tasa_usd and not tasa_eur:
            resultado['advertencia'] = 'No se encontraron tasas en el HTML del BCV'
        return resultado

    except Exception as e:
        return {'error': str(e)}


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


def convertir(monto: float, moneda_origen: str, moneda_destino: str,
              fecha: str = None) -> float | None:
    if moneda_origen == moneda_destino:
        return monto
    par_fwd = f'{moneda_origen}_{moneda_destino}'
    par_rev = f'{moneda_destino}_{moneda_origen}'
    con = get_con()
    query = """SELECT tasa_bcv FROM tasas_cambio
               WHERE par=? AND tasa_bcv IS NOT NULL
               ORDER BY fecha DESC LIMIT 1"""
    row = con.execute(query, (par_fwd,)).fetchone()
    if row:
        con.close()
        return monto * row['tasa_bcv']
    row = con.execute(query, (par_rev,)).fetchone()
    con.close()
    if row and row['tasa_bcv']:
        return monto / row['tasa_bcv']
    return None
