# Plan MVP de logging y monitorizacion en Azure - TalentRefine AI

Este documento adapta el checklist de monitorizacion y logging al estado actual de las ramas `main` de los tres repositorios:

- `TalentRefineAI-Backend`: tres microservicios FastAPI (`ingest-service`, `processing-service`, `rag-service`) con `structlog`, endpoints `/health` y logs por `stdout`.
- `TalentRefineAI-Infra`: Terraform con Azure Container Apps, Qdrant, Storage, Key Vault, Azure OpenAI, Log Analytics Workspace y Application Insights.
- `TalentRefineAI-Frontend`: Streamlit en Azure Container Apps, con healthcheck nativo `/_stcore/health` y cliente HTTP hacia `ingest-service` y `rag-service`.

La premisa de este plan es **MVP**: instrumentar solo lo necesario para responder rapido a estas preguntas:

1. La aplicacion esta viva?
2. Que servicio esta fallando?
3. Falla Azure OpenAI, Qdrant, Blob Storage o una llamada interna?
4. Cuanto tardan upload, indexacion y busqueda RAG?
5. Cuantos CVs se aceptan, se indexan y se consultan?

No se busca una plataforma completa de observabilidad. Se prioriza bajo coste, pocos cambios, consultas claras y evidencia suficiente para una demo o entrega academica.

## Alcance MVP

### Incluido

- Logs estructurados JSON en backend.
- Correlacion minima por `request_id` y `doc_id`.
- Medicion de duracion solo en errores, warnings, requests lentas y exitos finales relevantes.
- Eventos clave de exito, warning y error en ingesta, procesamiento y busqueda RAG.
- Uso de Log Analytics para consultar logs de Container Apps.
- Health checks existentes (`/health`) y healthcheck Streamlit (`/_stcore/health`).
- KQL basico para errores, latencia, flujo RAG y dependencias criticas.
- Alertas minimas: servicio caido, errores backend y fallos de dependencias criticas.

### Fuera del MVP

- OpenTelemetry completo.
- Trazas distribuidas profundas.
- Application Map.
- Workbooks avanzados.
- Metricas custom en Application Insights.
- Diagnostic Settings exhaustivos para todos los recursos Azure.
- Alertas FinOps detalladas.
- Monitorizacion de PII, prompts completos o contenido de CVs.

Application Insights ya existe en Terraform y se inyecta como `APPLICATIONINSIGHTS_CONNECTION_STRING`, pero en este MVP no es obligatorio instrumentarlo desde codigo. La fuente principal sera `stdout` de Container Apps hacia Log Analytics.

## Politica de niveles para coste minimo

Para reducir costes, Azure no debe operar con `LOG_LEVEL=INFO`.

Politica recomendada:

- `SUCCESS`: exitos finales imprescindibles para evidenciar el flujo MVP.
- `WARNING`: degradaciones, reintentos, datos incompletos, dependencias lentas o respuestas HTTP 4xx.
- `ERROR`: excepciones, fallos de dependencias, respuestas HTTP 5xx y flujos no completados.

No se registran logs `INFO` en Azure. Si hace falta depurar, se puede activar `INFO` temporalmente en dev durante una ventana corta y volver despues al nivel reducido.

Como Python logging no trae `SUCCESS` por defecto, hay dos opciones:

1. Opcion recomendada: definir un nivel custom `SUCCESS = 25`, por encima de `INFO` y por debajo de `WARNING`. En Azure usar `LOG_LEVEL=SUCCESS`.
2. Opcion mas restrictiva: no crear nivel custom y usar `LOG_LEVEL=WARNING`; en ese caso se pierden logs de exito y solo quedan warnings/errores.

Para este proyecto MVP se recomienda la opcion 1, porque permite mantener evidencia de negocio sin ingerir todos los logs informativos.

## Estado actual relevante

### Backend

Archivos clave:

- `app/shared/logging.py`: configura `structlog`, actualmente con `ConsoleRenderer`.
- `app/ingest_service/main.py`: expone `/health`.
- `app/processing_service/main.py`: expone `/health`.
- `app/rag_service/main.py`: expone `/health`.
- `app/ingest_service/services/ingest_service.py`: ya emite eventos como `blob_uploaded`, `processing_triggered`, `upload_failed`, `processing_trigger_failed`.
- `app/processing_service/services/indexing_service.py`: ya emite eventos como `indexing_started`, `metadata_extracted`, `chunks_generated`, `indexing_completed`, `qdrant_upsert_error`.
- `app/rag_service/services/rag_engine.py`: ya emite eventos como `qdrant_collection_ready`, `qdrant_search_attempt`, `qdrant_search_result`, `qdrant_search_failed`, `llm_extraction_failed`.

