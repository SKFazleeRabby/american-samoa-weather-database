\set ON_ERROR_STOP on

-- Time-series hypertables and aggregation functions.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Geographic types and station proximity queries.
CREATE EXTENSION IF NOT EXISTS postgis;

-- Query performance statistics; preloaded by the Dockerfile's CMD.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
