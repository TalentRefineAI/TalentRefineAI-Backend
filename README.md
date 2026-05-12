# TalentRefineAI-Backend

Backend del sistema **TalentRefine AI**, una plataforma de reclutamiento inteligente que permite a los reclutadores buscar candidatos mediante lenguaje natural. El sistema procesa CVs en formato PDF, los indexa en una base de datos vectorial y responde consultas con candidatos ordenados por relevancia, acompañados de un resumen y justificacion generados por GPT-4.

---

## Arquitectura

El backend esta compuesto por tres microservicios FastAPI independientes:

```
app/
|-- ingest_service/        # Recibe PDFs, valida y sube a Azure Blob Storage
|-- processing_service/    # Extrae texto, hace chunking y persiste en Qdrant
|-- rag_service/           # Resuelve consultas RAG y devuelve candidatos con score
`-- shared/                # Configuracion, modelos y excepciones compartidas
```

### Flujo de ingesta (asincrono)

```
Frontend --> POST /documents --> ingest-service --> Blob Storage
                                               --> (background task) --> processing-service
                                                                      --> Extraccion PDF (pypdf)
                                                                      --> Chunking (LlamaIndex)
                                                                      --> Embedding (Azure OpenAI)
                                                                      --> Qdrant
```

### Flujo de busqueda (sincrono, baja latencia)

```
Frontend --> POST /search --> rag-service --> Embedding de la query (Azure OpenAI)
                                         --> Busqueda vectorial (Qdrant)
                                         --> Prefiltrado por metadatos
                                         --> Construccion de prompt + GPT-4
                                         --> Candidatos con score, resumen y motivo
```

---

## Stack tecnologico

| Capa | Tecnologia |
|---|---|
| API | FastAPI >= 0.115 (async) |
| Orquestacion RAG | LlamaIndex >= 0.11 |
| Base de datos vectorial | Qdrant (qdrant-client >= 1.10) |
| Almacenamiento de PDFs | Azure Blob Storage |
| Modelos de lenguaje | Azure OpenAI (GPT-4o + text-embedding-3-large) |
| Extraccion de texto PDF | pypdf >= 4.3 |
| Secretos | Azure Key Vault |
| Autenticacion Azure | DefaultAzureCredential (Managed Identity) |
| Configuracion | pydantic-settings >= 2.3 |
| Logging | structlog >= 24.2 |
| HTTP interno async | httpx >= 0.27 |
| Empaquetado | Docker / Docker Compose |

---

## Estructura del proyecto

```
TalentRefineAI-Backend/
|-- app/
|   |-- shared/
|   |   |-- config.py          # Settings globales + cliente Key Vault
|   |   |-- models.py          # CandidatePayload, ApiResponse, DocumentStatus
|   |   |-- exceptions.py      # Excepciones de dominio
|   |   |-- fakes.py           # Adaptadores locales sin Azure (blob + embedding)
|   |   `-- logging.py         # Configuracion structlog compartida
|   |
|   |-- ingest_service/
|   |   |-- main.py
|   |   |-- routers/           # POST /documents  GET /documents/{id}/status
|   |   |-- services/          # Validacion, encolado y background task
|   |   |-- repositories/      # Acceso a Blob Storage
|   |   `-- integrations/      # Cliente Azure Blob
|   |
|   |-- processing_service/
|   |   |-- main.py
|   |   |-- routers/           # POST /processing/{doc_id}
|   |   |-- services/          # Pipeline PDF --> chunking --> Qdrant
|   |   |-- repositories/      # Blob (descarga) + Qdrant (escritura)
|   |   `-- integrations/      # Cliente Azure OpenAI (embeddings)
|   |
|   `-- rag_service/
|       |-- main.py
|       |-- routers/           # POST /search  schemas Pydantic
|       |-- services/          # Pipeline RAG con LlamaIndex
|       |-- repositories/      # Qdrant (lectura/busqueda)
|       `-- integrations/      # Cliente Azure OpenAI (embedding + GPT-4)
|
|-- tests/
|   `-- integration/
|       `-- test_local_fake_flow.py   # Test E2E con Docker Compose (modo fake)
|
|-- pyproject.toml                    # Dependencias y configuracion de herramientas
|-- docker-compose.yml                # Stack completo (Azure)
|-- docker-compose.local-fake.yml     # Override para modo local sin Azure
`-- run_tests.sh                      # Script de ejecucion de tests
```

---

## Contrato de API

Todos los endpoints siguen el mismo envelope de respuesta:

```json
// Exito
{ "data": "...", "error": null }

