#!/bin/bash
# Se ejecuta una sola vez al crear el volumen de PostgreSQL (docker-entrypoint-initdb.d).
# POSTGRES_USER (gsti_owner) es dueño del esquema y corre las migraciones.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE gsti_api     LOGIN PASSWORD '${GSTI_API_PASSWORD}';
    CREATE ROLE gsti_analyst LOGIN PASSWORD '${GSTI_ANALYST_PASSWORD}';
    REVOKE ALL ON DATABASE ${POSTGRES_DB} FROM PUBLIC;
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO gsti_api, gsti_analyst;
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOSQL
