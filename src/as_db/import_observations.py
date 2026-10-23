"""Import a Date-by-station CSV into observations for one registered variable."""

import argparse
from contextlib import nullcontext
import csv
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

from as_db.database import ImportResult, import_transaction, normalized_frame, number, text
from as_db.variable_catalog import ALIASES


def observation_time(value, zone):
    """Interpret dates/naive datetimes in station time; return UTC and local date."""
    try:
        if text(value) is None or isinstance(value, (int, float, bool)):
            raise ValueError('Expected a date or timestamp')
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.nanosecond:
            raise ValueError('Expected a timestamp with at most microsecond precision')
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(zone, ambiguous='raise', nonexistent='raise')
        local = stamp.tz_convert(zone)
        return stamp.tz_convert('UTC').to_pydatetime(), local.date()
    except Exception as exc:
        raise ValueError(f'Invalid or ambiguous date/time {value!r} for {zone}') from exc


def import_observations(
    csv_path, variable_name, *, interval_seconds=86400,
    on_conflict='skip', skip_unknown_stations=False, quality_flag='unreviewed',
    upload_id=None, connection=None, env_file=None,
):
    """Import one CSV atomically; only observations are written.

    Match a registered variable name or code, ignoring case and outer whitespace.
    Station headers may be names or codes. Blank/NA readings are skipped, zeros
    retained, and dates interpreted in station time. Values must use the variable's
    registered units. Daily intervals default to 86400 seconds. Existing readings
    are skipped unless on_conflict='update'. Empty unknown columns are ignored;
    populated unknown columns fail unless skip_unknown_stations=True.
    """
    source_context = (nullcontext(csv_path) if hasattr(csv_path, 'read')
                      else Path(csv_path).open(encoding='utf-8-sig', newline=''))
    with source_context as source:
        rows = csv.reader(source, strict=True)
        try:
            headers = next(rows)
        except StopIteration as exc:
            raise ValueError('CSV is empty; expected Date and station columns') from exc
        headers = [header.strip() for header in headers]
        headers = ['Date' if header.casefold() == 'date' else header for header in headers]
        if any(not header for header in headers):
            raise ValueError('CSV column names must not be blank')
        if len(headers) != len(set(headers)):
            raise ValueError('Duplicate CSV column names')
        if 'Date' not in headers or len(headers) < 2:
            raise ValueError('Expected Date and at least one station column')
        for position, row in enumerate(rows, 2):
            if row and len(row) != len(headers):
                raise ValueError(f'CSV row {position}: expected {len(headers)} fields, got {len(row)}')
        source.seek(0)
        frame = pd.read_csv(source, dtype=str)
        frame.columns = headers
    return _insert_observations(
        frame, variable_name=variable_name, interval_seconds=interval_seconds,
        on_conflict=on_conflict, skip_unknown_stations=skip_unknown_stations,
        quality_flag=quality_flag, upload_id=upload_id,
        connection=connection, env_file=env_file,
    )


