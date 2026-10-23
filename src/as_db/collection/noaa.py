"""Callable conversion of the supplied recent 48-hour NOAA METAR R routine.

This is a recent-report diagnostic/export, not a historical calendar-day source:
pcp24hr is a rolling 24-hour amount, and the R script selects next-day reports.
Use the Synoptic collector for station-local calendar-day archive imports.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import logging
from zoneinfo import ZoneInfo

import httpx

from .synoptic import CollectionError


def parse_reports(records, target_day, zone):
    candidates = []
    report_day = target_day + timedelta(days=1)
    for record in records:
        if record.get('icaoId', record.get('stationId', 'NSTU')) != 'NSTU':
            continue
        instant = datetime.fromtimestamp(record['obsTime'], timezone.utc)
        value = record.get('pcp24hr')
        if value is not None and instant.astimezone(zone).date() == report_day:
            value = float(value)
            if 0 <= value < float('inf'):
                candidates.append((value, instant))
    if not candidates:
        return None
    inches, instant = max(candidates)
    return {'target_date': str(target_day), 'report_at': instant.isoformat(), 'precipitation_24h_mm': round(inches * 25.4, 2), 'period': 'rolling_24h'}


def fetch_recent(target_day):
    logging.getLogger('httpx').setLevel(logging.WARNING)
    try:
        with httpx.Client(timeout=30, headers={'User-Agent': 'Fieldwork-AmericanSamoa/1.0'}) as client:
            r = client.get('https://aviationweather.gov/api/data/metar', params={'ids': 'NSTU', 'hours': 48, 'format': 'json'})
            if r.status_code != 200:
                raise CollectionError(f'NOAA METAR returned HTTP {r.status_code}')
            records = r.json()
    except (httpx.HTTPError, ValueError):
        raise CollectionError('NOAA METAR request failed') from None
    return parse_reports(records, target_day, ZoneInfo('Pacific/Pago_Pago'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, default=datetime.now(ZoneInfo('Pacific/Pago_Pago')).date() - timedelta(days=1))
    args = parser.parse_args()
    print(json.dumps(fetch_recent(args.date)))


if __name__ == '__main__':
    main()
