"""Collect daily summaries after each historical CSV, with bounded requests."""
import argparse
import csv
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
import logging
import math
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from as_db.history import historical_end_dates, history_manifest
from as_db.import_observations import import_observations
from as_db.variable_catalog import CATALOG, canonical_code
from as_db.web.config import ROOT, connection_kwargs, settings
from .synoptic import CollectionError, SynopticCollector
from .mesonet import MesonetFallbackCollector
from .wrcc import WRCCCollector
from .logging_config import configure_logging

logger = logging.getLogger(__name__)
LOCK = 717383451
DEFAULT_PROVIDERS = ('mesonet', 'wrcc', 'synoptic')
MESONET_IDS = {
    'Poloa Uh': '001AS', 'Aasu Uh': '002AS', 'Vaipito Uh': '003AS',
    'Afono Uh': '004AS', 'Aunuu Uh': '005AS',
}


def missing_windows(start, end, existing, max_days=31):
    """Bound contiguous requested gaps without querying one day at a time."""
    if max_days < 1:
        raise ValueError('max_days must be positive')
    first = last = None
    day = start
    while day <= end:
        if day not in existing:
            if first is None:
                first = day
            last = day
            if (last - first).days + 1 == max_days:
                yield first, last
                first = last = None
        elif first is not None:
            yield first, last
            first = last = None
        day += timedelta(days=1)
    if first is not None:
        yield first, last


def collect_provider(conn, collector, mode='backfill', start=None, end=None, history_dates=None):
    station = conn.execute('SELECT id, code, timezone FROM stations WHERE name=%s',
                           (collector.station_name,)).fetchone()
    if not station:
        raise CollectionError(f'Station {collector.station_name} is not registered')
    zone = ZoneInfo(station['timezone'])
    yesterday = datetime.now(zone).date() - timedelta(days=1)
    end = min(end or yesterday, yesterday)
    if history_dates is None:
        history_dates = historical_end_dates(history_manifest(ROOT)[1])
    codes = [code for code in collector.variable_codes if canonical_code(code) in history_dates]
    if not codes:
        raise CollectionError(f'{collector.station_name}: no historical CSV boundaries for supported variables')
    catalog = {canonical_code(row['code']): row for row in conn.execute(
        'SELECT code,unit FROM variables WHERE code=ANY(%s)',
        (list({value for code in codes for value in (code, canonical_code(code))}),))}
    for code in codes:
        registered = catalog.get(canonical_code(code))
        if registered is None or registered['unit'] != CATALOG[canonical_code(code)][1]:
            raise CollectionError(f'Missing variable or unexpected database units for {code}')
    existing, first_dates = {}, {}
    for code in codes:
        boundary = history_dates[canonical_code(code)] + timedelta(days=1)
        first = max(boundary, start or (yesterday if mode == 'daily' else boundary))
        first_dates[code] = first
        begin = datetime.combine(first, time(), zone).astimezone(timezone.utc)
        finish = datetime.combine(end + timedelta(days=1), time(), zone).astimezone(timezone.utc)
        existing[code] = {row['local_date'] for row in conn.execute(
            '''SELECT o.local_date FROM observations o JOIN variables v ON v.id=o.variable_id
               WHERE o.station_id=%s AND v.code=ANY(%s) AND o.interval_seconds=86400
               AND o.local_date BETWEEN %s AND %s
               AND o.observed_at >= %s AND o.observed_at < %s''',
            (station['id'], [code, canonical_code(code)], first, end, begin, finish))}
    gaps = {}
    for code in codes:
        for window in missing_windows(first_dates[code], end, existing[code],
                                      max_days=getattr(collector, 'max_window_days', 31)):
            gaps.setdefault(window, []).append(code)
    written = downloaded = missing = 0
    errors = []
    for (first, last), requested in sorted(gaps.items()):
        try:
            readings = collector.fetch(first, last, zone, requested)
            seen = set()
            grouped = {code: [] for code in requested}
            for reading in readings:
                if reading.variable_code not in requested or not first <= reading.day <= last:
                    raise CollectionError('Collector returned an unrequested date or variable')
                if isinstance(reading.value, bool) or not math.isfinite(reading.value):
                    raise CollectionError('Collector returned a non-finite or invalid value')
                key = (reading.day, reading.variable_code)
                if key in seen:
                    raise CollectionError('Collector returned duplicate daily values')
                seen.add(key)
                if reading.day not in existing[reading.variable_code]:
                    grouped[reading.variable_code].append(reading)
            batch_written = batch_received = 0
            with conn.transaction():
                for code, values in grouped.items():
                    if not values:
                        continue
                    source = StringIO()
                    writer = csv.writer(source)
                    writer.writerow(['Date', collector.station_name])
                    writer.writerows((reading.day.isoformat(), reading.value) for reading in values)
                    source.seek(0)
                    result = import_observations(source, canonical_code(code), connection=conn)
                    batch_written += result.written
                    batch_received += result.processed
            written += batch_written
            downloaded += batch_received
            expected = sum((last - first).days + 1 - sum(first <= day <= last for day in existing[code])
                           for code in requested)
            missing += expected - batch_received
            for code, values in grouped.items():
                existing[code].update(reading.day for reading in values)
            logger.info('%s/%s %s..%s: %s daily readings received, %s inserted',
                        collector.name, collector.station_name, first, last, batch_received, batch_written)
        except Exception as error:
            detail = str(error) if isinstance(error, (CollectionError, ValueError)) else type(error).__name__
            message = f'{collector.name}/{collector.station_name} {first}..{last}: {detail}'
            logger.error('%s', message)
            errors.append(message)
    if missing:
        message = f'{collector.name}/{collector.station_name}: {missing} daily readings unavailable or below coverage requirements'
        logger.warning('%s', message)
        errors.append(message)
    return {'provider': collector.name, 'station': collector.station_name, 'received': downloaded,
            'inserted': written, 'windows': len(gaps), 'missing': missing, 'errors': errors}