def _insert_observations(
    df, *, variable_name, interval_seconds, date_column='Date',
    on_conflict='skip', skip_unknown_stations=False, quality_flag='unreviewed',
    upload_id=None, connection=None, env_file=None,
):
    if type(interval_seconds) is not int or not 0 <= interval_seconds <= 2147483647:
        raise ValueError('interval_seconds must be a nonnegative 32-bit integer')
    if on_conflict not in ('skip', 'update'):
        raise ValueError("on_conflict must be 'skip' or 'update'")
    if quality_flag not in ('unreviewed', 'valid', 'suspect', 'invalid'):
        raise ValueError('Invalid quality_flag')
    frame = normalized_frame(df, {}, (date_column,))
    date_index = frame.columns.get_loc(date_column)
    columns = [(i, c) for i, c in enumerate(frame.columns) if c != date_column]
    if not columns:
        raise ValueError('Expected at least one station column')

    processed = missing = 0
    with import_transaction(connection, env_file) as conn, conn.cursor(row_factory=tuple_row) as cur:
        variable_key = text(variable_name)
        if variable_key is None:
            raise ValueError('Variable name must not be blank')
        variable_key = variable_key.casefold()
        aliases = {alias.casefold(): code for alias, code in ALIASES.items()}
        keys = [variable_key, aliases.get(variable_key, variable_key)]
        cur.execute('''SELECT id FROM variables
            WHERE lower(btrim(code)) = ANY(%s) OR lower(btrim(name)) = ANY(%s)''',
                    (keys, keys))
        variables = cur.fetchall()
        if not variables:
            raise ValueError(f'Unknown variable {variable_name!r}; register it before importing observations')
        if len(variables) != 1:
            raise ValueError(f'Ambiguous variable {variable_name!r}; use a unique variable code')
        variable_id = variables[0][0]
        cur.execute('SELECT id, code, name, timezone FROM stations')
        candidates = {}
        for station_id, code, name, timezone in cur.fetchall():
            for key in {code.strip().casefold(), name.strip().casefold()}:
                candidates.setdefault(key, {})[station_id] = timezone
        resolved, unknown, seen = [], [], set()
        for index, column in columns:
            matches = candidates.get(column.casefold(), {})
            if not matches:
                unknown.append(column)
                if frame.iloc[:, index].map(text).isna().all():
                    continue
                if not skip_unknown_stations:
                    raise ValueError(f'Unknown station column with readings: {column}')
                continue
            if len(matches) != 1:
                raise ValueError(f'Ambiguous station header {column!r}; use a unique station code')
            station_id, timezone = next(iter(matches.items()))
            if station_id in seen:
                raise ValueError(f'Multiple columns refer to station {column!r}')
            seen.add(station_id)
            resolved.append((index, column, station_id, ZoneInfo(timezone)))
        if not resolved:
            raise ValueError('No station columns could be matched')

        staging = sql.Identifier('observation_import_' + uuid4().hex)
        cur.execute(sql.SQL('''CREATE TEMP TABLE {} (
            station_id bigint, variable_id bigint, observed_at timestamptz,
            interval_seconds integer, local_date date, value double precision,
            quality_flag text, upload_id bigint,
            PRIMARY KEY (station_id, variable_id, interval_seconds, observed_at)
        ) ON COMMIT DROP''').format(staging))
        with cur.copy(sql.SQL('COPY {} FROM STDIN').format(staging)) as copy:
            for position, row in enumerate(frame.itertuples(index=False, name=None), 1):
                time_by_zone = {}
                for index, column, station_id, zone in resolved:
                    if zone not in time_by_zone:
                        time_by_zone[zone] = observation_time(row[date_index], zone)
                    observed_at, local_date = time_by_zone[zone]
                    value = number(row[index], f'Row {position}, station {column}')
                    if value is None:
                        missing += 1
                        continue
                    copy.write_row((station_id, variable_id, observed_at, interval_seconds,
                                    local_date, value, quality_flag, upload_id))
                    processed += 1
        action = sql.SQL('DO NOTHING') if on_conflict == 'skip' else sql.SQL('''
            DO UPDATE SET value=EXCLUDED.value, quality_flag=EXCLUDED.quality_flag,
                local_date=EXCLUDED.local_date, upload_id=EXCLUDED.upload_id
        ''')
        cur.execute(sql.SQL('''
            INSERT INTO observations
                (station_id, variable_id, observed_at, interval_seconds,
                 local_date, value, quality_flag, upload_id)
            SELECT * FROM {} WHERE true
            ON CONFLICT (station_id, variable_id, interval_seconds, observed_at) {}
        ''').format(staging, action))
        written = cur.rowcount
        cur.execute(sql.SQL('DROP TABLE {}').format(staging))
    return ImportResult(processed, written, processed - written, missing, tuple(unknown))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_path', type=Path, help='CSV with Date and station columns')
    parser.add_argument('variable_name', help='Registered variable name or code')
    parser.add_argument('--interval-seconds', type=int, default=86400,
                        help='Reporting interval: 86400 daily (default), 300 five-minute, 0 instantaneous')
    parser.add_argument('--on-conflict', choices=('skip', 'update'), default='skip')
    parser.add_argument('--skip-unknown-stations', action='store_true')
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args(argv)
    try:
        result = import_observations(
            args.csv_path, args.variable_name, interval_seconds=args.interval_seconds,
            on_conflict=args.on_conflict, skip_unknown_stations=args.skip_unknown_stations,
            env_file=args.env_file,
        )
    except (OSError, ValueError, csv.Error, pd.errors.ParserError, psycopg.Error) as exc:
        parser.exit(1, f'Import failed: {exc}\n')
    print(f'Import committed: {result.written:,} written; {result.skipped:,} already present; '
          f'{result.missing:,} missing cells.')
    if result.ignored_stations:
        print('Ignored station columns: ' + ', '.join(result.ignored_stations))


if __name__ == '__main__':
    main()
