import logging
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Table, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

logger = logging.getLogger(__name__)

# Tabla de asociación para la relación Many-to-Many entre Cuentas y Etiquetas
cuentas_etiquetas = Table(
    'cuentas_etiquetas',
    Base.metadata,
    Column('account_receivable_id', Integer, ForeignKey('account_receivable.id', ondelete="CASCADE"), primary_key=True),
    Column('tag_id', Integer, ForeignKey('tags.id', ondelete="CASCADE"), primary_key=True)
)

class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), unique=True, index=True, nullable=False)
    color = Column(String(7), nullable=True)  # Formato Hex #RRGGBB
    descripcion = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relación bidireccional corregida según lecciones aprendidas.
    # 'tags' en AccountReceivable apunta a 'accounts' aquí.
    accounts = relationship("AccountReceivable", secondary=cuentas_etiquetas, back_populates="tags")

    def __repr__(self):
        return f"<Tag(id={self.id}, nombre='{self.nombre}')>"