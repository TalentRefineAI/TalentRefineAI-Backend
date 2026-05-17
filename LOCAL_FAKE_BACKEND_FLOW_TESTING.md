# Pruebas del Flujo Backend con Integracion Local Fake

Este documento explica el modo `local_fake`, las configuraciones aplicadas y el proceso para probar el flujo del backend sin depender de recursos reales de Azure.

La diferencia principal frente a las pruebas mock de frontend es que aqui si se ejecutan los microservicios reales del backend y se prueban sus endpoints HTTP. Lo que se sustituye son las integraciones cloud:

- Azure Blob Storage se sustituye por almacenamiento local en disco.
- Azure Key Vault no se consulta.
- Azure OpenAI embeddings se sustituye por vectores deterministas locales.
- Azure OpenAI chat se sustituye por resumen y razon generados localmente.
- Qdrant se mantiene real, pero ejecutado en Docker local.

---

## Objetivo

Validar el flujo backend:

```text
POST /upload
  -> ingest_service real
  -> almacenamiento local fake
  -> llamada HTTP real a processing_service
  -> extraccion real de PDF con PyMuPDF y metadata con pypdf
  -> chunking real con RecursiveCharacterTextSplitter
  -> embeddings fake deterministas
  -> persistencia real en Qdrant local
  -> POST /search
  -> rag_service real
  -> busqueda real en Qdrant local
  -> respuesta con summary/reason fake
```

Esto permite probar features reales del backend sin coste ni dependencia cloud.

---

## Cambios aplicados

### 1. Nuevas variables de configuracion

Se añadieron a `app/shared/config.py`:

```python
use_fake_integrations: bool = False
local_blob_path: str = "/tmp/talentrefine-blobs"
fake_embedding_dimensions: int = 1536
```

Y a `.env.example`:

```env
USE_FAKE_INTEGRATIONS=False
LOCAL_BLOB_PATH=/tmp/talentrefine-blobs
FAKE_EMBEDDING_DIMENSIONS=1536
```

Cuando `USE_FAKE_INTEGRATIONS=True`, el backend evita llamadas a Azure Blob, Azure Key Vault y Azure OpenAI.

Como medida de seguridad, la configuracion impide arrancar con `USE_FAKE_INTEGRATIONS=True`
si `APP_ENV` es `production`, `prod` o `pro`. El modo fake existe para desarrollo,
validacion local y evidencias tecnicas; no debe activarse en entornos productivos.

---

### 2. Helpers fake compartidos

Se creó:

```text
app/shared/fakes.py
```

Incluye:

- `local_blob_upload()`: guarda bytes en disco.
- `local_blob_download()`: lee bytes desde disco.
- `fake_embedding()`: genera un vector determinista a partir de texto.

El embedding fake no representa semantica real, pero sirve para probar que el backend genera vectores, los persiste en Qdrant y luego consulta esa coleccion.

---

### 3. Aislamiento entre servicios reales y servicios fake

El modo `local_fake` esta aislado de la logica principal de los servicios de negocio.
La seleccion entre implementacion real y fake se hace en los routers al arrancar cada
microservicio, leyendo `USE_FAKE_INTEGRATIONS`.

En concreto:

- `processing_service/services/processing_service.py` contiene el pipeline principal orientado a Azure OpenAI.
- `processing_service/services/fake_processing_service.py` hereda el pipeline comun y solo sustituye la generacion de embeddings por vectores deterministas.
- `rag_service/services/rag_service.py` contiene el flujo RAG real con LlamaIndex, Azure OpenAI y Qdrant.
- `rag_service/services/fake_rag_service.py` contiene la busqueda local fake contra Qdrant y genera `summary`/`reason` sin llamar a GPT.
- `app/shared/fakes.py` concentra utilidades fake compartidas como Blob local y embeddings deterministas.

Esto evita que la logica fake quede mezclada dentro de los servicios principales y
allana el camino hacia produccion. Los servicios principales quedan preparados para
integrarse con Azure, mientras que los servicios fake quedan como adaptadores de
validacion local.

---

### 4. Blob local fake

En modo fake:

- `ingest_service/repositories/blob_repository.py` escribe PDFs en `LOCAL_BLOB_PATH`.
- `processing_service/repositories/blob_repository.py` lee PDFs desde `LOCAL_BLOB_PATH`.

Como son contenedores diferentes, Docker Compose monta un volumen compartido:

```yaml
local_fake_blobs:/tmp/talentrefine-blobs
```

---

### 5. Qdrant local real

Qdrant no se mockea. Se ejecuta en Docker:

```text
http://qdrant:6333
```

En modo fake:

- no se lee `qdrant-api-key` desde Key Vault,
- se crea la coleccion automaticamente si no existe,
- se usa dimension `1536`,
- se usa distancia coseno.

---

### 6. Processing con embeddings fake

El pipeline de processing sigue siendo real:

```text
download -> PyMuPDF + pypdf metadata -> RecursiveCharacterTextSplitter -> upsert_nodes
```

En modo real, `ProcessingService` genera embeddings con Azure OpenAI.

