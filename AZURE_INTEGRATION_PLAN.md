# Azure Integration Plan - TalentRefine AI Backend

## 1. Objetivo

Este documento alinea el plan de integracion Azure con el estado actual de la rama `dev` del backend tras la reunion de cambios de otras ramas.

El objetivo no es sustituir el flujo `local_fake`, sino mantenerlo como baseline funcional local y anadir una capa de validacion real contra Azure: Blob Storage, Key Vault o Managed Identity, Azure OpenAI, Qdrant desplegado en Azure, Azure Container Apps, Log Analytics/Application Insights y frontend consumiendo contratos reales.

La integracion debe hacerse de forma incremental. Cada fase debe dejar evidencia clara: comandos ejecutados, respuestas HTTP, logs estructurados, recursos validados y criterios de aceptacion verificables.

## 2. Estado actual contrastado en `dev`

### Backend

El backend esta dividido en tres microservicios FastAPI:

- `ingest_service`: recibe PDFs en `POST /upload`, valida `application/pdf`, sube el blob y dispara processing por HTTP interno.
- `processing_service`: recibe `POST /processing/{doc_id}`, descarga el PDF, extrae texto, crea chunks, genera embeddings y persiste puntos en Qdrant.
- `rag_service`: recibe `POST /search`, recupera candidatos desde Qdrant y genera respuesta RAG.

La configuracion comun vive en `app/shared/config.py` y actualmente define estas variables relevantes:

- `USE_FAKE_INTEGRATIONS`
- `KEY_VAULT_URL`
- `BLOB_ACCOUNT_URL`
- `BLOB_CONTAINER_NAME`
- `LOCAL_BLOB_PATH`
- `QDRANT_URL`
- `QDRANT_COLLECTION`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_VERSION`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `FAKE_EMBEDDING_DIMENSIONS`
- `CORS_ALLOWED_ORIGINS`
- `APP_ENV`
- `LOG_LEVEL`

El modo fake local esta implementado mediante condicionales dentro de servicios/repositorios existentes, no mediante clases separadas `FakeProcessingService` o `FakeRagService`:

- `IndexingService` usa `fake_embedding()` cuando `USE_FAKE_INTEGRATIONS=true`; si no, configura `LlamaSettings.embed_model` con Azure OpenAI.
- `RAGEngine` delega a `_search_fake()` o `_search_azure()` segun `USE_FAKE_INTEGRATIONS`.
- Los repositorios de Blob cambian entre disco local y Azure Blob segun `USE_FAKE_INTEGRATIONS`.
- `app/shared/fakes.py` concentra `local_blob_upload()`, `local_blob_download()` y `fake_embedding()`.
- `get_secret()` devuelve cadena vacia en fake; en modo real lee de Key Vault con `DefaultAzureCredential`.

Importante: a fecha de esta revision, `app/shared/config.py` no bloquea todavia arrancar con `USE_FAKE_INTEGRATIONS=true` en `APP_ENV=production`, `prod` o `pro`. Ese bloqueo debe tratarse como tarea pendiente de hardening, no como capacidad ya existente.

### Contratos HTTP actuales

| Servicio | Puerto local | Ruta | Estado actual |
| --- | --- | --- | --- |
| ingest | 8001 | `GET /health` | Implementado |
| ingest | 8001 | `POST /upload` | Implementado; devuelve envelope `ApiResponse` con lista de documentos |
| ingest | 8001 | `GET /upload/{doc_id}/status` | Implementado con estado en memoria del propio proceso |
| processing | 8002 | `GET /health` | Implementado |
| processing | 8002 | `POST /processing/{doc_id}?blob_name=...` | Implementado; encola background task |
| rag | 8003 | `GET /health` | Implementado |
| rag | 8003 | `POST /search` | Implementado; devuelve `SearchResponse` sin envelope `ApiResponse` |

El frontend debe consumir estas rutas o debe existir un gateway que traduzca rutas como `/api/v1/documents/upload` y `/api/v1/rag/query` a los contratos reales.

### Flujo local fake actual

El modo fake local se activa con `docker-compose.local-fake.yml`:

- `APP_ENV=local_fake`
- `USE_FAKE_INTEGRATIONS=true`
- `LOCAL_BLOB_PATH=/tmp/talentrefine-blobs`
- `QDRANT_URL=http://qdrant:6333`

