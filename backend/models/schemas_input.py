"""
Pydantic models para los bodies de entrada de la API.

Convención:
  - Campos requeridos: sin default
  - Campos opcionales: Optional[T] = None  o  Field(default=...)
  - Literales acotados: Literal[...]

Agregar nuevos modelos al final del archivo con un comentario de sección.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


# ── Autenticación ─────────────────────────────────────────────────────────────

class CambiarPasswordRequest(BaseModel):
    password_actual: str = Field(..., min_length=1)
    password_nueva:  str = Field(..., min_length=8)

    @field_validator('password_nueva')
    @classmethod
    def nueva_distinta(cls, v, info):
        if v == info.data.get('password_actual'):
            raise ValueError('La nueva contraseña debe ser diferente a la actual')
        return v


# ── Acuerdos de Pago ──────────────────────────────────────────────────────────

class AcuerdoCreate(BaseModel):
    cliente_id:       int
    descripcion:      str = Field(..., min_length=1)
    monto_total:      float = Field(..., gt=0)
    fecha_inicio:     str   # ISO date YYYY-MM-DD
    cliente_nombre:   Optional[str] = ''
    moneda:           Literal['USD', 'VES', 'USDT', 'EUR'] = 'USD'
    plazo_total_dias: int   = Field(90, ge=1)
    periodicidad:     Literal['semanal', 'quincenal', 'mensual', 'unico'] = 'semanal'
    porcentaje_abono: float = Field(0.0, ge=0, le=100)
    monto_cuota:      float = Field(0.0, ge=0)
    ordenes_odoo:     list[str] = []
    notas:            Optional[str] = ''


class AcuerdoUpdate(BaseModel):
    descripcion:  Optional[str] = None
    estado:       Optional[Literal['activo', 'cumplido', 'incumplido', 'cancelado']] = None
    notas:        Optional[str] = None
    ordenes_odoo: Optional[list[str]] = None


class CuotaPagoUpdate(BaseModel):
    monto_pagado: float = Field(..., ge=0)


# ── Cobranza / CRM ────────────────────────────────────────────────────────────

class GestionCreate(BaseModel):
    cliente_id:      int
    fecha_gestion:   str   # ISO date
    tipo_contacto:   Literal['llamada', 'whatsapp', 'email', 'visita', 'otro']
    resultado:       Literal['contactado', 'no_contesto', 'buzon', 'promesa_pago', 'pago_realizado']
    cliente_nombre:  Optional[str] = ''
    orden_name:      Optional[str] = None
    ejecutivo_id:    Optional[int] = None
    monto_prometido: Optional[float] = None
    fecha_promesa:   Optional[str] = None
    comentarios:     Optional[str] = ''
    proxima_accion:  Optional[str] = ''
    fecha_proxima:   Optional[str] = None


class GestionUpdate(BaseModel):
    tipo_contacto:   Optional[Literal['llamada', 'whatsapp', 'email', 'visita', 'otro']] = None
    resultado:       Optional[Literal['contactado', 'no_contesto', 'buzon', 'promesa_pago', 'pago_realizado']] = None
    monto_prometido: Optional[float] = None
    fecha_promesa:   Optional[str] = None
    comentarios:     Optional[str] = None
    proxima_accion:  Optional[str] = None
    fecha_proxima:   Optional[str] = None


class PlantillaCreate(BaseModel):
    nombre:         str = Field(..., min_length=1)
    tipo:           Literal['antes_vencimiento', 'dia_vencimiento', 'despues_vencimiento', 'recordatorio']
    mensaje:        str = Field(..., min_length=1)
    dias_relativos: int  = 0
    canal:          Literal['whatsapp', 'email', 'ambos'] = 'whatsapp'
    activa:         int  = 1


class PlantillaUpdate(BaseModel):
    nombre:         Optional[str] = None
    tipo:           Optional[Literal['antes_vencimiento', 'dia_vencimiento', 'despues_vencimiento', 'recordatorio']] = None
    mensaje:        Optional[str] = None
    dias_relativos: Optional[int] = None
    canal:          Optional[Literal['whatsapp', 'email', 'ambos']] = None
    activa:         Optional[int] = None


class PreviewPlantillaRequest(BaseModel):
    cliente:     str = Field(..., min_length=1)
    orden:       str = ''
    monto:       str = ''
    vencimiento: str = ''
    dias_vencida: int = 0


# ── Cambios de Divisa ─────────────────────────────────────────────────────────

MONEDAS = Literal['USD', 'VES', 'USDT', 'EUR']

class CambioDivisaCreate(BaseModel):
    fecha:                str   # ISO date
    monto_egreso:         float = Field(..., gt=0)
    moneda_egreso:        MONEDAS
    monto_ingreso:        float = Field(..., gt=0)
    moneda_ingreso:       MONEDAS
    tasa_cambio:          float = Field(..., gt=0)
    descripcion:          Optional[str] = None
    banco_egreso:         Optional[str] = ''
    banco_ingreso:        Optional[str] = ''
    odoo_journal_egreso:  Optional[int] = None
    odoo_journal_ingreso: Optional[int] = None
    notas:                Optional[str] = ''

    @field_validator('moneda_ingreso')
    @classmethod
    def monedas_distintas(cls, v, info):
        if v == info.data.get('moneda_egreso'):
            raise ValueError('Las monedas de egreso e ingreso deben ser diferentes')
        return v


class CambioDivisaUpdate(BaseModel):
    fecha:                Optional[str] = None
    monto_egreso:         Optional[float] = Field(None, gt=0)
    moneda_egreso:        Optional[MONEDAS] = None
    banco_egreso:         Optional[str] = None
    odoo_journal_egreso:  Optional[int] = None
    monto_ingreso:        Optional[float] = Field(None, gt=0)
    moneda_ingreso:       Optional[MONEDAS] = None
    banco_ingreso:        Optional[str] = None
    odoo_journal_ingreso: Optional[int] = None
    tasa_cambio:          Optional[float] = Field(None, gt=0)
    descripcion:          Optional[str] = None
    notas:                Optional[str] = None


class RechazoBody(BaseModel):
    motivo: str = Field(..., min_length=1)


# ── Gastos ────────────────────────────────────────────────────────────────────

class GastoCreate(BaseModel):
    tipo:                  Literal['unico', 'recurrente', 'servicio_publico']
    categoria:             str = Field(..., min_length=1)
    descripcion:           str = Field(..., min_length=1)
    monto:                 float = Field(..., gt=0)
    fecha:                 str   # ISO date
    moneda:                MONEDAS = 'VES'
    subcategoria:          Optional[str] = None
    proveedor_nombre:      Optional[str] = None
    proveedor_odoo_id:     Optional[int] = None
    equivalente_usd:       Optional[float] = None
    referencia:            Optional[str] = None
    periodo:               Optional[str] = None
    es_recurrente:         bool = False
    dia_pago:              int  = Field(1, ge=1, le=31)
    odoo_cuenta_gasto_id:  Optional[int] = None
    odoo_cuenta_gasto_codigo: Optional[str] = None
    odoo_cuenta_banco_id:  Optional[int] = None
    odoo_journal_id:       Optional[int] = None
    notas:                 Optional[str] = None


# ── Maestro de Operaciones ────────────────────────────────────────────────────

class MaestroOperacionCreate(BaseModel):
    monto:        float = Field(..., gt=0)
    moneda:       MONEDAS
    tipo:         Literal['ingreso', 'egreso']
    fecha:        Optional[str] = None   # default: hoy
    nro_documento: Optional[str] = None
    metodo:       Optional[str] = None
    categoria:    Optional[str] = None
    subcategoria: Optional[str] = None
    descripcion:  Optional[str] = None
    tasa_bcv:     Optional[float] = None
    tasa_real:    Optional[float] = None
    origen:       str = 'manual'
    pago_id:      Optional[int] = None
    odoo_ref:     Optional[str] = None
    estado:       str = 'confirmado'
    journal_nombre: Optional[str] = None


# ── Fraccionamiento ───────────────────────────────────────────────────────────

class FraccionamientoLoteCreate(BaseModel):
    producto_nombre:    str = Field(..., min_length=1)
    unidad_padre:       str = Field(..., min_length=1)
    subunidad_nombre:   str = Field(..., min_length=1)
    factor_conversion:  float = Field(..., gt=0)
    cantidad_padre:     float = Field(1.0, gt=0)
    producto_ref:       Optional[str] = None
    costo_padre_usd:    Optional[float] = None
    precio_venta_usd:   Optional[float] = None
    notas:              Optional[str] = None


class FraccionamientoVentaCreate(BaseModel):
    cliente_nombre: str = Field(..., min_length=1)
    notas:          Optional[str] = None


class FraccionamientoLineaCreate(BaseModel):
    lote_id:           int
    cantidad:          float = Field(..., gt=0)
    precio_unitario_usd: float = Field(..., ge=0)
    descuento_pct:     float = Field(0.0, ge=0, le=100)


class FraccionamientoPagoCreate(BaseModel):
    monto:      float = Field(..., gt=0)
    metodo:     str   = Field(..., min_length=1)
    fecha_pago: str   # ISO date
    moneda:     MONEDAS = 'USD'
    referencia: Optional[str] = None
    notas:      Optional[str] = None


# ── Fraccionamiento (extras) ──────────────────────────────────────────────────

class FraccionamientoRegaloCreate(BaseModel):
    cantidad:     float = Field(..., gt=0)
    destinatario: Optional[str] = None
    notas:        Optional[str] = None


class FraccionamientoAjusteCreate(BaseModel):
    cantidad_delta: float   # puede ser negativo (disminuir) o positivo (aumentar)
    motivo:         Optional[str] = None


# ── Requisiciones ─────────────────────────────────────────────────────────────

class RequisicionCreate(BaseModel):
    empleado_nombre:   str = Field(..., min_length=1)
    motivo:            str = Field(..., min_length=1)
    departamento:      Optional[str] = None
    descripcion:       Optional[str] = None
    odoo_employee_id:  Optional[int] = None
    odoo_cuenta_id:    Optional[int] = None
    odoo_cuenta_codigo: Optional[str] = None
    odoo_journal_id:   Optional[int] = None
    odoo_location_id:  Optional[int] = None
    monto_estimado:    Optional[float] = None
    lineas:            list[dict] = []   # [{producto_nombre, cantidad, unidad, ...}]
    notas:             Optional[str] = None


class RequisicionUpdate(BaseModel):
    empleado_nombre:   Optional[str] = None
    departamento:      Optional[str] = None
    motivo:            Optional[str] = None
    descripcion:       Optional[str] = None
    odoo_cuenta_id:    Optional[int] = None
    odoo_cuenta_codigo: Optional[str] = None
    odoo_journal_id:   Optional[int] = None
    odoo_location_id:  Optional[int] = None
    lineas:            Optional[list[dict]] = None
    notas:             Optional[str] = None


# ── Zelle Terceros ────────────────────────────────────────────────────────────

class ZelleTerceroCreate(BaseModel):
    fecha:             str   # ISO date
    proveedor_nombre:  str   = Field(..., min_length=1)
    monto_usd:         float = Field(..., gt=0)
    descripcion:       Optional[str] = None
    proveedor_id:      Optional[int] = None
    cliente_nombre:    Optional[str] = None
    orden_cobrada:     Optional[str] = None
    referencia_zelle:  Optional[str] = None
    notas:             Optional[str] = None


class ZelleTerceroUpdate(BaseModel):
    fecha:             Optional[str] = None
    descripcion:       Optional[str] = None
    proveedor_id:      Optional[int] = None
    proveedor_nombre:  Optional[str] = None
    cliente_nombre:    Optional[str] = None
    orden_cobrada:     Optional[str] = None
    monto_usd:         Optional[float] = Field(None, gt=0)
    referencia_zelle:  Optional[str] = None
    notas:             Optional[str] = None


# ── Nómina ────────────────────────────────────────────────────────────────────

class NominaCreate(BaseModel):
    periodo:               str   = Field(..., pattern=r'^\d{4}-\d{2}$')  # YYYY-MM
    tipo:                  Literal['nomina', 'bonificacion', 'hora_extra', 'bono', 'otro']
    descripcion:           str   = Field(..., min_length=1)
    monto:                 float = Field(..., gt=0)
    moneda:                MONEDAS = 'VES'
    empleado_nombre:       Optional[str] = None
    empleado_odoo_id:      Optional[int] = None
    es_grupal:             bool = False
    equivalente_usd:       Optional[float] = None
    odoo_cuenta_gasto_id:  Optional[int] = None
    odoo_cuenta_gasto_codigo: Optional[str] = None
    odoo_cuenta_banco_id:  Optional[int] = None
    odoo_journal_id:       Optional[int] = None
    notas:                 Optional[str] = None


class NominaUpdate(BaseModel):
    periodo:               Optional[str] = None
    tipo:                  Optional[Literal['nomina', 'bonificacion', 'hora_extra', 'bono', 'otro']] = None
    descripcion:           Optional[str] = None
    empleado_nombre:       Optional[str] = None
    empleado_odoo_id:      Optional[int] = None
    es_grupal:             Optional[bool] = None
    monto:                 Optional[float] = Field(None, gt=0)
    moneda:                Optional[MONEDAS] = None
    equivalente_usd:       Optional[float] = None
    odoo_cuenta_gasto_id:  Optional[int] = None
    odoo_cuenta_gasto_codigo: Optional[str] = None
    odoo_cuenta_banco_id:  Optional[int] = None
    odoo_journal_id:       Optional[int] = None
    notas:                 Optional[str] = None


class NominaTerceroCreate(BaseModel):
    periodo:         str   = Field(..., pattern=r'^\d{4}-\d{2}$')
    empleado_nombre: str   = Field(..., min_length=1)
    proveedor_nombre: str  = Field(..., min_length=1)
    monto:           float = Field(..., gt=0)
    moneda:          MONEDAS = 'USD'
    nomina_id:       Optional[int] = None
    descripcion:     Optional[str] = None
    proveedor_id:    Optional[int] = None
    referencia:      Optional[str] = None
    odoo_cuenta_gasto_id: Optional[int] = None
    odoo_cuenta_ap_id:    Optional[int] = None
    odoo_journal_id:      Optional[int] = None
    notas:           Optional[str] = None


class NominaImportarOdoo(BaseModel):
    fecha_desde: Optional[str] = None   # ISO date
    fecha_hasta: Optional[str] = None   # ISO date


# ── Pagos Fiscales ────────────────────────────────────────────────────────────

class PagoFiscalCreate(BaseModel):
    tipo:        Literal['alcaldia', 'inces', 'aseo', 'iva', 'islr', 'seniat', 'sso', 'otro']
    descripcion: str   = Field(..., min_length=1)
    monto:       float = Field(..., gt=0)
    fecha:       str   # ISO date
    moneda:      MONEDAS = 'VES'
    periodo:          Optional[str] = None
    referencia:       Optional[str] = None
    equivalente_usd:  Optional[float] = None
    odoo_cuenta_gasto_id:    Optional[int] = None
    odoo_cuenta_gasto_codigo: Optional[str] = None
    odoo_cuenta_haber_id:    Optional[int] = None
    odoo_cuenta_haber_codigo: Optional[str] = None
    odoo_journal_id:         Optional[int] = None
    notas:            Optional[str] = None


class PagoFiscalUpdate(BaseModel):
    tipo:        Optional[Literal['alcaldia', 'inces', 'aseo', 'iva', 'islr', 'seniat', 'sso', 'otro']] = None
    descripcion: Optional[str] = None
    periodo:     Optional[str] = None
    monto:       Optional[float] = Field(None, gt=0)
    moneda:      Optional[MONEDAS] = None
    equivalente_usd:  Optional[float] = None
    fecha:       Optional[str] = None
    referencia:  Optional[str] = None
    odoo_cuenta_gasto_id:    Optional[int] = None
    odoo_cuenta_gasto_codigo: Optional[str] = None
    odoo_cuenta_haber_id:    Optional[int] = None
    odoo_cuenta_haber_codigo: Optional[str] = None
    odoo_journal_id:         Optional[int] = None
    notas:       Optional[str] = None
