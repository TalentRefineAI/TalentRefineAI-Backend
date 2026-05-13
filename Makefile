# Makefile – TalentRefine AI Backend
# Equivalente a npm run dev / npm run start para el stack de microservicios
#
# Uso:
#   make dev       →  levanta todo en modo desarrollo con integraciones Azure reales
#   make dev-fake  →  levanta todo en modo local fake sin credenciales Azure
#   make start     →  levanta todo en modo producción (imágenes construidas, sin mounts)
#   make build     →  solo construye las imágenes Docker sin levantar
#   make down      →  para y elimina los contenedores
#   make logs      →  muestra los logs en tiempo real de todos los servicios
#   make ps        →  estado de los contenedores

.PHONY: dev dev-fake start build down logs ps

# Levanta todos los servicios en modo desarrollo con hot-reload y Azure real
dev:
	DEV_MODE=1 docker compose up --build

# Levanta el stack completo en modo local fake, sin Azure Blob/OpenAI/Key Vault
dev-fake:
	DEV_MODE=1 docker compose -f docker-compose.yml -f docker-compose.local-fake.yml up --build

# Levanta todos los servicios en modo producción (sin hot-reload ni mounts de código)
start:
	DEV_MODE=0 docker compose up --build

# Construye las imágenes sin levantar los contenedores
build:
	docker compose build

# Para y elimina los contenedores (los datos de Qdrant se conservan en el volumen)
down:
	docker compose down

# Muestra los logs en tiempo real de todos los servicios
logs:
	docker compose logs -f

# Muestra el estado actual de los contenedores
ps:
	docker compose ps
