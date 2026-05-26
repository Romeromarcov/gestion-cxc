# Plan de Trabajo — Auditoría GestionCxC
> Generado: 2026-05-25  
> Sistema: FastAPI + PostgreSQL + Odoo 18 (Lubrikca)

---

## Resumen de Hallazgos

| # | Hallazgo | Severidad | Responsable |
|---|---|---|---|
| H-01 | `python-jose` con CVEs activas instalado sin usar | 🔴 Crítico | Claude |
| H-02 | `SECRET_KEY` en `.env` es el placeholder de ejemplo | 🔴 Crítico | **Usuario** |
| H-03 | API Key real de Odoo en `.env` local | 🔴 Crítico | **Usuario** |
| H-04 | Sin pool de conexiones PostgreSQL | 🔴 Crítico | Claude |
| H-05 | Rate limiting solo en memoria (no escala) | 🔴 Crítico | Claude (parcial) |
| H-06 | `backend/requirements.txt` desincronizado (falta psycopg2) | 🔴 Crítico | Claude |
| H-07 | ~19 endpoints GET potencialmente sin auth | 🟠 Medio | Claude |
| H-08 | Dos librerías JWT instaladas, solo se usa una | 🟠 Medio | Claude (parte de H-01) |
| H-09 | Contraseña por defecto `admin1234` sin forzar cambio | 🟠 Medio | Claude |
| H-10 | Sin migraciones versionadas / tracking de esquema | 🟠 Medio | Claude |
| H-11 | Múltiples archivos de deployment inconsistentes | 🟠 Medio | Claude |
| H-12 | Puerto 5432 expuesto al host en docker-compose | 🟠 Medio | Claude |
| H-13 | Endpoints con `body: dict` sin validación Pydantic | 🟠 Medio | Claude |
| H-14 | Sin suite de tests | 🟡 Bajo | Claude (scaffold) |
| H-15 | Archivos de debug en root del repo | 🟡 Bajo | Claude |
| H-16 | God Files: `maestro.py` (941 l) y `reportes.py` (900 l) | 🟡 Bajo | Claude |
| H-17 | Dependencias desactualizadas | 🟡 Bajo | Claude |
| H-18 | Singleton Odoo no es thread-safe | 🟡 Bajo | Claude |

---

## Fases de Trabajo

---

### FASE 1 — Seguridad Crítica (Semana 1)
> Objetivo: eliminar vulnerabilidades que pueden comprometer el sistema en producción hoy.

#### H-01 + H-08 · Eliminar `python-jose` de requirements
**Responsable:** Claude  
**Archivos:** `requirements.txt`, `backend/requirements.txt`

- [ ] Eliminar línea `python-jose[cryptography]==3.3.0` de ambos archivos
- [ ] Verificar que ningún módulo importa `jose` (`grep -r "from jose" .`)
- [ ] Commit: `security: remove unused python-jose (CVE-2024-33664, CVE-2024-33663)`

#### H-06 · Sincronizar `backend/requirements.txt`
**Responsable:** Claude  
**Archivos:** `backend/requirements.txt`

- [ ] Agregar `psycopg2-binary==2.9.9` al archivo
- [ ] Agregar comentario explicando que el Dockerfile usa el raíz
- [ ] Evaluar si eliminar el archivo duplicado o mantenerlo como referencia de dev

#### H-02 · Endurecer validación de SECRET_KEY
**Responsable:** Claude (código) + **Usuario** (valor real en Railway)  
**Archivos:** `backend/config.py`

- [ ] Ampliar la validación para detectar cualquier secreto que contenga palabras obvias (`cambiar`, `aqui`, `placeholder`, `ejemplo`, `test`, `dev`)
- [ ] Lanzar `RuntimeError` (no solo warning) si el secreto es inseguro en entorno de producción
- [ ] (**Usuario**) Generar secreto real y actualizar en Railway Settings → Variables

#### H-04 · Implementar pool de conexiones PostgreSQL
**Responsable:** Claude  
**Archivos:** `backend/database.py`, `backend/db_adapter.py`

- [ ] Crear `ThreadedConnectionPool` con `minconn=2`, `maxconn=20`
- [ ] Modificar `get_con()` para obtener conexión del pool
- [ ] Modificar `CompatConnection.close()` para devolver al pool en vez de cerrar
- [ ] Agregar configuración `DATABASE_POOL_MAX` como variable de entorno
- [ ] Test de carga básico para verificar que el pool funciona

---

### FASE 2 — Seguridad Media (Semana 2)
> Objetivo: cerrar accesos no protegidos y consolidar el deployment.

#### H-07 · Auditar y proteger endpoints sin auth
**Responsable:** Claude  
**Archivos:** todos los routers

