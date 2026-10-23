# Keep PostgreSQL 17 and its original data-directory layout.
FROM postgres:17-bookworm

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install TimescaleDB from its signed repository and PostGIS from PGDG.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 python3-venv \
    && curl --fail --silent --show-error --location \
       https://packagecloud.io/timescale/timescaledb/gpgkey \
       --output /tmp/timescaledb-key.asc \
    && gpg --batch --yes --dearmor \
       --output /usr/share/keyrings/timescaledb.gpg /tmp/timescaledb-key.asc \
    && echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ bookworm main" \
       > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       timescaledb-2-postgresql-17 \
       postgresql-17-postgis-3 \
       postgresql-17-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/* /tmp/timescaledb-key.asc

COPY docker/bootstrap-requirements.txt /opt/weather/bootstrap-requirements.txt
RUN python3 -m venv /opt/weather/venv \
    && /opt/weather/venv/bin/pip install --no-cache-dir -r /opt/weather/bootstrap-requirements.txt

ENV PYTHONPATH=/opt/weather/src
COPY src/as_db/ /opt/weather/src/as_db/
COPY scripts/seed_history.py /opt/weather/seed_history.py
COPY stations.csv variables.csv /opt/weather/history/
COPY data/ /opt/weather/history/data/
COPY docker/history-imports.json /opt/weather/history/imports.json

# These scripts run only when PostgreSQL initializes an empty data directory.
COPY docker/init/ /docker-entrypoint-initdb.d/
COPY scripts/create_tables.py /opt/weather/create_tables.py

EXPOSE 5432

CMD ["postgres", "-c", "timezone=UTC", "-c", "shared_preload_libraries=timescaledb,pg_stat_statements", "-c", "password_encryption=scram-sha-256", "-c", "max_locks_per_transaction=1024"]
