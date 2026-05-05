# Instrucciones para IA – Backend TalentRefine AI

## Alcance de este repo
- SOLO contiene la **Capa de Negocio (Backend)**.
- Infraestructura (Terraform) y Frontend (Streamlit) viven en repos separados.
- Aquí NO se definen recursos Azure ni UI, solo:
  - APIs FastAPI
  - Lógica de ingesta y procesamiento de PDFs
  - Orquestación del flujo RAG
  - Integración con Blob, Qdrant y Azure OpenAI
- Si para una solicitud parece necesario modificar infraestructura (Terraform)
  o frontend (Streamlit), devolver instrucción para abrir issue/PR en esos repos
  y no proponer código aquí.

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
  - `vector` = embedding
  - `payload` JSON con, como mínimo:
    - `candidate_id`
    - `file_name`
    - `contact_info`
    - `exp_years`
    - `location`
    - `chunk_text`
- Diferenciar explícitamente:
  - Flujos **asíncronos**: ingesta/procesamiento de documentos.
  - Flujos **síncronos**: consultas de reclutadores.
- Los endpoints de ingesta (`POST /documents`) deben aceptar la petición,
  encolar el trabajo y devolver rápido; el procesamiento pesado se hace en
  background (task async / worker).
- Los endpoints de `/search` deben asumir baja latencia y **NO** disparar tareas
  de procesamiento de documentos.
- Todas las operaciones sobre Qdrant se harán contra una única colección
  (`talentrefine-candidates`, salvo que se indique otra en el código).
- El `payload` JSON debe seguir siempre este patrón mínimo (se pueden añadir
  campos nuevos solo si se documentan aquí primero):

```json
{
  "candidate_id": "str",
  "file_name": "str",
  "contact_info": {},
  "exp_years": 0,
  "location": "str or null",
  "chunk_text": "str"
}
```

## Contrato con el frontend (otro repo)
- Endpoints:
  - `POST /documents` → subir uno o varios PDFs (multipart), respuesta con IDs/estado.
  - `GET /documents/{id}/status` → estado de procesamiento.
  - `POST /search` → recibe descripción del puesto y filtros; devuelve lista de candidatos con score, resumen y motivo de selección.
- Respuesta estándar:
  - Éxito: `{ "data": ..., "error": null }`
  - Error: `{ "data": null, "error": "<mensaje claro>" }`
- Habilitar CORS solo para el origen del frontend.

## Reglas de acción de Copilot
- Para cada solicitud de código, actuar solo sobre lo que se pide explícitamente,
  sin avanzar fases futuras ni añadir endpoints no mencionados.
- No reintroducir código que haya sido eliminado en commits recientes.
- Si el código solicitado ya existe, no modificarlo salvo que se pida expresamente
  refactor o corrección.
- Si se pide ampliar una función o clase existente, agregar el nuevo código de
  forma incremental, respetando la interfaz pública actual.
- No generar código para funcionalidades o endpoints fuera del alcance descrito
  en este documento. Ante dudas, responder pidiendo más detalles.
- Mantener siempre la coherencia con la arquitectura (microservicios FastAPI,
  integración con Blob, Qdrant, Azure OpenAI y LlamaIndex).
  - Siempre que crees algun metodo o funcion nueva debes asegurarte que siempre lleve su breve comentario explicando su funcionalidad, y que el nombre de la función sea lo suficientemente descriptivo para entender su propósito sin necesidad de leer su implementación. Estos comentarios deben ser claros y concisos, evitando redundancias con el nombre de la función. El objetivo es que cualquier desarrollador pueda entender rápidamente qué hace la función solo con leer su firma y comentario, sin necesidad de profundizar en su código interno. Ademas no debe de llevar emoticonos ni nada por el estilo, solo texto claro y profesional.