- [ ] Revisar manualmente los 19 endpoints detectados:
  - `GET /reportes/ventas` — datos financieros, requiere auth
  - `GET /maestro/` — libro mayor, requiere auth
  - `GET /nomina/` — nómina, requiere auth
  - `GET /gastos/` — egresos, requiere auth
  - `GET /creditos/` — créditos a clientes, requiere auth
  - `GET /zelle-terceros/` — operaciones, requiere auth
  - `GET /pagos-fiscales/` — pagos fiscales, requiere auth
  - `GET /cobranza/gestiones` — gestiones CRM, requiere auth
  - `GET /creditos/disponibles` — requiere auth
  - `GET /cambios-divisa/` — requiere auth
  - `GET /cambios-divisa/cuentas` — requiere auth
  - `GET /compras-odoo/` — requiere auth
  - `GET /compras-odoo/reporte-cxp` — requiere auth
  - `GET /requisiciones/` — requiere auth
  - `GET /zelle-terceros/cuentas` — revisar si es config pública o privada
  - `GET /nomina/terceros` — requiere auth
  - `GET /nomina/cuentas` — revisar
  - `GET /pagos-fiscales/buscar-cuenta` — revisar
- [ ] Agregar `Depends(get_current_user)` o `Depends(require_roles(...))` según corresponda
- [ ] Commit por router afectado

#### H-09 · Mejorar protección de contraseña admin por defecto
**Responsable:** Claude  
**Archivos:** `backend/database.py`, `backend/routers/auth.py`

- [ ] Agregar campo `debe_cambiar_password INTEGER DEFAULT 0` a tabla `usuarios`
- [ ] En `_seed()`: crear admin con `debe_cambiar_password=1`
- [ ] En `get_current_user()`: si `debe_cambiar_password=1`, devolver respuesta `403` con mensaje claro
- [ ] Agregar endpoint `PUT /auth/cambiar-password` para cambio forzado
- [ ] (**Usuario**) Cambiar contraseña admin en producción

#### H-11 · Consolidar archivos de deployment
**Responsable:** Claude  
**Archivos:** `Procfile`, `nixpacks.toml`

- [ ] Eliminar `Procfile` (Railway usa Dockerfile según `railway.json`)
- [ ] Eliminar `nixpacks.toml` (ya no aplica con builder Dockerfile)
- [ ] Documentar en `README.md` el único método de deploy válido
- [ ] Commit: `chore: remove obsolete Procfile and nixpacks.toml`

#### H-12 · Restringir puerto PostgreSQL en docker-compose
**Responsable:** Claude  
**Archivos:** `docker-compose.yml`

- [ ] Cambiar `"5432:5432"` → `"127.0.0.1:5432:5432"` (solo accesible desde localhost)
- [ ] Actualizar contraseña `gestion_pass` → variable de entorno desde `.env`
- [ ] Agregar nota en el compose sobre seguridad

#### H-15 · Limpiar archivos de debug del root
**Responsable:** Claude

- [ ] Eliminar `fix_venc.py` del directorio raíz
- [ ] Eliminar `check_routes.js` del directorio raíz
- [ ] Eliminar directorio `unpacked_spec/` (es el Word desempaquetado)
- [ ] Verificar que `.gitignore` ya los cubre (sí los cubre)

---

### FASE 3 — Calidad y Robustez (Semana 3)
> Objetivo: mejorar mantenibilidad, validación de entradas y resiliencia.

#### H-13 · Agregar Pydantic models a endpoints con `body: dict`
**Responsable:** Claude  
**Archivos:** múltiples routers

- [ ] Identificar todos los endpoints con `body: dict` (aprox. 25–30)
- [ ] Priorizar los de mayor riesgo:
  - `cambios_divisa.py` (crear, actualizar, update_config)
  - `cobranza.py` (registrar_gestion, actualizar_gestion, crear_plantilla)
  - `acuerdos_pago.py` (crear_acuerdo, actualizar_acuerdo)
  - `gastos.py` (crear, actualizar)
  - `maestro.py` (crear, actualizar)
- [ ] Crear Pydantic models en `models/operaciones.py` o en un archivo por módulo
- [ ] Sustituir `body: dict` por el modelo correspondiente
- [ ] Verificar que el Swagger auto-generado queda documentado

#### H-10 · Agregar tabla de control de migraciones
**Responsable:** Claude  
**Archivos:** `backend/database.py`

- [ ] Crear tabla `schema_migrations(version TEXT PRIMARY KEY, aplicada_en TEXT)`
- [ ] Refactorizar `init_db()` para registrar y saltar migraciones ya aplicadas
- [ ] Las migraciones existentes se marcan como aplicadas en el primer arranque
- [ ] Documentar cómo agregar nuevas migraciones

