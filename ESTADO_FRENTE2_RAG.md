# Estado de tareas — Frente 2: Motor RAG e Inteligencia Artificial

---

## Completado

---

### [IA-01] Generacion de Embeddings

**Archivo:** `app/rag_service/integrations/azure_openai.py`

Completado al 100%. La funcion `get_embed_model()` conecta con Azure OpenAI y devuelve un `AzureOpenAIEmbedding` usando el modelo `text-embedding-3-small`. Se configura desde `shared/config.py` (endpoint, api_version, deployment). La instancia es singleton via `@lru_cache`.

El mismo archivo expone `get_llm()` para GPT-4o-mini (necesario para [IA-05]).

---

### [IA-02] Configuracion de Qdrant

**Archivo:** `app/processing_service/repositories/qdrant_repository.py` -> `_ensure_collection()`

Completado. La coleccion `talentrefine-candidates` se crea automaticamente al primer upsert con:
- Vector size: 1536 dimensiones (configurable)
- Distance: COSINE

El esquema del Payload almacenado en cada punto tiene los campos: `candidate_id`, `file_name`, `contact_info`, `exp_years`, `location`, `chunk_text` definido en `app/shared/models.py` como `CandidatePayload`.

Para levantar Qdrant en local via Docker basta con ejecutar `docker compose up qdrant` (incluido en `docker-compose.yml`).

---

### [IA-03] Logica de Insercion (Upsert)

**Archivo:** `app/processing_service/repositories/qdrant_repository.py` -> `upsert_nodes()`

Completado. La funcion recibe una lista de `TextNode` (con embeddings ya generados) y los convierte a `PointStruct` de Qdrant, anadiendo el payload completo de metadatos. Llama a `self._client.upsert()` de forma asincrona.

---

### [IA-04] Busqueda Semantica (Retrieval)

**Archivos:**
- `app/rag_service/services/rag_engine.py` -> `_search_azure()` y `_build_metadata_filters()`
- `app/rag_service/repositories/qdrant_repository.py` -> `get_vector_store()`

Completado. El flujo real es:
1. Se vectoriza `job_description` a traves del embed model configurado en LlamaSettings
2. `VectorIndexRetriever` ejecuta busqueda coseno con `similarity_top_k = top_k * 3`
3. Los filtros de metadatos se aplican de forma nativa en Qdrant via `MetadataFilters` de LlamaIndex (operador `GTE` para `exp_years`, `EQ` para `location`)
4. Se recuperan los Top-K fragmentos de CV

---

### [IA-05] Prompt Engineering y Generacion (LLM)

**Archivo:** `app/rag_service/services/rag_engine.py`

Completado. Se definen dos constantes:

- `_SYSTEM_PROMPT`: instruye al modelo a actuar como experto IT, evaluar solo con los fragmentos recuperados, y devolver exclusivamente un array JSON con `candidate_id`, `summary` y `justification`.
- `_QA_PROMPT_TMPL`: combina el system prompt con el bloque de contexto (`{context_str}`) y la query del reclutador (`{query_str}`).

El prompt se aplica via `PromptTemplate` de LlamaIndex inyectado en `RetrieverQueryEngine`. La respuesta se parsea con `_parse_llm_response()` que incluye fallback robusto si el JSON viene malformado.

---

## Pendiente

Ninguna tarea del Frente 2 queda pendiente de implementacion. **Todo el codigo esta escrito y los 28 tests unitarios pasan.** Lo que no esta validado aun en entorno real:

| Pendiente | Descripcion |
|---|---|
| Validacion E2E real | Los tests de integracion (`tests/integration/`) requieren el stack Docker corriendo con las variables de Azure reales. No se ha ejecutado contra Azure OpenAI real ni contra Qdrant Cloud. |
| Valores en `exp_years` y `location` | La extraccion de estos campos desde el PDF no esta implementada: se insertan siempre como `exp_years=0` y `location=None`. Quien desarrolla [BK-05] deberia enriquecer esos campos. |

---

## Fuera de Scope — Cosas tocadas que no son parte del Frente 2