### Infra

Archivos clave:

- `terraform/modules/monitoring/main.tf`: crea Log Analytics Workspace y Application Insights.
- `terraform/modules/compute/main.tf`: define Container Apps y pasa `APPLICATIONINSIGHTS_CONNECTION_STRING` a frontend y backends.
- `terraform/modules/compute/main.tf`: ya tiene Diagnostic Settings para ACR.
- `terraform/outputs.tf`: expone `log_analytics_workspace_id`, `application_insights_id` y `app_insights_connection_string`.

Punto a validar: el `azurerm_container_app_environment` debe quedar vinculado a Log Analytics para que existan tablas como `ContainerAppConsoleLogs_CL` y `ContainerAppSystemLogs_CL`.

### Frontend

Archivos clave:

- `app.py`: Streamlit.
- `utils/api_client.py`: cliente hacia `INGEST_API_URL`, `PROCESS_API_URL`, `RAG_API_URL`.
- `Dockerfile`: arranca Streamlit con `--logger.level=info`.

Para coste minimo, se recomienda revisar ese nivel del frontend y usar `warning` en Azure si no se necesitan logs informativos de Streamlit.

Para el MVP, el frontend se monitoriza principalmente con:

- disponibilidad de la Container App;
- `/_stcore/health`;
- errores HTTP mostrados por el cliente;
- logs de contenedor de Streamlit.

## Fase 1 - Conectar Container Apps con Log Analytics

### Objetivo

Garantizar que los logs de consola de frontend, backend y Qdrant llegan a Log Analytics.

### Pasos

1. Revisar `TalentRefineAI-Infra/terraform/modules/monitoring/main.tf`.
2. Confirmar que existe:

```hcl
resource "azurerm_log_analytics_workspace" "this" {
  sku               = "PerGB2018"
  retention_in_days = 30
}
```

3. Revisar `TalentRefineAI-Infra/terraform/modules/compute/main.tf`.
4. En `azurerm_container_app_environment.this`, validar si el entorno esta asociado al workspace. Si no lo esta, anadir el bloque soportado por la version del provider `azurerm` del repo para habilitar logs hacia Log Analytics.
5. Mantener `retention_in_days = 30`. Es suficiente para MVP.
6. Ejecutar en infra:

```bash
terraform fmt -recursive
terraform validate
terraform plan
```

7. Tras aplicar, validar que existen logs:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30m)
| project TimeGenerated, ContainerAppName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
| take 50
```

8. Validar eventos de plataforma:

```kusto
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(30m)
| project TimeGenerated, ContainerAppName_s, Reason_s, Log_s
| order by TimeGenerated desc
| take 50
```

### Criterio de aceptacion

Al abrir el frontend y llamar a `/health` de los backends, aparecen logs en `ContainerAppConsoleLogs_CL`.

## Fase 2 - Logging JSON en backend

### Objetivo

Convertir los logs actuales de `structlog` en JSON para poder filtrar por campos en KQL.

### Cambio minimo

Modificar `app/shared/logging.py` para usar JSON cuando no sea entorno local.

Campos minimos obligatorios:

- `timestamp`
- `level`
- `service`
- `event`
- `logger`
- `app_env`

Campos contextuales cuando aplique:

- `request_id`
- `doc_id`
- `blob_name`
- `operation`
- `status_code`
- `duration_ms`
- `error`
- `error_type`

### Implementacion recomendada

1. Mantener `ConsoleRenderer` solo si `APP_ENV=local`.
2. Definir nivel custom `SUCCESS=25` si se quieren conservar exitos finales sin habilitar `INFO`.
3. Usar `JSONRenderer` para Azure:

```python
renderer = (
    structlog.dev.ConsoleRenderer(colors=False)
    if settings.app_env == "local"
    else structlog.processors.JSONRenderer()
)
```

4. Anadir un processor para incluir `app_env`:

```python
def _add_app_env(app_env: str):
    def processor(logger, method_name, event_dict):
        event_dict["app_env"] = app_env
        return event_dict
    return processor
