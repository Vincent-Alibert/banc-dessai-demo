# Image source épinglée, sans SDK LLM, code de simulation copié dans l'image.
FROM apache/airflow:3.3.2-python3.12@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8     DBT_SEND_ANONYMOUS_USAGE_STATS=false

# uv 0.12.9, celui qui a écrit uv.lock ; appelé par son chemin, car l'image Airflow fournit déjà un autre uv.
COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /bin/uv

USER root
RUN mkdir -p /opt/banc-venv /opt/dbt-venv /opt/banc-dependances  && chown -R airflow:0 /opt/banc-venv /opt/dbt-venv /opt/banc-dependances
# Dépendances seules d'abord (cache des couches) : pyproject.toml et uv.lock sont l'unique source des versions.
COPY --chown=airflow:0 pyproject.toml uv.lock /opt/banc-dependances/
USER airflow
WORKDIR /opt/banc-dependances
# Venvs séparés de celui d'Airflow, qui reste intact (ses contraintes tiennent) ; versions exactes de uv.lock.
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_NO_CACHE=1
RUN UV_PROJECT_ENVIRONMENT=/opt/banc-venv /bin/uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python  && UV_PROJECT_ENVIRONMENT=/opt/dbt-venv /bin/uv sync --frozen --no-default-groups --only-group dbt --no-install-project --python /usr/local/bin/python

COPY --chown=airflow:0 banc /opt/banc/banc
COPY --chown=airflow:0 entrepot /opt/banc/entrepot
COPY --chown=airflow:0 dbt /opt/banc/dbt
COPY --chown=airflow:0 demo /opt/banc/demo
COPY --chown=airflow:0 personas /opt/banc/personas
COPY --chown=airflow:0 infra /opt/banc/infra
COPY --chown=airflow:0 airflow/dags /opt/airflow/dags
WORKDIR /opt/banc
