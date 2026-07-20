#!/bin/sh
set -eu

# Schemat bazy jest częścią kontraktu uruchomieniowego aplikacji. Kontener nie
# może zgłaszać gotowości, dopóki wszystkie wersjonowane migracje nie zostaną
# zastosowane do tej samej bazy, z której korzysta API.
alembic upgrade head

exec "$@"
