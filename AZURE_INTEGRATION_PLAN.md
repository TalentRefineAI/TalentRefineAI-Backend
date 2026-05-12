# Azure Integration Plan - TalentRefine AI Backend

## 1. Objetivo

Este documento define el plan técnico para pasar del flujo local fake actual a una integración real con Azure, manteniendo la trazabilidad y las evidencias que ya proporciona el test `local_fake`.

El objetivo no es sustituir el test local fake, sino complementarlo. El flujo local fake demuestra que la lógica principal del backend funciona de extremo a extremo sin depender de servicios cloud reales. La integración con Azure debe demostrar una capa distinta: que esa misma arquitectura puede ejecutarse correctamente con Blob Storage, Key Vault, Azure OpenAI, Qdrant desplegado en Azure, Container Apps, Application Insights y el frontend apuntando a contratos reales.

La transición debe hacerse de forma incremental. Cada fase debe dejar una evidencia clara: logs estructurados, comandos ejecutados, respuestas HTTP relevantes, recursos Azure validados y criterios de aceptación verificables.

## 2. Estado Actual

### Backend

El backend está dividido en tres servicios FastAPI:

- `ingest_service`: recibe PDFs, los sube a almacenamiento y dispara el procesamiento.
- `processing_service`: descarga el PDF, extrae texto, crea chunks, genera embeddings y persiste vectores.
- `rag_service`: recibe una descripción de puesto, consulta Qdrant y genera resultados RAG.

La configuración común vive en `app/shared/config.py`. Actualmente define, entre otras, estas variables relevantes para Azure:

