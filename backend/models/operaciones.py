from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date


# ── AUTH ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class UsuarioCreate(BaseModel):
    nombre: str
    email: str
    password: str
    rol: str = 'vendedor'


class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = None
    rol: Optional[str] = None
    activo: Optional[int] = None


# ── NOTAS DE CRÉDITO ──────────────────────────────────────────────────────────

class CondicionesNota(BaseModel):
    condicion_pago_requerido: int = 0
    condicion_moneda: Optional[str] = None
    condicion_dias_pago: Optional[int] = None


class CrearNotaRequest(BaseModel):
    odoo_order_name: str
    condiciones_opcionales: Optional[CondicionesNota] = None


class LineaDescuentoRequest(BaseModel):
    line_id: int
    descuento_pct: float


class ProponeDescuentosRequest(BaseModel):
    lineas: List[LineaDescuentoRequest]


class RechazarNotaRequest(BaseModel):
    motivo: str


class LimiteDescuentoCreate(BaseModel):
    tipo: str  # producto | categoria
    referencia: str
    limite_pct: float


# ── PAGOS ─────────────────────────────────────────────────────────────────────

class PagoCreate(BaseModel):
    odoo_order_name: Optional[str] = None
    partner_id: Optional[int] = None       # ID del cliente en Odoo
    venta_interna_id: Optional[int] = None
    monto: float
    moneda: str
    metodo: str
    banco: Optional[str] = None            # banco/cuenta destino
    tasa_bcv: Optional[float] = None
    tasa_custom: Optional[float] = None
    referencia: Optional[str] = None
    fecha_pago: Optional[str] = None


class RecibirPagoRequest(BaseModel):
    pago_id: int


class EnviarOdooRequest(BaseModel):
    pago_id: int
    journal_id: int


# ── PROMOCIONES ───────────────────────────────────────────────────────────────

class PromocionCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    activa: int = 1
    descuento_pct: float = 99.0
    producto_obsequio_ref: Optional[str] = None
    condicion_cliente_nuevo: int = 0
    condicion_min_productos: int = 0
    condicion_json: Optional[str] = None


class ValidarPromocionRequest(BaseModel):
    odoo_order_name: str
    promocion_id: int


# ── VENTAS INTERNAS ───────────────────────────────────────────────────────────

class VentaInternaCreate(BaseModel):
    cliente_nombre: str
    cliente_id_odoo: Optional[int] = None
    notas: Optional[str] = None


class LineaVentaInterna(BaseModel):
    producto_codigo: str
    producto_nombre: str
    cantidad: float
    precio_unitario: float
    descuento_pct: float = 0.0


class CompraInternaCreate(BaseModel):
    proveedor: str
    fecha: str
    total_usd: float
    lineas: List[dict] = []


# ── INVENTARIO ────────────────────────────────────────────────────────────────

class AjusteInventario(BaseModel):
    cantidad_delta: float
    motivo: Optional[str] = None


class ProductoInventario(BaseModel):
    producto_codigo: str
    producto_nombre: str
    stock_actual: float = 0
    costo_usd: Optional[float] = None


# ── LISTAS DE PRECIOS ─────────────────────────────────────────────────────────

class ListaPrecioCreate(BaseModel):
    nombre: str
    moneda: str = 'USD'
    activa: int = 1
    umbral_descuento_excluir: Optional[float] = None


class ItemListaPrecio(BaseModel):
    producto_ref: str
    precio: float


# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────

class MonedaCreate(BaseModel):
    codigo: str
    nombre: str
    simbolo: str
    activa: int = 1


class MetodoPagoCreate(BaseModel):
    nombre: str
    monedas_permitidas: str  # JSON array string
    odoo_journal_id: Optional[int] = None


class TasaCustomRequest(BaseModel):
    par: str = 'USD_VES'
    tasa_custom: float
    fecha: Optional[str] = None


class ProductoExtraUpdate(BaseModel):
    producto_ref: str
    marca: Optional[str] = None
    categoria_local: Optional[str] = None
    datos_extra: Optional[str] = None
