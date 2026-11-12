#!/bin/sh
# Apply migrations, then hand over to the CMD (uvicorn by default).
#
# NOVA_AUTO_CREATE_TABLES is false in the image, so the schema must come from
# Alembic. Set NOVA_RUN_MIGRATIONS=0 to skip (e.g. when a job already migrated).
set -eu

if [ "${NOVA_RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "nova: applying database migrations"
    alembic upgrade head
fi

exec "$@"
