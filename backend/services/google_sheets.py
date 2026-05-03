import os

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def get_sheets_service():
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials
    cred_file = os.getenv('GOOGLE_SHEETS_CRED', 'credentials.json')
    creds = Credentials.from_service_account_file(cred_file, scopes=SCOPES)
    return build('sheets', 'v4', credentials=creds)


def exportar_pagos(pagos: list) -> dict:
    """Exporta lista de pagos a Google Sheets. Retorna resultado o error."""
    try:
        service = get_sheets_service()
        sheet_id = os.getenv('GOOGLE_SHEET_ID', '')
        if not sheet_id:
            return {'error': 'GOOGLE_SHEET_ID no configurado en .env'}

        valores = [['Orden', 'Vendedor', 'Monto', 'Moneda', 'Método',
                    'Tasa BCV', 'Tasa Custom', 'Equiv USD', 'Equiv VES',
                    'Referencia', 'Fecha']]
        for p in pagos:
            valores.append([
                p.get('odoo_order_name', ''),
                p.get('vendedor', ''),
                p.get('monto', ''),
                p.get('moneda', ''),
                p.get('metodo', ''),
                p.get('tasa_bcv', ''),
                p.get('tasa_custom', ''),
                p.get('equivalente_usd', ''),
                p.get('equivalente_ves', ''),
                p.get('referencia', ''),
                p.get('fecha_pago', ''),
            ])

        result = service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range='Pagos!A1',
            valueInputOption='USER_ENTERED',
            body={'values': valores}
        ).execute()
        return {'ok': True, 'filas': len(pagos), 'resultado': result}
    except Exception as e:
        return {'error': str(e)}