- `KEY_VAULT_URL`
- `BLOB_ACCOUNT_URL`
- `BLOB_CONTAINER_NAME`
- `QDRANT_URL`
- `QDRANT_COLLECTION`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_VERSION`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `APP_ENV`
- `USE_FAKE_INTEGRATIONS`
- `LOG_LEVEL`

El modo `local_fake` usa adaptadores deterministas para Blob, OpenAI y Key Vault, pero mantiene Qdrant real en Docker. Esto permite probar gran parte del flujo sin depender de recursos cloud.

### Test Local Fake

El test principal es `tests/integration/test_local_fake_flow.py`. Este test levanta el stack Docker local fake y valida:

- subida de PDF;
- almacenamiento fake compartido entre servicios;
- trigger del procesamiento;
- extracción de texto;
- chunking;
- generación de embeddings fake;
- upsert en Qdrant real;
- búsqueda RAG;
- respuesta final asociada al `doc_id` procesado;
- captura de logs y evidencias bajo `tests/integration/logs/`.

Este test debe mantenerse como baseline local. Su valor principal es detectar regresiones funcionales del flujo backend sin coste cloud y sin ruido de infraestructura Azure.

### Infraestructura

El repositorio de infraestructura contiene módulos Terraform para:

- Resource Group;
- Networking;
- Private Endpoints;
- Storage Account, Blob Container y File Share;
- Azure OpenAI;
- Managed Identity y Key Vault;
- Monitoring;
- Azure Container Registry;
- Container Apps Environment;
- Container Apps para Qdrant, backend y frontend.

La infraestructura actual todavía no está completamente alineada con el backend real. El módulo de compute define un único Container App `backend` con una imagen placeholder, mientras el backend real está dividido en tres servicios. Además, hay diferencias entre los nombres de variables que Terraform inyecta y los nombres que el backend espera.

### Frontend

El frontend Streamlit conserva lógica mock. El cliente API apunta a rutas como:

- `/api/v1/documents/upload`
- `/api/v1/rag/query`
- `/api/v1/documents/status`

El backend actual expone contratos distintos, como:

- `/upload`
- `/upload/{doc_id}/status`
- `/search`

Antes de validar una integración extremo a extremo con Azure, el frontend debe alinearse con los endpoints reales o introducirse una capa gateway/API que exponga los contratos que el frontend espera.

## 3. Principios de Integración

- Mantener `local_fake` como primera capa de evidencia funcional.
- Integrar Azure por fases pequeñas y reversibles.
- Validar cada recurso cloud de forma aislada antes de ejecutar el flujo completo.
- No mezclar mocks de frontend con evidencias backend.
- No cambiar contratos HTTP sin documentar el impacto en frontend y tests.
- Usar configuración explícita por entorno.
- Evitar valores implícitos en Terraform que no coincidan con `app/shared/config.py`.
- Mantener logs estructurados en todos los servicios.
- Propagar `doc_id` y `blob_name` en todos los eventos del flujo documental.
- Usar entorno `dev` como primer destino de integración real.
- No escalar servicios backend hasta resolver persistencia de estado y coordinación entre réplicas.

## 4. Riesgos Detectados

### Topología backend no alineada con Terraform

El backend real tiene tres servicios, pero Terraform define un único Container App `backend`. Esto puede provocar que el despliegue Azure no refleje el flujo probado localmente.

Impacto:

- `ingest_service` no podrá resolver correctamente `processing-service` si ese servicio no existe en Azure.
- El flujo local fake pasará, pero el flujo Azure fallará al disparar procesamiento.
- La observabilidad quedará menos clara si se empaquetan responsabilidades sin rediseñar rutas y logs.

Recomendación inicial:

- Mantener tres Container Apps backend: ingest, processing y rag.
- Usar nombres DNS internos estables equivalentes al compose local.
- Dejar una alternativa documentada para unificar en un solo backend solo si se decide simplificar arquitectura.

### Variables de entorno inconsistentes

El backend espera `KEY_VAULT_URL`, pero Terraform inyecta `KEY_VAULT_URI`. El backend espera `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` y `AZURE_OPENAI_CHAT_DEPLOYMENT`, mientras Terraform usa `EMBEDDING_DEPLOYMENT_NAME` y `GPT_DEPLOYMENT_NAME`.

Impacto:

- La aplicación puede arrancar sin configuración válida.
- `pydantic-settings` puede marcar variables requeridas como ausentes.
- Los errores aparecerán en runtime, no necesariamente en `terraform plan`.

Recomendación:

- Hacer que Terraform inyecte exactamente los nombres definidos por `app/shared/config.py`.
- Evitar alias múltiples salvo que haya una razón de compatibilidad explícita.

### Estrategia de secretos e identidad no cerrada

El código usa `get_secret()` contra Key Vault para recuperar secretos como claves de Azure OpenAI y Qdrant. Terraform, sin embargo, concede permisos RBAC a Managed Identity y no evidencia la creación de esos secretos.

Impacto:

- El backend puede tener permisos RBAC pero seguir buscando secretos inexistentes.
- Azure OpenAI puede estar preparado para autenticación por identidad, mientras el código usa API key.
- Se puede duplicar innecesariamente la estrategia de seguridad.

Recomendación:

- Decidir explícitamente entre:
  - API keys almacenadas en Key Vault;
  - Managed Identity con autenticación Entra ID;
  - modo híbrido temporal solo para migración.

### Blob Storage no validado en modo real

En local fake, el almacenamiento se simula con un volumen compartido. En Azure, el flujo dependerá de:

- existencia del contenedor Blob correcto;
- permisos de la Managed Identity;
- resolución DNS y Private Endpoint si se endurece red;
- compatibilidad exacta de la llamada al SDK de Azure Blob.

Impacto:

- El upload puede funcionar en fake y fallar en Azure por permisos, nombre de contenedor o red.
- Processing puede no descargar el blob aunque ingest lo haya aceptado.

Recomendación:

- Validar primero upload/download real con un smoke test mínimo antes de ejecutar el flujo completo.

### Qdrant en Azure no se inicializa igual que en fake

El repositorio de Qdrant crea colección automáticamente en modo fake, pero no necesariamente en modo real. Además, la dimensión vectorial debe coincidir con el modelo de embeddings desplegado.

Impacto:

- Upsert fallará si la colección no existe.
- Search fallará o devolverá resultados inválidos si la dimensión no coincide.
- Cambiar de `text-embedding-3-large` a `text-embedding-3-small` sin ajustar dimensión puede romper la persistencia.

Recomendación:

- Definir una fase explícita de bootstrap o verificación de colección.
- Documentar dimensión esperada según deployment OpenAI.
- Validar upsert/search real antes del flujo end-to-end.

### Estado de documentos en memoria

El estado de documentos está almacenado en memoria dentro del servicio de ingest. Esto es suficiente para pruebas locales, pero no para Azure con escalado, reinicios o múltiples réplicas.

Impacto:

- El estado se pierde si el contenedor reinicia.
- Varias réplicas pueden mostrar estados inconsistentes.
- El frontend no podrá confiar en `/upload/{doc_id}/status` en producción.

Recomendación:

- Antes de escalar réplicas, persistir estado en un recurso compartido: Azure Table Storage, Cosmos DB, PostgreSQL o una cola/event log con estado materializado.

### Frontend no alineado con backend

El frontend mantiene rutas mock y contratos que no coinciden con el backend actual.

Impacto:

- Backend puede estar correctamente desplegado en Azure y aun así la demo end-to-end fallar desde Streamlit.
- Las evidencias del frontend no validarán el flujo real si `use_mock=True`.

Recomendación:

- Alinear cliente API con los endpoints reales o crear un gateway estable.
- Desactivar mocks en una configuración clara de entorno dev Azure.

## 5. Arquitectura Objetivo

La arquitectura recomendada para la primera integración Azure es mantener la misma separación lógica que ya existe en backend:

- Azure Container App `ingest-service`.
- Azure Container App `processing-service`.
- Azure Container App `rag-service`.
- Azure Container App interna para Qdrant.
- Azure File Share para persistencia de Qdrant.
- Azure Blob Storage para PDFs.
- Azure OpenAI para embeddings y chat.
- Azure Key Vault y/o Managed Identity para secretos y autenticación.
- Azure Container Registry para imágenes.
- Application Insights y Log Analytics para observabilidad.
- Frontend Streamlit en Azure Container Apps apuntando a la API real.

### Flujo objetivo

1. El frontend envía PDFs al endpoint público o interno de `ingest-service`.
2. `ingest-service` valida y sube cada PDF a Azure Blob Storage.
3. `ingest-service` registra el estado inicial del documento.
4. `ingest-service` dispara `processing-service` con `doc_id` y `blob_name`.
5. `processing-service` descarga el PDF desde Blob Storage.
6. `processing-service` extrae texto y metadata.
7. `processing-service` crea chunks.
8. `processing-service` genera embeddings con Azure OpenAI.
9. `processing-service` persiste vectores y payload en Qdrant.
10. `rag-service` recibe consultas del frontend.
11. `rag-service` genera embedding de la consulta.
12. `rag-service` busca en Qdrant.
13. `rag-service` genera o estructura la respuesta final.
14. Todos los servicios emiten logs estructurados con eventos trazables.

### Matriz de decisión de topología backend

| Opción | Descripción | Ventajas | Riesgos | Recomendación |
|   ---  |      ---    |   ---    |   ---   |      ---      |
| A | Tres Container Apps: ingest, processing y rag | Respeta arquitectura actual, facilita logs por servicio, minimiza refactor funcional | Más recursos, más configuración Terraform | Recomendada para primera integración |
| B | Un único backend FastAPI con routers de ingest, processing y rag | Despliegue más simple, menos DNS interno | Requiere refactor, mezcla responsabilidades, cambia supuestos del test local fake | Solo si se decide simplificar producto |

La recomendación inicial es la opción A.

## 6. Fases de Integración

### Fase 0 - Congelar baseline local fake

Objetivo:

Garantizar que existe una referencia funcional estable antes de introducir recursos Azure.

Tareas:

- Ejecutar el test `local_fake`.
- Confirmar que se generan logs bajo `tests/integration/logs/`.
- Confirmar que aparecen los eventos clave del flujo.
- Confirmar que el `doc_id` subido aparece en el resultado RAG.

Validación:

```bash
/home/jldom/TFM/TalentRefine/TR_Env/bin/pytest tests/integration/test_local_fake_flow.py -m integration -v
```

Criterios de aceptación:

- El test pasa.
- Existe `flow_evidence.txt`.
- Los logs contienen eventos como `document_upload_accepted`, `processing_started`, `chunks_created`, `qdrant_upsert_completed`, `rag_search_started`, `qdrant_search_completed` y `rag_search_completed`.

Evidencia:

- Carpeta local `tests/integration/logs/local_fake_flow_<timestamp>/`.

### Fase 1 - Alinear contratos de configuración

Objetivo:

Hacer que backend, Terraform y documentación usen los mismos nombres de variables.

Tareas:

- Revisar `app/shared/config.py` como fuente de verdad de variables backend.
- Actualizar Terraform para inyectar nombres compatibles con el backend.
- Eliminar o justificar aliases como `KEY_VAULT_URI`, `EMBEDDING_DEPLOYMENT_NAME` y `GPT_DEPLOYMENT_NAME`.
- Alinear `BLOB_CONTAINER_NAME` con el contenedor real creado por Terraform.

Validación:

- Revisar `terraform plan`.
- Revisar variables disponibles en Container Apps.
- Arrancar los contenedores con `USE_FAKE_INTEGRATIONS=false` en un entorno de prueba controlado para detectar errores de configuración temprano.

Criterios de aceptación:

- Cada variable obligatoria del backend tiene un valor definido por Terraform o por configuración segura.
- No hay dependencia implícita de nombres antiguos.
- La documentación de `.env.example`, Terraform y backend coincide.

Evidencia:

- Captura o salida de `terraform plan`.
- Lista de variables efectivas por Container App.

### Fase 2 - Decidir y materializar topología Azure backend

Objetivo:

Desplegar una topología Azure coherente con la arquitectura del backend.

Tareas:

- Crear tres Container Apps backend o rediseñar explícitamente a un backend único.
- Si se mantiene la arquitectura actual, definir:
  - `ingest-service`;
  - `processing-service`;
  - `rag-service`.
- Configurar imágenes reales desde ACR, no imágenes placeholder.
- Definir DNS interno o URLs internas entre servicios.
- Sustituir referencias hardcodeadas si no coinciden con nombres Azure.

Validación:

- Cada servicio arranca.
- Cada servicio expone `/health` si se incorpora healthcheck HTTP.
- `ingest-service` puede alcanzar `processing-service`.
- `rag-service` puede alcanzar Qdrant.

Criterios de aceptación:

- No queda ningún Container App backend con imagen placeholder.
- La topología desplegada coincide con la arquitectura documentada.
- Los logs permiten distinguir servicio, logger y entorno.

Evidencia:

- Revisión de Azure Container Apps.
- Logs de arranque por servicio en Log Analytics.

### Fase 3 - Integrar Azure Blob Storage real

Objetivo:

Sustituir el almacenamiento fake por Azure Blob Storage real en ingest y processing.

Tareas:

- Confirmar el nombre final del contenedor Blob.
- Confirmar `BLOB_ACCOUNT_URL`.
- Confirmar permisos de escritura y lectura para la identidad usada por los servicios.
- Validar upload desde `ingest-service`.
- Validar download desde `processing-service`.
- Añadir un smoke test de Blob que no dependa de OpenAI ni Qdrant.

Validación:

- Subir un PDF pequeño a Blob.
- Descargar el mismo blob desde processing.
- Comparar tamaño y hash si procede.

Criterios de aceptación:

- `ingest-service` puede escribir en el contenedor.
- `processing-service` puede leer el blob.
- Los errores de permisos se registran claramente.
- Los logs incluyen `doc_id` y `blob_name`.

Evidencia:

- Log de upload.
- Log de download.
- Comprobación del blob en Azure Storage.

### Fase 4 - Integrar Key Vault y estrategia de secretos/identidad

Objetivo:

Cerrar una estrategia única y verificable para autenticación y secretos.

Tareas:

- Decidir si Azure OpenAI se autentica con API key en Key Vault o con Managed Identity.
- Decidir si Qdrant necesita API key o se mantiene como servicio interno sin API key en dev.
- Si se usan secretos:
  - crear secretos en Key Vault;
  - documentar nombres exactos;
  - validar lectura desde Container Apps.
- Si se usa Managed Identity:
  - adaptar código para token auth donde aplique;
  - retirar lecturas innecesarias de Key Vault.

Validación:

- Smoke test de lectura de Key Vault desde el contenedor.
- Smoke test de autenticación contra Azure OpenAI.

Criterios de aceptación:

- No hay mezcla accidental entre RBAC y API keys.
- Los secretos requeridos existen.
- La Managed Identity tiene permisos mínimos necesarios.

Evidencia:

- Logs de lectura correcta de configuración no sensible.
- Confirmación de acceso sin imprimir secretos.

### Fase 5 - Integrar Azure OpenAI real

Objetivo:

Generar embeddings y respuestas RAG usando deployments reales de Azure OpenAI.

Tareas:

- Confirmar `AZURE_OPENAI_ENDPOINT`.
- Confirmar `AZURE_OPENAI_API_VERSION`.
- Confirmar `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`.
- Confirmar `AZURE_OPENAI_CHAT_DEPLOYMENT`.
- Confirmar dimensión del modelo de embedding.
- Ejecutar una llamada mínima de embedding.
- Ejecutar una llamada mínima de chat si el flujo RAG la usa.

Validación:

- Smoke test que genere embedding para un texto corto.
- Validar que la dimensión coincide con Qdrant.
- Smoke test de chat con prompt mínimo.

Criterios de aceptación:

- OpenAI responde desde Container Apps.
- No se imprimen claves en logs.
- Los errores de rate limit, deployment inexistente o permisos quedan diferenciados.

Evidencia:

- Log de embedding generado con dimensión.
- Log de respuesta chat resumida, sin datos sensibles.

### Fase 6 - Integrar Qdrant en Azure

Objetivo:

Garantizar que Qdrant desplegado en Azure puede persistir y recuperar vectores reales.

Tareas:

- Confirmar URL interna de Qdrant.
- Confirmar si el tráfico debe usar `http` o `https`.
- Definir política de `min_replicas`; evitar `0` si el cold start rompe pruebas.
- Confirmar persistencia en Azure File Share.
- Crear o verificar colección real.
- Alinear dimensión vectorial con Azure OpenAI.

Validación:

- Crear colección si no existe.
- Hacer upsert de un punto de prueba.
- Hacer search contra ese punto.
- Reiniciar Qdrant y validar persistencia.

Criterios de aceptación:

- Qdrant responde desde `processing-service` y `rag-service`.
- La colección existe con dimensión correcta.
- Los datos sobreviven a reinicio del contenedor.

Evidencia:

- Logs de creación/verificación de colección.
- Resultado de upsert/search.
- Evidencia de persistencia tras reinicio.

### Fase 7 - Endurecer observabilidad

Objetivo:

Conseguir que el flujo Azure genere evidencias tan claras como el flujo local fake.

Tareas:

- Mantener logs estructurados a stdout/stderr.
- Confirmar ingesta en Log Analytics.
- Incluir `service`, `environment`, `logger`, `level`, `timestamp`, `doc_id` y `blob_name`.
- Añadir correlation id si el flujo pasa por varios servicios.
- Revisar eventos mínimos por fase del flujo.

Eventos mínimos esperados:

- `document_upload_accepted`
- `local_blob_uploaded` o equivalente real como `azure_blob_uploaded`
- `processing_triggered`
- `processing_started`
- `pdf_text_extracted`
- `chunks_created`
- `fake_embeddings_generated` solo en fake
- `azure_embeddings_generated` en real
- `qdrant_upsert_completed`
- `rag_search_started`
- `qdrant_search_completed`
- `rag_search_completed`

Validación:

- Ejecutar un flujo real en dev.
- Consultar logs por `doc_id`.
- Reconstruir el flujo completo desde Log Analytics.

Criterios de aceptación:

- Un único `doc_id` permite seguir upload, processing, indexing y search.
- Los logs distinguen fake y real.
- No hay secretos ni contenido sensible completo en logs.

Evidencia:

- Query de Log Analytics.
- Export o captura de eventos ordenados por timestamp.

### Fase 8 - Alinear frontend con backend real

Objetivo:

Hacer que el frontend consuma contratos reales y deje de depender de mocks para la demo Azure.

Tareas:

- Revisar `utils/api_client.py`.
- Cambiar endpoints a los contratos reales o introducir API gateway.
- Configurar URL base desde variable de entorno compatible con Azure Container Apps.
- Desactivar `use_mock=True` en el flujo de integración real.
- Validar upload y search desde la UI.

Validación:

- Ejecutar frontend apuntando a backend Azure dev.
- Subir un PDF desde Streamlit.
- Lanzar consulta RAG.
- Confirmar que el resultado corresponde al documento procesado.

Criterios de aceptación:

- El frontend no usa datos mock en entorno Azure dev.
- Las rutas llamadas existen en backend.
- Los errores HTTP se muestran de forma comprensible.

Evidencia:

- Captura de UI o logs de Streamlit.
- Logs backend correlacionados con la acción del usuario.

### Fase 9 - Añadir pruebas cloud-readiness

Objetivo:

Crear una segunda capa de pruebas que valide recursos Azure reales sin contaminar el test local fake.

Tareas:

- Añadir tests marcados como `azure` o `cloud`.
- Separar credenciales/configuración de entorno.
- Añadir pruebas por recurso:
  - Blob upload/download;
  - Key Vault read;
  - Azure OpenAI embedding;
  - Qdrant upsert/search;
  - flujo end-to-end Azure dev.
- Hacer que los tests guarden evidencias similares a `flow_evidence.txt`.

Validación:

```bash
pytest tests/integration/test_local_fake_flow.py -m integration -v
pytest tests/integration/test_azure_smoke.py -m azure -v
pytest tests/integration/test_azure_flow.py -m azure -v
```

Criterios de aceptación:

- Los tests locales no requieren Azure.
- Los tests Azure fallan rápido si falta configuración.
- Las evidencias Azure permiten reconstruir el flujo sin revisar manualmente portales.

Evidencia:

- Carpeta local de logs por ejecución Azure.
- Logs en Application Insights.
- Respuestas HTTP resumidas.

## 7. Variables de Entorno Objetivo

| Variable | Consumidor | Propietario | Valor esperado en Azure dev | Observaciones |
| --- | --- | --- | --- | --- |
| `KEY_VAULT_URL` | Todos los servicios si se usa Key Vault | Terraform security/compute | URL completa del vault | Sustituir `KEY_VAULT_URI` o soportarlo explícitamente |
| `BLOB_ACCOUNT_URL` | ingest, processing | Terraform storage/compute | `https://<account>.blob.core.windows.net` | Debe coincidir con la cuenta desplegada |
| `BLOB_CONTAINER_NAME` | ingest, processing | Terraform storage/compute | `pdfs` o nombre acordado | Backend default actual es `cv-uploads`; Terraform crea `pdfs` |
| `QDRANT_URL` | processing, rag | Terraform compute | URL interna de Qdrant | Confirmar `http` vs `https` |
| `QDRANT_COLLECTION` | processing, rag | Backend/Terraform | `talentrefine-candidates` | Debe existir con dimensión correcta |
| `AZURE_OPENAI_ENDPOINT` | processing, rag | Terraform ai/compute | Endpoint del recurso OpenAI | Debe ser accesible desde Container Apps |
| `AZURE_OPENAI_API_VERSION` | processing, rag | Backend/Terraform | Versión soportada por SDK | Evitar hardcode no validado |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | processing, rag | Terraform ai/compute | Nombre del deployment de embeddings | Sustituir `EMBEDDING_DEPLOYMENT_NAME` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | rag | Terraform ai/compute | Nombre del deployment de chat | Sustituir `GPT_DEPLOYMENT_NAME` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Todos los servicios | Terraform monitoring/compute | Connection string App Insights | No es secreto crítico, pero debe tratarse como config |
| `APP_ENV` | Todos los servicios | Terraform compute | `dev` | Distingue logs y comportamiento |
| `USE_FAKE_INTEGRATIONS` | Todos los servicios | Terraform compute | `false` | En Azure real debe estar desactivado |
| `LOG_LEVEL` | Todos los servicios | Terraform compute | `INFO` | `DEBUG` solo temporalmente |
| `AZURE_CLIENT_ID` | Todos los servicios con Managed Identity | Terraform security/compute | Client ID de User Assigned Managed Identity | Necesario para `DefaultAzureCredential` en algunos escenarios |