#### H-18 · Thread-safety del singleton Odoo
**Responsable:** Claude  
**Archivos:** `backend/routers/ventas.py`

- [ ] Agregar `threading.Lock` para proteger `_odoo_instance`
- [ ] Usar `with _odoo_lock:` en `get_odoo()` al verificar/reemplazar la instancia

#### H-17 · Actualizar dependencias desactualizadas
**Responsable:** Claude  
**Archivos:** `requirements.txt`, `backend/requirements.txt`

- [ ] Actualizar `bcrypt` 3.2.2 → 4.x
- [ ] Actualizar `uvicorn` 0.30.0 → última estable
- [ ] Evaluar `passlib` (sin mantenimiento desde 2023) — considerar migrar a `bcrypt` directo
- [ ] Actualizar `fastapi` 0.115.0 → última estable
- [ ] Ejecutar tests de regresión después de cada actualización
- [ ] Fijar versiones exactas en requirements

---

### FASE 4 — Deuda Técnica (Mes 2)
> Objetivo: tests, refactorización de god files, escalabilidad.

#### H-14 · Scaffold de tests
**Responsable:** Claude (estructura) + **Usuario** (casos de negocio)

- [ ] Configurar `pytest` + `pytest-asyncio` + `httpx` en el proyecto
- [ ] Crear `backend/tests/` con estructura:
  ```
  tests/
    conftest.py          # fixtures: BD de prueba, cliente HTTP
    test_auth.py         # login, tokens, rate limit
    test_db_adapter.py   # traducción SQL SQLite→PostgreSQL
    test_tasas_cambio.py # cálculos de conversión
    test_replicas.py     # lógica de réplicas Odoo
    test_reportes.py     # totales CxC, NC deductions
  ```
- [ ] Implementar tests de `db_adapter.py` (traducción de queries)
- [ ] Implementar tests de `auth.py` (login, token, rate limit)
- [ ] (**Usuario**) Agregar casos de negocio específicos de Lubrikca

#### H-16 · Dividir god files
**Responsable:** Claude  
**Archivos:** `backend/routers/maestro.py`, `backend/routers/reportes.py`

- [ ] `maestro.py` → separar en:
  - `maestro_operaciones.py` (CRUD)
  - `maestro_sync.py` (sincronización Odoo)
  - `maestro_reportes.py` (resúmenes y exportación)
- [ ] `reportes.py` → separar en:
  - `reportes_cxc.py` (reporte principal CxC)
  - `reportes_excel.py` (exportación Excel)
  - `reportes_dashboard.py` (resumen y alertas)
- [ ] Mantener los prefijos de URL idénticos para no romper el frontend

#### H-05 · Rate limiting distribuido (si se escala)
**Responsable:** Claude (código) + **Usuario** (infraestructura Redis)

- [ ] (**Usuario**) Provisionar Redis en Railway (add-on)
- [ ] Integrar `slowapi` + Redis como backend de rate limiting
- [ ] Reemplazar `_login_attempts` dict por `slowapi` limiter
- [ ] Extender rate limiting a endpoints críticos (no solo login)

---

## Resumen de Commits Planificados

```
FASE 1
  security: remove python-jose (CVE-2024-33664, CVE-2024-33663)
  fix: sync backend/requirements.txt — add psycopg2-binary
  security: harden SECRET_KEY validation — raise on weak secrets
  feat(db): implement psycopg2 ThreadedConnectionPool

FASE 2
  security: add auth guards to unprotected GET endpoints
  feat(auth): force password change for default admin account
  chore: remove Procfile and nixpacks.toml
  fix(docker): restrict postgres port to 127.0.0.1
  chore: remove debug files (fix_venc.py, check_routes.js, unpacked_spec/)

FASE 3
  feat(models): add Pydantic schemas to body:dict endpoints
  feat(db): add schema_migrations tracking table
  fix(odoo): thread-safe singleton with Lock
  chore(deps): update bcrypt, uvicorn, fastapi to latest stable

FASE 4
  test: add pytest scaffold with auth and db_adapter tests
  refactor: split maestro.py into 3 focused modules
  refactor: split reportes.py into 3 focused modules
  feat(ratelimit): redis-backed slowapi rate limiting
```

---

## Lo que debes hacer TÚ (Usuario)

> Las siguientes acciones **no puede ejecutarlas el asistente** porque requieren acceso a sistemas externos, credenciales, o decisiones de infraestructura.

### 🔴 Urgente — Hacer esta semana