```

5. Mantener `_add_service_name(service_name)`.
6. Cambiar el ultimo processor por `renderer`.
7. En Azure configurar `LOG_LEVEL=SUCCESS` o, si se quiere maximo ahorro, `LOG_LEVEL=WARNING`.

### Criterio de aceptacion

Un log como `document_upload_completed` debe verse en Log Analytics como JSON parseable:

```json
{
  "event": "document_upload_completed",
  "service": "ingest-service",
  "level": "success",
  "doc_id": "...",
  "file_name": "...",
  "duration_ms": 1234,
  "app_env": "development"
}
```

## Fase 3 - Middleware minimo de request logging

### Objetivo

Medir latencia y resultado HTTP sin generar un log por cada request normal.

### Cambio minimo

Crear `app/shared/request_logging.py` con un middleware ASGI/FastAPI que:

- Lee `X-Request-ID` si llega.
- Si no llega, genera un UUID.
- Mide duracion de la request.
- Registra solo requests relevantes para coste minimo:
  - `http_request_failed` en respuestas 5xx o excepciones.
  - `http_request_warning` en respuestas 4xx.
  - `http_request_slow` si supera un umbral, por ejemplo 3s en `/upload` o 10s en `/search`.
- Devuelve `X-Request-ID` en la respuesta.

Campos del evento:

- `request_id`
- `method`
- `path`
- `status_code`
- `duration_ms`
- `client_host`
- `service`

### Ejemplo orientativo

```python
from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            path = request.url.path
            is_slow = duration_ms >= 10000 or (path.startswith("/upload") and duration_ms >= 3000)
            if status_code >= 500:
                logger.error(
                    "http_request_failed",
                    request_id=request_id,
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_host=request.client.host if request.client else None,
                )
            elif status_code >= 400:
                logger.warning(
                    "http_request_warning",
                    request_id=request_id,
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_host=request.client.host if request.client else None,
                )
            elif is_slow:
                logger.warning(
                    "http_request_slow",
                    request_id=request_id,
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_host=request.client.host if request.client else None,
                )
            if "response" in locals():
                response.headers["X-Request-ID"] = request_id
```

Registrar en:

- `app/ingest_service/main.py`
- `app/processing_service/main.py`
- `app/rag_service/main.py`

```python
from shared.request_logging import RequestLoggingMiddleware

app.add_middleware(RequestLoggingMiddleware)
```

### Criterio de aceptacion

Las requests normales a `/health` no generan logs en Azure. Las requests lentas, 4xx y 5xx generan eventos con `duration_ms`.

## Fase 4 - Eventos imprescindibles del flujo RAG

### Objetivo

Tener una linea temporal suficiente para seguir un CV desde subida hasta busqueda.

### Eventos MVP por servicio

#### Ingest service

Eventos necesarios:

- `document_upload_completed`
- `rejected_file`
- `upload_failed`
- `processing_trigger_failed`

Acciones:

1. No registrar cada aceptacion como `INFO`.
2. Registrar `document_upload_completed` como `SUCCESS` cuando el PDF queda subido a Blob y processing ha sido disparado correctamente.
3. Incluir:
   - `doc_id`
   - `file_name`
   - `file_size_bytes`
   - `duration_ms`
4. Mantener `rejected_file` como `WARNING` para no-PDF.
5. Mantener `upload_failed` y `processing_trigger_failed` como `ERROR`.
6. En errores, anadir `error_type=type(exc).__name__`.
7. No es necesario emitir `blob_uploaded` y `processing_triggered` como eventos separados en Azure si ya existe `document_upload_completed`.

#### Processing service

Eventos necesarios:

- `indexing_completed`
- `indexing_degraded`
- `indexing_failed`

Acciones:

1. No registrar `indexing_started`, `metadata_extracted`, `chunks_generated` ni `embeddings_generated` como logs normales en Azure.
2. Registrar `indexing_completed` como `SUCCESS` al final del flujo:
   - `doc_id`
   - `n_chunks`
   - `embedding_count`
   - `qdrant_points`
   - `duration_ms`
3. Registrar `indexing_degraded` como `WARNING` si faltan metadatos esperados, si Qdrant esta vacio/cargando o si una dependencia tarda demasiado pero el flujo no falla.
4. Registrar `indexing_failed` como `ERROR` antes de propagar excepcion.
5. No loggear texto del CV ni respuesta completa del LLM.

#### RAG service

Eventos necesarios:

- `rag_search_completed`
- `rag_search_degraded`
- `rag_search_failed`

Acciones:

1. No registrar `rag_search_started`, `qdrant_search_attempt` ni `qdrant_search_result` en Azure por defecto.
2. Registrar `rag_search_completed` como `SUCCESS` con:
   - `top_k`
   - `min_exp_years`
   - `has_location_filter`
   - `results_count`
   - `duration_ms`
3. Registrar `rag_search_degraded` como `WARNING` si Qdrant esta cargando, no hay resultados inesperadamente o falla la extraccion LLM pero hay fallback.
4. En excepciones, loggear `rag_search_failed` como `ERROR` con:
   - `error`
   - `error_type`
5. No loggear `job_description` completo. Como maximo, `query_length`.

### Criterio de aceptacion

Con un `doc_id`, se pueden ver estos eventos:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend log=parse_json(Log_s)
| where tostring(log.doc_id) == "<doc_id>"
| where tostring(log.level) in ("success", "warning", "error")
| project TimeGenerated, service=tostring(log.service), level=tostring(log.level), event=tostring(log.event), duration_ms=todouble(log.duration_ms), Log_s
| order by TimeGenerated asc
```

