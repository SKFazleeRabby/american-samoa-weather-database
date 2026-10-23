import argparse
import os
import subprocess


SCHEMA_SQL = """
SET LOCAL search_path TO public;

CREATE TABLE IF NOT EXISTS stations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code varchar(100) NOT NULL UNIQUE,
    name varchar(255) NOT NULL,
    external_id varchar(255),
    operator varchar(255),
    latitude double precision CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision CHECK (longitude BETWEEN -180 AND 180),
    elevation_m double precision,
    timezone varchar(100) NOT NULL,
    category varchar(50) NOT NULL,
    status varchar(50) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS variables (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code varchar(100) NOT NULL UNIQUE,
    name varchar(255) NOT NULL,
    unit varchar(100) NOT NULL,
    description text,
    synoptic_code varchar(100),
    aggregation varchar(20)
);

CREATE TABLE IF NOT EXISTS sources (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id bigint NOT NULL REFERENCES stations(id),
    variable_id bigint NOT NULL REFERENCES variables(id),
    source varchar(100) NOT NULL,
    source_station_code varchar(255),
    source_variable_code varchar(255),
    api_endpoint text,
    is_active boolean NOT NULL DEFAULT true,
    UNIQUE (station_id, variable_id, source)
);

CREATE TABLE IF NOT EXISTS users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email varchar(320) NOT NULL,
    password_hash text NOT NULL,
    role varchar(50) NOT NULL DEFAULT 'user'
        CHECK (role IN ('admin', 'user')),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users (lower(email));

CREATE TABLE IF NOT EXISTS uploads (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES users(id),
    filename varchar(255) NOT NULL,
    storage_path text NOT NULL,
    file_checksum varchar(128),
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    status varchar(50) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    accepted_rows integer NOT NULL DEFAULT 0 CHECK (accepted_rows >= 0),
    rejected_rows integer NOT NULL DEFAULT 0 CHECK (rejected_rows >= 0)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES users(id),
    key_prefix varchar(100) NOT NULL,
    key_hash text NOT NULL UNIQUE,
    scopes text NOT NULL DEFAULT 'read',
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    revoked_at timestamptz,
    CHECK (expires_at IS NULL OR expires_at > created_at)
);

CREATE TABLE IF NOT EXISTS observations (
    station_id bigint NOT NULL REFERENCES stations(id),
    variable_id bigint NOT NULL REFERENCES variables(id),
    observed_at timestamptz NOT NULL,
    interval_seconds integer NOT NULL DEFAULT 0 CHECK (interval_seconds >= 0),
    local_date date,
    value double precision NOT NULL
        CHECK (value > '-Infinity'::double precision
           AND value < 'Infinity'::double precision),
    quality_flag varchar(50) NOT NULL DEFAULT 'unreviewed'
        CHECK (quality_flag IN ('unreviewed', 'valid', 'suspect', 'invalid')),
    upload_id bigint REFERENCES uploads(id),
    PRIMARY KEY (station_id, variable_id, interval_seconds, observed_at)
);

-- Existing populated ordinary tables need a separately planned migration.
SELECT create_hypertable(
    'public.observations',
    by_range('observed_at', INTERVAL '365 days'),
    if_not_exists => TRUE
);

-- Support variable-wide downloads and foreign-key lookups.
CREATE INDEX IF NOT EXISTS observations_variable_time_idx
    ON observations (variable_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS observations_upload_idx ON observations (upload_id);
CREATE INDEX IF NOT EXISTS sources_variable_idx ON sources (variable_id);
CREATE INDEX IF NOT EXISTS uploads_user_idx ON uploads (user_id);
CREATE INDEX IF NOT EXISTS api_keys_user_idx ON api_keys (user_id);
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-sql", action="store_true", help="Print SQL without connecting")
    args = parser.parse_args()
    if args.print_sql:
        print(SCHEMA_SQL)
        return

    database = os.environ.get("POSTGRES_DB")
    if not database:
        parser.error("POSTGRES_DB must be set; Docker Compose reads it from .env")

    environment = os.environ.copy()
    if "POSTGRES_PASSWORD" in environment:
        environment["PGPASSWORD"] = environment["POSTGRES_PASSWORD"]

    command = [
        "psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--single-transaction",
        "--username", environment.get("POSTGRES_USER", "postgres"),
        "--dbname", database, "--file=-",
    ]
    try:
        subprocess.run(command, input=SCHEMA_SQL, text=True, env=environment, check=True)
    except FileNotFoundError:
        parser.exit(1, "psql was not found. Run this script inside the database container.\n")
    except subprocess.CalledProcessError as error:
        parser.exit(error.returncode, "Schema creation failed; the transaction was rolled back.\n")
    print("Weather tables, relationships, and observation hypertable are ready.")


if __name__ == "__main__":
    main()
