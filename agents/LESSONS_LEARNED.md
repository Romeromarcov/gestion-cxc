# Lessons Learned

> Generado automáticamente por la Fábrica de Software.
> Cada agente que escribe código recibe este documento al inicio.
> **No editar manualmente** — se actualiza tras cada feature.

<!-- actualizado: 2026-06-27 19:29 UTC -->

### Base de Datos
- [2026-06-27 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] Error en configuración de relación SQLAlchemy. La propiedad `tags` define `back_populates="tags"`, pero en el modelo `Tag` la relación se llama `accounts`. Debe ser `back_populates="accounts"` para mantener la bidireccionalidad. Esto causará errores al intentar acceder a las etiquetas desde una cuenta

<!-- actualizado: 2026-06-27 19:32 UTC -->

### Backend
- [2026-06-27 | CRUD de etiquetas para clasificar cuentas por cobrar | SECOPS | CRÍTICA] `app/models/account_receivable.py` (Data Integrity/Availability)**

<!-- actualizado: 2026-06-27 20:05 UTC -->

### Backend
- [2026-06-27 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante (`app/models/tag.py`)**. El archivo `account_receivable.py` intenta importar `account_tag` desde `app.models.tag`. Dado que este archivo no se proporcionó en el código backend y no se puede asumir su existencia en una revisión de código, la aplicación fallará con `ImportError` al iniciar. Esto impide verificar la corrección de la relación bidireccional (`back_populates="accounts"`)
- [2026-06-27 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivos faltantes (Router, CRUD, Schemas)**. No se ha proporcionado el código para `tags.py` (router), `crud_tag.py` ni `schemas/tag.py`. Sin estos archivos, la funcionalidad CRUD de etiquetas no existe, y por ende no se pueden verificar las correcciones de seguridad solicitadas (401/403 en endpoints) ni el happy path
- [2026-06-27 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | MEDIA] **Manejo de error genérico en API**. En `api.request`, si ocurre un error de red que no sea 401/403 (ej: servidor caído 500 o timeout), se captura en el bloque `catch`, se lanza el error y se muestra "Error de conexión con el servidor". Sin embargo, `loadTags` no tiene un bloque `try/catch` específico (ya está dentro de `try/catch` pero si `api.getTags` falla, solo loguea a consola). Sería ideal validar si el estado de la UI refleja claramente el fallo de carga