En modo local fake, el router usa `FakeProcessingService`, que reutiliza el pipeline comun
de descarga, extraccion, limpieza, chunking y persistencia, pero sustituye la generacion
de embeddings por `fake_embedding()`.

---

### 7. RAG local fake

En modo real, `RagService` usa LlamaIndex, Azure OpenAI y Qdrant.

En modo fake, el router usa `FakeRagService`, que:

- no instancia Azure OpenAI,
- no usa LlamaIndex con Azure embeddings,
- consulta Qdrant directamente con `query_points`,
- genera `summary` y `reason` localmente.

Esto prueba el contrato y la recuperacion desde Qdrant sin llamar a GPT.

---

### 8. Docker Compose override

Se añadió:

```text
docker-compose.local-fake.yml
```

Este archivo activa el modo fake sobre el compose base:

```yaml
USE_FAKE_INTEGRATIONS: "true"
APP_ENV: local_fake
LOCAL_BLOB_PATH: /tmp/talentrefine-blobs
QDRANT_URL: http://qdrant:6333
```

---

## Requisitos

Necesitas:

- Docker.
- Docker Compose.
- Un PDF real con texto seleccionable.
- Puertos libres:
  - `8001`: ingest-service
  - `8002`: processing-service
  - `8003`: rag-service
  - `6333`: Qdrant

El PDF debe contener texto extraible. Si es un escaneo o imagen, PyMuPDF probablemente extraera texto vacio.

---

## Levantar el entorno local fake

Desde el backend:

```bash
cd /home/jldom/TFM/TalentRefine/TalentRefineAI-Backend
```

Levanta los servicios backend y Qdrant:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  up --build qdrant ingest-service processing-service rag-service
```

Si quieres ejecutarlo en segundo plano:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  up --build -d qdrant ingest-service processing-service rag-service
```

Verifica estado:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  ps
```

---

## Probar el flujo manualmente

### 1. Definir PDF de prueba

Usa un PDF local con texto:

```bash
export PDF_PATH=/ruta/a/cv_de_prueba.pdf
export PDF_NAME=$(basename "$PDF_PATH")
```

---

### 2. Subir PDF al ingest-service

```bash
curl -s -X POST http://localhost:8001/upload \
  -F "files=@${PDF_PATH};type=application/pdf"
```

Respuesta esperada:

```json
{
  "data": [
    {
      "id": "uuid",
      "file_name": "cv_de_prueba.pdf",
      "status": "pending"
    }
  ],
  "error": null
}
```

Si tienes `jq` instalado:

```bash
export DOC_ID=$(
  curl -s -X POST http://localhost:8001/upload \
    -F "files=@${PDF_PATH};type=application/pdf" \
  | jq -r '.data[0].id'
)
```

El `blob_name` construido por el backend sera:

```text
{DOC_ID}/{PDF_NAME}
```

---

### 3. Revisar logs de ingesta y procesamiento

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  logs -f ingest-service
```

En otra terminal:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  logs -f processing-service
```

Deberias ver eventos de:

- upload local del blob,
- llamada a processing-service,
- descarga local del blob,
- procesamiento del documento,
- upsert en Qdrant.

---

### 4. Comprobar colecciones en Qdrant

```bash
curl http://localhost:6333/collections
```

Deberia aparecer:

```text
talentrefine-candidates
```

---

### 5. Ejecutar busqueda en rag-service

```bash
curl -s -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{
    "job_description": "Busco un perfil con experiencia en Python, backend, APIs y cloud",
    "filters": {},
    "top_k": 5
  }'
```

Respuesta esperada:

```json
{
  "data": [
    {
      "candidate_id": "uuid",
      "file_name": "uuid/cv_de_prueba.pdf",
      "score": 0.1234,
      "summary": "Resumen local fake: ...",
      "reason": "Coincidencia local fake ...",
      "contact_info": {},
      "exp_years": 0,
      "location": null
    }
  ],
  "error": null
}
```

El score no representa similitud semantica real. Es una similitud vectorial basada en embeddings deterministas fake.

---

## Prueba directa de processing

Normalmente `ingest_service` llama a `processing_service` automaticamente. Si necesitas probar processing de forma aislada, puedes llamar:

```bash
curl -X POST "http://localhost:8002/processing/${DOC_ID}?blob_name=${DOC_ID}/${PDF_NAME}"
```

Esto solo funcionara si el blob ya existe en el volumen local fake, es decir, si previamente subiste el PDF con `/upload`.

---

## Limitaciones conocidas del modo local fake

### El estado de ingest no pasa a `completed`

Actualmente `ingest_service` mantiene estados en memoria. `processing_service` procesa el documento en otro microservicio, pero no devuelve una actualizacion persistente al estado de ingest.

Por eso, en esta arquitectura actual, `GET /upload/{doc_id}/status` puede quedarse en:

```text
processing
```

aunque el documento ya haya sido indexado en Qdrant.

Para resolverlo en una fase posterior haria falta:

- una base de datos compartida,
- una cola,
- una tabla de estados,
- o un callback desde processing hacia ingest.

### Los embeddings no son semanticos

`fake_embedding()` es determinista, pero no entiende lenguaje natural. Sirve para validar plumbing tecnico, no calidad RAG.

### El LLM no es real

`summary` y `reason` se generan localmente. No validan GPT ni prompt engineering.

### La extraccion PDF si es real

La extraccion de texto se hace con PyMuPDF y la metadata se lee con `pypdf`. Por tanto, esta prueba si puede detectar problemas con PDFs sin texto extraible.

---

## Detener el entorno

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  down
```