// Error
{ "data": null, "error": "mensaje descriptivo" }
```

### ingest-service (puerto 8001)

| Metodo | Ruta | Status HTTP | Descripcion |
|---|---|---|---|
| `POST` | `/documents` | 202 | Sube uno o varios PDFs (multipart/form-data). Responde de inmediato; el procesamiento es asincrono. |
| `GET` | `/documents/{id}/status` | 200 / 404 | Consulta el estado de procesamiento de un documento. |

**Respuesta 202 de `POST /documents`:**
```json
{
  "data": [
    { "id": "uuid", "file_name": "cv.pdf", "status": "pending" }
  ],
  "error": null
}
```

> **Nota:** El estado de los documentos se almacena en memoria dentro del proceso. No persiste si el servicio se reinicia. Pendiente de migrar a almacenamiento externo.

### processing-service (puerto 8002)

| Metodo | Ruta | Status HTTP | Descripcion |
|---|---|---|---|
| `POST` | `/processing/{doc_id}` | 202 | Encola el pipeline PDF --> Qdrant. **Solo llamado internamente por ingest-service.** |

### rag-service (puerto 8003)

| Metodo | Ruta | Status HTTP | Descripcion |
|---|---|---|---|
| `POST` | `/search` | 200 / 500 | Recibe descripcion del puesto y filtros opcionales; devuelve candidatos con score, resumen y motivo. |

**Request `POST /search`:**
```json
{
  "job_description": "Ingeniero Python con experiencia en FastAPI",
  "filters": {
    "min_exp_years": 3,
    "location": "Madrid"
  },
  "top_k": 5
}
```

**Respuesta:**
```json
{
  "data": [
    {
      "candidate_id": "uuid",
      "file_name": "cv_nombre.pdf",
      "score": 0.9123,
      "summary": "Candidato con 5 anios de experiencia en...",
      "reason": "Encaja porque domina FastAPI y tiene experiencia en...",
      "contact_info": {},
      "exp_years": 5,
      "location": "Madrid"
    }
  ],
  "error": null
}
```

---

## Modelo de datos en Qdrant

- **Coleccion:** `talentrefine-candidates` (configurable con `QDRANT_COLLECTION`)
- **Vector:** dimension 1536, distancia coseno (`text-embedding-3-large`)

Cada punto almacena el embedding del chunk y el siguiente payload:

```json
{
  "candidate_id": "uuid-del-documento",
  "file_name": "ruta/blob/en/storage",
  "contact_info": {},
  "exp_years": 0,
  "location": null,
  "chunk_text": "fragmento del CV..."
}
```

> `contact_info`, `exp_years` y `location` se persisten vacios actualmente. El enriquecimiento con NLP esta pendiente de implementar en el pipeline de procesamiento.

---

## Configuracion

### Variables de entorno (`.env`)

| Variable | Obligatoria | Por defecto | Descripcion |
|---|---|---|---|
| `KEY_VAULT_URL` | Si | -- | `https://<vault>.vault.azure.net/` |
| `BLOB_ACCOUNT_URL` | Si | -- | `https://<account>.blob.core.windows.net` |
| `BLOB_CONTAINER_NAME` | No | `cv-uploads` | Contenedor Blob para los PDFs |
| `QDRANT_URL` | Si | -- | `http://<host>:6333` |
| `QDRANT_COLLECTION` | No | `talentrefine-candidates` | Nombre de la coleccion |
| `AZURE_OPENAI_ENDPOINT` | Si | -- | Endpoint del recurso Azure OpenAI |
| `AZURE_OPENAI_API_VERSION` | No | `2024-02-15-preview` | Version de la API |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | No | `text-embedding-3-large` | Nombre del deployment de embeddings |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | No | `gpt-4o` | Nombre del deployment de chat |
| `CORS_ALLOWED_ORIGINS` | No | `["http://localhost:8501"]` | Origenes permitidos (frontend) |
| `APP_ENV` | No | `development` | Entorno de ejecucion |
| `USE_FAKE_INTEGRATIONS` | No | `false` | Activa adaptadores locales sin Azure |
| `LOCAL_BLOB_PATH` | No | `/tmp/talentrefine-blobs` | Directorio local en modo fake |
| `FAKE_EMBEDDING_DIMENSIONS` | No | `1536` | Dimension del vector en modo fake |
| `LOG_LEVEL` | No | `INFO` | Nivel de log de structlog |

Los secretos `azure-openai-api-key` y `qdrant-api-key` se leen desde **Azure Key Vault**, nunca de variables de entorno.

---

## Ejecucion local

### Opcion A -- Stack completo con Azure

Requiere un recurso Azure OpenAI, Blob Storage y Key Vault activos, con credenciales configuradas mediante `az login`.

```bash
# Crear el .env con los valores reales (ver tabla de variables de entorno)
# Levantar todos los servicios
docker compose up --build
```

### Opcion B -- Modo local fake (sin Azure)

Reemplaza Azure Blob por el sistema de ficheros local y Azure OpenAI por embeddings deterministas (SHA-256). Qdrant corre en Docker con la coleccion autocreada. **Recomendado para desarrollo y testing.**

```bash
docker compose -f docker-compose.yml -f docker-compose.local-fake.yml up --build -d
```

Con este modo activo:
- Los PDFs se guardan en un volumen Docker compartido (`local_fake_blobs`)
- Los embeddings se generan con SHA-256 (deterministas, sin coste ni llamadas a Azure)
- La coleccion Qdrant se crea automaticamente si no existe
- Key Vault no se consulta

Los servicios quedan disponibles en:
- `http://localhost:8001` -- ingest-service
- `http://localhost:8002` -- processing-service
- `http://localhost:8003` -- rag-service
- `http://localhost:6333` -- Qdrant (dashboard en `:6333/dashboard`)

### Tests de integracion

```bash
# Requiere Docker en ejecucion
pytest -m integration tests/integration/

# O usando el script incluido
./run_tests.sh
```

El test levanta el stack en modo fake, sube un PDF sintetico, espera el procesamiento y llama a `/search` verificando el flujo E2E completo. Los logs se guardan en `tests/integration/logs/`.

---

## Seguridad

- Toda autenticacion con Azure se realiza mediante `DefaultAzureCredential` (Managed Identity en produccion, `az login` en local).
- Los secretos (API keys, credenciales Qdrant) se almacenan en **Azure Key Vault** y se recuperan en tiempo de ejecucion. Nunca se hardcodean ni se pasan como variables de entorno.
- CORS habilitado unicamente para el origen del frontend configurado en `CORS_ALLOWED_ORIGINS`.
- El processing-service no expone CORS: es un servicio interno no accesible desde el exterior.

---

## Repositorios relacionados

| Repo | Descripcion |
|---|---|
| `TalentRefineAI-Frontend` | Interfaz de usuario en Streamlit |
| `TalentRefineAI-Infra` | Infraestructura como codigo (Terraform) en Azure |
