FROM apache/airflow:2.8.4-python3.11

USER airflow

WORKDIR /opt/ecg-pipeline

# Install pipeline dependencies into the Airflow image so DAG tasks can import and run.
COPY --chown=airflow:root pyproject.toml README.md /opt/ecg-pipeline/
COPY --chown=airflow:root src /opt/ecg-pipeline/src

RUN pip install --no-cache-dir --user -e .
