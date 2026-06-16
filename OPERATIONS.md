# OPERATIONS.md — Guía de Gestión del Sistema TalentRefineAI

Referencia operacional completa para gestionar, desplegar, mantener y destruir la infraestructura
del sistema TalentRefineAI en Azure.

---

## 0. Inventario de recursos

| Recurso | Nombre | Región |
|---------|--------|--------|
| Resource Group | `rg-talentrefine-dev-spc` | Spain Central |
| Azure Container Registry | `crtalentrefinedevspc` | Spain Central |
| Container App — Frontend | `ca-frontend-talentrefine-dev-spc` | Spain Central |
| Container App — Ingest | `ca-ingesta-talentrefine-dev-spc` | Spain Central |
| Container App — RAG | `ca-rag-talentrefine-dev-spc` | Spain Central |
| Container App — Processing | `ca-proc-talentrefine-dev-spc` | Spain Central |
| Container Apps Environment | (environment del RG) | Spain Central |
| Azure OpenAI | `oai-talentrefine-dev-spc` (o similar) | — |
| Key Vault | `kv-talentrefine-dev-spc` (o similar) | Spain Central |
| Storage Account | `sttalentrefinedevspc` (o similar) | Spain Central |
| Qdrant | Qdrant Cloud (externo) | — |

**FQDNs de Container Apps:**
```
Frontend:   ca-frontend-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io
Ingest:     ca-ingesta-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io
RAG:        ca-rag-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io
Processing: ca-proc-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io
```

**Variables útiles (pegar en terminal antes de ejecutar comandos):**
```bash
RG=rg-talentrefine-dev-spc
ACR=crtalentrefinedevspc
CA_FRONTEND=ca-frontend-talentrefine-dev-spc
CA_INGEST=ca-ingesta-talentrefine-dev-spc
CA_RAG=ca-rag-talentrefine-dev-spc
CA_PROC=ca-proc-talentrefine-dev-spc
```

---

## 1. Azure Container Registry (ACR)

### Login
```bash
az acr login --name $ACR
```

### Construir y subir imágenes

Ejecutar desde la raíz del repositorio backend:
```bash
# Ingest Service
az acr build --registry $ACR   --image ingest-service:latest   --file app/ingest_service/Dockerfile .   --resource-group $RG

# Processing Service
az acr build --registry $ACR   --image processing-service:latest   --file app/processing_service/Dockerfile .   --resource-group $RG

# RAG Service
az acr build --registry $ACR   --image rag-service:latest   --file app/rag_service/Dockerfile .   --resource-group $RG
```

Ejecutar desde la raíz del repositorio frontend:
```bash
az acr build --registry $ACR   --image rag-hr-frontend:latest   --file Dockerfile .   --resource-group $RG
```

### Listar repositorios e imágenes
```bash
az acr repository list --name $ACR --output table

# Ver tags de una imagen
az acr repository show-tags --name $ACR --repository rag-service --output table
az acr repository show-tags --name $ACR --repository rag-hr-frontend --output table
```

### Eliminar tags o manifiestos antiguos
```bash
# Eliminar un tag específico
az acr repository delete --name $ACR --image rag-service:old-tag --yes

# Purgar imágenes con más de 7 días que no sean 'latest'
az acr run --registry $ACR --cmd   "acr purge --filter 'rag-service:.*' --ago 7d --untagged --keep 3" /dev/null
```

---

## 2. Azure Container Apps (ACA)

### Ver revisiones activas
```bash
az containerapp revision list --name $CA_RAG --resource-group $RG --output table
```

### Desplegar nueva revisión (actualizar imagen)
```bash
# Crear nueva revisión con suffix personalizado
az containerapp update   --name $CA_RAG   --resource-group $RG   --image $ACR.azurecr.io/rag-service:latest   --revision-suffix v20260605

az containerapp update   --name $CA_PROC   --resource-group $RG   --image $ACR.azurecr.io/processing-service:latest   --revision-suffix v20260605

az containerapp update   --name $CA_INGEST   --resource-group $RG   --image $ACR.azurecr.io/ingest-service:latest   --revision-suffix v20260605


# Lo mismo para frontend
az containerapp update   --name $CA_FRONTEND   --resource-group $RG   --image $ACR.azurecr.io/rag-hr-frontend:latest   --revision-suffix v20260605
```