#### 1. Cambiar SECRET_KEY en Railway
```bash
# Genera un secreto seguro
python -c "import secrets; print(secrets.token_hex(32))"
```
Ve a Railway → tu proyecto → Variables → `SECRET_KEY` y pega el valor generado.  
⚠️ Todos los usuarios activos tendrán que volver a iniciar sesión (tokens invalidados).

#### 2. Rotar la API Key de Odoo
1. Entra a Odoo → `gerencia.lubrikca@gmail.com` → Configuración → API Keys
2. Elimina la key actual (`6045919225b...`)
3. Genera una nueva
4. Actualiza en Railway → Variables → `ODOO_API_KEY`
5. Actualiza tu `.env` local con el nuevo valor

#### 3. Cambiar contraseña del admin en producción
Una vez que Claude implemente el forzado de cambio (H-09), entra con `admin@gestioncxc.local` / `admin1234` y cambia la contraseña en la primera pantalla.  
Si ya tienes acceso directo a la BD:
```sql
UPDATE usuarios SET password_hash = '...' WHERE email = 'admin@gestioncxc.local';
```
(Usar `bcrypt` para generar el hash, no SHA-256.)

### 🟠 Esta semana si es posible

#### 4. Verificar variables de entorno en Railway
Confirmar que en Railway → Variables están correctamente configuradas:
- `SECRET_KEY` — valor aleatorio real (≥32 chars)
- `ODOO_API_KEY` — nueva clave rotada
- `ODOO_HOST`, `ODOO_DB`, `ODOO_USER` — correctos
- `DATABASE_URL` — apuntando a la BD de Railway, no a localhost
- `ALLOWED_ORIGINS` — limitado a tu dominio (no `*`)

#### 5. Revisar ALLOWED_ORIGINS en producción
Si `ALLOWED_ORIGINS=*` en Railway, cualquier sitio web puede hacer requests a tu API autenticados con cookies. Cambia a:
```
ALLOWED_ORIGINS=https://tu-dominio.railway.app,https://app.lubrikca.com
```

### 🟡 Próximas semanas

#### 6. Definir casos de prueba de negocio
Cuando Claude cree el scaffold de tests (Fase 4), necesitas proveer:
- Ejemplos reales de cálculo de NC (nota de crédito) con montos esperados
- Casos de borde en conversión de monedas (VES/USD/USDT)
- Flujos de pago completos para tests de integración

#### 7. Decisión sobre Redis para rate limiting
Si el sistema va a escalar a múltiples instancias en Railway (horizontal scaling):
- Añadir Redis como add-on en Railway (~$5/mes)
- Avisarle a Claude para proceder con la integración `slowapi` + Redis

#### 8. Monitoreo de logs en Railway
Configurar alertas en Railway para:
- Errores 500 repetidos (posible bug)
- Warnings de `sync_pagos_odoo` y `auto_sync_pagos_clientes`
- Fallos de conexión a Odoo
Considera Datadog, Sentry o el propio Railway Observability.

---

## Tablero de Progreso

| Fase | Ítem | Estado |
|---|---|---|
| **F1** | H-01 Eliminar python-jose | ✅ `e64a9d2` |
| **F1** | H-06 Sync requirements.txt | ✅ `e64a9d2` |
| **F1** | H-02 Endurecer validación SECRET_KEY | ✅ `f90a314` |
| **F1** | H-02 **[USUARIO]** Actualizar SECRET_KEY en Railway | ⬜ Pendiente |
| **F1** | H-03 **[USUARIO]** Rotar API Key Odoo | ⬜ Pendiente |
| **F1** | H-04 Pool de conexiones PostgreSQL | ✅ `6772027` |
| **F2** | H-07 Proteger endpoints sin auth | ✅ `0ab41bf` |
| **F2** | H-09 Forzar cambio contraseña admin | ✅ `6325d9f` |
| **F2** | H-11 Consolidar deployment files | ✅ `1000b21` |
| **F2** | H-12 Restringir puerto PostgreSQL | ✅ `1000b21` |
| **F2** | H-15 Limpiar archivos de debug | ✅ `1000b21` |
| **F3** | H-13 Pydantic models en body:dict | ✅ `2407bfa` |
| **F3** | H-10 Control de migraciones | ✅ `2407bfa` |
| **F3** | H-18 Thread-safety Odoo singleton | ✅ `2407bfa` |
| **F3** | H-17 Actualizar dependencias | ✅ `2407bfa` |
| **F4** | H-16 Dividir maestro.py y reportes.py | ✅ `b06ef21` |
| **F4** | H-14 Scaffold de tests | ✅ pendiente push |
| **F4** | H-05 **[USUARIO]** Redis para rate limiting | ⬜ Pendiente |
