import os
import logging
import threading

logger = logging.getLogger(__name__)

# ── Pool de conexiones PostgreSQL ─────────────────────────────────────────────
# Se inicializa la primera vez que se llama a get_con() (lazy init).
# minconn=2  → conexiones siempre abiertas (warm)
# maxconn    → configurable vía DATABASE_POOL_MAX (default 20)
# ThreadedConnectionPool es seguro para uso desde múltiples threads de uvicorn.

_pool = None
_pool_lock = threading.Lock()


def _build_pool():
    from psycopg2 import pool as pg_pool
    from config import DATABASE_URL
    max_conn = int(os.getenv('DATABASE_POOL_MAX', '20'))
    p = pg_pool.ThreadedConnectionPool(minconn=2, maxconn=max_conn, dsn=DATABASE_URL)
    logger.info('Pool de conexiones PostgreSQL creado (max=%d)', max_conn)
    return p


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:          # double-checked locking
                _pool = _build_pool()
    return _pool


def get_con():
    """
    Obtiene una conexión del pool y la devuelve envuelta en el adaptador
    sqlite3-compatible. Llama a con.close() cuando termines: eso devuelve
    la conexión al pool en lugar de cerrarla físicamente.
    """
    from db_adapter import CompatConnection
    pool = _get_pool()
    pg = pool.getconn()
    # autocommit=False (default psycopg2) — las transacciones deben commitearse explícitamente
    return CompatConnection(pg, pool=pool)