Qdrant no se mockea: se ejecuta como contenedor real `qdrant/qdrant:v1.17.1` y se comparte entre processing y rag.

El test E2E principal esta ahora en `tests/integration/local_fake/test_backend_flow.py`, con harness en `tests/integration/local_fake/conftest.py`. El fichero historico `tests/integration/test_local_fake_flow.py` queda como referencia de compatibilidad y no contiene ya el flujo activo. El E2E actual valida:

- disponibilidad de servicios;
- subida de PDF;
- disparo automatico de processing desde `ingest-service`;
- espera activa mediante polling de `POST /search` hasta encontrar el `doc_id` subido;
- busqueda RAG;
- validacion de esquema para `job_description` corto.

El E2E genera evidencias automaticamente bajo `tests/integration/logs/local_fake_flow_<timestamp>/`, incluyendo `flow_evidence.txt`, `docker_compose_ps.txt`, `docker_compose_config.txt` y logs por servicio. Ademas, los unitarios generan evidencias bajo `tests/unit/logs/unit_<timestamp>/`.

Los comandos mantenidos por el `Makefile` son:

- `make test-unit`
- `make test-fake-flow`
- `make test-fake-all`

El `Makefile` usa `PYTEST_BIN ?= python -m pytest`, por lo que cada colaborador puede activar su propio entorno virtual o sobrescribir la ruta con `PYTEST_BIN=/ruta/a/pytest`.

### Estado en memoria

`IngestService` guarda estados en un diccionario local:

```text
self._status_store: dict[uuid.UUID, DocumentStatus] = {}
```

Esto sirve para desarrollo y pruebas locales, pero no es valido para Container Apps con reinicios, escalado o multiples replicas. Ademas, processing no actualiza ese estado al terminar porque no existe un store compartido entre servicios.

### Observabilidad actual

`app/shared/logging.py` configura `structlog` con:

- `logger`
- `level`
- `service`
- `timestamp`

Eventos actuales relevantes:

- `document_upload_accepted`
- `fake_blob_uploaded`
- `blob_uploaded`
- `processing_triggered`
- `blob_uploaded_and_processing_triggered`
- `indexing_started`
- `chunks_generated`
- `fake_embedding_generated`
- `qdrant_collection_created`
- `qdrant_upsert`
- `indexing_completed`
- `llm_response_parse_failed`

Gaps frente al objetivo Azure:

- No se incluye todavia `environment` en todos los logs.
- No hay `correlation_id`.
- Los nombres de eventos no estan normalizados con el plan anterior (`chunks_created` vs `chunks_generated`, `qdrant_upsert_completed` vs `qdrant_upsert`).
- `rag_service` tiene poca instrumentacion de inicio/fin de busqueda.
- No se miden `duration_ms` de llamadas externas.

### Infraestructura contrastada en repo vecino

Se reviso tambien `/home/jldom/TFM/TalentRefine/TalentRefineAI-Infra/terraform` porque este plan contiene decisiones de Terraform. Estado observado:

- Existe modulo de storage con Blob container `pdfs` y Azure File Share para Qdrant.
- Existe modulo de compute con ACR, Container Apps Environment, Qdrant, un unico Container App `backend` y un Container App `frontend`.
- `backend` y `frontend` usan imagen placeholder `mcr.microsoft.com/azuredocs/containerapps-helloworld:latest`.
- El modulo compute ya inyecta variables alineadas con backend para Key Vault, OpenAI y Blob: `KEY_VAULT_URL`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `AZURE_OPENAI_CHAT_DEPLOYMENT`, `BLOB_ACCOUNT_URL`, `BLOB_CONTAINER_NAME`, `USE_FAKE_INTEGRATIONS=false`, `APP_ENV=development` y `APPLICATIONINSIGHTS_CONNECTION_STRING`.
- El modulo compute aun no inyecta explicitamente `AZURE_OPENAI_API_VERSION`, `QDRANT_COLLECTION` ni `LOG_LEVEL`; el backend usara sus defaults si no se definen.
- `QDRANT_URL` se construye como `https://${azurerm_container_app.qdrant.ingress[0].fqdn}`.
- No hay aun tres Container Apps separados para `ingest-service`, `processing-service` y `rag-service`.

