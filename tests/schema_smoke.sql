\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    station bigint;
    variable bigint;
    found_tables integer;
BEGIN
    SELECT count(*) INTO found_tables FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name IN (
        'stations', 'variables', 'sources', 'observations', 'uploads', 'users', 'api_keys'
    );
    IF found_tables <> 7 THEN
        RAISE EXCEPTION 'Expected seven application tables, found %', found_tables;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public' AND hypertable_name = 'observations'
    ) THEN
        RAISE EXCEPTION 'Observations is not a hypertable';
    END IF;

    INSERT INTO stations (code, name, timezone, category)
    VALUES ('schema-smoke', 'Test Station', 'Pacific/Pago_Pago', 'weather')
    RETURNING id INTO station;
    INSERT INTO variables (code, name, unit)
    VALUES ('schema-smoke', 'Test Temperature', 'degC') RETURNING id INTO variable;
    INSERT INTO observations (station_id, variable_id, observed_at, value)
    VALUES (station, variable, '2026-01-01T00:00:00Z', 25.0);

    BEGIN
        INSERT INTO observations (station_id, variable_id, observed_at, value)
        VALUES (station, variable, '2026-01-01T00:00:00Z', 26.0);
        RAISE EXCEPTION 'Duplicate reading was accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO observations (station_id, variable_id, observed_at, value)
        VALUES (-1, variable, '2026-01-02T00:00:00Z', 25.0);
        RAISE EXCEPTION 'Missing station was accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO observations (station_id, variable_id, observed_at, value)
        VALUES (station, variable, '2026-01-02T00:00:00Z', 'NaN');
        RAISE EXCEPTION 'Nonfinite value was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO observations (station_id, variable_id, observed_at, interval_seconds, value)
        VALUES (station, variable, '2026-01-02T00:00:00Z', -1, 25.0);
        RAISE EXCEPTION 'Negative interval was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END;
$$;

ROLLBACK;
\echo Schema smoke checks passed.