## 8. Estrategia de Identidad y Secretos

### Opción A - API keys en Key Vault

Funcionamiento:

- Terraform crea Key Vault.
- Se crean secretos como `azure-openai-api-key` y `qdrant-api-key`.
- Los servicios leen secretos con `DefaultAzureCredential`.

Ventajas:

- Encaja con el patrón actual de `get_secret()`.
- Es simple de entender y depurar.
- Permite rotación controlada desde Key Vault.

Riesgos:

- Hay que crear y mantener secretos.
- Se debe evitar imprimir valores sensibles.
- Duplica parcialmente RBAC si Azure OpenAI también permite identidad.

### Opción B - Managed Identity/RBAC

Funcionamiento:

- Los servicios usan la Managed Identity asignada.
- Azure OpenAI y Storage autorizan mediante roles.
- Key Vault queda para secretos que no soporten RBAC nativo o no se usa para OpenAI.

Ventajas:

- Menos secretos.
- Mejor alineación con prácticas cloud modernas.
- Menor riesgo de exposición de claves.

Riesgos:

- Puede requerir cambios de código.
- No todos los SDKs o integraciones de LlamaIndex pueden estar configurados igual que con API key.
- La depuración inicial puede ser más compleja.

### Recomendación

Para una primera integración rápida, usar API keys en Key Vault si el objetivo prioritario es validar el flujo. Para una versión más robusta, migrar a Managed Identity donde los SDKs lo soporten bien.