Por tanto, la infra ha avanzado en configuracion, pero sigue desalineada en topologia y en uso de imagenes reales.

### Frontend contrastado en repo vecino

Se reviso `/home/jldom/TFM/TalentRefine/TalentRefineAI-Frontend`, actualmente en rama `main`. Estado observado:

- `utils/api_client.py` conserva `use_mock=True` por defecto.
- El cliente real todavia llama a rutas `/api/v1/documents/upload`, `/api/v1/rag/query`, `/api/v1/documents/status`, `/api/v1/candidates/{id}` y `/api/v1/filters/...`.
- El backend actual expone `POST /upload`, `GET /upload/{doc_id}/status` y `POST /search`; no expone aun esas rutas `/api/v1`.
- `components/results_view.py` fuerza `get_api_client(use_mock=True)`, por lo que la busqueda sigue usando mocks aunque se configure otra cosa fuera.
- `docker-compose.yml` del frontend define `USE_MOCK_DATA=True` y no define `BACKEND_URL`, `INGEST_API_URL` ni `RAG_API_URL`.
- Terraform define `BACKEND_URL` para el Container App frontend, pero el codigo actual del frontend no lo usa para construir `RAGAPIClient`.

Por tanto, el frontend sigue pendiente de alineacion funcional con backend real o de una capa gateway compatible con `/api/v1`.

## 3. Principios de integracion

- Mantener `local_fake` como primera capa de evidencia funcional.
- No presentar fake como prueba productiva.
- Separar con claridad lo implementado de lo pendiente.
- Integrar Azure por fases pequenas, verificables y reversibles.
- Usar `app/shared/config.py` como fuente de verdad de variables backend.
- No cambiar contratos HTTP sin documentar impacto en frontend y tests.
- Desplegar en Azure una topologia coherente con el backend: tres servicios o un backend unificado tras refactor explicito.
- Evitar escalar servicios backend hasta persistir estado documental fuera de memoria.
- Evitar logs con CVs completos, secretos, API keys o tokens.

## 4. Riesgos detectados

### Topologia backend no alineada con Terraform

El backend real tiene tres servicios. Terraform define un unico Container App `backend` con imagen placeholder.

Impacto:

- `ingest_service` llama a `http://processing-service:8000/processing/{doc_id}`; esa resolucion no existira en Azure si solo hay un Container App `backend`.
- Los logs y responsabilidades quedan mezclados o directamente no desplegados.
- El flujo local fake puede pasar mientras el flujo Azure falla en el primer salto interno.

Recomendacion:

- Primera opcion: desplegar tres Container Apps backend: `ingest-service`, `processing-service` y `rag-service`.
- Alternativa: crear un backend unificado, pero solo tras refactor de codigo, compose, tests, rutas y documentacion.

### Variables de entorno parcialmente alineadas

Tras el ultimo pull de infra, Terraform ya inyecta los nombres principales que espera el backend:

- `KEY_VAULT_URL`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `BLOB_ACCOUNT_URL`
- `BLOB_CONTAINER_NAME`
- `USE_FAKE_INTEGRATIONS`
- `APP_ENV`
- `APPLICATIONINSIGHTS_CONNECTION_STRING`

Persisten tres puntos a cerrar:

- `AZURE_OPENAI_API_VERSION`, `QDRANT_COLLECTION` y `LOG_LEVEL` no se inyectan explicitamente.
- Backend default para Blob es `cv-uploads`, mientras Terraform crea e inyecta `pdfs`.
- `QDRANT_URL` se inyecta como URL `https` de Qdrant con ingress interno; hay que validarla desde processing/rag y confirmar que el cliente Qdrant funciona con esa ruta.

Recomendacion:

- Mantener los nombres actuales alineados con `app/shared/config.py`.
- Inyectar explicitamente `AZURE_OPENAI_API_VERSION`, `QDRANT_COLLECTION` y `LOG_LEVEL` para evitar defaults implicitos.
- Decidir si `BLOB_CONTAINER_NAME` sera `pdfs` o `cv-uploads`; reflejarlo en `.env.example`, Terraform y despliegue.
- Validar `QDRANT_URL` real desde ambos servicios backend.

