# TalentRefineAI-Backend

Backend del sistema **TalentRefine AI**, una plataforma de reclutamiento inteligente que permite a los reclutadores buscar candidatos mediante lenguaje natural. El sistema procesa CVs en formato PDF, los indexa en una base de datos vectorial y responde consultas con candidatos ordenados por relevancia, acompañados de un resumen y justificación generados por GPT-4.

---

## Arquitectura

El backend está compuesto por tres microservicios FastAPI independientes que se comunican de forma asíncrona:

```
app/
├── ingest-service/        # Recibe PDFs, valida y sube a Azure Blob Storage
├── processing-service/    # Extrae texto, hace chunking y persiste en Qdrant
├── rag-service/           # Resuelve consultas RAG y devuelve candidatos con score
├── shared/                # Configuración, modelos y excepciones compartidas
└── tests/                 # Tests unitarios e integración por servicio
```

### Flujo de ingesta (asíncrono)

```
Frontend → POST /documents → ingest-service → Blob Storage
                                           → (background) → processing-service
                                                          → Extracción PDF (pypdf)
                                                          → Chunking (LlamaIndex)
                                                          → Embedding (Azure OpenAI)
                                                          → Qdrant
```

### Flujo de búsqueda (síncrono, baja latencia)

```
Frontend → POST /search → rag-service → Embedding de la query (Azure OpenAI)
                                      → Búsqueda vectorial (Qdrant)
                                      → Prefiltrado por metadatos
                                      → Construcción de prompt + GPT-4
                                      → Candidatos con score, resumen y motivo
```

---

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| API | FastAPI (async) |
| Orquestación RAG | LlamaIndex |
| Base de datos vectorial | Qdrant |
| Almacenamiento de PDFs | Azure Blob Storage |
| Modelos de lenguaje | Azure OpenAI (GPT-4o + text-embedding-3-large) |
| Secretos | Azure Key Vault |
| Autenticación Azure | DefaultAzureCredential (Managed Identity) |
| Configuración | pydantic-settings |
| Logging | structlog |
| Empaquetado | Docker / Docker Compose |

---

## Estructura del proyecto

```
TalentRefineAI-Backend/
├── app/
│   ├── shared/
│   │   ├── config.py          # Settings globales + cliente Key Vault
│   │   ├── models.py          # CandidatePayload, ApiResponse, DocumentStatus
│   │   └── exceptions.py      # Excepciones de dominio
│   │
│   ├── ingest-service/
│   │   ├── main.py
│   │   ├── routers/           # POST /documents · GET /documents/{id}/status
│   │   ├── services/          # Validación, encolado y background task
│   │   ├── repositories/      # Acceso a Blob Storage
│   │   └── integrations/      # Cliente Azure Blob
│   │
│   ├── processing-service/
│   │   ├── main.py
│   │   ├── routers/           # POST /processing/{doc_id}
│   │   ├── services/          # Pipeline PDF → chunking → Qdrant
│   │   ├── repositories/      # Blob (descarga) + Qdrant (escritura)
│   │   └── integrations/      # Cliente Azure OpenAI (embeddings)
│   │
│   ├── rag-service/
│   │   ├── main.py
│   │   ├── routers/           # POST /search · schemas Pydantic
│   │   ├── services/          # Pipeline RAG con LlamaIndex
│   │   ├── repositories/      # Qdrant (lectura/búsqueda)
│   │   └── integrations/      # Cliente Azure OpenAI (embedding + GPT-4)
│   │
│   └── tests/
│       ├── ingest/
│       ├── processing/
│       └── rag/
│
├── pyproject.toml             # Dependencias y configuración de herramientas
├── docker-compose.yml         # Orquestación local de los tres servicios
└── .env.example               # Plantilla de variables de entorno
```

---

## Contrato de API

Todos los endpoints siguen el mismo envelope de respuesta:

```json
// Éxito
{ "data": "...", "error": null }

// Error
{ "data": null, "error": "mensaje descriptivo" }
```

### ingest-service (puerto 8001)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/documents` | Sube uno o varios PDFs (multipart/form-data). Responde 202 de inmediato. |
| `GET` | `/documents/{id}/status` | Consulta el estado de procesamiento de un documento. |

### rag-service (puerto 8003)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/search` | Recibe descripción del puesto y filtros opcionales; devuelve candidatos con score, resumen y motivo. |

---

## Modelo de datos en Qdrant

Colección única: `talentrefine-candidates`

Cada punto almacena el embedding del chunk y el siguiente payload mínimo:

```json
{
  "candidate_id": "uuid",
  "file_name": "cv_nombre.pdf",
  "contact_info": {},
  "exp_years": 0,
  "location": "Madrid",
  "chunk_text": "fragmento del CV..."
}
```

---

## Configuración local

### 1. Requisitos previos

- Python 3.12+
- Docker y Docker Compose
- Azure CLI (`az login` para autenticación local)

### 2. Variables de entorno

```bash
cp .env.example .env
# Editar .env con los endpoints reales de Azure
```

### 3. Arrancar los servicios

```bash
docker compose up --build
```

Los servicios quedan disponibles en:
- `http://localhost:8001` — ingest-service
- `http://localhost:8002` — processing-service
- `http://localhost:8003` — rag-service

---

## Seguridad

- Toda autenticación con Azure se realiza mediante `DefaultAzureCredential` (Managed Identity en producción, `az login` en local).
- Los secretos (API keys, credenciales Qdrant) se almacenan en **Azure Key Vault** y nunca en variables de entorno ni en el código.
- CORS habilitado únicamente para el origen del frontend configurado.

---

## Repositorios relacionados

| Repo | Descripción |
|---|---|
| `TalentRefineAI-Frontend` | Interfaz de usuario en Streamlit |
| `TalentRefineAI-Infra` | Infraestructura como código (Terraform) en Azure |
