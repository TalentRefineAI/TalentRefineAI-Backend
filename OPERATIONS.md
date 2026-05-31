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
az containerapp update   --name $CA_RAG   --resource-group $RG   --image $ACR.azurecr.io/rag-service:latest   --revision-suffix v20250601

# Lo mismo para frontend
az containerapp update   --name $CA_FRONTEND   --resource-group $RG   --image $ACR.azurecr.io/rag-hr-frontend:latest   --revision-suffix v20250601
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
az containerapp ingress traffic set   --name $CA_RAG   --resource-group $RG   --revision-weight ca-rag-talentrefine-dev-spc--prodfix=90                     ca-rag-talentrefine-dev-spc--v20250601=10
```

### Desactivar/activar una revisión
```bash
az containerapp revision deactivate   --name $CA_RAG   --resource-group $RG   --revision ca-rag-talentrefine-dev-spc--old-revision

az containerapp revision activate   --name $CA_RAG   --resource-group $RG   --revision ca-rag-talentrefine-dev-spc--prodfix
```

### Ver logs en tiempo real
```bash
az containerapp logs show   --name $CA_RAG   --resource-group $RG   --follow --tail 50

# Con filtro de revisión
az containerapp logs show   --name $CA_RAG   --resource-group $RG   --revision ca-rag-talentrefine-dev-spc--prodfix   --follow --tail 50
```

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

### Ver logs de todos los servicios (Log Analytics)
Los Container Apps envían logs a Log Analytics automáticamente.

```bash
# Logs del rag-service (últimas 50 entradas)
az monitor log-analytics query   --workspace "la-talentrefine-dev-spc"   --analytics-query "ContainerAppConsoleLogs_CL | where ContainerAppName_s == 'ca-rag-talentrefine-dev-spc' | order by TimeGenerated desc | take 50"   --output table

# Logs de errores en todos los servicios
az monitor log-analytics query   --workspace "la-talentrefine-dev-spc"   --analytics-query "ContainerAppConsoleLogs_CL | where Log_s contains 'ERROR' or Log_s contains 'error' | order by TimeGenerated desc | take 100"   --output table
```

### Consultas KQL útiles

```kql
// Latencia de peticiones /search
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ca-rag-talentrefine-dev-spc"
| where Log_s contains "search_completed"
| extend duration = extract("duration_ms=(\\d+)", 1, Log_s)
| project TimeGenerated, duration

// Errores de LLM extraction
ContainerAppConsoleLogs_CL
| where Log_s contains "llm_extraction_failed"
| project TimeGenerated, Log_s

// Documentos procesados por día
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ca-proc-talentrefine-dev-spc"
| where Log_s contains "chunks_upserted"
| summarize count() by bin(TimeGenerated, 1d)
```

### Health checks rápidos
```bash
# Todos los servicios
for CA in $CA_FRONTEND $CA_INGEST $CA_RAG $CA_PROC; do
  echo "--- $CA ---"
  az containerapp show --name $CA --resource-group $RG     --query "properties.latestRevisionFqdn" -o tsv
  az containerapp revision list --name $CA --resource-group $RG     --query "[?properties.active].{name:name,replicas:properties.replicas,traffic:properties.trafficWeight}"     --output table
done
```
