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

<!-- actualizado: 2026-06-28 17:02 UTC -->

### Backend
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | ALTA] **Import de módulo no provisto (`database`)**. El archivo importa `from database import get_db`. No se ha proporcionado `database.py` en el código entregado, lo que provoca un `ImportError` al ejecutar la aplicación, impidiendo el arranque del servicio
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | MEDIA] **Eficiencia de N+1 Potencial**. Aunque se usa un join inicial (`query(Invoice, Customer)`), si `Invoice` tiene relaciones lazy-loaded adicionales (ej. payments, aunque no se usan en el reporte, el ORM podría intentar cargarlas si se acceden inadvertidamente). En este caso específico la consulta es eficiente, pero se recomienda explícitar `load_only` o solo seleccionar las columnas necesarias para reducir el payload de la base de datos

### Seguridad
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | CRÍTIC] **Falta de Autenticación y Autorización**. El endpoint `GET /aging` no utiliza la dependencia `Depends(get_current_user)` ni verifica permisos. Cualquier usuario anónimo puede acceder a información financiera sensible (saldos vencidos). Esto viola el requisito "Auth (401 sin token)"

<!-- actualizado: 2026-06-28 17:09 UTC -->

### Backend
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | CRÍTIC] **Archivo faltante (Schema)**. El código importa `from app.schemas.reports import AgingReportResponse, CustomerAgingSummary, InvoiceAgingDetail`. Sin el archivo `app/schemas/reports.py`, la aplicación fallará con `ImportError` al iniciar y no se podrá validar la respuesta del API
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | ALTA] **Violación de Convención de Importación**. El código usa `from app.models.invoice import Invoice` y `from app.models.customer import Customer`. El fingerprint establece explícitamente: "Imports planos dentro del dir del entrypoint... NO uses un paquete `app.` inexistente". Esto causará `ModuleNotFoundError` si la estructura no tiene `__init__.py` en `app`
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | MEDIA] **Posible Excepción en Cálculo de Fechas**. La línea `delta = cutoff_date - invoice.due_date` asume que `invoice.due_date` nunca es `None`. Si la base de datos contiene facturas sin fecha de vencimiento, el endpoint lanzará un error 500 interno (`TypeError: unsupported operand type(s)`). Se debe validar `if invoice.due_date is None`

### Seguridad
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | CRÍTIC] **Control de Acceso Roto (A01)**. La dependencia `get_current_user` valida la presencia del token y el formato "Bearer", pero **no implementa verificación de roles**. Cualquier usuario con un token válido (ej. cliente básico) puede acceder al reporte financiero completo. El requisito de testing "Auth (403 con permisos insuficientes)" no se cumple, ya que el endpoint devuelve 200 en lugar de 403 para usuarios no autorizados
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | ALTA] **Definición de Dependencia en Router**. La función `get_current_user` se define localmente dentro de `reports.py`. Esto viola el principio DRY y las mejores prácticas de seguridad, ya que la lógica de validación de tokens (que incluye decodificación de JWT en un futuro real) debería estar centralizada en `backend/dependencies.py` o similar para asegurar consistencia en toda la API

<!-- actualizado: 2026-06-28 17:12 UTC -->

### Backend
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | SECOPS | CRÍTICA] A01:2021 - Broken Access Control**
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | SECOPS | CRÍTICA] Availability / Integrity**
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | SECOPS | ALTA] A05:2021 - Security Misconfiguration / Error Handling**
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | SECOPS | ALTA] Architecture / Maintainability**

### Seguridad
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | SECOPS | ALTA] A07:2021 - Identification and Authentication Failures**

<!-- actualizado: 2026-06-28 17:17 UTC -->

### Backend
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | ALTA] **Riesgo de ImportError**. Se importa `from database import get_db`. Según el fingerprint, la estructura es `backend/main.py` y `backend/routers/`. Asumiendo que `database.py` reside en `backend/`, la importación relativa `from .database import get_db` es más segura y convencional para evitar problemas si el directorio de ejecución cambia. Si `database.py` está en la raíz, fallará a menos que se configure PYTHONPATH globalmente
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | MEDIA] **Falta validación de fecha límite**. El input `cutoffDate` no tiene una restricción `max` (hoy) en el HTML, permitiendo seleccionar fechas futuras. Aunque el backend puede manejarlo, visualmente podría confundir al reporte de "deuda vencida"

### Seguridad
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | CRÍTIC] **Autenticación no enviada al Backend**. La línea `// 'Authorization': ${token}` está comentada en el objeto `headers` dentro de `generateReport()`. Dado que el backend (`backend/routers/reports.py`) requiere estrictamente el header `Authorization` (ver línea 41 y 64 del backend), la petición fallará con **401 Unauthorized** cuando el usuario intente generar un reporte real (no demo), rompiendo el flujo funcional
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | MEDIA] **Simulación de Auth Insegura (Hardcoded)**. La función `get_current_user` valida el rol buscando el string "admin" dentro del token: `if "admin" in token.lower():`. Esto es una simulación frágil. En producción, esto debe reemplazarse por una decodificación real del JWT y verificación de claims `roles` o `scopes`

<!-- actualizado: 2026-06-28 17:24 UTC -->

### Frontend
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | ALTA] **Inconsistencia de Ruta API**. El frontend llama a `http://localhost:8000/api/v1/reports/aging`. Sin embargo, el router en `backend/routers/reports.py` define la ruta como `@router.get("/aging")`. Para que esto funcione, el archivo `backend/main.py` (no provisto en el código) debe incluir el router con el prefijo exacto `/api/v1/reports`. Si el `main.py` usa el prefijo genérico `/api/v1` o `/reports`, la petición fallará con **404 Not Found**. Se recomienda validar la configuración en `main.py`

### Seguridad
- [2026-06-28 | Reporte de antiguedad de saldos de cuentas por cobrar | QA | CRÍTIC] **Fallo de Autenticación (Header Comentado)**. La línea `// 'Authorization': ${token}` dentro del objeto `headers` está comentada. El backend (`backend/routers/reports.py`) requiere estrictamente el header `Authorization` (dependencia `get_current_user`). Al no enviarlo, cualquier petición real (demo mode desactivado) resultará en un error **401 Unauthorized**, rompiendo el flujo funcional principal del reporte