Si quieres borrar tambien Qdrant local y blobs fake:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  down -v
```

---

## Ejecutar test automatizado principal

La suite automatizada mantenida en este backend valida el flujo local fake completo usando
Docker Compose y el entorno virtual del proyecto:

```bash
cd /home/jldom/TFM/TalentRefine/TalentRefineAI-Backend
/home/jldom/TFM/TalentRefine/TR_Env/bin/pytest tests/integration/test_local_fake_flow.py -m integration -v
```

Tambien se puede usar el wrapper del repositorio:

```bash
PYTEST_BIN=/home/jldom/TFM/TalentRefine/TR_Env/bin/pytest ./run_tests.sh integration
```

Resultado esperado:

```text
1 passed
```

Durante la ejecución, el test guarda automáticamente los logs y el estado de Docker Compose en una carpeta del repositorio:

```text
tests/integration/logs/local_fake_flow_<timestamp>/
```

Esto incluye:

- `docker_compose_ps.txt`
- `docker_compose_config.txt`
- `ingest-service.log`
- `processing-service.log`
- `rag-service.log`
- `qdrant.log`
- `*_<container_id>.log` (fallback de `docker logs` si `docker compose logs` no devuelve salida)

El test captura primero `docker compose logs` y, si no hay salida, hace un fallback a `docker logs` por contenedor.

Estos logs son evidencia directa del flujo y se conservan incluso después de que el stack se detenga.
La carpeta `tests/integration/logs/` esta ignorada por Git para evitar versionar artefactos de
ejecucion.

---

## Como volver al modo Azure real

Cuando existan recursos cloud reales, debes desactivar el modo fake y usar el compose base o variables reales.
El codigo incorpora una proteccion adicional: si `APP_ENV` es `production`, `prod` o `pro`,
la aplicacion falla al arrancar si `USE_FAKE_INTEGRATIONS=True`.

### 1. No usar el override local fake

En vez de:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-fake.yml \
  up --build
```

usa:

```bash
docker compose up --build
```

### 2. Configurar `.env` real del backend

```env
APP_ENV=development
USE_FAKE_INTEGRATIONS=False

KEY_VAULT_URL=https://<vault-name>.vault.azure.net/

BLOB_ACCOUNT_URL=https://<storage-account>.blob.core.windows.net
BLOB_CONTAINER_NAME=cv-uploads

QDRANT_URL=https://<qdrant-endpoint>
QDRANT_COLLECTION=talentrefine-candidates

AZURE_OPENAI_ENDPOINT=https://<openai-resource>.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o

CORS_ALLOWED_ORIGINS=["http://localhost:8501"]
LOG_LEVEL=INFO
```

Para un entorno productivo:

```env
APP_ENV=production
USE_FAKE_INTEGRATIONS=False
```

No debe existir ningun despliegue productivo que use `docker-compose.local-fake.yml`,
`APP_ENV=local_fake` o `USE_FAKE_INTEGRATIONS=True`.

### 3. Cargar secretos en Key Vault

Secretos esperados:

```text
azure-openai-api-key
qdrant-api-key
```

Ejemplo:

```bash
az keyvault secret set \
  --vault-name <vault-name> \
  --name azure-openai-api-key \
  --value "<azure-openai-api-key>"

az keyvault secret set \
  --vault-name <vault-name> \
  --name qdrant-api-key \
  --value "<qdrant-api-key>"
```

### 4. Autenticacion local

Para ejecutar localmente con Azure real:

```bash
az login
```

`DefaultAzureCredential` usara tus credenciales locales.

En Azure, lo correcto es usar Managed Identity con permisos RBAC.

### 5. Validar por capas

Antes de probar desde frontend, valida backend:

```bash
curl -X POST http://localhost:8001/upload \
  -F "files=@${PDF_PATH};type=application/pdf"
```

Luego revisa:

```bash
docker compose logs -f ingest-service
docker compose logs -f processing-service
docker compose logs -f rag-service
```

Y finalmente:

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{
    "job_description": "Busco un perfil senior Python FastAPI",
    "top_k": 5
  }'
```

---

## Criterios de exito

El modo local fake se considera correcto si:

- `POST /upload` acepta un PDF.
- El PDF se escribe en el volumen local fake.
- `ingest_service` llama a `processing_service`.
- `processing_service` extrae texto con PyMuPDF y metadata con `pypdf`.
- Se crean chunks con RecursiveCharacterTextSplitter.
- Se generan embeddings fake.
- Qdrant local recibe puntos en la coleccion `talentrefine-candidates`.
- `POST /search` devuelve candidatos desde Qdrant.
- La respuesta mantiene el contrato `ApiResponse`.

Esto demuestra el flujo tecnico del backend sin depender de Azure.