def create_collectors(name, conn, env):
    if name == 'synoptic':
        return [SynopticCollector(env.get('SYNOPTIC_TOKEN'))]
    if name == 'wrcc':
        return [WRCCCollector('Toa Ridge Wrcc', 'sam1'), WRCCCollector('Siufaga Wrcc', 'sam2')]
    if name == 'mesonet':
        with (ROOT / 'stations.csv').open(encoding='utf-8-sig', newline='') as source:
            identifiers = [row['Station ID'] for row in csv.DictReader(source)
                           if row.get('API source') == 'mesonet' and row['Status'] == 'active']
        stations = conn.execute(
            "SELECT name,external_id FROM stations WHERE status='active' AND external_id=ANY(%s) ORDER BY id",
            (identifiers,)).fetchall()
        if not stations:
            raise CollectionError('No registered active mesonet stations match stations.csv')
        return [MesonetFallbackCollector(env.get('HCDP_API'), env.get('SYNOPTIC_TOKEN'),
                                         station['name'], station['external_id'], MESONET_IDS[station['name']])
                for station in stations]
    raise CollectionError(f'Unknown collector {name}')


def run(mode='backfill', start=None, end=None, providers=None):
    if mode not in ('backfill', 'daily'):
        raise ValueError('mode must be backfill or daily')
    if start and end and start > end:
        raise ValueError('start must be on or before end')
    env = settings()
    history_dates = historical_end_dates(history_manifest(ROOT)[1])
    logger.info('Historical CSV end dates: %s', history_dates)
    results, errors = [], []
    with psycopg.connect(**connection_kwargs(admin=True), autocommit=True, row_factory=dict_row) as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (LOCK,)).fetchone()['acquired']:
            return {'results': [], 'errors': [], 'skipped': 'another collector is running'}
        try:
            for name in providers if providers is not None else DEFAULT_PROVIDERS:
                try:
                    collectors = create_collectors(name, conn, env)
                except Exception as error:
                    message = str(error) if isinstance(error, CollectionError) else f'{name}: setup failed ({type(error).__name__})'
                    logger.error('%s', message)
                    errors.append(message)
                    continue
                for collector in collectors:
                    try:
                        result = collect_provider(conn, collector, mode, start, end, history_dates)
                        results.append(result)
                        errors.extend(result['errors'])
                    except Exception as error:
                        detail = str(error) if isinstance(error, (CollectionError, ValueError)) else type(error).__name__
                        message = f'{name}/{collector.station_name}: {detail}'
                        logger.error('%s', message)
                        errors.append(message)
                    finally:
                        collector.close()
        finally:
            conn.execute('SELECT pg_advisory_unlock(%s)', (LOCK,))
    return {'results': results, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['backfill', 'daily'], default='backfill')
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--end', type=date.fromisoformat)
    parser.add_argument('--provider', action='append', choices=DEFAULT_PROVIDERS)
    args = parser.parse_args()
    if args.start and args.end and args.start > args.end:
        parser.error('--start must be on or before --end')
    configure_logging()
    outcome = run(args.mode, args.start, args.end, args.provider)
    logger.info('Collection finished: %s', outcome)
    raise SystemExit(1 if outcome['errors'] else 0)


if __name__ == '__main__':
    main()
