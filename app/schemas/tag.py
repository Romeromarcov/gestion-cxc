import logging
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

class TagBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100, description="Nombre único de la etiqueta")
    color: Optional[str] = Field(None, pattern=r'^#[0-9A-Fa-f]{6}$', description="Código hexadecimal de color")
    descripcion: Optional[str] = Field(None, max_length=255, description="Descripción opcional")

class TagCreate(TagBase):
    pass

class TagUpdate(TagBase):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    color: Optional[str] = Field(None, pattern=r'^#[0-9A-Fa-f]{6}$')
    descripcion: Optional[str] = Field(None, max_length=255)

class Tag(TagBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime