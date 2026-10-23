"""Python conversion of AS_WRCC_yesterday_acquisition.R (sam1 and sam2)."""
from datetime import date, datetime
from html import unescape
import logging
import re

import httpx

from .synoptic import CollectionError, Reading


def parse_wrcc(text, start, end):
    # The legacy CGI omits its closing PRE tag for years with no records.
    block = re.search(r'<pre\b[^>]*>(.*?)(?:</pre>|$)', text, re.I | re.S)
    if not block:
        raise CollectionError('WRCC response has no ASCII data block')
    text = unescape(block.group(1))
    if not re.search(r'\bDate\b', text) or not re.search(r'\bmm\b', text):
        raise CollectionError('WRCC response does not identify daily metric precipitation')
    result = []
    full_daily = all(re.search(r'\b' + word + r'\b', text) for word in ('Air', 'Temperature', 'Relative', 'Humidity'))
    if full_daily and (not re.search(r'Deg\.\s*C', text) or 'percent' not in text):
        raise CollectionError('WRCC temperature/humidity units are not metric')
    for line in text.splitlines():
        fields = line.strip().split()
        if not fields or not re.fullmatch(r'\d{2}/\d{2}/\d{4}', fields[0]):
            continue
        day = datetime.strptime(fields[0], '%m/%d/%Y').date()
        if not start <= day <= end:
            continue
        columns = [('RF_1_Tot300s', -1, 0, float('inf'))]
        if full_daily:
            if len(fields) != 15:
                raise CollectionError('WRCC daily column layout changed; no values guessed')
            columns.extend([('Tair_1_Avg', 8, -90, 65), ('RH_1_Avg', 11, 0, 100)])
        for code, index, low, high in columns:
            try:
                value = float(fields[index])
            except ValueError:
                continue  # Missing tokens affect their own variable only.
            if not low <= value <= high or not abs(value) < float('inf'):
                continue
            result.append(Reading(day, code, value))
    return result


class WRCCCollector:
    name = 'wrcc'
    variable_codes = ('RF_1_Tot300s', 'Tair_1_Avg', 'RH_1_Avg')
    max_window_days = 366

    def __init__(self, station_name, remote_id):
        self.station_name, self.remote_id = station_name, remote_id
        self.archive = {}
        logging.getLogger('httpx').setLevel(logging.WARNING)
        self.client = httpx.Client(timeout=httpx.Timeout(90, connect=15), follow_redirects=True, transport=httpx.HTTPTransport(retries=2))

    def close(self):
        self.client.close()

    def _download(self, start, end):
        p = {'stn': self.remote_id, 'smon': start.strftime('%m'), 'sday': start.strftime('%d'), 'syea': start.strftime('%y'), 'emon': end.strftime('%m'), 'eday': end.strftime('%d'), 'eyea': end.strftime('%y'), 'qBasic': 'ON', 'qPR': 'ON', 'unit': 'M', 'Ofor': 'A', 'Datareq': 'A', 'qc': 'N', 'miss': '08', 'obs': 'N', 'WsMon': '01', 'WsDay': '01', 'WeMon': '12', 'WeDay': '31'}
        try:
            r = self.client.get('https://wrcc.dri.edu/cgi-bin/wea_dysimts2.pl', params=p)
            if r.status_code != 200:
                raise CollectionError(f'WRCC returned HTTP {r.status_code}')
        except httpx.HTTPError:
            raise CollectionError('WRCC request failed') from None
        return parse_wrcc(r.text, start, end)

    def fetch(self, start, end, zone, requested):
        result = []
        for year in range(start.year, end.year + 1):
            first, last = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
            window = (first, last)
            if window not in self.archive:
                self.archive[window] = self._download(first, last)
            result.extend(self.archive[window])
        return [r for r in result if r.variable_code in requested]