### Estrategia de secretos e identidad no cerrada

El codigo actual usa `get_secret("qdrant-api-key")` y `get_secret("azure-openai-api-key")` con Key Vault en modo real.

La infra concede RBAC a Managed Identity para Storage, pero no queda cerrada la estrategia para OpenAI/Qdrant ni la creacion de secretos.

Recomendacion:

- Para primera validacion rapida: API keys en Key Vault con nombres exactos documentados.
- Para version robusta: Managed Identity/RBAC donde SDKs y LlamaIndex lo soporten bien.
- No mezclar ambas estrategias sin documentar la razon.

### Activacion accidental del modo fake

El modo fake esta controlado por `USE_FAKE_INTEGRATIONS`, pero no existe aun bloqueo productivo.

Impacto:

- Un entorno Azure podria arrancar por error con Blob local y embeddings deterministas si se configura mal.

Recomendacion:

- Anadir validacion en `Settings` para fallar si `APP_ENV in {"production", "prod", "pro"}` y `USE_FAKE_INTEGRATIONS=true`.
- Fijar `USE_FAKE_INTEGRATIONS=false` desde Terraform para cualquier entorno Azure real.

### Estado documental no persistente

El estado de `/upload/{doc_id}/status` vive en memoria del `ingest-service`.

Impacto:

- Se pierde con reinicios.
- Es inconsistente con multiples replicas.
- No refleja finalizacion real del processing.

Recomendacion:

- Persistir estado en Azure Table Storage, Cosmos DB, PostgreSQL o un event log/cola con estado materializado.
- Antes de eso, mantener replicas backend en 1 para pruebas.

### Qdrant real necesita bootstrap explicito

`processing_service` crea la coleccion automaticamente solo en modo fake. En modo real, `_ensure_collection()` retorna sin crearla.

Impacto:

- El primer upsert real fallara si la coleccion no existe.
- La dimension debe coincidir con el modelo de embedding real.

Recomendacion:

- Crear bootstrap o smoke test de Qdrant en Azure.
- Documentar dimension esperada: `text-embedding-3-small` usa 1536 dimensiones.

## 5. Arquitectura objetivo recomendada

Para la primera integracion Azure, mantener separacion por servicio:

- Container App `ingest-service`
- Container App `processing-service`
- Container App `rag-service`
- Container App `qdrant` con Azure File Share
- Azure Blob Storage para PDFs
- Azure OpenAI para embeddings y chat
- Key Vault y/o Managed Identity
- ACR para imagenes reales
- Log Analytics/Application Insights
- Frontend Streamlit apuntando a endpoints reales

Flujo objetivo:

1. Frontend envia PDFs a `POST /upload`.
2. `ingest-service` valida y sube cada PDF a Blob Storage.
3. `ingest-service` registra estado inicial en store persistente.
4. `ingest-service` invoca `processing-service`.
5. `processing-service` descarga el PDF.
6. `processing-service` extrae texto y metadata.
7. `processing-service` crea chunks.
8. `processing-service` genera embeddings con Azure OpenAI.
9. `processing-service` hace upsert en Qdrant.
10. `rag-service` recibe `POST /search`.
11. `rag-service` consulta Qdrant y usa Azure OpenAI chat.
12. Los tres servicios emiten logs trazables por `doc_id` o `correlation_id`.

## 6. Fases de integracion

### Fase 0 - Congelar baseline local fake

Tareas:

- Levantar stack local fake.
- Ejecutar tests unitarios.
- Ejecutar tests de integracion.
- Verificar evidencias automaticas de unitarios y E2E.

Comandos:

```bash
make test-unit
make test-fake-flow
```

Criterios:

- Unit tests pasan.
- Integration tests pasan contra servicios locales.
- `POST /upload` dispara processing automaticamente.
- `POST /search` devuelve el candidato correspondiente al `doc_id` subido.
- Las evidencias quedan en `tests/unit/logs/` y `tests/integration/logs/`.
- No aparecen patrones criticos en logs de `processing-service`.

### Fase 1 - Alinear configuracion