### Ver tráfico entre revisiones
```bash
az containerapp ingress traffic show --name $CA_RAG --resource-group $RG
```

### Redistribuir tráfico (canary / rollback)
```bash
# Enviar 100% a una revisión específica (rollback)
az containerapp ingress traffic set   --name $CA_RAG   --resource-group $RG   --revision-weight ca-rag-talentrefine-dev-spc--prodfix=100

# Canary: 90% stable, 10% nueva
az containerapp ingress traffic set   --name $CA_RAG   --resource-group $RG   --revision-weight ca-rag-talentrefine-dev-spc--prodfix=90                     ca-rag-talentrefine-dev-spc--v20260605=10
```

### Desactivar/activar una revisión
```bash
az containerapp revision deactivate   --name $CA_RAG   --resource-group $RG   --revision ca-rag-talentrefine-dev-spc--old-revision

az containerapp revision activate   --name $CA_RAG   --resource-group $RG   --revision ca-rag-talentrefine-dev-spc--prodfix
```

### Ver logs en tiempo real

> **Cómo monitorizar servicios scale-to-zero:** los logs de CONSOLA solo se sirven desde una
> réplica viva. Los servicios escalan a cero cuando están inactivos, así que para capturar logs
> hay que **dejar `--follow` corriendo ANTES de generar la actividad** (subir CV, buscar, etc.):
> al llegar el tráfico la réplica arranca y los logs empiezan a fluir en tu terminal.
> Para logs históricos cuando el servicio ya está a cero, ver §9 (Log Analytics).

```bash
# 1) MONITOREO EN VIVO — deja esto corriendo y luego actúa en la app (sube un CV, busca, etc.)
#    Es la forma fiable de ver el procesamiento de CVs de principio a fin.
az containerapp logs show --name $CA_PROC --resource-group $RG --follow --tail 100

# Lo mismo para rag-service (búsquedas) o ingest (subidas)
az containerapp logs show --name $CA_RAG    --resource-group $RG --follow --tail 50
az containerapp logs show --name $CA_INGEST --resource-group $RG --follow --tail 50

# 2) EVENTOS DE SISTEMA — funciona SIEMPRE, incluso con 0 réplicas
#    (scaling KEDA, arranques, crashes, "ManuallyStopped", OOM, etc.)
az containerapp logs show --name $CA_PROC --resource-group $RG --type system --tail 30

# 3) Snapshot puntual (sin --follow): solo devuelve algo si hay réplica activa en ese instante
az containerapp logs show --name $CA_RAG --resource-group $RG --tail 50
```

> ⚠️ `--type console` (por defecto) sin `--follow` y con 0 réplicas devuelve
> `ERROR: Could not find a replica for this app`. No es un fallo de la app: es que está dormida.
> Usa `--follow` (opción 1) o `--type system` (opción 2).

### Reiniciar réplicas (forzar restart sin nueva revisión)
```bash
az containerapp revision restart   --name $CA_RAG   --resource-group $RG   --revision ca-rag-talentrefine-dev-spc--prodfix
```

### Actualizar variables de entorno
```bash
az containerapp update   --name $CA_RAG   --resource-group $RG   --set-env-vars MY_VAR=new-value
```

### Escalar manualmente (min/max replicas)
```bash
az containerapp update   --name $CA_RAG   --resource-group $RG   --min-replicas 1   --max-replicas 3
```

### Verificar estado HTTP de todos los servicios
```bash
for FQDN in   "ca-frontend-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io"   "ca-ingesta-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io/health"   "ca-rag-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io/health"   "ca-proc-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io/health"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "https://$FQDN")
  echo "$CODE  $FQDN"
done
```

---

## 3. Azure Key Vault

