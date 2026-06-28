# Lessons Learned

> Generado automáticamente por la Fábrica de Software.
> Cada agente que escribe código recibe este documento al inicio.
> **No editar manualmente** — se actualiza tras cada feature.

<!-- actualizado: 2026-06-28 04:31 UTC -->

### Backend
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | MEDIA] **Violación DRY y sintaxis obsoleta.** La función `remove` utiliza `db.query(Tag).get(id)` en lugar de reutilizar el método existente `self.get(db, id=id)`. Esto duplica la lógica de consulta y viola el principio DRY. Además, `query(Model).get()` es sintaxis de SQLAlchemy < 1.4; en versiones modernas (2.0) esto puede fallar o lanzar advertencias, debiendo usarse `db.get(Tag, id)`
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | ALTA] **Riesgo de ImportError/Arquitectura.** Se importa el router desde `from app.routers import tags`. Sin embargo, el resto de la aplicación utiliza `from app.api.v1.endpoints import ...`. A menos que la estructura de directorios se haya modificado explícitamente para crear `app/routers/`, lo cual no se evidencia en el resto del código provisto (solo se muestra el archivo `app/routers/tags.py` aislado), la aplicación fallará al arrancar con `ModuleNotFoundError`. Debería seguir la convención `app.api.v1.endpoints.tags`

<!-- actualizado: 2026-06-28 04:34 UTC -->

### Backend
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | SECOPS | CRÍTICA] `app/api/v1/api.py`**

<!-- actualizado: 2026-06-28 04:36 UTC -->

### Backend
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante**. No se ha proporcionado el código del Router. No se puede verificar la implementación de endpoints, lógica de autorización (403), ni validaciones. El archivo `app/api/v1/api.py` intenta importarlo, lo que causará un `ImportError` al ejecutar la aplicación
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante**. No se puede verificar la validación de esquemas (Pydantic) ni que la API rechace datos inválidos con 422
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante**. No se puede verificar el manejo de transacciones (`db.rollback()`), la lógica de negocio ni la captura de `IntegrityError` en las operaciones de actualización
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | ALTA] **Riesgo de ImportError en tiempo de ejecución**. Aunque la ruta de importación `from app.api.v1.endpoints import tags` es arquitectónicamente correcta (corrige el error anterior de `app.routers`), al no existir los archivos anteriores (`tags.py` dentro de `endpoints`), la aplicación fallará inevitablemente al iniciar

### Base de Datos
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante**. Sin el modelo `Tag`, no se puede verificar la relación N:M con `AccountReceivable`, ni la corrección de `back_populates="accounts"` reportada en iteraciones anteriores

<!-- actualizado: 2026-06-28 04:37 UTC -->

### Backend
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | SECOPS | CRÍTICA] BUG-001 a BUG-004 (Archivos Faltantes)**: La ausencia de `tags.py`, `tag.py` (model), `tag.py` (schema) y `crud_tag.py` provoca que la aplicación falle al iniciar (`ImportError`) o al intentar acceder a los endpoints. Esto representa una denegación de servicio (DoS) funcional

### Tests
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | SECOPS | ALTA] A01: Broken Access Control (Potencial)**: Dado que los endpoints no están implementados, existe el riesgo inminente de que, al crearlos, no se restrinja el acceso a operaciones de escritura (POST, PUT, DELETE). Los tests de QA (`test_create_tag_forbidden`) verifican explícitamente que solo superusuarios puedan crear etiquetas. Si esto no se implementa, se vulnera la confidencialidad e integridad de los datos del catálogo

<!-- actualizado: 2026-06-28 04:40 UTC -->

### Backend
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Relación SQLAlchemy comentada**. La línea `cuentas = relationship(...)` está comentada, lo que rompe la relación bidireccional requerida por el plan. Esto impedirá que las cuentas por cobrar recuperen sus etiquetas
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante**. No se ha proporcionado el Router. Sin este archivo, no existen endpoints para probar (401, Happy Path, etc.), provocando `ImportError` en `api.py`
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante**. No se ha proporcionado la lógica CRUD. No se pueden realizar operaciones de base de datos ni verificar transacciones/rollbacks

### Base de Datos
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante/No actualizado**. No se ha proporcionado el código del modelo `AccountReceivable` para verificar la relación `tags` y el `back_populates="accounts"`, requisito fundamental para la integridad del feature

### Seguridad
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Endpoints inseguros (implícito)**. Dado que el Router y la lógica de Auth no se proporcionaron, no se puede verificar que los endpoints implementen las dependencias `get_current_user` o verificaciones de roles (403), dejando la API expuesta si se implementara sin estos controles

### Tests
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] **Archivo faltante**. No se han definido los Schemas Pydantic (`TagCreate`, `TagUpdate`, etc.). Es imposible validar los datos de entrada/salida ni ejecutar los tests de validación (422)

<!-- actualizado: 2026-06-28 04:45 UTC -->

### Backend
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] La relación `cuentas = relationship(...)` se encuentra comentada. Esto rompe la relación bidireccional requerida en el plan y mencionada en las lecciones aprendidas, impidiendo que `AccountReceivable` recupere sus etiquetas
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] Archivo faltante. No se definieron los schemas Pydantic (`TagCreate`, `TagUpdate`). Es imposible validar la entrada de datos (422) ni estructurar la respuesta de la API
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] Archivo faltante. No existe la lógica CRUD necesaria para interactuar con la base de datos. No se puede verificar el manejo de transacciones ni la lógica de negocio (ej. validación de nombre duplicado)

### Base de Datos
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] Archivo no actualizado/faltante. El modelo `AccountReceivable` (o `CuentaPorCobrar`) no se ha actualizado con la relación `etiquetas` y el `back_populates`, por lo que la relación N:M no es funcional en la práctica

### Tests
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | QA | CRÍTIC] Archivo faltante. No se proporcionó el Router. Sin este archivo, los endpoints (`GET`, `POST`, etc.) no existen, haciendo imposible pasar los tests de Happy Path y Autenticación solicitados

<!-- actualizado: 2026-06-28 04:46 UTC -->

### Backend
- [2026-06-28 | CRUD de etiquetas para clasificar cuentas por cobrar | SECOPS | CRÍTICA] *   **Endpoint de Listado:** Si el endpoint `GET /tags` se implementa siguiendo el patrón estándar de CRUD (ej. `session.query(Tag).all()` o `.limit()`), expondrá inmediatamente todas las etiquetas de todas las empresas a cualquier usuario autenticado