Estas piezas fueron necesarias para que el motor RAG funcionara, pero pertenecen tecnicamente a otros frentes:

---

### 1. `app/shared/config.py` — Reescritura completa

**Frente responsable:** Infraestructura / Shared (nadie asignado especificamente)

**Que se hizo:** Se anyadio `use_fake_integrations`, `local_blob_path`, `fake_embedding_dimensions`, se hicieron opcionales `key_vault_url`, `blob_account_url` y `azure_openai_endpoint`, y se anadio la logica en `get_secret()` para cortocircuitar Key Vault en fake mode.

**Justificacion:** Sin este cambio, cualquier import de `shared.config` falla al arrancar en local porque intenta conectar con Azure Key Vault. Era el cuello de botella que bloqueaba todo el desarrollo local.

**Impacto si se elimina:** El motor RAG y todos los demas servicios dejan de funcionar en local. No hay forma de probar nada sin credenciales Azure reales. Afecta a los tres microservicios.

---

### 2. `app/shared/fakes.py` — Fichero creado desde cero

**Frente responsable:** Infraestructura / Shared

**Que se hizo:** Implementacion de `fake_embedding()` (SHA-256, determinista, L2-normalizado, 1536 dims) y `local_blob_upload/download()`.

**Justificacion:** `fake_embedding()` es usada por el motor RAG en modo local (tanto en `rag_engine.py` como en `qdrant_repository.py` del rag_service). Sin ella el motor RAG no puede operar sin Azure.

**Impacto si se elimina:** El motor RAG pierde completamente la capacidad de funcionar en modo fake. Las pruebas unitarias de [IA-04] y [IA-05] fallan. Solo funcionaria con Azure real.

---

### 3. `app/shared/models.py` — Clase `CandidatePayload`

**Frente responsable:** Backend / Shared (Integrante 1, [BK-05])

**Que se hizo:** El modelo `CandidatePayload` ya existia. Se uso tal cual como contrato del payload de Qdrant.

**Impacto si se modifica:** Si alguien cambia los nombres de campo (`exp_years`, `location`, `candidate_id`) se rompe la busqueda semantica porque `_payload_matches_filters()` y `_parse_llm_response()` los referencian por nombre literal.

---

### 4. `app/processing_service/services/indexing_service.py` — Creado desde cero

**Frente responsable:** Integrante 1 (Frente Backend, [BK-05])

**Que se hizo:** Pipeline completo de extraccion PDF -> chunking -> embedding -> upsert Qdrant, incluyendo el modo fake.

**Justificacion:** El Integrante 2 necesita datos en Qdrant para poder probar el retrieval. Sin que exista un pipeline de ingesta que pueble Qdrant (aunque sea en fake), los tests de integracion del RAG no tienen candidatos que recuperar.

**Impacto si se elimina:** El flujo completo de prueba (upload -> process -> search) se rompe. El motor RAG puede existir de forma aislada, pero no habria forma de poblar Qdrant con candidatos para validarlo.

---

### 5. `app/processing_service/repositories/qdrant_repository.py` — Modo fake anadido

**Frente responsable:** Integrante 1 (Frente Backend)

**Que se hizo:** Se anadio `_ensure_collection()` (auto-crea la coleccion en Qdrant si no existe) y el soporte de `fake_embedding()` como fallback en `upsert_nodes()`.

**Justificacion:** Sin `_ensure_collection()`, el upsert falla con collection not found al ejecutar en local por primera vez. Es infraestructura necesaria para que el pipeline de ingesta (y por tanto el RAG) funcione en modo fake.

**Impacto si se elimina:** La coleccion hay que crearla manualmente en Qdrant antes de cada prueba. Bloquea el flujo de testing en local.

---

### 6. `docker-compose.local-fake.yml` y `tests/` — Infraestructura de testing

**Frente responsable:** DevOps / Compartido

**Que se hizo:** Se creo el override de Docker Compose para modo fake y los tests unitarios e integracion.

**Justificacion:** Necesarios para validar que el motor RAG funciona correctamente antes de desplegar.

**Impacto si se elimina:** Se pierde toda la cobertura de tests. La validacion del motor RAG depende de ejecucion manual.