### Listar secretos
```bash
az keyvault secret list --vault-name kv-talentrefine-dev-spc --output table
```

### Leer el valor de un secreto
```bash
az keyvault secret show   --vault-name kv-talentrefine-dev-spc   --name azure-openai-api-key   --query value -o tsv
```

### Crear o actualizar un secreto
```bash
az keyvault secret set   --vault-name kv-talentrefine-dev-spc   --name qdrant-api-key   --value "NEW_VALUE_HERE"
```

### Rotar un secreto (nueva versión)
```bash
# Equivalente a set, Key Vault guarda historial de versiones automáticamente
az keyvault secret set   --vault-name kv-talentrefine-dev-spc   --name azure-openai-api-key   --value "ROTATED_KEY"
```

---

## 4. Azure Blob Storage

### Listar contenedores
```bash
az storage container list   --account-name sttalentrefinedevspc   --auth-mode login   --output table
```

### Listar blobs en un contenedor
```bash
az storage blob list   --account-name sttalentrefinedevspc   --container-name cv-uploads   --auth-mode login   --output table
```

### Eliminar todos los blobs (limpiar CVs)
```bash
# Mediante la API del ingest-service (forma recomendada):
curl -X DELETE "https://ca-ingesta-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io/upload/all"

# O directamente con az cli:
az storage container delete   --account-name sttalentrefinedevspc   --name cv-uploads   --auth-mode login
# Recrear:
az storage container create   --account-name sttalentrefinedevspc   --name cv-uploads   --auth-mode login
```

---

## 5. Azure OpenAI

### Ver deployments disponibles
```bash
az cognitiveservices account deployment list   --name oai-talentrefine-dev-spc   --resource-group $RG   --output table
```

### Ver cuota y uso de una región
```bash
az cognitiveservices usage list --location spaincentral --output table
```

### Ver detalles de un deployment específico
```bash
az cognitiveservices account deployment show   --name oai-talentrefine-dev-spc   --resource-group $RG   --deployment-name gpt-4o-mini
```

---

## 6. Qdrant

### Via REST API (requiere API key)

```bash
QDRANT_URL="https://xxx.qdrant.io"
QDRANT_KEY="your-api-key"

# Estado del cluster
curl -s -H "api-key: $QDRANT_KEY" "$QDRANT_URL/"

# Listar colecciones
curl -s -H "api-key: $QDRANT_KEY" "$QDRANT_URL/collections"

# Ver info de la colección
curl -s -H "api-key: $QDRANT_KEY" "$QDRANT_URL/collections/talentrefine-candidates"

# Estadísticas (num vectores, puntos, etc.)
curl -s -H "api-key: $QDRANT_KEY" "$QDRANT_URL/collections/talentrefine-candidates" | python -m json.tool
```

### Via API del rag-service (forma recomendada en producción)

```bash
BASE="https://ca-rag-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io"

# Listar candidatos indexados
curl "$BASE/search/candidates"

# Eliminar toda la colección
curl -X DELETE "$BASE/search/candidates"
```

---

## 7. Flujo de despliegue completo (nueva versión)

```bash
SUFFIX="v$(date +%Y%m%d)"

# --- Backend (solo servicios modificados) ---
cd "C:/Users/MSI/Cloud e IA/Proyectos/TFM/TalentRefineAI-Backend"
git add -A && git commit -m "feat: descripción del cambio" && git push

# Construir imagen
az acr build --registry $ACR   --image rag-service:latest   --file app/rag_service/Dockerfile .   --resource-group $RG

# Desplegar nueva revisión
az containerapp update   --name $CA_RAG   --resource-group $RG   --image $ACR.azurecr.io/rag-service:latest   --revision-suffix $SUFFIX

# --- Frontend ---
cd "C:/Users/MSI/Cloud e IA/Proyectos/TFM/TalentRefineAI-Frontend"
git add -A && git commit -m "fix: descripción del cambio" && git push

az acr build --registry $ACR   --image rag-hr-frontend:latest   --file Dockerfile .   --resource-group $RG

az containerapp update   --name $CA_FRONTEND   --resource-group $RG   --image $ACR.azurecr.io/rag-hr-frontend:latest   --revision-suffix $SUFFIX

# --- Verificar ---
curl -s -o /dev/null -w "%{http_code}"   "https://ca-frontend-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io/"
```