Tareas:

- Tomar `app/shared/config.py` como contrato.
- Mantener en Terraform los nombres ya alineados: `KEY_VAULT_URL`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `AZURE_OPENAI_CHAT_DEPLOYMENT`, `BLOB_ACCOUNT_URL` y `BLOB_CONTAINER_NAME`.
- Inyectar tambien `AZURE_OPENAI_API_VERSION`, `QDRANT_COLLECTION` y `LOG_LEVEL`.
- Resolver el nombre final del contenedor Blob.
- Anadir validacion anti-fake en produccion.

Criterios:

- `.env.example`, backend y Terraform usan los mismos nombres.
- Azure real fija `USE_FAKE_INTEGRATIONS=false`.
- No quedan defaults criticos dependientes de valores implicitos del backend.
- Produccion falla al arrancar si fake esta activado.

### Fase 2 - Alinear topologia de Container Apps

Tareas:

- Sustituir el Container App unico `backend` o complementarlo con tres apps reales.
- Construir y publicar imagenes reales de:
  - `app/ingest_service/Dockerfile`
  - `app/processing_service/Dockerfile`
  - `app/rag_service/Dockerfile`
- Definir URL interna de `processing-service` y evitar hardcode exclusivo de Docker Compose si Azure necesita otro nombre.
- Mantener Qdrant con persistencia en Azure File Share.

Criterios:

- No quedan imagenes placeholder en backend.
- Cada servicio responde `/health`.
- `ingest-service` alcanza a `processing-service`.
- `rag-service` alcanza Qdrant.

### Fase 3 - Validar Blob Storage real

Tareas:

- Confirmar `BLOB_ACCOUNT_URL`.
- Confirmar `BLOB_CONTAINER_NAME`.
- Confirmar permisos de escritura para ingest.
- Confirmar permisos de lectura para processing.
- Crear smoke test upload/download sin OpenAI ni Qdrant.

Criterios:

- Ingest escribe en Blob.
- Processing lee el mismo blob.
- Logs incluyen `doc_id` y `blob_name`.

### Fase 4 - Cerrar identidad y secretos

Tareas:

- Elegir API keys en Key Vault o Managed Identity.
- Si se usa Key Vault, crear/documentar:
  - `azure-openai-api-key`
  - `qdrant-api-key` si aplica
- Validar lectura desde contenedores sin imprimir secretos.

Criterios:

- La estrategia esta documentada.
- No hay secretos en variables no seguras ni en logs.

### Fase 5 - Validar Azure OpenAI real

Tareas:

- Smoke test de embedding.
- Smoke test de chat.
- Confirmar dimension de embeddings.
- Confirmar version API soportada.

Criterios:

- `processing-service` genera embeddings reales.
- `rag-service` genera respuesta con chat real.
- Errores de deployment, permisos o rate limit se registran claramente.

### Fase 6 - Validar Qdrant en Azure

Tareas:

- Confirmar URL interna y protocolo.
- Crear/verificar coleccion con dimension 1536.
- Ejecutar upsert/search de prueba.
- Reiniciar Qdrant y validar persistencia.

Criterios:

- Processing puede hacer upsert.
- Rag puede buscar.
- Datos sobreviven reinicio.

### Fase 7 - Endurecer observabilidad

Tareas:

- Anadir `environment` a logs.
- Anadir logs de inicio/fin en RAG.
- Normalizar eventos o documentar nombres reales.
- Anadir `duration_ms` para Blob, OpenAI, Qdrant y HTTP interno.
- Introducir `correlation_id` o propagar `doc_id` donde aplique.

Eventos objetivo:

- `document_upload_accepted`
- `blob_uploaded`
- `processing_triggered`
- `indexing_started`
- `pdf_text_extracted`
- `chunks_generated`
- `azure_embeddings_generated`
- `qdrant_upsert`
- `rag_search_started`
- `qdrant_search_completed`
- `rag_search_completed`

### Fase 8 - Alinear frontend

Tareas:

