"""Callable conversion of get_data.ipynb, with bounded API pages and daily QC."""
from datetime import datetime, time, timedelta, timezone
import logging

import httpx
import pandas as pd

from .synoptic import CollectionError, Reading, SynopticCollector

VARIABLE_CODES = ('RF_1_Tot300s', 'Tair_1_Avg', 'RH_1_Avg', 'Rin_1_Avg', 'SM_1_Avg')
ENDPOINT = 'https://api.hcdp.ikewai.org/mesonet/db/measurements'


def clean_records(records, station_id, requested, zone, start, end, expected=288, threshold=.95):
    if not records:
        return []
    frame = pd.DataFrame(records)
    required = {'station_id', 'timestamp', 'variable', 'value'}
    if not required.issubset(frame.columns):
        raise CollectionError('HCDP response is missing required measurement fields')
    if not pd.to_numeric(frame.station_id, errors='raise').eq(int(station_id)).all():
        raise CollectionError('HCDP returned an unrequested station')
    frame = frame[frame.variable.isin(requested)].copy()
    if 'interval_seconds' in frame and not pd.to_numeric(frame.interval_seconds).eq(300).all():
        raise CollectionError('HCDP returned measurements with an unexpected interval')
    frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True).dt.tz_convert(zone)
    frame['value'] = pd.to_numeric(frame.value, errors='coerce')
    frame = frame[frame.value.notna() & frame.value.between(float('-inf'), float('inf'), inclusive='neither')]
    if 'flag' in frame:
        frame = frame[pd.to_numeric(frame.flag, errors='coerce').eq(0)]
    frame = frame[(frame.timestamp.dt.date >= start) & (frame.timestamp.dt.date <= end)]
    # As in the notebook pivot, duplicates at an identical timestamp count once.
    unique = frame.groupby(['variable', 'timestamp'], as_index=False).value.mean()
    unique['day'] = unique.timestamp.dt.date
    result = []
    for (code, day), values in unique.groupby(['variable', 'day']):
        if len(values) / expected < threshold:
            continue
        if code == 'RF_1_Tot300s':
            if (values.value < 0).any():
                continue
            value = values.value.sum()
        else:
            value = values.value.mean()
        result.append(Reading(day, code, round(float(value), 2)))
    return result


class MesonetCollector:
    name = 'mesonet'
    variable_codes = VARIABLE_CODES

    def __init__(self, token, station_name, station_id):
        if not token:
            raise CollectionError('HCDP_API is not configured')
        logging.getLogger('httpx').setLevel(logging.WARNING)
        self.station_name = station_name
        self.station_id = str(station_id)
        self.client = httpx.Client(headers={'Authorization': f'Bearer {token}'}, timeout=httpx.Timeout(60, connect=10), transport=httpx.HTTPTransport(retries=2))

    def close(self):
        self.client.close()

    def request(self, **params):
        try:
            r = self.client.get(ENDPOINT, params={'station_ids': self.station_id, 'location': 'american_samoa', 'row_mode': 'json', 'local_tz': 'False', 'intervals': '300', **params})
            if r.status_code != 200:
                raise CollectionError(f'HCDP returned HTTP {r.status_code}')
            records = r.json()
        except (httpx.HTTPError, ValueError):
            raise CollectionError('HCDP request failed') from None
        if not isinstance(records, list):
            raise CollectionError('Unexpected HCDP measurement response')
        return records

    def fetch(self, start, end, zone, requested):
        first = datetime.combine(start, time(), zone).astimezone(timezone.utc)
        last = datetime.combine(end + timedelta(days=1), time(), zone).astimezone(timezone.utc) - timedelta(seconds=300)
        records, offset, page_size = [], 0, 100000
        while True:
            page = self.request(var_ids=','.join(requested), start_date=first.isoformat(), end_date=last.isoformat(), limit=page_size, offset=offset, join_metadata='True')
            records.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
        # HCDP variable units are included by join_metadata. Validate before using.
        for r in records:
            unit = str(r.get('units_plain', r.get('units', ''))).lower().replace(' ', '')
            expected = {'RF_1_Tot300s': {'mm'}, 'Tair_1_Avg': {'c', '°c', 'degc', 'celsius', 'degreec', 'degreescelsius'}, 'RH_1_Avg': {'%', 'percent'}, 'Rin_1_Avg': {'w/m²', 'w/m2', 'w/m^2'}, 'SM_1_Avg': {'%', 'percent'}}
            if r.get('variable') in expected and unit not in expected[r['variable']]:
                raise CollectionError(f'Unexpected HCDP units for {r["variable"]}')
        return clean_records(records, self.station_id, requested, zone, start, end)


class MesonetFallbackCollector:
    name = 'mesonet'
    variable_codes = VARIABLE_CODES
    max_window_days = 31

    def __init__(self, hcdp_token, synoptic_token, station_name, station_id, stid):
        self.station_name, self.station_id, self.stid = station_name, station_id, stid
        self.hcdp_token, self.synoptic_token = hcdp_token, synoptic_token
        self.primary = self.fallback = None
        self.primary_unavailable = False

    def close(self):
        for client in (self.primary, self.fallback):
            if client is not None:
                client.close()

    def fetch(self, start, end, zone, requested):
        readings = {}
        if not self.primary_unavailable:
            try:
                if self.primary is None:
                    self.primary = MesonetCollector(self.hcdp_token, self.station_name, self.station_id)
                for reading in self.primary.fetch(start, end, zone, requested):
                    key = (reading.day, reading.variable_code)
                    if key in readings:
                        raise CollectionError('HCDP returned duplicate daily values')
                    readings[key] = reading
            except (CollectionError, ValueError, KeyError, TypeError) as error:
                logging.getLogger(__name__).warning('HCDP/%s failed (%s); trying Synoptic',
                                                    self.station_name, type(error).__name__)
                if not self.hcdp_token or any(value in str(error) for value in ('HTTP 401', 'HTTP 403', 'HTTP 429')):
                    self.primary_unavailable = True
        incomplete = [code for code in requested
                      if sum(key[1] == code for key in readings) < (end - start).days + 1]
        if incomplete:
            try:
                if self.fallback is None:
                    self.fallback = SynopticCollector(self.synoptic_token, self.stid,
                                                      self.station_name, self.variable_codes)
                for reading in self.fallback.fetch(start, end, zone, incomplete):
                    readings.setdefault((reading.day, reading.variable_code), reading)
            except CollectionError:
                if not readings:
                    raise
                logging.getLogger(__name__).warning('Synoptic/%s could not fill all HCDP gaps', self.station_name)
        return list(readings.values())