---

## 8. Terraform — Gestión de infraestructura

El código Terraform está en el repositorio `TalentRefineAI-Infra`.

```bash
cd "C:/Users/MSI/Cloud e IA/Proyectos/TalentRefineAI-Infra"

# Ver estado actual
terraform show

# Planificar cambios
terraform plan -out=tfplan

# Aplicar cambios
terraform apply tfplan

# Destruir TODA la infraestructura (irreversible)
terraform destroy
# Se pedirá confirmación. Escribe "yes" solo si estás seguro.
```

> ⚠️ `terraform destroy` elimina **todos los recursos** del resource group incluyendo datos en Blob Storage y la colección Qdrant.

### Reconstrucción completa desde cero

```bash
# 1. Destruir
cd "C:/Users/MSI/Cloud e IA/Proyectos/TalentRefineAI-Infra"
terraform destroy

# 2. Reconstruir infraestructura
terraform apply

# 3. Repopular secretos en Key Vault (ver sección 3)

# 4. Construir todas las imágenes en ACR (ver sección 1)

# 5. Desplegar todas las Container Apps (ver sección 2)

# 6. Re-indexar CVs subiendo desde el frontend
```

---

## 9. Monitoreo y observabilidad

### Ver logs históricos always-on (Log Analytics)

> ⚠️ **Estado actual: NO configurado.** El Container Apps Environment `cae-talentrefine-dev-spc`
> tiene el destino de logs vacío (`appLogsConfiguration.destination = ""`), así que la tabla
> `ContainerAppConsoleLogs_CL` **no existe** y los comandos KQL de abajo fallan con
> *"Failed to resolve table"*. Mientras no se active, usa el monitoreo en vivo de la §2.
>
> **Para activarlo (recomendado para monitorizar en todo momento):** vincula el entorno al
> workspace `law-talentrefine-dev-spc`. Requiere customerId + clave compartida:
>
> ```bash
> LAW_ID=710642dd-24f8-45d3-9bdd-c39da3184490
> LAW_KEY=$(az monitor log-analytics workspace get-shared-keys \
>   --resource-group $RG --workspace-name law-talentrefine-dev-spc \
>   --query primarySharedKey -o tsv)
> az containerapp env update --name cae-talentrefine-dev-spc --resource-group $RG \
>   --logs-destination log-analytics \
>   --logs-workspace-id $LAW_ID --logs-workspace-key $LAW_KEY
> ```
>
> 📌 La infra está gestionada por Terraform (repo `TalentRefineAI-Infra`). Para que el cambio
> **persista** y no se revierta en el próximo `terraform apply`, añádelo también al módulo
> `monitoring`/`compute` (bloque `log_analytics_workspace` del `azurerm_container_app_environment`).
> Los logs tardan ~2-5 min en empezar a aparecer tras activarlo.

Una vez activado, el `--workspace` usa el **ID** (customerId), no el nombre:

```bash
LAW_ID=710642dd-24f8-45d3-9bdd-c39da3184490

# Logs del rag-service (últimas 50 entradas)
az monitor log-analytics query --workspace $LAW_ID \
  --analytics-query "ContainerAppConsoleLogs_CL | where ContainerAppName_s == 'ca-rag-talentrefine-dev-spc' | order by TimeGenerated desc | take 50" \
  --output table

# Errores en todos los servicios (última hora)
az monitor log-analytics query --workspace $LAW_ID \
  --analytics-query "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(1h) | where Log_s contains 'error' or Log_s contains 'failed' | order by TimeGenerated desc | take 100" \
  --output table
```

### Consultas KQL útiles

