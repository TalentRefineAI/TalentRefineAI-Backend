# Makefile - TalentRefine AI Backend
#
# Uso:
#   make dev       ->  levanta los 3 servicios en tu maquina con Azure real (requiere az login)
#                      hot-reload activo; ideal para desarrollo con credenciales Azure
#   make dev-fake  ->  levanta el stack completo en modo local fake sin credenciales Azure
#   make stop      ->  para los 3 servicios lanzados por make dev
#   make start     ->  levanta todo en modo produccion (imagenes construidas, sin mounts)
#   make build     ->  solo construye las imagenes Docker sin levantar
#   make down      ->  para y elimina los contenedores
#   make logs      ->  muestra los logs en tiempo real de todos los servicios
#   make ps        ->  estado de los contenedores

PYTHON := .venv/Scripts/python
UVICORN := .venv/Scripts/uvicorn

.PHONY: dev dev-fake stop ingest processing rag start build down logs ps

# Levanta solo el ingest-service. Solo requiere az login + Blob Storage real.
# QDRANT_URL, KEY_VAULT_URL y AZURE_OPENAI_ENDPOINT pueden ser valores dummy en .env
ingest:
	@echo "  ingest-service  http://127.0.0.1:8001/docs   http://127.0.0.1:8001/health"
	PYTHONPATH=app $(UVICORN) ingest_service.main:app --port 8001 --reload

# Levanta solo el processing-service. Requiere az login + Blob Storage + Qdrant + Azure OpenAI.
processing:
	@echo "  processing-service  http://127.0.0.1:8002/docs   http://127.0.0.1:8002/health"
	PYTHONPATH=app $(UVICORN) processing_service.main:app --port 8002 --reload

# Levanta solo el rag-service. Requiere az login + Qdrant + Azure OpenAI + Key Vault.
rag:
	@echo "  rag-service  http://127.0.0.1:8003/docs   http://127.0.0.1:8003/health"
	PYTHONPATH=app $(UVICORN) rag_service.main:app --port 8003 --reload

# Levanta los 3 microservicios directamente en la maquina con hot-reload.
# Lee las credenciales de .env. Requiere az login o variables de entorno de Azure.
dev:
	@echo "Arrancando stack en local (CTRL+C para parar)..."
	@PYTHONPATH=app $(UVICORN) ingest_service.main:app --port 8001 --reload &
	@PYTHONPATH=app $(UVICORN) processing_service.main:app --port 8002 --reload &
	@PYTHONPATH=app $(UVICORN) rag_service.main:app --port 8003 --reload &
	@echo ""
	@echo "  ingest-service     http://127.0.0.1:8001/docs   http://127.0.0.1:8001/health"
	@echo "  processing-service http://127.0.0.1:8002/docs   http://127.0.0.1:8002/health"
	@echo "  rag-service        http://127.0.0.1:8003/docs   http://127.0.0.1:8003/health"
	@echo ""
	@wait

# Para los procesos uvicorn lanzados por make dev
stop:
	@pkill -f "uvicorn ingest_service.main" 2>/dev/null || true
	@pkill -f "uvicorn processing_service.main" 2>/dev/null || true
	@pkill -f "uvicorn rag_service.main" 2>/dev/null || true
	@echo "Servicios parados."

# Levanta el stack completo en modo local fake, sin Azure Blob/OpenAI/Key Vault
dev-fake:
	DEV_MODE=1 docker compose -f docker-compose.yml -f docker-compose.local-fake.yml up --build

# Levanta todos los servicios en modo produccion (sin hot-reload ni mounts de codigo)
start:
	docker compose up --build

# Construye las imagenes Docker sin levantarlas
build:
	docker compose build

# Para el stack Docker
down:
	docker compose down

# Logs en tiempo real del stack Docker
logs:
	docker compose logs -f

# Estado de los contenedores Docker
ps:
	docker compose ps