## Fase 5 - Health checks y disponibilidad

### Objetivo

Comprobar disponibilidad sin crear endpoints complejos.

### Estado actual

Los tres backends tienen:

- `GET /health`

El frontend Streamlit tiene:

- `GET /_stcore/health`

### Acciones MVP

1. Mantener `/health` en los tres backends.
2. No crear todavia `/health/live` y `/health/ready`, salvo que Azure Container Apps vaya a usar probes separadas.
3. Configurar probes de Container Apps contra:
   - backend: `/health`
   - frontend: `/_stcore/health`
4. Si se anaden probes en Terraform, usar `target_port = 8000` para backends y `8501` para frontend.
5. Para Qdrant, usar health interno de Qdrant si se configura probe; si no, validar desde logs de `rag-service`/`processing-service`.

### Criterio de aceptacion

Estos endpoints responden 200:

```bash
curl https://<frontend-fqdn>/_stcore/health
curl https://<ingest-fqdn>/health
curl https://<rag-fqdn>/health
curl https://<processing-fqdn>/health
```

## Fase 6 - KQL minimo para operacion

### Objetivo

Guardar consultas suficientes para demo, troubleshooting y evidencias.

Se recomienda crear en el backend:

```text
docs/kql/mvp-observability.kql
```

### Logs recientes por servicio

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1h)
| extend log=parse_json(Log_s)
| project
    TimeGenerated,
    app=ContainerAppName_s,
    service=tostring(log.service),
    level=tostring(log.level),
    event=tostring(log.event),
    message=Log_s
| order by TimeGenerated desc
| take 100
```

### Errores backend

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend log=parse_json(Log_s)
| where tostring(log.level) in ("error", "exception")
   or tostring(log.event) has "failed"
   or Log_s has_any ("Traceback", "Exception", "error")
| project
    TimeGenerated,
    app=ContainerAppName_s,
    service=tostring(log.service),
    event=tostring(log.event),
    error=tostring(log.error),
    error_type=tostring(log.error_type),
    raw=Log_s
| order by TimeGenerated desc
```

### Latencia por endpoint

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend log=parse_json(Log_s)
| where tostring(log.event) in ("http_request_failed", "http_request_warning", "http_request_slow")
| summarize
    requests=count(),
    avg_ms=avg(todouble(log.duration_ms)),
    p95_ms=percentile(todouble(log.duration_ms), 95),
    errors=countif(toint(log.status_code) >= 500)
  by service=tostring(log.service), path=tostring(log.path), bin(TimeGenerated, 15m)
| order by TimeGenerated desc
```

### Flujo de un documento

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend log=parse_json(Log_s)
| where tostring(log.doc_id) == "<doc_id>"
| where tostring(log.level) in ("success", "warning", "error")
| project
    TimeGenerated,
    app=ContainerAppName_s,
    service=tostring(log.service),
    level=tostring(log.level),
    event=tostring(log.event),
    duration_ms=todouble(log.duration_ms),
    raw=Log_s
| order by TimeGenerated asc
```

