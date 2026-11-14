# Image source épinglée, sans SDK LLM, code de simulation copié dans l'image.
FROM apache/airflow:3.3.2-python3.12@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8     DBT_SEND_ANONYMOUS_USAGE_STATS=false

USER root
RUN mkdir -p /opt/banc-venv /opt/dbt-venv /opt/banc-dependances  && chown -R airflow:0 /opt/banc-venv /opt/dbt-venv /opt/banc-dependances
COPY --chown=airflow:0 requirements.txt /opt/banc-dependances/banc.txt
COPY --chown=airflow:0 dbt/requirements.txt /opt/banc-dependances/dbt.txt
USER airflow
RUN python -m venv /opt/banc-venv  && /opt/banc-venv/bin/pip install --no-cache-dir -r /opt/banc-dependances/banc.txt  && python -m venv /opt/dbt-venv  && /opt/dbt-venv/bin/pip install --no-cache-dir -r /opt/banc-dependances/dbt.txt

COPY --chown=airflow:0 banc /opt/banc/banc
COPY --chown=airflow:0 entrepot /opt/banc/entrepot
COPY --chown=airflow:0 dbt /opt/banc/dbt
COPY --chown=airflow:0 demo /opt/banc/demo
COPY --chown=airflow:0 personas /opt/banc/personas
COPY --chown=airflow:0 infra /opt/banc/infra
COPY --chown=airflow:0 airflow/dags /opt/airflow/dags
WORKDIR /opt/banc
