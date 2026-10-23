"""Insert station information from a pandas DataFrame."""

import re
import unicodedata
from zoneinfo import ZoneInfo

from as_db.database import ImportResult, import_transaction, normalized_frame, number, text


def insert_stations(df, *, timezone='Pacific/Pago_Pago', connection=None, env_file=None):
    """Upsert by code, preserving station IDs. Accept CSV headers or DB names.

    If code is absent, generate a stable code from the station name. Keep this
    code in later DataFrames when renaming a station. Existing values are
    replaced, including blanks. Input DataFrames are never modified.
    """
    frame = normalized_frame(df, {
        'Station': 'name', 'Station Code': 'code', 'Code': 'code',
        'Station ID': 'external_id', 'Observer': 'operator',
        'Elevetaion': 'elevation_m', 'Elevation': 'elevation_m',
        'Latitude': 'latitude', 'Longitude': 'longitude',
        'Category': 'category', 'Status': 'status', 'Timezone': 'timezone',
    }, ('name',))
    records, seen = [], set()
    for position, row in enumerate(frame.to_dict('records'), 1):
        name = text(row['name'])
        if not name:
            raise ValueError(f'Row {position}: station name is required')
        code = text(row.get('code'))
        if not code:
            ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
            code = re.sub(r'[^a-z0-9]+', '_', ascii_name.lower()).strip('_')
        if not code or code in seen:
            raise ValueError(f'Row {position}: empty or duplicate station code {code!r}')
        seen.add(code)
        zone = text(row.get('timezone')) or timezone
        ZoneInfo(zone)
        latitude = number(row.get('latitude'), f'Row {position} latitude')
        longitude = number(row.get('longitude'), f'Row {position} longitude')
        if latitude is not None and not -90 <= latitude <= 90:
            raise ValueError(f'Row {position}: latitude is outside [-90, 90]')
        if longitude is not None and not -180 <= longitude <= 180:
            raise ValueError(f'Row {position}: longitude is outside [-180, 180]')
        external_id = text(row.get('external_id'))
        # pandas commonly reads nullable integer identifiers as floats.
        raw_id = row.get('external_id')
        if isinstance(raw_id, float) and external_id is not None and raw_id.is_integer():
            external_id = str(int(raw_id))
        records.append((code, name, external_id, text(row.get('operator')),
                        latitude, longitude, number(row.get('elevation_m'), 'Elevation'),
                        zone, text(row.get('category')) or 'weather',
                        text(row.get('status')) or 'active'))
    with import_transaction(connection, env_file) as conn, conn.cursor() as cur:
        cur.executemany('''
            INSERT INTO stations
                (code, name, external_id, operator, latitude, longitude,
                 elevation_m, timezone, category, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (code) DO UPDATE SET
                name=EXCLUDED.name, external_id=EXCLUDED.external_id,
                operator=EXCLUDED.operator, latitude=EXCLUDED.latitude,
                longitude=EXCLUDED.longitude, elevation_m=EXCLUDED.elevation_m,
                timezone=EXCLUDED.timezone, category=EXCLUDED.category, status=EXCLUDED.status
        ''', records)
        written = cur.rowcount
    return ImportResult(processed=len(records), written=written)