### KPIs MVP

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend log=parse_json(Log_s)
| where tostring(log.event) in (
    "document_upload_completed",
    "indexing_completed",
    "rag_search_completed",
    "upload_failed",
    "indexing_failed",
    "rag_search_failed"
)
| summarize count() by event=tostring(log.event), bin(TimeGenerated, 1h)
| order by TimeGenerated desc
```

### Fallos Qdrant

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend log=parse_json(Log_s)
| where tostring(log.event) has "qdrant"
| where tostring(log.level) in ("error", "warning") or tostring(log.event) has "failed"
| project TimeGenerated, service=tostring(log.service), event=tostring(log.event), raw=Log_s
| order by TimeGenerated desc
```

### Fallos Azure OpenAI / LLM

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend log=parse_json(Log_s)
| where Log_s has_any ("OpenAI", "embedding", "LLM", "llm", "429", "Timeout")
| project TimeGenerated, app=ContainerAppName_s, service=tostring(log.service), event=tostring(log.event), raw=Log_s
| order by TimeGenerated desc
```

### Reinicios o problemas de Container Apps

```kusto
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(24h)
| where Log_s has_any ("Restart", "BackOff", "Killing", "Failed", "Unhealthy")
| project TimeGenerated, ContainerAppName_s, Reason_s, Log_s
| order by TimeGenerated desc
```

## Fase 7 - Alertas minimas

### Objetivo

Crear solo alertas accionables para MVP.

### Alertas recomendadas

#### 1. Servicio con errores 5xx

Query:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(10m)
| extend log=parse_json(Log_s)
| where tostring(log.event) == "http_request_failed"
| summarize errors=countif(toint(log.status_code) >= 500) by service=tostring(log.service)
| where errors >= 3
```

Severidad: 2.

#### 2. Fallo de indexacion

Query:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(15m)
| extend log=parse_json(Log_s)
| where tostring(log.event) == "indexing_failed"
| summarize failures=count()
| where failures >= 1
```

Severidad: 3.

#### 3. Fallo de busqueda RAG

Query:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(15m)
| extend log=parse_json(Log_s)
| where tostring(log.event) == "rag_search_failed"
| summarize failures=count()
| where failures >= 1
```

Severidad: 3.

#### 4. Container con reinicios

Query:

```kusto
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(15m)
| where Log_s has_any ("Restart", "BackOff", "Killing")
| summarize restarts=count() by ContainerAppName_s
| where restarts > 0
```

Severidad: 3.

### Fuera del MVP

No crear inicialmente alertas separadas para CPU, memoria, Key Vault, Blob, Storage Transactions, tokens o coste. Revisarlas manualmente en Azure Portal durante la demo o pruebas de carga.

## Fase 8 - Validacion end-to-end

### Objetivo

Demostrar que la observabilidad MVP cubre el flujo principal.

### Caso 1 - Health

1. Abrir frontend.
2. Llamar `/health` de los tres backends.
3. Confirmar que no se generan logs de exito para health normales.
4. Validar que un 4xx/5xx o una request lenta si genera log.