La decisión debe cerrarse antes de modificar Terraform o código de autenticación. No se debe asumir que RBAC y API keys son intercambiables.

## 9. Estrategia de Testing

La estrategia recomendada tiene cuatro capas.

### Capa 1 - Local fake

Objetivo:

Validar el flujo backend completo sin Azure.

Debe seguir ejecutándose con:

```bash
/home/jldom/TFM/TalentRefine/TR_Env/bin/pytest tests/integration/test_local_fake_flow.py -m integration -v
```

Esta capa detecta regresiones en:

- contratos internos entre servicios;
- extracción y chunking;
- escritura/lectura Qdrant local;
- eventos de logs;
- forma básica de respuestas HTTP.

No valida:

- permisos Azure;
- red Azure;
- Key Vault real;
- Azure OpenAI real;
- Blob Storage real;
- despliegue Container Apps.

### Capa 2 - Smoke tests Azure por recurso

Objetivo:

Validar cada recurso cloud de forma aislada.

Pruebas mínimas:

- Blob upload/download.
- Key Vault secret read o token auth.
- Azure OpenAI embedding.
- Azure OpenAI chat.
- Qdrant health/upsert/search.

Estas pruebas deben fallar con mensajes claros si falta configuración.

### Capa 3 - Integración Azure backend

