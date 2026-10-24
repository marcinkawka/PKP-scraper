# Local Postgres for testing. Credentials come from .env (DB_* vars).
include .env
export

.PHONY: help up down reset logs psql deploy revert verify status rebuild

help:
	@echo "up       start local Postgres (port $(DB_PORT)) and wait until healthy"
	@echo "down     stop container (data kept)"
	@echo "reset    stop container and delete data volume"
	@echo "logs     follow Postgres logs"
	@echo "psql     open psql shell in container"
	@echo "deploy   sqitch deploy to local db"
	@echo "revert   sqitch revert local db"
	@echo "verify   sqitch verify local db"
	@echo "status   sqitch status of local db"
	@echo "rebuild  reset + up + deploy"

up:
	docker compose up -d --wait

down:
	docker compose down

reset:
	docker compose down -v

logs:
	docker compose logs -f db

psql:
	docker compose exec db psql -U $(DB_USER) -d $(DB_NAME)

deploy: up
	./sqitch.sh deploy

revert:
	./sqitch.sh revert -y

verify:
	./sqitch.sh verify

status:
	./sqitch.sh status

rebuild: reset deploy