- Alinear cliente frontend con `POST /upload`, `GET /upload/{doc_id}/status` y `POST /search`.
- O crear gateway/API versionada que exponga rutas `/api/v1/...`.
- Desactivar mocks en Azure dev.
- Configurar URLs por entorno.
- Hacer que `RAGAPIClient` lea `BACKEND_URL`, `INGEST_API_URL` o `RAG_API_URL` desde entorno.
- Eliminar usos forzados de `get_api_client(use_mock=True)` en vistas que deban probar Azure real.

Criterios:

- Demo Azure no usa datos mock.
- Upload y search desde UI llegan al backend real.
- El frontend desplegado por Terraform aprovecha `BACKEND_URL` o variables equivalentes.

### Fase 9 - Cloud-readiness tests

Tareas:

- Anadir marker `azure` en `pyproject.toml`.
- Crear tests separados de:
  - Blob upload/download;
  - Key Vault read;
  - Azure OpenAI embedding/chat;
  - Qdrant health/upsert/search;
  - flujo backend Azure dev.
- Guardar evidencias de cada ejecucion.

Criterios:

- Tests locales no requieren Azure.
- Tests Azure fallan rapido si falta configuracion.

## 7. Variables de entorno objetivo

| Variable | Consumidor | Valor esperado en Azure dev | Estado |
| --- | --- | --- | --- |
| `KEY_VAULT_URL` | servicios que lean secretos | URL completa del vault | Terraform ya lo inyecta |
| `BLOB_ACCOUNT_URL` | ingest, processing | `https://<account>.blob.core.windows.net` | Terraform ya lo inyecta |
| `BLOB_CONTAINER_NAME` | ingest, processing | `pdfs` o `cv-uploads` | Terraform inyecta `pdfs`; decidir si se cambia backend default o se acepta por entorno |
| `QDRANT_URL` | processing, rag | URL interna de Qdrant | Terraform genera FQDN interno con `https`; falta smoke test |
| `QDRANT_COLLECTION` | processing, rag | `talentrefine-candidates` | Backend default definido; recomendable inyectar explicitamente |
| `AZURE_OPENAI_ENDPOINT` | processing, rag | Endpoint OpenAI | Backend y Terraform lo contemplan |
| `AZURE_OPENAI_API_VERSION` | processing, rag | Version soportada | Backend default `2024-02-15-preview`; recomendable inyectar |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | processing, rag | deployment embeddings | Terraform ya lo inyecta desde `embedding-deployment` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | rag | deployment chat | Terraform ya lo inyecta desde `gpt-deployment` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | todos | connection string | No consumida aun por codigo, util para runtime Azure |
| `APP_ENV` | todos | `development` o `dev` | Terraform inyecta `development`; backend lo lee, falta uso en logs/validacion |
| `USE_FAKE_INTEGRATIONS` | todos | `false` | Terraform ya lo fija en `false` para backend |
| `LOG_LEVEL` | todos | `INFO` | Backend lo soporta; Terraform no lo inyecta explicitamente |
| `AZURE_CLIENT_ID` | servicios con UAMI | client id | Necesario si se usa User Assigned Managed Identity |
| `BACKEND_URL` | frontend | URL backend o gateway | Terraform lo inyecta, pero frontend actual no lo consume |

## 8. Checklists

### Pre-deploy

- Tests locales pasan.
- `.env.example` coincide con `app/shared/config.py`.
- Terraform usa nombres de variables backend.
- `USE_FAKE_INTEGRATIONS=false` en Azure.
- Validacion anti-fake productiva implementada.
- Topologia cerrada: tres Container Apps o backend unificado refactorizado.
- Imagenes reales publicadas en ACR.
- Blob container creado y nombre alineado.
- Estrategia Key Vault/Managed Identity cerrada.
- Deployments Azure OpenAI existentes.
- Qdrant con persistencia y bootstrap de coleccion.
- Frontend sin mocks para prueba Azure.

### Post-deploy

- Los tres servicios responden `/health`.
- Logs aparecen en Log Analytics.
- Blob upload/download funciona desde contenedores.
- OpenAI responde desde processing y rag.
- Qdrant responde desde processing y rag.
- Un PDF puede indexarse completo.
- `POST /search` devuelve resultados del documento indexado.
- El flujo se reconstruye por `doc_id` o `correlation_id`.
- No aparecen eventos fake en entorno Azure real.

