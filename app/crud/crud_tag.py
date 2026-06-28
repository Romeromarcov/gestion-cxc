import logging
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status
from app.models.tag import Tag
from app.schemas.tag import TagCreate, TagUpdate

logger = logging.getLogger(__name__)

def get_tag(db: Session, tag_id: int):
    logger.debug(f"Buscando tag con ID: {tag_id}")
    return db.query(Tag).filter(Tag.id == tag_id).first()

def get_tags(db: Session, skip: int = 0, limit: int = 100):
    logger.info("Obteniendo lista de tags")
    return db.query(Tag).offset(skip).limit(limit).all()

def create_tag(db: Session, tag: TagCreate):
    logger.info(f"Intentando crear tag: {tag.nombre}")
    db_tag = Tag(**tag.model_dump())
    try:
        db.add(db_tag)
        db.commit()
        db.refresh(db_tag)
        logger.info(f"Tag creado exitosamente: {db_tag.id}")
        return db_tag
    except IntegrityError:
        db.rollback()
        logger.warning(f"Error de integridad: El nombre '{tag.nombre}' ya existe")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una etiqueta con ese nombre"
        )

def update_tag(db: Session, tag_id: int, tag: TagUpdate):
    logger.info(f"Intentando actualizar tag ID: {tag_id}")
    db_tag = get_tag(db, tag_id)
    if not db_tag:
        logger.warning(f"Tag ID: {tag_id} no encontrado para actualización")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etiqueta no encontrada")

    update_data = tag.model_dump(exclude_unset=True)
    
    for key, value in update_data.items():
        setattr(db_tag, key, value)

    try:
        db.commit()
        db.refresh(db_tag)
        logger.info(f"Tag ID: {tag_id} actualizado correctamente")
        return db_tag
    except IntegrityError:
        db.rollback()
        logger.warning(f"Error de integridad al actualizar tag ID: {tag_id} (nombre duplicado?)")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflicto: Ya existe una etiqueta con ese nombre"
        )

def delete_tag(db: Session, tag_id: int):
    logger.info(f"Intentando eliminar tag ID: {tag_id}")
    db_tag = get_tag(db, tag_id)
    if not db_tag:
        logger.warning(f"Tag ID: {tag_id} no encontrado para eliminación")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etiqueta no encontrada")
    
    try:
        db.delete(db_tag)
        db.commit()
        logger.info(f"Tag ID: {tag_id} eliminado correctamente")
        return {"message": "Etiqueta eliminada correctamente"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error al eliminar tag ID: {tag_id}. Error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al eliminar la etiqueta")