Objetivo:

Validar el flujo backend completo en Azure dev sin depender del frontend.

Secuencia:

1. POST de PDF a `ingest-service`.
2. Polling de estado o espera controlada.
3. Consulta a `rag-service`.
4. Validación de que el resultado corresponde al documento procesado.
5. Recopilación de logs por `doc_id`.

### Capa 4 - End-to-end Azure con frontend

Objetivo:

Validar experiencia real desde Streamlit.

Condiciones:

- `use_mock=False`.
- URL backend real.
- Rutas frontend alineadas.
- Evidencias backend y frontend correlacionables.

## 10. Observabilidad y Evidencias

La integración Azure debe conservar la filosofía de evidencia del test local fake.

### Eventos mínimos por flujo

| Evento | Servicio | Cuándo debe emitirse |
| --- | --- | --- |
| `document_upload_accepted` | ingest | Cuando el PDF es aceptado y se asigna `doc_id` |
| `azure_blob_uploaded` | ingest | Cuando el PDF queda persistido en Blob Storage |
| `processing_triggered` | ingest | Cuando se invoca processing |
| `processing_started` | processing | Al empezar el procesamiento |
| `pdf_text_extracted` | processing | Tras extraer texto del PDF |
| `chunks_created` | processing | Tras crear chunks |
| `azure_embeddings_generated` | processing/rag | Tras generar embeddings reales |
| `qdrant_upsert_completed` | processing | Tras persistir vectores |
| `rag_search_started` | rag | Al recibir consulta RAG |
| `qdrant_search_completed` | rag | Tras consultar Qdrant |
| `rag_search_completed` | rag | Tras construir respuesta final |

