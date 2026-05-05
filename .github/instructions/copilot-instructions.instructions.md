# Instrucciones para IA – Backend TalentRefine AI

## Alcance de este repo
- SOLO contiene la **Capa de Negocio (Backend)**.
- Infraestructura (Terraform) y Frontend (Streamlit) viven en repos separados.
- Aquí NO se definen recursos Azure ni UI, solo:
  - APIs FastAPI
  - Lógica de ingesta y procesamiento de PDFs
  - Orquestación del flujo RAG
  - Integración con Blob, Qdrant y Azure OpenAI

## Arquitectura esperada (según Capítulo 4)
- Microservicios FastAPI:
  - `ingest-service`: recibe PDFs, valida, sube a Blob y encola procesamiento.
  - `processing-service`: extrae texto/metadata, hace chunking y persiste en Qdrant (vector + payload JSON).
  - `rag-service`: resuelve consultas:
    1. Embedding de la query con Azure OpenAI
    2. Prefiltrado por metadatos en Qdrant
    3. Búsqueda vectorial
    4. Construcción de prompt y llamada a GPT‑4
    5. Respuesta justificada al frontend.
- Orquestación RAG implementada con **LlamaIndex**; NO usar CrewAI salvo que el alcance cambie.

## Reglas para generar código
- Usar **FastAPI async** en todos los endpoints con I/O (Blob, Qdrant, Azure OpenAI).
- Separar capas:
  - `routers/` → HTTP + validación Pydantic
  - `services/` → lógica de negocio (ingesta, chunking, RAG)
  - `repositories/` → acceso a Blob, Qdrant, Azure OpenAI
  - `integrations/` → clientes Azure (Blob, OpenAI) usando `DefaultAzureCredential`.
- No meter lógica de negocio en los routers.
- Toda la configuración por `pydantic-settings`; secretos siempre vía **Key Vault** (no `os.environ` directo).
- Mantener el modelo de datos alineado con el patrón Qdrant:
  - vector = embedding
  - payload JSON con: `candidate_id`, `file_name`, `contact_info`, `exp_years`, `location`, `chunk_text`, etc.
- Diferenciar explícitamente:
  - Flujos **asíncronos**: ingesta/procesamiento de documentos.
  - Flujos **síncronos**: consultas de reclutadores.

## Contrato con el frontend (otro repo)
- Endpoints:
  - `POST /documents` → subir uno o varios PDFs (multipart), respuesta con IDs/estado.
  - `GET /documents/{id}/status` → estado de procesamiento.
  - `POST /search` → recibe descripción del puesto y filtros; devuelve lista de candidatos con score, resumen y motivo de selección.
- Respuesta estándar:
  - Éxito: `{ "data": ..., "error": null }`
  - Error: `{ "data": null, "error": "<mensaje claro>" }`
- Habilitar CORS solo para el origen del frontend.

## Pruebas
- Tests con `pytest` + `httpx.AsyncClient`.
- Mockear Azure OpenAI, Blob y Qdrant; no llamar servicios reales en tests.
- Casos mínimos:
  - Ingesta PDF inválido / duplicado
  - Indexado correcto en Qdrant (vector + payload coherente)
  - Consulta con filtros que reduce el conjunto antes de la búsqueda vectorial
  - Respuesta sin alucinaciones cuando falta información en el CV.