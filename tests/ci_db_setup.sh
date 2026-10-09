#!/usr/bin/env bash
# Build the integration-test database from tests/fixtures/gdelt_slice.sql.gz (a real slice of the
# 2024 extract: Texas, Ontario and Estado de México for 2024-02-26..03-03, plus the Fort Worth event
# of 2024-01-09), then derive every precomputed table with the repository's own backfill.
#
#   DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=root DB_PASSWORD=... DB_NAME=gdelt tests/ci_db_setup.sh
#
# MYSQL_CLIENT overrides the client command (e.g. "docker exec -i some_container mysql").
set -euo pipefail
cd "$(dirname "$0")/.."

: "${DB_HOST:=127.0.0.1}" "${DB_PORT:=3306}" "${DB_USER:=root}" "${DB_PASSWORD:=rootpassword}" "${DB_NAME:=gdelt}"
export DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME
MYSQL=${MYSQL_CLIENT:-"mysql -h $DB_HOST -P $DB_PORT"}
SQL="$MYSQL -u$DB_USER -p$DB_PASSWORD --default-character-set=utf8mb4"

$SQL -e "CREATE DATABASE IF NOT EXISTS $DB_NAME CHARACTER SET utf8mb4"
gzip -dc tests/fixtures/gdelt_slice.sql.gz | $SQL "$DB_NAME"
$SQL "$DB_NAME" < db_scripts/precompute_tables.sql
python db_scripts/backfill_precompute.py --start 2024-01-09 --end 2024-03-03
python db_scripts/backfill_precompute.py --verify 500
