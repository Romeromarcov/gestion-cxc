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
        venta_interna_id INTEGER,
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

    CREATE TABLE IF NOT EXISTS ventas_internas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE,
        cliente_nombre TEXT,
        cliente_id_odoo INTEGER,
        vendedor_id INTEGER,
        estado TEXT DEFAULT 'borrador',
        total_usd REAL,
        notas TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ventas_internas_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venta_id INTEGER,
        producto_codigo TEXT,
        producto_nombre TEXT,
        cantidad REAL,
        precio_unitario REAL,
        descuento_pct REAL DEFAULT 0,
        FOREIGN KEY(venta_id) REFERENCES ventas_internas(id)
    );

    CREATE TABLE IF NOT EXISTS inventario_interno (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_codigo TEXT UNIQUE,
        producto_nombre TEXT,
        stock_actual REAL DEFAULT 0,
        costo_usd REAL,
        ultima_actualizacion TEXT
    );

    CREATE TABLE IF NOT EXISTS compras_internas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proveedor TEXT,
        fecha TEXT,
        total_usd REAL,
        estado TEXT DEFAULT 'borrador',
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS compras_internas_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        compra_id INTEGER,
        producto_codigo TEXT,
        producto_nombre TEXT,
        cantidad REAL,
        costo_unitario REAL,
        FOREIGN KEY(compra_id) REFERENCES compras_internas(id)
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