> 📌 El catálogo completo y mantenido de queries vive en `docs/kql/mvp-observability.kql`
> (lo gestiona el reparto de observabilidad MVP, ver `MONITORIZACION_LOGGING_AZURE_PLAN.md`).
> Usa **los nombres de evento reales** emitidos por el backend; los ejemplos de abajo ya están
> alineados con ellos.

Eventos de negocio reales emitidos por el backend (logs JSON parseables por `parse_json(Log_s)`):

| Servicio | Éxito final | Degradación | Error |
|----------|-------------|-------------|-------|
| ingest | `document_upload_completed` | `rejected_file` | `upload_failed`, `processing_trigger_failed` |
| processing | `indexing_completed` | `indexing_degraded` | `indexing_failed` |
| rag | `rag_search_completed` | `rag_search_degraded` | `rag_search_failed` |

```kql
// Latencia de búsquedas RAG completadas
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ca-rag-talentrefine-dev-spc"
| extend log = parse_json(Log_s)
| where tostring(log.event) == "rag_search_completed"
| project TimeGenerated, duration_ms = todouble(log.duration_ms), results_count = toint(log.results_count)

// Errores y degradaciones de búsqueda RAG
ContainerAppConsoleLogs_CL
| extend log = parse_json(Log_s)
| where tostring(log.event) in ("rag_search_failed", "rag_search_degraded")
| project TimeGenerated, event = tostring(log.event), reason = tostring(log.reason), error = tostring(log.error)

// Documentos indexados por día
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ca-proc-talentrefine-dev-spc"
| extend log = parse_json(Log_s)
| where tostring(log.event) == "indexing_completed"
| summarize count() by bin(TimeGenerated, 1d)
```

> ⚠️ Hasta que el nivel custom `SUCCESS=25` esté registrado en `app/shared/logging.py`, los
> eventos `*_completed` se emiten a nivel `info` (no `success`). Filtra por `event`, no por `level`,
> mientras tanto.

### Health checks rápidos
```bash
# Todos los servicios
for CA in $CA_FRONTEND $CA_INGEST $CA_RAG $CA_PROC; do
  echo "--- $CA ---"
  az containerapp show --name $CA --resource-group $RG     --query "properties.latestRevisionFqdn" -o tsv
  az containerapp revision list --name $CA --resource-group $RG     --query "[?properties.active].{name:name,replicas:properties.replicas,traffic:properties.trafficWeight}"     --output table
done
```

---

## 10. Riesgos conocidos

### 10.1 Ingesta asíncrona vs scale-to-zero (riesgo latente, NO resuelto)

**Qué pasa:** `POST /upload` (ingest-service) valida el PDF, responde `202 Accepted` y **delega el
trabajo a un `BackgroundTasks.add_task`**: la subida a Blob y el disparo de `POST /processing/{doc_id}`
ocurren *después* de responder, en segundo plano dentro de la misma réplica
([app/ingest_service/services/ingest_service.py](app/ingest_service/services/ingest_service.py)).

**Por qué es un riesgo en Azure:** las Container Apps corren con `min_replicas=0`. Si la réplica de
**ingest** escala a cero tras devolver el `202` (sin peticiones HTTP entrantes que la mantengan viva),
el BackgroundTask puede ser terminado por KEDA antes de completar la subida o el disparo de processing.
El CV quedaría aceptado pero **nunca indexado**, sin error visible para el usuario. Es exactamente el
patrón scale-to-zero que ya motivó hacer **síncrono** el processing-service.

**Síntoma observable:** en Log Analytics aparece el `202` pero **no** aparece
`document_upload_completed` ni `indexing_completed` para ese `doc_id`; o aparece
`processing_trigger_failed`/`ConnectError` si la réplica de processing tampoco estaba lista.

**Mitigaciones posibles (a decidir por el equipo, fuera del MVP de observabilidad):**
1. Hacer la ingesta **síncrona**: subir a Blob + disparar processing *antes* de responder (cambia el
   contrato a una respuesta más lenta pero fiable). Es la opción coherente con el diseño del
   processing-service.
