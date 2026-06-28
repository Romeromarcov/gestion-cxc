import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_active_user
from app.schemas.tag import Tag, TagCreate, TagUpdate
from app.crud import crud_tag
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/", response_model=List[Tag])
def read_tags(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Obtiene la lista de todas las etiquetas.
    Requiere autenticación.
    """
    logger.info(f"Usuario {current_user.id} listando etiquetas")
    tags = crud_tag.get_tags(db, skip=skip, limit=limit)
    return tags

@router.post("/", response_model=Tag, status_code=status.HTTP_201_CREATED)
def create_tag(
    tag: TagCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Crea una nueva etiqueta.
    Requiere autenticación y permisos de administrador (superuser).
    """
    # Verificación de autorización (403)
    if not current_user.is_superuser:
        logger.warning(f"Usuario {current_user.id} sin permisos intentó crear etiqueta")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para realizar esta acción"
        )
    
    logger.info(f"Usuario {current_user.id} creando etiqueta: {tag.nombre}")
    return crud_tag.create_tag(db=db, tag=tag)

@router.get("/{tag_id}", response_model=Tag)
def read_tag(
    tag_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Obtiene una etiqueta específica por ID.
    Requiere autenticación.
    """
    logger.info(f"Usuario {current_user.id} buscando etiqueta ID: {tag_id}")
    db_tag = crud_tag.get_tag(db, tag_id=tag_id)
    if db_tag is None:
        raise HTTPException(status_code=404, detail="Etiqueta no encontrada")
    return db_tag

@router.put("/{tag_id}", response_model=Tag)
def update_tag(
    tag_id: int,
    tag: TagUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Actualiza una etiqueta existente.
    Requiere autenticación y permisos de administrador (superuser).
    """
    if not current_user.is_superuser:
        logger.warning(f"Usuario {current_user.id} sin permisos intentó actualizar etiqueta {tag_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para realizar esta acción"
        )

    logger.info(f"Usuario {current_user.id} actualizando etiqueta ID: {tag_id}")
    db_tag = crud_tag.update_tag(db=db, tag_id=tag_id, tag=tag)
    return db_tag

@router.delete("/{tag_id}")
def delete_tag(
    tag_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Elimina una etiqueta.
    Requiere autenticación y permisos de administrador (superuser).
    """
    if not current_user.is_superuser:
        logger.warning(f"Usuario {current_user.id} sin permisos intentó eliminar etiqueta {tag_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para realizar esta acción"
        )

    logger.info(f"Usuario {current_user.id} eliminando etiqueta ID: {tag_id}")
    return crud_tag.delete_tag(db=db, tag_id=tag_id)