### Campos mínimos de log

- `timestamp`
- `level`
- `service`
- `environment`
- `logger`
- `event`
- `doc_id` cuando exista
- `blob_name` cuando exista
- `correlation_id` cuando se introduzca
- `duration_ms` en llamadas externas relevantes
- `status_code` en llamadas HTTP entre servicios

### Evidencias esperadas

Para cada ejecución relevante en Azure dev debe poder recuperarse:

- request de upload;
- `doc_id`;
- blob persistido;
- procesamiento completado;
- número de chunks;
- dimensión de embeddings;
- upsert Qdrant;
- búsqueda RAG;
- respuesta resumida;
- errores si los hubo.

No deben registrarse:

- contenido completo de CVs;
- secretos;
- API keys;
- tokens;
- payloads sensibles completos.

## 11. Checklist Pre-Deploy

- El test `local_fake` pasa.
- `.env.example` está alineado con `app/shared/config.py`.
- Terraform inyecta los mismos nombres de variables que espera el backend.
- La topología elegida está cerrada: tres Container Apps o backend único.
- Las imágenes backend reales se construyen y publican en ACR.
- Los Container Apps no usan imágenes placeholder.
- `USE_FAKE_INTEGRATIONS=false` en Azure.
- `APP_ENV=dev` en el primer entorno cloud.
- Blob container existe y coincide con `BLOB_CONTAINER_NAME`.
- La identidad tiene permisos sobre Storage.
- Key Vault existe y la estrategia de secretos está cerrada.
- Azure OpenAI tiene deployments reales y nombres conocidos.
- Qdrant tiene persistencia configurada.
- Qdrant tiene colección con dimensión correcta o bootstrap definido.
- Application Insights y Log Analytics están conectados.
- Frontend tiene una URL backend real configurada.
- Los mocks del frontend no se usan en la prueba Azure.

