"""Parameterized, timestamp-bounded queries; never average unlike observations."""
import base64
import csv
from datetime import date, datetime, time, timedelta, timezone
import io
import json

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Literal


class Filters(BaseModel):
    start: date
    end: date
    station_ids: list[int] = Field(default_factory=list, max_length=100)
    variable_ids: list[int] = Field(default_factory=list, max_length=100)
    status: Literal['active', 'inactive', 'all'] = 'active'
    categories: list[str] = Field(default_factory=list, max_length=20)
    interval_seconds: int = Field(default=86400, ge=0, le=31536000)
    sort: Literal['asc', 'desc'] = 'desc'

    @model_validator(mode='after')
    def validate_range(self):
        if self.start > self.end:
            raise ValueError('Start date must be on or before end date')
        if self.end >= date(9999, 12, 30) or self.start <= date(1, 1, 2):
            raise ValueError('Dates are outside the supported range')
        if any(i <= 0 for i in self.station_ids + self.variable_ids):
            raise ValueError('Station and variable IDs must be positive')
        if any(len(c) > 50 for c in self.categories):
            raise ValueError('Invalid station category')
        return self


DAY = "COALESCE(o.local_date, (o.observed_at AT TIME ZONE s.timezone)::date)"


def where_clause(filters):
    # UTC limits include every possible local timezone; local_date is the exact filter.
    start = datetime.combine(filters.start, time(), timezone.utc) - timedelta(hours=14)
    end = datetime.combine(filters.end + timedelta(days=1), time(), timezone.utc) + timedelta(hours=12)
    parts = ['o.observed_at >= %s', 'o.observed_at < %s', f'{DAY} BETWEEN %s AND %s', 'o.interval_seconds=%s']
    params = [start, end, filters.start, filters.end, filters.interval_seconds]
    if filters.status != 'all':
        parts.append('s.status=%s'); params.append(filters.status)
    for values, field in [(filters.station_ids, 'o.station_id'), (filters.variable_ids, 'o.variable_id'), (filters.categories, 's.category')]:
        if values:
            parts.append(f'{field}=ANY(%s)'); params.append(values)
    return ' AND '.join(parts), params


def decode_cursor(value):
    if not value:
        return None
    try:
        if len(value) > 512:
            raise ValueError()
        day, station, instant = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        instant = datetime.fromisoformat(instant)
        if instant.tzinfo is None or int(station) <= 0:
            raise ValueError()
        return date.fromisoformat(day), int(station), instant
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(422, 'Invalid pagination cursor') from None


def encode_cursor(row):
    return base64.urlsafe_b64encode(json.dumps([str(row['date']), row['station_id'], row['observed_at'].isoformat()]).encode()).decode().rstrip('=')


def observation_sql(filters, page_size=None, cursor=None, count=False):
    where, params = where_clause(filters)
    if cursor:
        day, station, instant = decode_cursor(cursor)
        operator = '>' if filters.sort == 'asc' else '<'
        where += f' AND ({DAY}, o.station_id, o.observed_at) {operator} (%s,%s,%s)'
        params += [day, station, instant]
    if count:
        return f'''SELECT count(*) AS total FROM (
            SELECT 1 FROM observations o JOIN stations s ON s.id=o.station_id
            WHERE {where} GROUP BY {DAY}, o.station_id, o.observed_at
        ) counted''', params
    direction = 'ASC' if filters.sort == 'asc' else 'DESC'
    query = f'''SELECT {DAY} AS date, o.station_id, o.observed_at,
        o.interval_seconds, jsonb_object_agg(o.variable_id::text, o.value) AS values,
        jsonb_object_agg(o.variable_id::text, o.quality_flag) AS quality
        FROM observations o JOIN stations s ON s.id=o.station_id
        WHERE {where} GROUP BY {DAY}, o.station_id, o.observed_at, o.interval_seconds
        ORDER BY date {direction}, o.station_id {direction}, o.observed_at {direction}'''
    if page_size is not None:
        query += ' LIMIT %s'; params.append(page_size + 1)
    return query, params


def csv_cell(value):
    # Prevent station names/metadata from being interpreted as spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def csv_line(cells):
    buffer = io.StringIO(newline='')
    csv.writer(buffer).writerow([csv_cell(c) for c in cells])
    return buffer.getvalue()