Evidencia:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30m)
| extend log=parse_json(Log_s)
| where tostring(log.path) in ("/health", "/_stcore/health")
| project TimeGenerated, ContainerAppName_s, service=tostring(log.service), status=tostring(log.status_code)
```

### Caso 2 - Upload e indexacion

1. Subir un CV PDF desde frontend.
2. Guardar `doc_id`.
3. Validar eventos:
   - `document_upload_completed`
   - `indexing_completed`

### Caso 3 - Busqueda RAG

1. Ejecutar busqueda desde frontend.
2. Validar eventos:
   - `rag_search_completed`
3. Validar `duration_ms` en `rag_search_completed`.

### Caso 4 - Fallo controlado

En entorno dev, provocar uno de estos fallos:

- busqueda sin Qdrant disponible;
- `RAG_API_URL` incorrecta en frontend;
- documento no PDF;
- Azure OpenAI throttling o timeout controlado.

Validar que aparece al menos uno:

- `rejected_file`
- `processing_trigger_failed`
- `indexing_failed`
- `qdrant_search_failed`
- `rag_search_failed`

## Orden recomendado de implementacion

1. Conectar Container Apps Environment a Log Analytics.
2. Cambiar backend a JSON logs en Azure con nivel `SUCCESS` o `WARNING`.
3. Anadir middleware solo para requests lentas, warnings y errores.
4. Completar eventos MVP de exito final, warning y error.
5. Crear `docs/kql/mvp-observability.kql`.
6. Crear 3 o 4 alertas minimas.
7. Ejecutar validacion end-to-end y guardar evidencias.

## Checklist reducido y reparto entre 4 integrantes

Este reparto intenta que cada integrante trabaje en una zona distinta del proyecto para minimizar conflictos de Git. La regla practica es: una persona toca infraestructura, otra toca logging compartido, otra toca eventos de negocio backend y otra toca validacion/documentacion/frontend.

### Integrante 1 - Infraestructura Azure y Log Analytics

Zona principal de trabajo: `TalentRefineAI-Infra`.

Archivos previstos:

- `terraform/modules/compute/main.tf`
- `terraform/modules/compute/variables.tf`, solo si hace falta.
- `terraform/modules/monitoring/main.tf`, solo si hace falta.
- `terraform/outputs.tf`, solo si hace falta.

Checklist:

- [ ] Validar que `azurerm_log_analytics_workspace` existe con retencion de 30 dias.
- [ ] Validar que `azurerm_application_insights` existe y esta asociado al workspace.
- [ ] Conectar el Container Apps Environment con Log Analytics si todavia no lo esta.
- [ ] Confirmar que las Container Apps reciben `APPLICATIONINSIGHTS_CONNECTION_STRING`.
- [ ] Confirmar que frontend, ingest, processing y rag tienen `min_replicas` adecuado para demo MVP.
- [ ] Anadir probes basicas solo si se decide gestionarlas por Terraform:
  - backend: `/health`;
  - frontend: `/_stcore/health`.
- [ ] Ejecutar `terraform fmt -recursive`, `terraform validate` y preparar evidencia de `terraform plan`.
- [ ] Entregar los nombres reales de:
  - Resource Group;
  - Log Analytics Workspace;
  - Container Apps;
  - URLs publicas de frontend, ingest y rag.

Criterio de cierre:

- `ContainerAppConsoleLogs_CL` y `ContainerAppSystemLogs_CL` existen y reciben datos tras generar trafico.

### Integrante 2 - Logging compartido y latencia HTTP

Zona principal de trabajo: backend compartido.

Archivos previstos:

- `app/shared/logging.py`
- `app/shared/request_logging.py`
- `app/ingest_service/main.py`
- `app/processing_service/main.py`
- `app/rag_service/main.py`
- `requirements/*.txt`, solo si finalmente se anade alguna dependencia, aunque el MVP no deberia requerirla.

Checklist:

- [ ] Cambiar `structlog` para emitir JSON en Azure y consola legible solo en local.
- [ ] Definir `SUCCESS=25` si se quieren conservar exitos finales sin habilitar `INFO`.
- [ ] Configurar Azure con `LOG_LEVEL=SUCCESS` o `LOG_LEVEL=WARNING`.
- [ ] Anadir `app_env` y `service` a todos los logs.
- [ ] Crear middleware `RequestLoggingMiddleware`.
- [ ] Registrar solo `http_request_failed`, `http_request_warning` y `http_request_slow`.
- [ ] Incluir en ese evento:
  - `request_id`;
  - `method`;
  - `path`;
  - `status_code`;
  - `duration_ms`;
  - `client_host`.
- [ ] Propagar `X-Request-ID` en la respuesta.
- [ ] Registrar el middleware en los tres `main.py`.
- [ ] Probar localmente `/health` en los tres servicios y comprobar que no emite logs normales de exito.
- [ ] Probar un 4xx/5xx o request lenta y comprobar que si se emite log.

Criterio de cierre:

- Las requests lentas, 4xx y 5xx generan un log JSON parseable con latencia y codigo HTTP. Las requests normales no generan log.

### Integrante 3 - Eventos MVP del flujo backend

Zona principal de trabajo: servicios de negocio backend.

Archivos previstos:

- `app/ingest_service/services/ingest_service.py`
- `app/processing_service/services/indexing_service.py`
- `app/processing_service/repositories/qdrant_repository.py`, solo si el upsert correcto se registra mejor ahi.
- `app/rag_service/services/rag_engine.py`

Checklist:

- [ ] En ingesta, asegurar `document_upload_completed` como exito final con:
  - `doc_id`;
  - `file_name`;
  - `file_size_bytes`;
  - `duration_ms`.
- [ ] No emitir `document_upload_accepted`, `blob_uploaded` ni `processing_triggered` como logs normales en Azure.
- [ ] Mantener y revisar `rejected_file`, `upload_failed`, `processing_trigger_failed`.
- [ ] Anadir `error_type` en errores de ingesta.
- [ ] En procesamiento, asegurar:
  - `indexing_completed`;
  - `indexing_degraded`;
  - `indexing_failed`.
- [ ] En RAG, asegurar:
  - `rag_search_completed`;
  - `rag_search_degraded`;
  - `rag_search_failed`.
- [ ] No registrar texto completo del CV, prompts completos ni respuestas completas del LLM.
- [ ] Medir `duration_ms` en indexacion completa y busqueda RAG completa.

Criterio de cierre:

- Un `doc_id` permite reconstruir el flujo upload -> Blob -> processing -> chunks -> embeddings -> Qdrant.

### Integrante 4 - KQL, alertas, frontend y validacion E2E

Zona principal de trabajo: documentacion operativa, queries y pruebas.

Archivos previstos:

- `docs/kql/mvp-observability.kql`
- `MONITORIZACION_LOGGING_AZURE_PLAN.md`, solo para actualizar evidencias.
- `TalentRefineAI-Frontend/utils/api_client.py`, solo si hace falta propagar `X-Request-ID` desde frontend.
- `TalentRefineAI-Frontend/README.md`, solo si se documenta una variable o URL.

Checklist:

- [ ] Crear `docs/kql/mvp-observability.kql` con las queries de:
  - logs recientes;
  - errores backend;
  - latencia por endpoint;
  - flujo por `doc_id`;
  - KPIs MVP;
  - fallos Qdrant/OpenAI;
  - reinicios Container Apps.
- [ ] Crear o documentar 4 alertas minimas:
  - errores 5xx;
  - fallo de indexacion;
  - fallo de busqueda RAG;
  - reinicios de contenedor.
- [ ] Ejecutar validacion de health:
  - frontend `/_stcore/health`;
  - backends `/health`.
- [ ] Ejecutar validacion de upload e indexacion con un CV PDF.
- [ ] Ejecutar validacion de busqueda RAG.
- [ ] Provocar un fallo controlado y guardar evidencia.
- [ ] Recopilar capturas o salidas de Log Analytics para la entrega.

Criterio de cierre:

- Existe evidencia de un flujo correcto y un fallo controlado consultables en Log Analytics.

### Coordinacion para no pisarse

- Integrante 1 trabaja solo en `TalentRefineAI-Infra`.
- Integrante 2 trabaja solo en `app/shared/*` y `main.py` de cada servicio.
- Integrante 3 trabaja solo en `services/*` y, si hace falta, repositorios backend concretos.
- Integrante 4 trabaja principalmente en `docs/kql/*`, validacion y documentacion.
- Antes de tocar un archivo fuera de la zona asignada, avisar al grupo.
- Hacer PRs pequenos por integrante.
- Orden recomendado de merge:
  1. Infra.
  2. Logging compartido.
  3. Eventos backend.
  4. KQL, alertas y evidencias.

## Definition of Done MVP

La observabilidad MVP queda cerrada cuando:

- `ContainerAppConsoleLogs_CL` recibe logs de frontend, ingest, processing y rag.
- Los logs backend son JSON parseable.
- No se emiten logs `INFO` en Azure.
- Las requests normales no generan logs; solo requests lentas, warnings y errores.
- Un `doc_id` permite seguir upload, procesamiento e indexacion.
- Una busqueda permite ver finalizacion exitosa o fallo/degradacion.
- Existen KQL guardadas para errores, latencia, KPIs y fallos Qdrant/OpenAI.
- Hay alertas para 5xx, fallo de indexacion, fallo de busqueda y reinicios.
- La validacion end-to-end tiene evidencia de un caso correcto y un fallo controlado.
