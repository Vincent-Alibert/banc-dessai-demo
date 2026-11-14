#!/bin/bash
# Initialisation d'un volume NEUF et isolé, avec identifiants publics réservés à cette démo.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v banc_mdp="$BANC_DB_PASSWORD" -v airflow_mdp="$AIRFLOW_DB_PASSWORD" \
  -v metabase_mdp="$METABASE_DB_PASSWORD" -v lecture_mdp="$METABASE_LECTURE_PASSWORD" <<'EOSQL'
CREATE ROLE banc LOGIN PASSWORD :'banc_mdp';
CREATE ROLE airflow LOGIN PASSWORD :'airflow_mdp';
CREATE ROLE metabase LOGIN PASSWORD :'metabase_mdp';
CREATE ROLE metabase_lecture LOGIN PASSWORD :'lecture_mdp';
ALTER ROLE metabase_lecture SET default_transaction_read_only = on;
CREATE DATABASE banc OWNER banc;
CREATE DATABASE airflow OWNER airflow;
CREATE DATABASE metabase OWNER metabase;
REVOKE ALL ON DATABASE banc FROM PUBLIC;
REVOKE ALL ON DATABASE airflow FROM PUBLIC;
REVOKE ALL ON DATABASE metabase FROM PUBLIC;
GRANT CONNECT ON DATABASE banc TO metabase_lecture;
EOSQL
