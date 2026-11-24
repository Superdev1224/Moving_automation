#!/bin/sh
# Nightly Postgres dump of n8n (workflows, credentials, history). Keeps 14 days.
cd /root/Moving_automation || exit 1
. ./.env
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "backups/n8n-$(date +%F).sql.gz"
find backups -name 'n8n-*.sql.gz' -mtime +14 -delete
