"""Synoptic daily summaries and batched five-minute fallback for UH stations."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import math

import httpx

logger = logging.getLogger(__name__)
UH_STATIONS = {'001AS', '002AS', '003AS', '004AS', '005AS'}


class CollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reading:
    day: date
    variable_code: str
    value: float


VARIABLES = {
    'Tair_1_Avg': ('air_temp', 'mean'),
    'Tair_1_Max': ('air_temp', 'max'),
    'Tair_1_Min': ('air_temp', 'min'),
    'RH_1_Avg': ('relative_humidity', 'mean'),
    'Rin_1_Avg': ('solar_radiation', 'mean'),
    'SM_1_Avg': ('soil_moisture', 'mean'),
    'RF_1_Tot300s': ('precipitation', 'total'),
}
UNITS = {
    'air_temp': {'celsius', 'c', '°c'},
    'relative_humidity': {'%', 'percent'},
    'solar_radiation': {'w/m**2', 'w/m^2', 'w/m2', 'w/m²', 'watts/meter_squared'},
    'soil_moisture': {'%', 'percent'},
    'precipitation': {'millimeters', 'millimetres', 'mm'},
}


def finite(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise CollectionError('Synoptic returned a boolean observation')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise CollectionError('Synoptic returned a non-numeric observation') from None
    if not math.isfinite(result):
        raise CollectionError('Synoptic returned a non-finite observation')
    return result


def check_unit(payload, variable, unit_variable=None):
    unit = payload.get('UNITS', {}).get(variable, '')
    if unit.lower() not in UNITS[unit_variable or variable]:
        raise CollectionError(f'Unexpected Synoptic units for {variable}')


def selected_station(payload, stid, zone):
    stations = payload.get('STATION', [])
    if any(station.get('STID', '').upper() != stid for station in stations):
        raise CollectionError('Synoptic returned an unrequested station')
    if len(stations) > 1:
        raise CollectionError('Synoptic returned duplicate stations')
    if not stations:
        return None
    station = stations[0]
    if station.get('TIMEZONE') != str(zone):
        raise CollectionError('Synoptic station timezone does not match the registered station')
    return station


def statistic_readings(payload, zone, start, end, requested, stid='NSTU'):
    station = selected_station(payload, stid, zone)
    if station is None:
        return []
    result = []
    for code in requested:
        if code == 'RF_1_Tot300s':
            continue
        sensor, statistic = VARIABLES[code]
        records = station.get('STATISTICS', {}).get(f'{sensor}_set_1', [])
        if not records:
            continue
        check_unit(payload, sensor)
        for record in records:
            period = record.get('time_period', {})
            if period.get('type') != 'day' or period.get('timezone') != str(zone):
                raise CollectionError('Synoptic did not return station-local daily statistics')
            day = date.fromisoformat(period['value'])
            value = finite(record.get(statistic))
            if value is not None and record.get('count', 0) > 0 and start <= day <= end:
                result.append(Reading(day, code, value))
    return result


def precipitation_readings(payload, zone, start, end, stid='NSTU'):
    if not payload.get('STATION'):
        return []
    check_unit(payload, 'precipitation')
    result = []
    for station in payload['STATION']:
        if station.get('STID', '').upper() != stid:
            raise CollectionError('Synoptic returned an unrequested precipitation station')
        for record in station.get('OBSERVATIONS', {}).get('precipitation', []):
            if float(record.get('interval', 0)) != 24:
                raise CollectionError('Synoptic returned an unexpected precipitation interval')
            first = datetime.fromisoformat(record['first_report'].replace('Z', '+00:00'))
            last = datetime.fromisoformat(record['last_report'].replace('Z', '+00:00'))
            if first.tzinfo is None or last.tzinfo is None:
                raise CollectionError('Synoptic precipitation timestamps lack a timezone')
            day = first.astimezone(zone).date()
            boundary = datetime.combine(day, time(), zone)
            if last < first or last > boundary + timedelta(days=1):
                raise CollectionError('Synoptic precipitation crosses a local-day boundary')
            value = finite(record.get('total'))
            if value is not None and value >= 0 and record.get('count', 0) > 0 and start <= day <= end:
                result.append(Reading(day, 'RF_1_Tot300s', value))
    return result


def report_readings(payload, zone, start, end, requested, stid):
    """Aggregate a whole request window by local day; never merge sensor positions."""
    if stid not in UH_STATIONS:
        return []
    station = selected_station(payload, stid, zone)
    if station is None:
        return []
    observations = station.get('OBSERVATIONS', {})
    stamps = [datetime.fromisoformat(value.replace('Z', '+00:00'))
              for value in observations.get('date_time', [])]
    if any(stamp.tzinfo is None for stamp in stamps):
        raise CollectionError('Synoptic report timestamps lack a timezone')
    result = []
    for code in requested:
        variable, statistic = VARIABLES[code]
        sensor = 'precip_accum_five_minute' if variable == 'precipitation' else variable
        values = observations.get(f'{sensor}_set_1')
        if values is None:
            continue
        if len(values) != len(stamps):
            raise CollectionError(f'Synoptic returned misaligned timestamps for {sensor}')
        check_unit(payload, sensor, variable)
        grouped = {}
        for stamp, value in zip(stamps, values):
            if value is None or isinstance(value, bool):
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or (variable == 'precipitation' and value < 0):
                continue
            local = stamp.astimezone(zone)
            day = (local - timedelta(microseconds=1)).date() if variable == 'precipitation' else local.date()
            if not start <= day <= end:
                continue
            reports = grouped.setdefault(day, {})
            if stamp in reports and reports[stamp] != value:
                raise CollectionError('Synoptic returned conflicting duplicate reports')
            reports[stamp] = value
        for day, reports in sorted(grouped.items()):
            midnight = datetime.combine(day, time(), zone)
            if variable == 'precipitation':
                expected = {midnight + timedelta(seconds=300 * index) for index in range(1, 289)}
                if set(reports) != expected:
                    continue
            else:
                offsets = [(stamp - midnight).total_seconds() for stamp in reports]
                if (len(reports) < math.ceil(288 * .95) or len(reports) > 288
                        or any(offset % 300 for offset in offsets)
                        or min(offsets) >= 3600 or max(offsets) < 23 * 3600):
                    continue
            numbers = list(reports.values())
            value = {'mean': lambda: sum(numbers) / len(numbers),
                     'min': lambda: min(numbers), 'max': lambda: max(numbers),
                     'total': lambda: sum(numbers)}[statistic]()
            result.append(Reading(day, code, round(value, 2)))
    return result


class SynopticCollector:
    name = 'synoptic'
    station_name = 'Airport5101'
    variable_codes = ('RF_1_Tot300s', 'Tair_1_Avg', 'RH_1_Avg')
    max_window_days = 31

    def __init__(self, token, stid='NSTU', station_name=None, variable_codes=None):
        if not token:
            raise CollectionError('SYNOPTIC_TOKEN is not configured')
        self.token, self.stid = token, stid
        self.station_name = station_name or self.station_name
        self.variable_codes = variable_codes or self.variable_codes
        self.unavailable = {}
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        self.client = httpx.Client(timeout=httpx.Timeout(90, connect=10),
                                   transport=httpx.HTTPTransport(retries=2))

    def close(self):
        self.client.close()

    def request(self, service, **params):
        if service in self.unavailable:
            raise CollectionError(self.unavailable[service])
        try:
            query = {key: value for key, value in {'token': self.token, 'stid': self.stid, **params}.items()
                     if value is not None and value != ''}
            response = self.client.get(f'https://api.synopticdata.com/v2/stations/{service}', params=query)
            if response.status_code != 200:
                message = f'Synoptic {service}: HTTP {response.status_code}'
                if response.status_code in (401, 403, 429):
                    self.unavailable[service] = message
                raise CollectionError(message)
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise CollectionError(f'Synoptic {service}: request failed') from None
        if not isinstance(data, dict):
            raise CollectionError(f'Synoptic {service}: invalid JSON response')
        code = data.get('SUMMARY', {}).get('RESPONSE_CODE')
        if code not in (1, 2):
            message = f'Synoptic {service}: response code {code}; check account access and token permissions'
            if code in (403, 429):
                self.unavailable[service] = message
            raise CollectionError(message)
        return data

    def fetch(self, start, end, zone, requested):
        readings, failures = {}, []
        begin = datetime.combine(start, time(), zone).astimezone(timezone.utc)
        finish = datetime.combine(end + timedelta(days=1), time(), zone).astimezone(timezone.utc)
        for service in ('statistics', 'precip'):
            codes = [code for code in requested if (code == 'RF_1_Tot300s') == (service == 'precip')]
            if not codes:
                continue
            try:
                if service == 'statistics':
                    payload = self.request(service, start=start.strftime('%Y%m%d'), end=end.strftime('%Y%m%d'),
                        period='day', statistic='all', obtimezone='local', units='metric',
                        vars=','.join(sorted({VARIABLES[code][0] for code in codes})))
                    values = statistic_readings(payload, zone, start, end, codes, self.stid)
                else:
                    payload = self.request(service, start=begin.strftime('%Y%m%d%H%M'),
                        end=finish.strftime('%Y%m%d%H%M'), pmode='intervals',
                        interval=24, interval_window=0, units='metric', obtimezone='local')
                    values = precipitation_readings(payload, zone, start, end, self.stid)
                for reading in values:
                    key = (reading.day, reading.variable_code)
                    if key in readings:
                        raise CollectionError('Synoptic returned duplicate daily values')
                    readings[key] = reading
            except CollectionError as error:
                failures.append(str(error))
                logger.warning('%s/%s: %s', self.name, self.station_name, error)
        incomplete = [code for code in requested
                      if sum(key[1] == code for key in readings) < (end - start).days + 1]
        if incomplete and self.stid in UH_STATIONS:
            try:
                variables = {('precip_accum_five_minute' if code == 'RF_1_Tot300s' else VARIABLES[code][0])
                             for code in incomplete}
                payload = self.request('timeseries', start=begin.strftime('%Y%m%d%H%M'),
                    end=finish.strftime('%Y%m%d%H%M'), vars=','.join(sorted(variables)),
                    units='metric', obtimezone='local', qc='on', qc_remove_data='on', qc_checks='sl_range_check')
                for reading in report_readings(payload, zone, start, end, incomplete, self.stid):
                    readings.setdefault((reading.day, reading.variable_code), reading)
            except CollectionError as error:
                failures.append(str(error))
        if not readings and failures:
            raise CollectionError('; '.join(failures))
        return list(readings.values())