## 9. Decisiones pendientes

1. Topologia final en Azure:
   - tres Container Apps;
   - o backend unificado tras refactor.

2. Nombre definitivo de Blob container:
   - `pdfs`;
   - o `cv-uploads`.

3. Estrategia de secretos:
   - API keys en Key Vault;
   - Managed Identity/RBAC;
   - hibrida temporal documentada.

4. Persistencia de estado documental:
   - Azure Table Storage;
   - Cosmos DB;
   - PostgreSQL;
   - cola/event log con estado materializado.

5. Contrato frontend:
   - consumir rutas actuales;
   - o introducir gateway `/api/v1`.

6. Normalizacion de eventos de log:
   - adaptar codigo a nombres objetivo;
   - o actualizar tests/evidencias a nombres reales.

## 10. Proxima accion recomendada

Antes de tocar Azure, cerrar y ejecutar esta secuencia en `dev`:

1. Implementar bloqueo anti-fake productivo en `app/shared/config.py`.
2. Anadir `environment` a logs.
3. Decidir `BLOB_CONTAINER_NAME`.
4. Alinear Terraform con variables reales del backend.
5. Dividir compute en tres Container Apps o documentar refactor a backend unico.
6. Ejecutar unit + local fake como baseline.

Con eso, la primera prueba Azure dejara de depender de supuestos desactualizados y pasara a validar el backend que realmente existe en la rama `dev`.

## 11. Checklist ejecutivo ordenado

1. Confirmar baseline local:
   - Ejecutar `make test-unit`.
   - Ejecutar `make test-fake-flow`.
   - Revisar evidencias en `tests/unit/logs/` y `tests/integration/logs/`.

2. Bloquear configuraciones peligrosas:
   - Implementar fallo de arranque si `USE_FAKE_INTEGRATIONS=true` y `APP_ENV` es `production`, `prod` o `pro`.
   - Verificar que Terraform mantiene `USE_FAKE_INTEGRATIONS=false` en Azure.

3. Cerrar contratos de configuracion:
   - Alinear `.env.example`, Terraform y `app/shared/config.py`.
   - Decidir `BLOB_CONTAINER_NAME`: `pdfs` o `cv-uploads`.
   - Inyectar explicitamente `AZURE_OPENAI_API_VERSION`, `QDRANT_COLLECTION` y `LOG_LEVEL`.

4. Decidir topologia Azure:
   - Elegir tres Container Apps backend o backend unificado.
   - Si se mantienen tres servicios, definir URLs internas de `ingest-service`, `processing-service` y `rag-service`.
   - Sustituir imagenes placeholder por imagenes reales publicadas en ACR.

5. Preparar dependencias cloud:
   - Validar Blob Storage con permisos de escritura para ingest y lectura para processing.
   - Crear o validar secretos `azure-openai-api-key` y `qdrant-api-key` si aplica.
   - Crear o verificar coleccion Qdrant con dimension compatible con el modelo real.

6. Ejecutar smoke tests Azure por componente:
   - Blob upload/download.
   - Key Vault read.
   - Azure OpenAI embedding/chat.
   - Qdrant health/upsert/search.
   - Healthcheck de cada microservicio desplegado.

7. Probar flujo backend Azure completo:
   - Subir PDF con `POST /upload`.
   - Confirmar que processing se dispara y completa.
   - Consultar `POST /search`.
   - Verificar que el resultado corresponde al documento indexado.

8. Endurecer observabilidad:
   - Anadir `environment`, `correlation_id` o `doc_id` consistente.
   - Registrar inicio/fin de RAG.
   - Medir `duration_ms` en Blob, OpenAI, Qdrant y llamadas HTTP internas.

9. Alinear frontend:
   - Decidir si consumira rutas actuales o un gateway `/api/v1`.
   - Eliminar mocks forzados en la demo Azure.
   - Hacer que el cliente use URLs de entorno desplegadas por Terraform.

10. Preparar aceptacion final:
    - Guardar evidencias de tests locales y Azure.
    - Revisar logs en Log Analytics/Application Insights.
    - Confirmar ausencia de eventos fake en Azure real.
    - Documentar decisiones finales de topologia, secretos, Blob container y contrato frontend.
