import sqlite3
import os

# DATA_DIR puede sobreescribirse con variable de entorno (ej: Railway Volume en /app/data)
_data_dir = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(__file__), '..', 'data'))
DB = os.path.join(_data_dir, 'gestion_cxc.db')


def get_con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


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


def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
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
    con.close()


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

    con.execute("""INSERT OR IGNORE INTO usuarios(nombre,email,password_hash,rol)
                   VALUES(?,?,?,?)""",
                ('Administrador', 'admin@gestioncxc.local', pw_hash, 'admin'))
    con.commit()