def close_pool():
    """Cierra todas las conexiones del pool. Llamar en shutdown de la app."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
                logger.info('Pool de conexiones PostgreSQL cerrado.')
            except Exception as e:
                logger.warning('close_pool: error al cerrar el pool — %s', e)
            finally:
                _pool = None


def migrate(con):
    """Migraciones incrementales de esquema."""
    # v1.1 — campo banco en pagos
    try:
        con.execute("ALTER TABLE pagos ADD COLUMN banco TEXT")
        con.commit()
    except Exception:
        pass
    # v1.1 — tabla para trackear pagos importados de Odoo
    con.execute("""CREATE TABLE IF NOT EXISTS pagos_odoo_importados (
        odoo_payment_id INTEGER PRIMARY KEY,
        importado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.commit()

    # v1.3 — módulo maestro de operaciones financieras
    con.execute("""CREATE TABLE IF NOT EXISTS categorias_operacion (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,        -- 'ingreso' | 'egreso' | 'ambos'
        categoria TEXT NOT NULL,   -- 'Cobranza', 'Gasto', etc.
        subcategoria TEXT,         -- 'Combustible', 'IVSS', etc.
        cuenta_odoo TEXT,
        activa INTEGER DEFAULT 1
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS maestro_operaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        nro_documento TEXT,
        monto REAL NOT NULL,
        moneda TEXT NOT NULL,
        metodo TEXT,
        tipo TEXT NOT NULL,         -- 'ingreso' | 'egreso'
        categoria TEXT,
        subcategoria TEXT,
        descripcion TEXT,
        tasa_bcv REAL,
        monto_usd_bcv REAL,
        tasa_real REAL,
        monto_real_usd REAL,
        origen TEXT DEFAULT 'manual', -- 'manual' | 'pago_sistema' | 'odoo_gasto'
        pago_id INTEGER,
        odoo_ref TEXT,
        estado TEXT DEFAULT 'confirmado',
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(pago_id) REFERENCES pagos(id),
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS permisos_usuario (
        usuario_id INTEGER NOT NULL,
        vista TEXT NOT NULL,
        puede_ver INTEGER DEFAULT 1,
        puede_editar INTEGER DEFAULT 0,
        PRIMARY KEY(usuario_id, vista),
        FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS salidas_inventario (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE,
        solicitante_id INTEGER,
        aprobado_por INTEGER,
        estado TEXT DEFAULT 'pendiente', -- 'pendiente'|'aprobada'|'despachada'|'cancelada'
        motivo TEXT NOT NULL,            -- 'obsequio'|'asignacion'|'muestra'|'otro'
        destinatario TEXT,
        notas TEXT,
        odoo_picking_id INTEGER,
        odoo_picking_name TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS salidas_inventario_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        salida_id INTEGER NOT NULL,
        producto_codigo TEXT,
        producto_nombre TEXT,
        cantidad REAL,
        FOREIGN KEY(salida_id) REFERENCES salidas_inventario(id)
    )""")
    # Seed categorías por defecto
    cats = [
        ('ingreso', 'Cobranza', None),
        ('ingreso', 'Cambio Divisas', None),
        ('ingreso', 'Ingreso Directiva', None),
        ('egreso', 'Gasto', 'Combustible'),
        ('egreso', 'Gasto', 'Nómina / Salarios'),
        ('egreso', 'Gasto', 'Nómina / Cestaticket'),
        ('egreso', 'Gasto', 'IVSS'),
        ('egreso', 'Gasto', 'Banavih'),
        ('egreso', 'Gasto', 'Electricidad / Relleno'),
        ('egreso', 'Gasto', 'Agua Potable'),
        ('egreso', 'Gasto', 'Internet'),
        ('egreso', 'Gasto', 'Aseo Urbano'),
        ('egreso', 'Gasto', 'Impuesto Municipal'),
        ('egreso', 'Gasto', 'Comisión Bancaria'),
        ('egreso', 'Gasto', 'Mantenimiento Oficina'),
        ('egreso', 'Gasto', 'Mantenimiento Equipos'),
        ('egreso', 'Gasto', 'Computación'),
        ('egreso', 'Gasto', 'Ferretería'),
        ('egreso', 'Gasto', 'Artículos de Cocina'),
        ('egreso', 'Gasto', 'Artículos de Limpieza'),
        ('egreso', 'Gasto', 'Comidas Personal'),
        ('egreso', 'Gasto', 'Refrigerios'),
        ('egreso', 'Gasto', 'Traslado'),
        ('egreso', 'Gasto', 'Bonificación'),
        ('egreso', 'Gasto', 'Página WEB'),
        ('egreso', 'Compra', None),
        ('egreso', 'Impuestos', None),
        ('egreso', 'Retiro Directiva', None),
        ('egreso', 'Cambio Divisas', None),
    ]
    for tipo, cat, subcat in cats:
        con.execute("""INSERT OR IGNORE INTO categorias_operacion(tipo,categoria,subcategoria)
                       VALUES(?,?,?)""", (tipo, cat, subcat))
    con.commit()

    # v1.4 — sincronización maestro ↔ Odoo (pagos proveedor + conciliación)
    for col, ddl in [
        ("odoo_payment_id", "INTEGER"),
        ("odoo_conciliado",  "INTEGER DEFAULT 0"),
        ("odoo_journal_id",  "INTEGER"),
        ("odoo_partner_id",  "INTEGER"),
    ]:
        try:
            con.execute(f"ALTER TABLE maestro_operaciones ADD COLUMN {col} {ddl}")
            con.commit()
        except Exception:
            pass

    for col, ddl in [
        ("odoo_journal_id",    "INTEGER"),
        ("odoo_account_id",    "INTEGER"),
        ("odoo_account_code",  "TEXT"),
    ]:
        try:
            con.execute(f"ALTER TABLE categorias_operacion ADD COLUMN {col} {ddl}")
            con.commit()
        except Exception:
            pass

    # v1.2 — configuración de condiciones para notas de crédito
    con.execute("""CREATE TABLE IF NOT EXISTS config_nota_credito (
        clave TEXT PRIMARY KEY,
        valor TEXT,
        descripcion TEXT
    )""")
    defaults = [
        ('requiere_pago',     '0',   'Requiere pago registrado para aplicar nota de crédito'),
        ('moneda_pago',       '',    'Moneda requerida del pago (vacío = cualquiera)'),
        ('dias_max_entrega',  '0',   'Días máximos desde entrega (0 = sin límite)'),
        ('descuento_maximo',  '100', 'Descuento máximo global por defecto (%)'),
    ]
    for clave, valor, desc in defaults:
        con.execute(
            "INSERT OR IGNORE INTO config_nota_credito(clave,valor,descripcion) VALUES(?,?,?)",
            (clave, valor, desc)
        )
    con.commit()


def migrate_v15(con):
    """v1.5 — NC multi-condición, CRM cobranza, acuerdos de pago, maestro journal nombre."""
    # journal_nombre en maestro_operaciones
    try:
        con.execute("ALTER TABLE maestro_operaciones ADD COLUMN journal_nombre TEXT")
        con.commit()
    except Exception:
        pass

    # NC condiciones múltiples con multi-moneda
    con.execute("""CREATE TABLE IF NOT EXISTS nc_condiciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        monedas TEXT NOT NULL DEFAULT '["USD"]',
        descuento_max_pct REAL NOT NULL DEFAULT 5.0,
        requiere_pago INTEGER DEFAULT 0,
        dias_max_entrega INTEGER DEFAULT 0,
        activa INTEGER DEFAULT 1,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    # Seed: migrar config legacy si existe y nc_condiciones está vacía
    count = con.execute("SELECT COUNT(*) FROM nc_condiciones").fetchone()[0]
    if count == 0:
        try:
            cfg = {r['clave']: r['valor'] for r in
                   con.execute("SELECT clave,valor FROM config_nota_credito").fetchall()}
            moneda = cfg.get('moneda_pago', '') or 'USD'
            monedas = f'["{moneda}"]'
            con.execute("""INSERT INTO nc_condiciones(nombre,monedas,descuento_max_pct,
                           requiere_pago,dias_max_entrega)
                           VALUES(?,?,?,?,?)""",
                        ('General', monedas,
                         float(cfg.get('descuento_maximo', '5') or 5),
                         int(cfg.get('requiere_pago', '0') or 0),
                         int(cfg.get('dias_max_entrega', '0') or 0)))
            con.commit()
        except Exception:
            pass

    # CRM Cobranza — gestiones de contacto
    con.execute("""CREATE TABLE IF NOT EXISTS cobranza_gestiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        cliente_nombre TEXT,
        orden_name TEXT,
        ejecutivo_id INTEGER,
        fecha_gestion TEXT NOT NULL,
        tipo_contacto TEXT NOT NULL,   -- llamada|whatsapp|email|visita|otro
        resultado TEXT NOT NULL,       -- contactado|no_contesto|buzon|promesa_pago|pago_realizado
        monto_prometido REAL,
        fecha_promesa TEXT,
        comentarios TEXT,
        proxima_accion TEXT,
        fecha_proxima TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(ejecutivo_id) REFERENCES usuarios(id)
    )""")

    # CRM — plantillas de mensajes (WhatsApp/email)
    con.execute("""CREATE TABLE IF NOT EXISTS cobranza_plantillas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        tipo TEXT NOT NULL,        -- antes_vencimiento|dia_vencimiento|despues_vencimiento|recordatorio
        dias_relativos INTEGER DEFAULT 0,
        canal TEXT DEFAULT 'whatsapp',  -- whatsapp|email|ambos
        mensaje TEXT NOT NULL,
        activa INTEGER DEFAULT 1,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    # Seed plantillas por defecto
    plantillas_seed = [
        ('Recordatorio Pre-Vencimiento', 'antes_vencimiento', -3, 'whatsapp',
         'Estimado {cliente}, le recordamos que su factura {orden} por {monto} vence el {vencimiento}. Por favor coordine el pago. Gracias, Lubrikca.'),
        ('Aviso Día Vencimiento', 'dia_vencimiento', 0, 'whatsapp',
         'Estimado {cliente}, hoy vence su factura {orden} por {monto}. Si ya realizó el pago, háganos llegar el comprobante. Gracias, Lubrikca.'),
        ('Primer Recordatorio (3d)', 'despues_vencimiento', 3, 'whatsapp',
         'Estimado {cliente}, su factura {orden} por {monto} venció hace {dias_vencida} días. Le solicitamos regularizar su situación. Lubrikca.'),
        ('Recordatorio Semanal', 'recordatorio', 7, 'whatsapp',
         'Estimado {cliente}, seguimos en espera del pago de {monto} correspondiente a {orden}. Vencida hace {dias_vencida} días. Contáctenos para acordar una solución.'),
    ]
    for nombre, tipo, dias, canal, msg in plantillas_seed:
        con.execute("""INSERT OR IGNORE INTO cobranza_plantillas(nombre,tipo,dias_relativos,canal,mensaje)
                       VALUES(?,?,?,?,?)""", (nombre, tipo, dias, canal, msg))
    con.commit()

    # Acuerdos de pago especiales
    con.execute("""CREATE TABLE IF NOT EXISTS acuerdos_pago (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        cliente_nombre TEXT,
        descripcion TEXT NOT NULL,
        monto_total REAL NOT NULL,
        moneda TEXT DEFAULT 'USD',
        plazo_total_dias INTEGER DEFAULT 90,
        periodicidad TEXT DEFAULT 'semanal',  -- semanal|quincenal|mensual|unico
        porcentaje_abono REAL DEFAULT 0,      -- % por cuota (0=monto fijo)
        monto_cuota REAL DEFAULT 0,
        fecha_inicio TEXT NOT NULL,
        fecha_vencimiento TEXT,
        ordenes_odoo TEXT DEFAULT '[]',       -- JSON array de order names
        estado TEXT DEFAULT 'activo',         -- activo|cumplido|incumplido|cancelado
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS acuerdos_pago_cuotas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        acuerdo_id INTEGER NOT NULL,
        numero_cuota INTEGER NOT NULL,
        fecha_vencimiento TEXT NOT NULL,
        monto_esperado REAL NOT NULL,
        monto_pagado REAL DEFAULT 0,
        estado TEXT DEFAULT 'pendiente',     -- pendiente|pagado|parcial|vencido
        pago_ids TEXT DEFAULT '[]',
        notas TEXT,
        FOREIGN KEY(acuerdo_id) REFERENCES acuerdos_pago(id)
    )""")
    con.commit()


def migrate_v16(con):
    """v1.6 — réplicas de órdenes, config_app, cantidad en NC líneas."""
    # cantidad en notas_credito_lineas
    try:
        con.execute("ALTER TABLE notas_credito_lineas ADD COLUMN cantidad REAL DEFAULT 1")
        con.commit()
    except Exception:
        pass

    # tabla de configuración genérica clave-valor
    con.execute("""CREATE TABLE IF NOT EXISTS config_app (
        clave TEXT PRIMARY KEY,
        valor TEXT,
        descripcion TEXT
    )""")
    config_seeds = [
        ('pricelist_ves_nombre',  'Precio USD Pago VES',
         'Nombre exacto de la lista de precios VES en Odoo'),
        ('pricelist_usd_nombre',  'Lista USD',
         'Nombre exacto de la lista de precios USD en Odoo'),
        ('tolerancia_pago_usd',   '0.01',
         'Tolerancia en USD para considerar una orden como pagada'),
    ]
    for clave, valor, desc in config_seeds:
        con.execute(
            "INSERT OR IGNORE INTO config_app(clave,valor,descripcion) VALUES(?,?,?)",
            (clave, valor, desc)
        )
    con.commit()

    # réplicas de órdenes Odoo con Lista USD
    con.execute("""CREATE TABLE IF NOT EXISTS ordenes_replica (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odoo_order_name TEXT UNIQUE NOT NULL,
        moneda_orden TEXT,
        total_lista_ves REAL,
        total_lista_usd REAL,
        estado TEXT DEFAULT 'activa',   -- activa | inactiva | error
        error_detalle TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        actualizado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS ordenes_replica_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        replica_id INTEGER NOT NULL,
        odoo_line_id INTEGER,
        producto_ref TEXT,
        producto_nombre TEXT,
        cantidad REAL,
        precio_lista_ves REAL,
        precio_lista_usd REAL,
        subtotal_ves REAL,
        subtotal_usd REAL,
        FOREIGN KEY(replica_id) REFERENCES ordenes_replica(id)
    )""")
    con.commit()


def migrate_v17(con):
    """v1.7 — subtotales e impuestos en réplicas; skip para Lista USD."""
    # Nuevas columnas en ordenes_replica
    for col, ddl in [
        ("subtotal_lista_ves", "REAL"),
        ("tax_lista_ves",      "REAL"),
        ("subtotal_lista_usd", "REAL"),
        ("tax_lista_usd",      "REAL"),
    ]:
        try:
            con.execute(f"ALTER TABLE ordenes_replica ADD COLUMN {col} {ddl}")
            con.commit()
        except Exception:
            pass

    # Nuevas columnas en ordenes_replica_lineas
    for col, ddl in [
        ("tax_rate",  "REAL DEFAULT 0"),
        ("tax_ves",   "REAL"),
        ("total_ves", "REAL"),
        ("tax_usd",   "REAL"),
        ("total_usd", "REAL"),
    ]:
        try:
            con.execute(f"ALTER TABLE ordenes_replica_lineas ADD COLUMN {col} {ddl}")
            con.commit()
        except Exception:
            pass


def migrate_v19(con):
    """v1.9 — cambios de divisas."""
    con.execute("""CREATE TABLE IF NOT EXISTS cambios_divisa (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        descripcion TEXT,
        -- Egreso (sale)
        monto_egreso REAL NOT NULL,
        moneda_egreso TEXT NOT NULL,
        banco_egreso TEXT,
        odoo_journal_egreso INTEGER,
        -- Ingreso (entra)
        monto_ingreso REAL NOT NULL,
        moneda_ingreso TEXT NOT NULL,
        banco_ingreso TEXT,
        odoo_journal_ingreso INTEGER,
        -- Tasa
        tasa_cambio REAL NOT NULL,
        -- Estado
        estado TEXT DEFAULT 'borrador',
        enviado_odoo INTEGER DEFAULT 0,
        odoo_move_id INTEGER,
        maestro_egreso_id INTEGER,
        maestro_ingreso_id INTEGER,
        -- Meta
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    con.commit()


def migrate_v20(con):
    """v2.0 — pagos fiscales (alcaldía, INCES, aseo, etc.)."""
    con.execute("""CREATE TABLE IF NOT EXISTS pagos_fiscales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        periodo TEXT,
        monto REAL NOT NULL,
        moneda TEXT NOT NULL DEFAULT 'VES',
        equivalente_usd REAL,
        fecha TEXT NOT NULL,
        referencia TEXT,
        -- Odoo mapping
        odoo_cuenta_gasto_id INTEGER,
        odoo_cuenta_gasto_codigo TEXT,
        odoo_cuenta_haber_id INTEGER,
        odoo_cuenta_haber_codigo TEXT,
        odoo_journal_id INTEGER,
        odoo_move_id INTEGER,
        -- Maestro
        maestro_op_id INTEGER,
        -- Estado
        estado TEXT DEFAULT 'borrador',
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    con.commit()


def migrate_v21(con):
    """v2.1 — requisiciones de mercancía."""
    con.execute("""CREATE TABLE IF NOT EXISTS requisiciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_nombre TEXT NOT NULL,
        departamento TEXT,
        motivo TEXT NOT NULL,
        descripcion TEXT,
        -- Odoo
        odoo_scrap_id INTEGER,
        odoo_move_id INTEGER,
        odoo_cuenta_id INTEGER,
        odoo_cuenta_codigo TEXT,
        odoo_journal_id INTEGER,
        odoo_location_id INTEGER,
        -- Estado
        estado TEXT DEFAULT 'solicitada',
        aprobado_por INTEGER,
        fecha_aprobacion TEXT,
        notas_aprobacion TEXT,
        -- Meta
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id),
        FOREIGN KEY(aprobado_por) REFERENCES usuarios(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS requisiciones_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requisicion_id INTEGER NOT NULL,
        producto_ref TEXT,
        producto_nombre TEXT NOT NULL,
        producto_odoo_id INTEGER,
        cantidad REAL NOT NULL DEFAULT 1,
        unidad TEXT DEFAULT 'unidades',
        costo_unitario REAL,
        FOREIGN KEY(requisicion_id) REFERENCES requisiciones(id)
    )""")
    con.commit()


def migrate_v23(con):
    """v2.3 — Gastos y Compromisos (únicos, recurrentes, servicios públicos)."""
    con.execute("""CREATE TABLE IF NOT EXISTS gastos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL DEFAULT 'unico',   -- unico | recurrente | servicio_publico
        categoria TEXT NOT NULL,
        subcategoria TEXT,
        descripcion TEXT NOT NULL,
        proveedor_nombre TEXT,
        proveedor_odoo_id INTEGER,
        -- Monto de referencia (para recurrentes es el monto habitual)
        monto REAL NOT NULL,
        moneda TEXT NOT NULL DEFAULT 'VES',
        equivalente_usd REAL,
        fecha TEXT NOT NULL,
        referencia TEXT,
        periodo TEXT,                          -- 'YYYY-MM' si es recurrente/fiscal
        -- Recurrencia
        es_recurrente INTEGER DEFAULT 0,
        dia_pago INTEGER DEFAULT 1,           -- día del mes esperado
        activo INTEGER DEFAULT 1,             -- si el compromiso sigue vigente
        -- Mapeo contable Odoo (heredado de categorias_operacion si aplica)
        odoo_cuenta_gasto_id INTEGER,
        odoo_cuenta_gasto_codigo TEXT,
        odoo_cuenta_banco_id INTEGER,         -- cuenta banco/caja que paga
        odoo_journal_id INTEGER,
        -- Resultado en Odoo
        odoo_move_id INTEGER,
        odoo_payment_id INTEGER,
        -- Maestro
        maestro_op_id INTEGER,
        -- Estado del registro puntual
        estado TEXT DEFAULT 'borrador',       -- borrador | pagado | enviado_odoo
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    # Historial mensual de pagos de gastos recurrentes
    con.execute("""CREATE TABLE IF NOT EXISTS gastos_pagos_mensuales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gasto_id INTEGER NOT NULL,
        periodo TEXT NOT NULL,               -- 'YYYY-MM'
        monto_pagado REAL,
        fecha_pago TEXT,
        referencia TEXT,
        odoo_move_id INTEGER,
        odoo_payment_id INTEGER,
        maestro_op_id INTEGER,
        estado TEXT DEFAULT 'pendiente',     -- pendiente | pagado | enviado_odoo
        notas TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(gasto_id) REFERENCES gastos(id)
    )""")
    # Configuración contable por tipo de gasto/servicio
    con.execute("""CREATE TABLE IF NOT EXISTS gastos_config_cuentas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        categoria TEXT NOT NULL,
        subcategoria TEXT,
        odoo_cuenta_gasto_id INTEGER,
        odoo_cuenta_gasto_codigo TEXT,
        odoo_cuenta_gasto_nombre TEXT,
        odoo_journal_id INTEGER,
        odoo_journal_nombre TEXT,
        UNIQUE(categoria, subcategoria)
    )""")
    # Seed: categorías de gastos recurrentes comunes
    seeds = [
        ('Servicios Públicos', 'Electricidad / Corpoelec'),
        ('Servicios Públicos', 'Agua / HIDROCAPITAL'),
        ('Servicios Públicos', 'Internet / CANTV'),
        ('Servicios Públicos', 'Gas'),
        ('Servicios Públicos', 'Teléfono'),
        ('Alquiler', None),
        ('Seguro', None),
        ('Gasto', 'Combustible'),
        ('Gasto', 'Mantenimiento'),
        ('Gasto', 'Papelería y Útiles'),
    ]
    for cat, sub in seeds:
        con.execute("""INSERT OR IGNORE INTO gastos_config_cuentas(categoria, subcategoria)
                       VALUES(?,?)""", (cat, sub))
    con.commit()


def migrate_v24(con):
    """v2.4 — Nómina: importación Odoo + bonificaciones manuales + terceros."""
    con.execute("""CREATE TABLE IF NOT EXISTS nomina_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        periodo TEXT NOT NULL,               -- 'YYYY-MM'
        tipo TEXT NOT NULL,                  -- nomina | bonificacion | hora_extra | bono | otro
        descripcion TEXT NOT NULL,
        -- Origen
        origen TEXT DEFAULT 'manual',        -- manual | odoo_payslip
        odoo_payslip_batch_id INTEGER,       -- si vino de Odoo HR Payslip batch
        -- Beneficiario (puede ser todo el personal o uno)
        empleado_nombre TEXT,
        empleado_odoo_id INTEGER,
        es_grupal INTEGER DEFAULT 0,
        -- Monto
        monto REAL NOT NULL,
        moneda TEXT NOT NULL DEFAULT 'VES',
        equivalente_usd REAL,
        -- Mapeo contable Odoo
        odoo_cuenta_gasto_id INTEGER,        -- cuenta de gasto nómina
        odoo_cuenta_gasto_codigo TEXT,
        odoo_cuenta_banco_id INTEGER,        -- cuenta banco que paga
        odoo_journal_id INTEGER,
        -- Resultado Odoo
        odoo_move_id INTEGER,
        odoo_payment_id INTEGER,
        -- Maestro
        maestro_op_id INTEGER,
        -- Estado
        estado TEXT DEFAULT 'borrador',      -- borrador | pagado | enviado_odoo
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    # Nóminas pagadas a través de proveedor (descuento AP)
    con.execute("""CREATE TABLE IF NOT EXISTS nomina_terceros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nomina_id INTEGER,                   -- FK nomina_registros (opcional)
        periodo TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        empleado_nombre TEXT NOT NULL,
        monto REAL NOT NULL,
        moneda TEXT DEFAULT 'USD',
        -- Proveedor que paga (se descuenta de su AP)
        proveedor_id INTEGER,
        proveedor_nombre TEXT NOT NULL,
        referencia TEXT,
        -- Odoo
        odoo_payment_id INTEGER,             -- pago outbound al proveedor
        odoo_move_id INTEGER,                -- asiento si es más complejo
        odoo_journal_id INTEGER,
        -- Mapeo contable
        odoo_cuenta_gasto_id INTEGER,        -- gasto nómina
        odoo_cuenta_ap_id INTEGER,           -- cuenta AP del proveedor
        -- Maestro
        maestro_op_id INTEGER,
        estado TEXT DEFAULT 'pendiente',     -- pendiente | descontado | pagado
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(nomina_id) REFERENCES nomina_registros(id),
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    # Configuración contable de nómina por tipo
    con.execute("""CREATE TABLE IF NOT EXISTS nomina_config_cuentas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL UNIQUE,           -- nomina | bonificacion | hora_extra | bono
        odoo_cuenta_gasto_id INTEGER,
        odoo_cuenta_gasto_codigo TEXT,
        odoo_cuenta_gasto_nombre TEXT,
        odoo_journal_id INTEGER,
        odoo_journal_nombre TEXT
    )""")
    for tipo in ('nomina', 'bonificacion', 'hora_extra', 'bono', 'otro'):
        con.execute("INSERT OR IGNORE INTO nomina_config_cuentas(tipo) VALUES(?)", (tipo,))
    con.commit()


def migrate_v26(con):
    """v2.6 — Configuraciones por tipo: fiscal, cambios divisa, métodos de pago."""
    # Config cuentas Odoo por tipo de pago fiscal
    con.execute("""CREATE TABLE IF NOT EXISTS pagos_fiscales_config (
        tipo TEXT PRIMARY KEY,
        odoo_cuenta_gasto_id INTEGER,
        odoo_cuenta_gasto_nombre TEXT,
        odoo_cuenta_haber_id INTEGER,
        odoo_cuenta_haber_nombre TEXT,
        odoo_journal_id INTEGER,
        odoo_journal_nombre TEXT,
        actualizado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    for t in ('alcaldia','inces','aseo','iva','islr','seniat','sso','otro'):
        con.execute("INSERT OR IGNORE INTO pagos_fiscales_config(tipo) VALUES(?)", (t,))
    con.commit()

    # Config default de Cambios de Divisa (singleton id=1)
    con.execute("""CREATE TABLE IF NOT EXISTS cambios_divisa_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        journal_egreso_id INTEGER,
        journal_egreso_nombre TEXT,
        cuenta_egreso_id INTEGER,
        cuenta_egreso_nombre TEXT,
        journal_ingreso_id INTEGER,
        journal_ingreso_nombre TEXT,
        cuenta_ingreso_id INTEGER,
        cuenta_ingreso_nombre TEXT
    )""")
    con.execute("INSERT OR IGNORE INTO cambios_divisa_config(id) VALUES(1)")
    con.commit()

    # Config journal Odoo por método de pago (para Pagos vendedores → envío a Odoo)
    con.execute("""CREATE TABLE IF NOT EXISTS pagos_metodos_journal (
        metodo TEXT PRIMARY KEY,
        journal_id INTEGER,
        journal_nombre TEXT
    )""")
    for m in ('efectivo','transferencia','pago_movil','zelle','zelle_tercero','binance'):
        con.execute("INSERT OR IGNORE INTO pagos_metodos_journal(metodo) VALUES(?)", (m,))
    con.commit()


def migrate_v25(con):
    """v2.5 — Zelle terceros integrado en Pagos; CxP separado."""
    # pago_id: enlaza zelle_terceros con el pago que lo originó (metodo=zelle_tercero)
    try:
        con.execute("ALTER TABLE zelle_terceros ADD COLUMN pago_id INTEGER REFERENCES pagos(id)")
        con.commit()
    except Exception:
        pass
    # journal_efectivo en config: ID del diario de caja/efectivo para Zelle terceros
    try:
        con.execute("ALTER TABLE zelle_terceros ADD COLUMN journal_efectivo_id INTEGER")
        con.commit()
    except Exception:
        pass


def migrate_v22(con):
    """v2.2 — zelle de terceros (pagos Zelle recibidos en cuentas de proveedores)."""
    con.execute("""CREATE TABLE IF NOT EXISTS zelle_terceros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        descripcion TEXT,
        -- Proveedor cuya cuenta Zelle recibió el pago
        proveedor_id INTEGER,
        proveedor_nombre TEXT NOT NULL,
        -- Cliente que realizó el pago Zelle
        cliente_nombre TEXT,
        orden_cobrada TEXT,            -- orden de venta que se estaba cobrando
        -- Monto recibido
        monto_usd REAL NOT NULL,
        referencia_zelle TEXT,         -- nro de confirmación / referencia
        -- Acción tomada
        tipo_accion TEXT,              -- NULL | 'abonar' | 'reintegro'
        -- Para "abonar": pago de proveedor en Odoo (reducción de AP)
        odoo_payment_id INTEGER,
        odoo_journal_id INTEGER,       -- diario de banco/caja usado
        -- Para "reintegro": asiento contable
        odoo_move_id INTEGER,
        comision_pct REAL DEFAULT 0,   -- % comisión cobrada por el proveedor
        monto_comision REAL DEFAULT 0,
        monto_reintegro REAL,          -- monto_usd - monto_comision
        odoo_cuenta_cobrar_id INTEGER, -- cuenta "por cobrar a proveedor"
        odoo_cuenta_comision_id INTEGER,
        -- Maestro
        maestro_op_id INTEGER,
        -- Estado
        estado TEXT DEFAULT 'pendiente',  -- pendiente | abonado | reintegro_pendiente | reintegrado | anulado
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    con.commit()


def migrate_v18(con):
    """v1.8 — créditos a favor de clientes por sobrepago o ajuste."""
    con.execute("""CREATE TABLE IF NOT EXISTS creditos_cliente (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        partner_id INTEGER,
        partner_nombre TEXT,
        odoo_order_name TEXT,        -- orden que originó el crédito
        monto REAL NOT NULL,         -- monto en la moneda indicada
        moneda TEXT NOT NULL DEFAULT 'USD',
        equivalente_usd REAL,        -- equivalente en USD al momento del registro
        motivo TEXT DEFAULT 'sobrepago',  -- sobrepago | ajuste | devolucion
        notas TEXT,
        estado TEXT DEFAULT 'disponible',  -- disponible | aplicado | devuelto | anulado
        aplicado_a_orden TEXT,       -- orden donde se aplicó el crédito
        pago_id INTEGER,             -- pago del sistema que originó el sobrepago
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id),
        FOREIGN KEY(pago_id) REFERENCES pagos(id)
    )""")
    con.commit()


def migrate_modulo_interno(con):
    """
    Módulo Interno — tablas para ventas internas, inventario local,
    compras internas y cuentas por pagar.
    Deshabilitado por defecto (config_app.modulo_interno_activo = '0').
    """
    # Ventas internas (cabecera)
    con.execute("""CREATE TABLE IF NOT EXISTS ventas_internas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE NOT NULL,
        cliente_nombre TEXT NOT NULL,
        cliente_id_odoo INTEGER,
        vendedor_id INTEGER,
        total_usd REAL DEFAULT 0,
        estado TEXT DEFAULT 'borrador',   -- borrador | confirmada | anulada
        notas TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(vendedor_id) REFERENCES usuarios(id)
    )""")

    # Ventas internas (líneas de detalle)
    con.execute("""CREATE TABLE IF NOT EXISTS ventas_internas_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venta_id INTEGER NOT NULL,
        producto_codigo TEXT NOT NULL,
        producto_nombre TEXT NOT NULL,
        cantidad REAL NOT NULL DEFAULT 1,
        precio_unitario REAL NOT NULL DEFAULT 0,
        descuento_pct REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(venta_id) REFERENCES ventas_internas(id)
    )""")

    # Inventario interno (stock local)
    con.execute("""CREATE TABLE IF NOT EXISTS inventario_interno (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_codigo TEXT UNIQUE NOT NULL,
        producto_nombre TEXT NOT NULL,
        stock_actual REAL NOT NULL DEFAULT 0,
        costo_usd REAL,
        ultima_actualizacion TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # Compras internas (cabecera)
    con.execute("""CREATE TABLE IF NOT EXISTS compras_internas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proveedor TEXT NOT NULL,
        fecha TEXT NOT NULL,
        total_usd REAL NOT NULL DEFAULT 0,
        estado TEXT DEFAULT 'confirmada',   -- confirmada | anulada
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # Compras internas (líneas de detalle)
    con.execute("""CREATE TABLE IF NOT EXISTS compras_internas_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        compra_id INTEGER NOT NULL,
        producto_codigo TEXT NOT NULL,
        producto_nombre TEXT NOT NULL,
        cantidad REAL NOT NULL DEFAULT 1,
        costo_unitario REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(compra_id) REFERENCES compras_internas(id)
    )""")

    # Cuentas por Pagar (generadas desde compras internas)
    con.execute("""CREATE TABLE IF NOT EXISTS cuentas_por_pagar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        compra_id INTEGER,                        -- compra interna que la originó
        proveedor TEXT NOT NULL,
        fecha_emision TEXT NOT NULL,
        fecha_vencimiento TEXT,
        monto_total REAL NOT NULL,
        moneda TEXT NOT NULL DEFAULT 'USD',
        saldo_pendiente REAL NOT NULL,
        estado TEXT DEFAULT 'pendiente',          -- pendiente | parcial | pagada | cancelada
        notas TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(compra_id) REFERENCES compras_internas(id)
    )""")

    # Pagos registrados contra CxP
    con.execute("""CREATE TABLE IF NOT EXISTS cuentas_por_pagar_pagos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cxp_id INTEGER NOT NULL,
        monto REAL NOT NULL,
        moneda TEXT NOT NULL DEFAULT 'USD',
        metodo TEXT NOT NULL,
        referencia TEXT,
        fecha_pago TEXT NOT NULL,
        notas TEXT,
        registrado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(cxp_id) REFERENCES cuentas_por_pagar(id),
        FOREIGN KEY(registrado_por) REFERENCES usuarios(id)
    )""")
    con.commit()

    # Seed: clave de feature-flag (desactivada por defecto)
    con.execute("""INSERT OR IGNORE INTO config_app(clave, valor, descripcion)
                   VALUES('modulo_interno_activo', '0',
                          'Activa Ventas Internas, Compras Internas, Inventario Interno y CxP')""")
    con.commit()

    # Índices para el módulo interno
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_vi_vendedor   ON ventas_internas(vendedor_id)",
        "CREATE INDEX IF NOT EXISTS idx_vi_estado     ON ventas_internas(estado)",
        "CREATE INDEX IF NOT EXISTS idx_ci_fecha      ON compras_internas(fecha)",
        "CREATE INDEX IF NOT EXISTS idx_cxp_proveedor ON cuentas_por_pagar(proveedor)",
        "CREATE INDEX IF NOT EXISTS idx_cxp_estado    ON cuentas_por_pagar(estado)",
        "CREATE INDEX IF NOT EXISTS idx_cxp_venc      ON cuentas_por_pagar(fecha_vencimiento)",
    ]:
        try:
            con.execute(ddl)
        except Exception as e:
            logger.warning('migrate_modulo_interno idx: %s', e)
    con.commit()
    logger.info('migrate_modulo_interno: tablas e índices verificados.')


def migrate_v27(con):
    """v2.7 — Requisiciones: odoo_employee_id + config por defecto."""
    # Columna para vincular al empleado de Odoo HR
    try:
        con.execute("ALTER TABLE requisiciones ADD COLUMN odoo_employee_id INTEGER")
        con.commit()
    except Exception:
        pass
    # Columna para guardar nombre del aprobador (snapshot)
    try:
        con.execute("ALTER TABLE requisiciones ADD COLUMN aprobado_por_nombre TEXT")
        con.commit()
    except Exception:
        pass
    # Tabla de configuración por defecto de requisiciones
    con.execute("""CREATE TABLE IF NOT EXISTS requisiciones_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        odoo_cuenta_id INTEGER,
        odoo_cuenta_nombre TEXT,
        odoo_journal_id INTEGER,
        odoo_journal_nombre TEXT,
        odoo_location_id INTEGER,
        odoo_location_nombre TEXT,
        odoo_cuenta_credito_id INTEGER,
        odoo_cuenta_credito_nombre TEXT
    )""")
    con.execute("INSERT OR IGNORE INTO requisiciones_config(id) VALUES(1)")
    con.commit()


def migrate_aprobaciones(con):
    """Approval workflow columns for gastos, cambios_divisa, compras_internas."""
    for table in ('gastos', 'cambios_divisa', 'compras_internas'):
        for col, ddl in [
            ('aprobacion_estado', "TEXT DEFAULT 'sin_solicitar'"),
            ('aprobacion_solicitada_en', 'TEXT'),
            ('aprobado_por', 'INTEGER'),
            ('aprobado_en', 'TEXT'),
            ('rechazo_motivo', 'TEXT'),
        ]:
            try:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
                con.commit()
            except Exception:
                pass
    # Seed: feature flag disabled by default
    con.execute("""INSERT OR IGNORE INTO config_app(clave, valor, descripcion)
                   VALUES('aprobacion_egresos_activo', '0',
                          'Requiere aprobación para gastos, cambios de divisa y compras internas')""")
    con.commit()


def migrate_fraccionamiento(con):
    """Sub-units: break a parent product into traceable sub-units for sale/gift."""
    con.execute("""CREATE TABLE IF NOT EXISTS fraccionamiento_lotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE,
        producto_ref TEXT,
        producto_nombre TEXT NOT NULL,
        unidad_padre TEXT NOT NULL,
        cantidad_padre REAL NOT NULL DEFAULT 1,
        subunidad_nombre TEXT NOT NULL,
        factor_conversion REAL NOT NULL,
        total_subunidades REAL NOT NULL,
        stock_disponible REAL NOT NULL,
        costo_padre_usd REAL,
        costo_subunidad_usd REAL,
        precio_venta_usd REAL,
        estado TEXT DEFAULT 'activo',
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fraccionamiento_movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lote_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        cantidad REAL NOT NULL,
        precio_unitario_usd REAL,
        destinatario TEXT,
        venta_id INTEGER,
        referencia TEXT,
        notas TEXT,
        creado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(lote_id) REFERENCES fraccionamiento_lotes(id),
        FOREIGN KEY(creado_por) REFERENCES usuarios(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fraccionamiento_ventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE,
        cliente_nombre TEXT NOT NULL,
        vendedor_id INTEGER,
        total_usd REAL DEFAULT 0,
        saldo_pendiente REAL DEFAULT 0,
        estado TEXT DEFAULT 'borrador',
        notas TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(vendedor_id) REFERENCES usuarios(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fraccionamiento_ventas_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venta_id INTEGER NOT NULL,
        lote_id INTEGER NOT NULL,
        cantidad REAL NOT NULL DEFAULT 1,
        precio_unitario_usd REAL NOT NULL DEFAULT 0,
        descuento_pct REAL NOT NULL DEFAULT 0,
        subtotal_usd REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(venta_id) REFERENCES fraccionamiento_ventas(id),
        FOREIGN KEY(lote_id) REFERENCES fraccionamiento_lotes(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fraccionamiento_pagos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venta_id INTEGER NOT NULL,
        monto REAL NOT NULL,
        moneda TEXT DEFAULT 'USD',
        metodo TEXT NOT NULL,
        referencia TEXT,
        fecha_pago TEXT NOT NULL,
        notas TEXT,
        estado TEXT DEFAULT 'recibido',
        registrado_por INTEGER,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(venta_id) REFERENCES fraccionamiento_ventas(id),
        FOREIGN KEY(registrado_por) REFERENCES usuarios(id)
    )""")
    con.commit()
    # Feature flag
    con.execute("""INSERT OR IGNORE INTO config_app(clave, valor, descripcion)
                   VALUES('modulo_fraccionamiento_activo', '0',
                          'Activa el módulo de Fraccionamiento (sub-unidades)')""")
    con.commit()
    # Indexes
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_frac_lote_estado ON fraccionamiento_lotes(estado)",
        "CREATE INDEX IF NOT EXISTS idx_frac_mov_lote ON fraccionamiento_movimientos(lote_id)",
        "CREATE INDEX IF NOT EXISTS idx_frac_vta_estado ON fraccionamiento_ventas(estado)",
        "CREATE INDEX IF NOT EXISTS idx_frac_pago_venta ON fraccionamiento_pagos(venta_id)",
    ]:
        try:
            con.execute(ddl)
        except Exception:
            pass
    con.commit()


def init_db():
    con = get_con()
    # El adaptador traduce AUTOINCREMENT→SERIAL y omite los PRAGMA de SQLite
    con.executescript('''
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        rol TEXT DEFAULT 'vendedor',
        activo INTEGER DEFAULT 1,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS notas_credito (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odoo_order_name TEXT NOT NULL,
        odoo_order_id INTEGER,
        vendedor_id INTEGER,
        estado TEXT DEFAULT 'borrador',
        condicion_pago_requerido INTEGER DEFAULT 0,
        condicion_moneda TEXT,
        condicion_dias_pago INTEGER,
        aprobado_por INTEGER,
        aprobado_en TEXT,
        rechazado_motivo TEXT,
        aplicado_odoo INTEGER DEFAULT 0,
        aplicado_factura INTEGER DEFAULT 0,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(vendedor_id) REFERENCES usuarios(id)
    );

    CREATE TABLE IF NOT EXISTS notas_credito_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nota_id INTEGER NOT NULL,
        odoo_line_id INTEGER,
        producto_id INTEGER,
        producto_nombre TEXT,
        producto_ref TEXT,
        categoria TEXT,
        precio_original REAL,
        descuento_propuesto REAL,
        descuento_maximo REAL,
        descuento_aprobado REAL,
        FOREIGN KEY(nota_id) REFERENCES notas_credito(id)
    );

    CREATE TABLE IF NOT EXISTS limites_descuento (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,
        referencia TEXT NOT NULL,
        limite_pct REAL NOT NULL,
        creado_por INTEGER
    );

    CREATE TABLE IF NOT EXISTS promociones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        activa INTEGER DEFAULT 1,
        descuento_pct REAL DEFAULT 99.0,
        producto_obsequio_ref TEXT,
        condicion_cliente_nuevo INTEGER DEFAULT 0,
        condicion_min_productos INTEGER DEFAULT 0,
        condicion_json TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS pagos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odoo_order_name TEXT,
        vendedor_id INTEGER,
        monto REAL NOT NULL,
        moneda TEXT NOT NULL,
        metodo TEXT NOT NULL,
        tasa_usd REAL,
        tasa_bcv REAL,
        tasa_custom REAL,
        equivalente_usd REAL,
        equivalente_ves REAL,
        referencia TEXT,
        estado TEXT DEFAULT 'propuesto',
        recibido_por INTEGER,
        recibido_en TEXT,
        odoo_payment_id INTEGER,
        fecha_pago TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS monedas (
        codigo TEXT PRIMARY KEY,
        nombre TEXT,
        simbolo TEXT,
        activa INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS metodos_pago (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE,
        monedas_permitidas TEXT,
        odoo_journal_id INTEGER,
        activo INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS tasas_cambio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        par TEXT NOT NULL,
        tasa_bcv REAL,
        tasa_custom REAL,
        fuente TEXT DEFAULT 'bcv'
    );

    CREATE TABLE IF NOT EXISTS listas_precios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        moneda TEXT DEFAULT 'USD',
        activa INTEGER DEFAULT 1,
        umbral_descuento_excluir REAL
    );

    CREATE TABLE IF NOT EXISTS listas_precios_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lista_id INTEGER,
        producto_ref TEXT,
        precio REAL,
        FOREIGN KEY(lista_id) REFERENCES listas_precios(id)
    );

    CREATE TABLE IF NOT EXISTS productos_extra (
        producto_ref TEXT PRIMARY KEY,
        marca TEXT,
        categoria_local TEXT,
        datos_extra TEXT
    );
    ''')
    con.commit()

    # Datos iniciales
    _seed(con)
    migrate(con)
    migrate_v15(con)
    migrate_v16(con)
    migrate_v17(con)
    migrate_v18(con)
    migrate_v19(con)
    migrate_v20(con)
    migrate_v21(con)
    migrate_v22(con)
    migrate_v23(con)
    migrate_v24(con)
    migrate_v25(con)
    migrate_v26(con)
    migrate_v27(con)
    migrate_modulo_interno(con)
    migrate_aprobaciones(con)
    migrate_fraccionamiento(con)
    migrate_forzar_cambio_password(con)
    _create_indexes(con)
    con.close()


def migrate_forzar_cambio_password(con):
    """
    v2.8 — Campo debe_cambiar_password en usuarios.
    El admin creado por defecto arranca con este flag en 1.
    El sistema bloquea el acceso hasta que se cambie la contraseña.
    """
    try:
        con.execute("ALTER TABLE usuarios ADD COLUMN debe_cambiar_password INTEGER DEFAULT 0")
        con.commit()
    except Exception:
        pass  # La columna ya existe

    # Marcar al admin por defecto para que fuerce el cambio si su hash
    # corresponde a la contraseña vacía o débil conocida ('admin1234').
    # Solo actualiza si aún no cambió (debe_cambiar_password sigue en 0).
    try:
        con.execute("""
            UPDATE usuarios
            SET debe_cambiar_password = 1
            WHERE email = 'admin@gestioncxc.local'
              AND debe_cambiar_password = 0
        """)
        con.commit()
    except Exception:
        pass


def _create_indexes(con):
    """
    Índices para las columnas más consultadas.
    Todos usan IF NOT EXISTS para ser idempotentes.
    """
    indexes = [
        # pagos — filtros frecuentes en reportes CxC y sync Odoo
        "CREATE INDEX IF NOT EXISTS idx_pagos_order      ON pagos(odoo_order_name)",
        "CREATE INDEX IF NOT EXISTS idx_pagos_estado     ON pagos(estado)",
        "CREATE INDEX IF NOT EXISTS idx_pagos_odoo_id    ON pagos(odoo_payment_id)",
        "CREATE INDEX IF NOT EXISTS idx_pagos_vendedor   ON pagos(vendedor_id)",
        # maestro_operaciones — libro mayor, filtros por fecha/tipo/conciliación
        "CREATE INDEX IF NOT EXISTS idx_maestro_fecha    ON maestro_operaciones(fecha)",
        "CREATE INDEX IF NOT EXISTS idx_maestro_tipo     ON maestro_operaciones(tipo)",
        "CREATE INDEX IF NOT EXISTS idx_maestro_origen   ON maestro_operaciones(origen)",
        "CREATE INDEX IF NOT EXISTS idx_maestro_odoo_pid ON maestro_operaciones(odoo_payment_id)",
        "CREATE INDEX IF NOT EXISTS idx_maestro_concil   ON maestro_operaciones(odoo_conciliado)",
        # tasas_cambio — consulta diaria BCV
        "CREATE INDEX IF NOT EXISTS idx_tasas_par_fecha  ON tasas_cambio(par, fecha DESC)",
        # notas_credito — filtro por orden y estado
        "CREATE INDEX IF NOT EXISTS idx_nc_order         ON notas_credito(odoo_order_name)",
        "CREATE INDEX IF NOT EXISTS idx_nc_estado        ON notas_credito(estado)",
        # cobranza_gestiones — filtro por cliente y fecha
        "CREATE INDEX IF NOT EXISTS idx_cobr_cliente     ON cobranza_gestiones(cliente_id)",
        "CREATE INDEX IF NOT EXISTS idx_cobr_fecha       ON cobranza_gestiones(fecha_gestion)",
        # ordenes_replica — join frecuente en reportes
        "CREATE INDEX IF NOT EXISTS idx_replica_order    ON ordenes_replica(odoo_order_name)",
        "CREATE INDEX IF NOT EXISTS idx_replica_estado   ON ordenes_replica(estado)",
        # gastos y nómina — filtro por periodo y estado
        "CREATE INDEX IF NOT EXISTS idx_gastos_periodo   ON gastos(periodo)",
        "CREATE INDEX IF NOT EXISTS idx_gastos_estado    ON gastos(estado)",
        "CREATE INDEX IF NOT EXISTS idx_nomina_periodo   ON nomina_registros(periodo)",
    ]
    for ddl in indexes:
        try:
            con.execute(ddl)
        except Exception as e:
            logger.warning('_create_indexes: %s — %s', ddl[:60], e)
    con.commit()
    logger.info('Índices de BD verificados/creados.')


def _seed(con):
    # Monedas por defecto
    monedas = [('USD', 'Dólar US', '$'), ('VES', 'Bolívar', 'Bs.'),
               ('USDT', 'Tether', 'USDT'), ('EUR', 'Euro', '€')]
    for cod, nom, sim in monedas:
        con.execute("INSERT OR IGNORE INTO monedas(codigo,nombre,simbolo) VALUES(?,?,?)",
                    (cod, nom, sim))

    # Métodos de pago por defecto
    metodos = [
        ('efectivo', '["USD","VES"]'),
        ('transferencia', '["VES"]'),
        ('pago_movil', '["VES"]'),
        ('zelle', '["USD"]'),
        ('binance', '["USDT"]'),
        ('efectivo_usd', '["USD"]'),
    ]
    for nombre, monedas_json in metodos:
        con.execute("INSERT OR IGNORE INTO metodos_pago(nombre,monedas_permitidas) VALUES(?,?)",
                    (nombre, monedas_json))

    # Usuario admin por defecto (password: admin1234)
    import hashlib
    try:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=['bcrypt'], deprecated='auto')
        pw_hash = ctx.hash('admin1234')
    except Exception:
        pw_hash = hashlib.sha256(b'admin1234').hexdigest()

    inserted = con.execute(
        """INSERT OR IGNORE INTO usuarios(nombre,email,password_hash,rol,debe_cambiar_password)
           VALUES(?,?,?,?,?)""",
        ('Administrador', 'admin@gestioncxc.local', pw_hash, 'admin', 1)
    )
    if inserted.rowcount:
        logger.warning(
            'Usuario admin creado con contraseña por defecto (admin1234). '
            'El sistema forzará el cambio en el primer inicio de sesión.'
        )
    con.commit()