## 12. Checklist Post-Deploy

- Los tres servicios backend arrancan sin errores de configuración.
- Los logs aparecen en Log Analytics.
- Qdrant responde desde processing y rag.
- Blob upload/download funciona desde los contenedores.
- Azure OpenAI responde desde processing y rag.
- Key Vault o Managed Identity funcionan según la estrategia elegida.
- Un PDF real puede subirse desde backend.
- El procesamiento completa extracción, chunking, embeddings y upsert.
- Una consulta RAG devuelve resultados asociados al documento procesado.
- Se puede reconstruir todo el flujo buscando por `doc_id`.
- El frontend puede ejecutar upload y búsqueda sin mocks.
- Los errores se registran sin secretos ni datos sensibles.

## 13. Decisiones Pendientes

1. Confirmar topología final de backend en Azure:
   - tres Container Apps;
   - o backend unificado.

2. Confirmar estrategia de autenticación:
   - API keys en Key Vault;
   - Managed Identity;
   - híbrido temporal.

3. Confirmar nombre definitivo del contenedor Blob:
   - mantener `pdfs`;
   - o cambiar Terraform a `cv-uploads`;
   - o cambiar backend default/documentación.

4. Confirmar nombres definitivos de deployments Azure OpenAI:
   - `embedding-deployment` y `gpt-deployment`;
   - o nombres alineados con modelos.

5. Confirmar modelo de embeddings y dimensión vectorial:
   - `text-embedding-3-small`;
   - `text-embedding-3-large`;
   - otra opción.

6. Confirmar cómo se inicializa Qdrant en modo real:
   - Terraform/provisioning;
   - bootstrap de aplicación;
   - script operativo previo al despliegue.

7. Confirmar persistencia de estado documental:
   - mantener memoria solo en dev inicial;
   - Azure Table Storage;
   - Cosmos DB;
   - PostgreSQL;
   - cola/event log más estado materializado.

8. Confirmar contrato público frontend-backend:
   - adaptar frontend a `/upload` y `/search`;
   - o crear gateway `/api/v1/...`.

## No Hacer Todavía

- No borrar `local_fake`.
- No integrar Azure real dentro del test `local_fake`.
- No mezclar evidencias de mocks frontend con evidencias backend.
- No asumir que RBAC y API keys son equivalentes.
- No escalar Container Apps backend sin resolver persistencia de estado.
- No cambiar rutas HTTP públicas sin coordinar frontend y tests.
- No registrar contenido completo de CVs en logs.
- No versionar logs generados localmente.
- No usar imágenes placeholder para validar una integración funcional.