2. Mantener `min_replicas=1` en ingest mientras haya tráfico esperado (coste extra, no FinOps).
3. Mover el trabajo a una **cola durable** (Azure Storage Queue / Service Bus) + worker, que sobrevive
   al reciclado de réplicas. Es la solución productiva real, pero excede el alcance del TFM MVP.

> Mientras no se mitigue, la observabilidad MVP **detecta** el problema (ausencia de
> `document_upload_completed`/`indexing_completed` para un `doc_id` con `202`), que es justamente lo que
> permite diagnosticarlo en una demo o entrega.

### 10.2 Incidente resuelto — `404 DeploymentNotFound` en metadatos (`indexing_degraded`)

**Fecha:** 2026-06-16.

**Síntoma:** al subir varios CVs, `processing-service` emite `indexing_degraded`
(`reason=metadata_llm_failed`) con `error: Error code: 404 ... DeploymentNotFound`. Los CVs **se
indexan igual** pero con metadatos solo-regex (sin nombre/ubicación/skills/exp del LLM); la búsqueda
RAG muestra `name` vacío, `exp_years=0` y `location=null`.

**Causa raíz (NO era el script ni carga/429):** al Container App de processing (`ca-proc`) le faltaba
la variable `AZURE_OPENAI_CHAT_DEPLOYMENT`. El backend caía a su default `gpt-4o-mini`
(`app/shared/config.py`), que **no existe** como deployment — el real en Azure OpenAI se llama
`gpt-deployment`. Los embeddings funcionaban porque `AZURE_OPENAI_EMBEDDING_DEPLOYMENT=embedding-deployment`
sí estaba puesto. `ca-rag` ya tenía la var correcta; solo `ca-proc` divergía.

**Diagnóstico (read-only):**
```bash
# Deployments reales:
az cognitiveservices account deployment list --name oai-talentrefine-dev-spc \
  --resource-group rg-talentrefine-dev-spc \
  --query "[].{deployment:name, model:properties.model.name}" -o table
# → embedding-deployment / gpt-deployment   (NO 'gpt-4o-mini')

# Env vars del Container App (comparar ca-proc vs ca-rag):
az containerapp show --name ca-proc-talentrefine-dev-spc -g rg-talentrefine-dev-spc \
  --query "properties.template.containers[0].env[?contains(name,'OPENAI')]" -o table
```

**Fix inmediato (aplicado, crea nueva revisión):**
```bash
az containerapp update --name ca-proc-talentrefine-dev-spc \
  --resource-group rg-talentrefine-dev-spc \
  --set-env-vars AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-deployment
```

**Fix persistente (PENDIENTE, Integrante 1):** añadir `AZURE_OPENAI_CHAT_DEPLOYMENT = "gpt-deployment"`
al Container App de processing en Terraform (`TalentRefineAI-Infra`), espejo de `ca-rag`. Si no, el
`az update` se revierte en el próximo `terraform apply`.

**Reproceso de CVs degradados:** los blobs ya están en Storage; re-disparar processing es idempotente
(el upsert por `candidate_id` sobrescribe). `ca-proc` y `ca-rag` tienen ingress externo:
```bash
PROC=https://ca-proc-talentrefine-dev-spc.greenwave-4d796cb5.spaincentral.azurecontainerapps.io
curl -X POST -G "$PROC/processing/<doc_id>" --data-urlencode "blob_name=<doc_id>/<fichero>.pdf"
```
> ⚠️ **Gotcha al scriptar el reproceso en Windows/Git Bash:** `az ... -o tsv` emite líneas con CRLF y
> `mapfile -t` conserva el `\r` final → el `blob_name` queda como `...pdf\r`, que al URL-encodearse es
> `...pdf%0D` → Azure Storage responde `500 InvalidUri` ("The requested URI does not represent any
> resource on the server"). Limpia el `\r`: `az ... -o tsv | tr -d '\r'`.

**Verificación de cierre:** `GET /search/candidates` y `POST /search` deben devolver `name`, `email`,
`exp_years` (>0 cuando aplica) y `location` poblados; `indexing_degraded`/`metadata_llm_failed` cae a 0
para nuevas ingestas.
